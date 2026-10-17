// keys_reader.cpp — see keys_reader.hpp. The Python it ports is named at each
// rule: `drivers/uart_input.py` (the line), `keys_service.py` (the UI's
// socket), `fastnote.FastNotes.key` (the note).
#include "keys_reader.hpp"

#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace arb::host {

namespace {

constexpr int64_t kProbeMod = int64_t(1) << 31;              // uart_input.PROBE_WRAP

// One decimal field of a `KP:rr:cc` / `EN:ii:±ddd` line: an optional sign and
// 1-6 digits, the whole field (Python's int() on the field; `00-1` is #613's
// malformed line and must not parse).
bool field(const char* s, std::size_t n, int& out) {
    std::size_t i = 0;
    bool neg = false;
    if (i < n && (s[i] == '-' || s[i] == '+')) neg = s[i++] == '-';
    if (i == n || n - i > 6) return false;
    int v = 0;
    for (; i < n; ++i) {
        if (s[i] < '0' || s[i] > '9') return false;
        v = v * 10 + (s[i] - '0');
    }
    out = neg ? -v : v;
    return true;
}

// `kind:a:b` after the two-letter kind and its colon.
bool two_fields(const char* s, std::size_t n, int& a, int& b) {
    const char* colon = static_cast<const char*>(std::memchr(s, ':', n));
    if (!colon) return false;
    const std::size_t na = std::size_t(colon - s);
    return field(s, na, a) && field(colon + 1, n - na - 1, b);
}

// The value after `"key":` in one JSON line, whitespace skipped; nullptr if
// absent. A scanner, not a parser: the UI's two commands are flat objects of
// known keys (keys_client.py writes them), and a JSON library allocates.
const char* value_of(const char* s, const char* key) {
    char pat[24];
    std::snprintf(pat, sizeof pat, "\"%s\"", key);
    const char* p = std::strstr(s, pat);
    if (!p) return nullptr;
    p += std::strlen(pat);
    while (*p == ' ') ++p;
    if (*p != ':') return nullptr;
    ++p;
    while (*p == ' ') ++p;
    return p;
}

int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// The ASCII whitespace Python's bytes.strip() takes off (\v and \f included).
bool blank(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\v' || c == '\f';
}

bool named(int r, int c) {
    return r >= 0 && r < keys::kRows && c >= 0 && c < keys::kCols &&
           keys::kNoteIndex[r][c] != keys::kSpare;
}

}  // namespace

// ---- the port ------------------------------------------------------------------

void KeysReader::feed(const uint8_t* data, std::size_t n, int64_t now_ns) {
    for (std::size_t i = 0; i < n; ++i) {
        const char ch = char(data[i]);
        if (ch == '\n') {
            if (!overflow_) line(buf_, len_, now_ns);
            len_ = 0;
            overflow_ = false;
            continue;
        }
        if (overflow_) continue;
        if (len_ == kLineMax) {               // 64 bytes and no newline: dropped
            ++n_.malformed;
            overflow_ = true;
            continue;
        }
        buf_[len_++] = ch;
    }
}

void KeysReader::line(char* s, std::size_t n, int64_t now_ns) {
    // Python's bytes.strip(): the firmware ends lines with \r\n.
    while (n && blank(s[n - 1])) --n;
    std::size_t lead = 0;
    while (lead < n && blank(s[lead])) ++lead;
    s += lead;
    n -= lead;
    if (!n) return;
    s[n] = '\0';
    last_line_ns_ = now_ns;

    if (n == 4 && std::memcmp(s, "PONG", 4) == 0) {
        pong_ = true;
        missed_ = 0;
        // A handshake that ran out of attempts left NO KEYBOARD; the watchdog's
        // PING answered after it is the keyboard back, with no boot banner to
        // say so: step 4 now (#827, § 6.2.3).
        if (!handshaking_ && status_ == Status::NoKeyboard) {
            say("keys: the ATmega answered after a failed handshake — configuring it");
            configure();
            set_status(Status::Ok);
        }
        return;
    }
    // `in`, not `==`: a reset cuts the line it was sending (`KP:0KEYBOARD_…`).
    if (std::strstr(s, "KEYBOARD_READY")) {
        if (!handshaking_) unrequested_reset(now_ns);
        return;
    }
    // `KP:` alone is a line that did not parse, as uart_input.py counts it (#1020).
    const bool kp = n >= 3 && std::memcmp(s, "KP:", 3) == 0;
    const bool kr = n >= 3 && std::memcmp(s, "KR:", 3) == 0;
    const bool en = n >= 3 && std::memcmp(s, "EN:", 3) == 0;
    if (!kp && !kr && !en) return;           // BL:n, V…, MCP:OK, the K dump
    int a = 0, b = 0;
    if (!two_fields(s + 3, n - 3, a, b)) {
        ++n_.malformed;
        return;
    }
    if (en) {
        char out[96];
        const int m = std::snprintf(out, sizeof out, "{\"t\":\"enc\",\"i\":%d,\"d\":%d,\"at\":%.6f}\n",
                                    a, b, double(now_ns) / 1e9);
        if (ui_ && to_ui(out, m)) ++n_.forwarded;
        return;
    }
    key(a, b, kp, now_ns);
}

void KeysReader::key(int r, int c, bool pressed, int64_t now_ns) {
    if (!named(r, c)) return;                 // a spare, or a garbled position
    down_[r][c] = pressed;
    int32_t probe = -1;
    if (probe_ && pressed) probe = int32_t((now_ns / 1000) % kProbeMod);
    const bool sounded = sound(r, c, pressed, probe, now_ns);
    forward_key(r, c, pressed, sounded, probe, now_ns);
}

// fastnote.FastNotes.key: the snapshot, the held map, a lost KR.
bool KeysReader::sound(int r, int c, bool pressed, int32_t probe, int64_t now_ns) {
    const int idx = keys::kNoteIndex[r][c];
    if (idx < 0) return false;                // not a chromatic key: never sounds
    if (!pressed) {
        // A release never asks the snapshot: nothing may stay sounding
        // because the gate closed while the key was down.
        const int note = held_[r][c];
        if (note < 0) return false;           // not sounded here; the tick owns it
        held_[r][c] = -1;
        if (note_off(r, c, note, now_ns)) ++n_.sounded;
        return true;                          // pushed, or due: it will go
    }
    if (!snap_.sound) {
        ++n_.refused_quiet;
        return false;
    }
    // An off still waiting for the ring goes first; while one waits, no on
    // may pass it (it could be its own note's).
    retry_offs(now_ns);
    if (n_off_due_) {
        ++n_.ring_full;
        return false;
    }
    const int note = keys::kNoteBaseMidi + idx + snap_.transpose;
    const int stale = held_[r][c];
    // The same key down twice with no release between (a lost KR, or OCTAVE
    // moved): let go of what is sounding before adding to it.
    if (stale >= 0 && stale != note) {
        held_[r][c] = -1;
        note_off(r, c, stale, now_ns);
        if (n_off_due_) {
            ++n_.ring_full;
            return false;
        }
    }
    held_[r][c] = int16_t(note);
    if (!sink_.note(note, true, snap_.velocity, probe, now_ns)) {
        // The same note already on the ring is still sounding: keep holding
        // it, so its release lets it go (#827).
        if (stale != note) held_[r][c] = -1;
        ++n_.ring_full;
        return false;
    }
    ++n_.sounded;
    return true;
}

// keys_service.KeysService._forward: a release goes only to the connection
// its press went to.
void KeysReader::forward_key(int r, int c, bool pressed, bool sounded, int32_t probe,
                             int64_t now_ns) {
    if (!ui_) return;
    if (pressed) {
        sent_down_[r][c] = true;
    } else {
        if (!sent_down_[r][c]) return;        // its press went to an earlier UI
        sent_down_[r][c] = false;
    }
    char pr[16];
    if (probe >= 0) std::snprintf(pr, sizeof pr, "%d", probe);
    else std::memcpy(pr, "null", 5);
    char out[128];
    const int m = std::snprintf(out, sizeof out,
                                "{\"t\":\"key\",\"r\":%d,\"c\":%d,\"p\":%s,\"s\":%s,\"probe\":%s,\"at\":%.6f}\n",
                                r, c, pressed ? "true" : "false", sounded ? "true" : "false", pr,
                                double(now_ns) / 1e9);
    if (to_ui(out, m)) ++n_.forwarded;
}

bool KeysReader::to_ui(const char* s, int n) {
    if (!ui_ || n <= 0) return false;
    if (sink_.ui(s, std::size_t(n))) return true;
    ui_drop("a write to the UI failed");
    return false;
}

void KeysReader::handshake_begin() {
    handshaking_ = true;
    pong_ = false;
}

void KeysReader::handshake_end(bool ok, int64_t now_ns) {
    handshaking_ = false;
    missed_ = 0;
    last_line_ns_ = now_ns;
    last_ping_ns_ = now_ns;
    if (ok) {
        configure();
        set_status(Status::Ok);
    } else {
        set_status(Status::NoKeyboard);
    }
}

void KeysReader::port_lost(int64_t now_ns) {
    release_held(now_ns);
    len_ = 0;
    overflow_ = false;
    set_status(Status::NoKeyboard);
}

void KeysReader::configure() {
    const uint8_t b[] = {'B', kDebounceReads};
    const uint8_t l[] = {'L', brightness_};
    sink_.port(b, 2);
    sink_.port(l, 2);
    sink_.port(reinterpret_cast<const uint8_t*>("S"), 1);
    sink_.port(reinterpret_cast<const uint8_t*>("E"), 1);
}

// uart_input.UartInput._watchdog.
KeysReader::Watchdog KeysReader::tick(int64_t now_ns) {
    if (now_ns - last_line_ns_ < kIdlePingNs) return Watchdog::None;
    if (now_ns - last_ping_ns_ < kIdlePingNs) return Watchdog::None;
    last_ping_ns_ = now_ns;
    if (pong_) {
        pong_ = false;
        missed_ = 0;
    } else {
        ++missed_;
    }
    sink_.port(reinterpret_cast<const uint8_t*>("P"), 1);
    if (missed_ < kMissedPongs) return Watchdog::None;
    say("keys: %d missed PONGs — re-initialising the ATmega", missed_);
    release_held(now_ns);
    missed_ = 0;
    return Watchdog::Reinit;
}

int64_t KeysReader::next_deadline_ns() const {
    const int64_t a = last_line_ns_ + kIdlePingNs, b = last_ping_ns_ + kIdlePingNs;
    return a > b ? a : b;
}

// #614: the ATmega restarted on its own; scanning is off until it hears `S`.
void KeysReader::unrequested_reset(int64_t now_ns) {
    say("keys: the ATmega reset on its own — re-sending its set-up");
    release_held(now_ns);
    configure();
    missed_ = 0;
    set_status(Status::Ok);
}

// A re-init loses the matrix state: release every held key, the note here and
// the key at the UI.
void KeysReader::release_held(int64_t now_ns) {
    for (int r = 0; r < R; ++r)
        for (int c = 0; c < C; ++c) {
            if (!down_[r][c]) continue;
            down_[r][c] = false;
            const bool sounded = sound(r, c, false, -1, now_ns);
            forward_key(r, c, false, sounded, -1, now_ns);
        }
}

void KeysReader::release_notes() {
    for (int r = 0; r < R; ++r)
        for (int c = 0; c < C; ++c) {
            const int note = held_[r][c];
            if (note < 0) continue;
            held_[r][c] = -1;
            note_off(r, c, note, last_line_ns_);
        }
}

// The one way a held note is let go: onto the ring (true), or due until it
// fits (false, counted as ring full).
bool KeysReader::note_off(int r, int c, int note, int64_t now_ns) {
    if (sink_.note(note, false, 0.0f, -1, now_ns)) return true;
    ++n_.ring_full;
    off_due_[r][c] = int16_t(note);     // empty: a press is refused while any is due
    ++n_off_due_;
    return false;
}

void KeysReader::retry_offs(int64_t now_ns) {
    for (int r = 0; r < R && n_off_due_; ++r)
        for (int c = 0; c < C && n_off_due_; ++c) {
            const int note = off_due_[r][c];
            if (note < 0) continue;
            if (!sink_.note(note, false, 0.0f, -1, now_ns)) return;   // still full
            off_due_[r][c] = -1;
            --n_off_due_;
        }
}

void KeysReader::set_status(Status s) {
    if (s == status_) return;
    status_ = s;
    say("keys: %s", s == Status::Ok ? "keyboard ok" : "no keyboard");
    send_status();
}

void KeysReader::send_status() {
    if (status_ == Status::Unknown) return;
    const char* line = status_ == Status::Ok ? "{\"t\":\"status\",\"text\":\"KEYBOARD OK\"}\n"
                                             : "{\"t\":\"status\",\"text\":\"NO KEYBOARD\"}\n";
    to_ui(line, int(std::strlen(line)));
}

// ---- the UI ------------------------------------------------------------------

void KeysReader::ui_connect() {
    ui_ = true;
    snapped_ = false;
    snap_ = Snapshot{};
    std::memset(sent_down_, 0, sizeof sent_down_);
    ulen_ = 0;
    uoverflow_ = false;
    say("keys: the UI connected");
    send_status();
}

// The stuck-note rule: no UI, nothing held and nothing sounds.
void KeysReader::ui_drop(const char* why) {
    if (!ui_) return;
    ui_ = false;
    snapped_ = false;
    snap_ = Snapshot{};
    std::memset(sent_down_, 0, sizeof sent_down_);
    int held = 0;
    for (auto& row : held_)
        for (int16_t h : row) held += h >= 0;
    release_notes();
    ++n_.ui_drops;
    say("keys: %s — %d held note%s released", why, held, held == 1 ? "" : "s");
}

void KeysReader::ui_feed(const uint8_t* data, std::size_t n) {
    for (std::size_t i = 0; i < n && ui_; ++i) {
        const char ch = char(data[i]);
        if (ch == '\n') {
            if (!uoverflow_) {
                ubuf_[ulen_] = '\0';
                ui_line(ubuf_, ulen_);
            }
            ulen_ = 0;
            uoverflow_ = false;
            continue;
        }
        if (uoverflow_) continue;
        if (ulen_ == kUiLineMax) {
            say("keys: a line from the UI is over %zu bytes — dropped", kUiLineMax);
            uoverflow_ = true;
            continue;
        }
        ubuf_[ulen_++] = ch;
    }
}

void KeysReader::ui_line(char* s, std::size_t n) {
    if (!n) return;
    const char* t = value_of(s, "t");
    if (t && std::strncmp(t, "\"snap\"", 6) == 0) {
        Snapshot snap{};
        const char* v = value_of(s, "sound");
        snap.sound = v && std::strncmp(v, "true", 4) == 0;
        if ((v = value_of(s, "transpose"))) snap.transpose = int(std::strtol(v, nullptr, 10));
        if ((v = value_of(s, "velocity"))) snap.velocity = std::strtof(v, nullptr);
        snap_ = snap;
        if (!snapped_) {
            snapped_ = true;
            // The bench waits for this line before it presses (#578).
            say("keys: the UI's first snapshot (sound %s)", snap.sound ? "on" : "off");
        }
        return;
    }
    if (t && std::strncmp(t, "\"send\"", 6) == 0) {
        const char* h = value_of(s, "hex");
        if (!h || *h != '"') return;
        ++h;
        uint8_t bytes[kUiLineMax / 2];
        std::size_t m = 0;
        while (h[0] && h[0] != '"' && m < sizeof bytes) {
            const int hi = hexval(h[0]), lo = h[1] ? hexval(h[1]) : -1;
            if (hi < 0 || lo < 0) return;     // keys_service: not hex, not sent
            bytes[m++] = uint8_t(hi * 16 + lo);
            h += 2;
        }
        if (!m) return;
        // The backlight a re-init restores (§ 1.3.4 part 1: arbutus-keys
        // re-sent its own 255 there).
        if (m == 2 && bytes[0] == 'L') brightness_ = bytes[1];
        sink_.port(bytes, m);
        return;
    }
    say("keys: a line from the UI is not a command: %.80s", s);
}

int KeysReader::summary(char* buf, std::size_t n) const {
    return std::snprintf(buf, n,
                         "keys fast notes: %ld sounded, %ld events forwarded, %ld UI disconnects; "
                         "presses left to the tick: %ld quiet (no snapshot, or it said no), "
                         "%ld ring full; %ld malformed lines",
                         n_.sounded, n_.forwarded, n_.ui_drops, n_.refused_quiet, n_.ring_full,
                         n_.malformed);
}

void KeysReader::say(const char* fmt, ...) {
    char line[192];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    sink_.log(line);
}

}  // namespace arb::host
