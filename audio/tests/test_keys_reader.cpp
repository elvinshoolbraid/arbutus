// host/keys_reader — the key thread's state machine (#638, device-os-spec.md
// § 1.3.4 part 8 step 2): bytes in, notes and UI lines out, against a sink of
// arrays. No framework (test_events.cpp is the pattern), and the same counting
// operator new: 10 000 mixed lines must allocate nothing.

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <new>
#include <string>
#include <vector>

#include <keys_reader.hpp>

namespace {

std::atomic<long> g_news{0};
int failures = 0;

#define CHECK(cond, ...)                                                        \
    do {                                                                        \
        if (!(cond)) {                                                          \
            std::printf("FAIL %s:%d  " #cond "\n      ", __FILE__, __LINE__);   \
            std::printf(__VA_ARGS__);                                           \
            std::printf("\n");                                                  \
            ++failures;                                                         \
        }                                                                       \
    } while (0)

using arb::host::KeysReader;
namespace keys = arb::host::keys;

struct Note { int note; bool on; float vel; int32_t probe; };

// The sink as arrays, sized up front so the counting test's sink allocates
// nothing either; `record` off keeps the strings out of it.
struct Sink final : arb::host::KeysSink {
    std::vector<Note> notes;
    std::vector<std::string> ui_lines, logs;
    std::string port_bytes;
    bool record = true;
    bool ring_full = false, ui_wedged = false;
    long n_notes = 0, n_ui = 0;

    bool note(int n, bool on, float vel, int32_t probe, int64_t) override {
        if (ring_full) return false;
        ++n_notes;
        if (record) notes.push_back({n, on, vel, probe});
        return true;
    }
    bool ui(const char* s, std::size_t n) override {
        if (ui_wedged) return false;
        ++n_ui;
        if (record) ui_lines.emplace_back(s, n);
        return true;
    }
    void port(const uint8_t* d, std::size_t n) override {
        if (record) port_bytes.append(reinterpret_cast<const char*>(d), n);
    }
    void log(const char* s) override {
        if (record) logs.emplace_back(s);
    }
    void clear() {
        notes.clear();
        ui_lines.clear();
        logs.clear();
        port_bytes.clear();
    }
};

constexpr int64_t kMs = 1'000'000;

void feed(KeysReader& k, const char* s, int64_t now = 1000 * kMs) {
    k.feed(reinterpret_cast<const uint8_t*>(s), std::strlen(s), now);
}
void ui(KeysReader& k, const char* s) {
    k.ui_feed(reinterpret_cast<const uint8_t*>(s), std::strlen(s));
}
// A reader past its handshake, a UI connected, and that UI's first `snap`.
void ready(KeysReader& k, Sink& s, bool sound = true, int transpose = 0) {
    k.handshake_begin();
    k.handshake_end(true, 0);
    k.ui_connect();
    char snap[96];
    std::snprintf(snap, sizeof snap, "{\"t\":\"snap\",\"sound\":%s,\"transpose\":%d,\"velocity\":1.0}\n",
                  sound ? "true" : "false", transpose);
    ui(k, snap);
    s.clear();
}
bool has(const std::vector<std::string>& v, const char* needle) {
    for (const auto& x : v)
        if (x.find(needle) != std::string::npos) return true;
    return false;
}

void test_every_position() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    for (int r = 0; r < keys::kRows; ++r)
        for (int c = 0; c < keys::kCols; ++c) {
            s.clear();
            char line[32];
            std::snprintf(line, sizeof line, "KP:%02d:%02d\r\nKR:%02d:%02d\r\n", r, c, r, c);
            feed(k, line);
            const int idx = keys::kNoteIndex[r][c];
            if (idx == keys::kSpare) {
                CHECK(s.ui_lines.empty() && s.notes.empty(), "(%d,%d) is spare: nothing", r, c);
                continue;
            }
            CHECK(s.ui_lines.size() == 2, "(%d,%d): press and release forwarded, got %zu", r, c,
                  s.ui_lines.size());
            if (idx < 0) {
                CHECK(s.notes.empty(), "(%d,%d) is not a note key and sounded", r, c);
                CHECK(has(s.ui_lines, "\"s\":false"), "(%d,%d) forwarded unsounded", r, c);
                continue;
            }
            CHECK(s.notes.size() == 2 && s.notes[0].on && !s.notes[1].on &&
                      s.notes[0].note == 53 + idx && s.notes[1].note == 53 + idx,
                  "(%d,%d) NOTE_%d: on and off of %d", r, c, idx, 53 + idx);
            CHECK(s.ui_lines.size() == 2 && s.ui_lines[0].find("\"s\":true") != std::string::npos,
                  "(%d,%d) forwarded as sounded", r, c);
        }
    for (int r = 0; r < 4; ++r)
        CHECK(keys::kNoteIndex[r][11] == keys::kNever, "COL11 (encoder push %d) never sounds", r);
    CHECK(k.counters().sounded == 48, "24 note keys, press and release: %ld", k.counters().sounded);
}

void test_the_ui_line_is_keys_service_s() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    feed(k, "KP:03:00\n", 12'345'678'000);
    CHECK(s.ui_lines.size() == 1 &&
              s.ui_lines[0] == "{\"t\":\"key\",\"r\":3,\"c\":0,\"p\":true,\"s\":true,\"probe\":null,\"at\":12.345678}\n",
          "got %s", s.ui_lines.empty() ? "(none)" : s.ui_lines[0].c_str());
    s.clear();
    feed(k, "EN:02:-3\n", 1'500'000'000);
    CHECK(s.ui_lines.size() == 1 && s.ui_lines[0] == "{\"t\":\"enc\",\"i\":2,\"d\":-3,\"at\":1.500000}\n",
          "got %s", s.ui_lines.empty() ? "(none)" : s.ui_lines[0].c_str());
}

void test_lines_split_anywhere() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    const char* all = "KP:03:04\r\nKR:03:04\r\n";
    for (const char* p = all; *p; ++p) k.feed(reinterpret_cast<const uint8_t*>(p), 1, 0);
    CHECK(s.notes.size() == 2, "a byte at a time: %zu notes", s.notes.size());
}

void test_malformed_lines_are_counted_and_reading_goes_on() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    feed(k, "EN:00:00-1\nKP:xx:01\nKP:03\nKP::\n");
    std::string longline(80, 'K');
    longline += "\n";
    feed(k, longline.c_str());
    CHECK(k.counters().malformed == 5, "four bad lines and one overlong: %ld", k.counters().malformed);
    CHECK(s.ui_lines.empty() && s.notes.empty(), "a bad line goes nowhere");
    feed(k, "BL:200\nV1.7\nMCP:OK\n");
    CHECK(k.counters().malformed == 5 && s.ui_lines.empty(), "informational lines are not input");
    feed(k, "KP:03:00\n");
    CHECK(s.notes.size() == 1, "the next good line still sounds");
}

void test_no_sound_before_the_first_snap() {
    Sink s;
    KeysReader k(s);
    k.handshake_begin();
    k.handshake_end(true, 0);
    k.ui_connect();
    s.clear();
    feed(k, "KP:03:00\nKR:03:00\n");
    CHECK(s.notes.empty(), "no snap yet: nothing sounds");
    CHECK(s.ui_lines.size() == 2 && has(s.ui_lines, "\"s\":false"), "forwarded unsounded");
    CHECK(k.counters().refused_quiet == 1, "one quiet press: %ld", k.counters().refused_quiet);
    ui(k, "{\"t\":\"snap\",\"sound\":false,\"transpose\":0,\"velocity\":1.0}\n");
    CHECK(has(s.logs, "first snapshot (sound off)"), "the first snap is journalled");
    feed(k, "KP:03:00\n");
    CHECK(s.notes.empty(), "a snap saying no: nothing sounds");
}

void test_octave_under_a_held_key_and_a_lost_release() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    feed(k, "KP:03:00\n");
    ui(k, "{\"t\":\"snap\",\"sound\":true,\"transpose\":12,\"velocity\":0.5}\n");
    feed(k, "KR:03:00\n");
    CHECK(s.notes.size() == 2 && s.notes[1].note == 53 && !s.notes[1].on,
          "the release lets go of what was pressed (53), not 65");
    s.clear();
    feed(k, "KP:03:01\n");      // 54 + 12 = 66
    ui(k, "{\"t\":\"snap\",\"sound\":true,\"transpose\":-12,\"velocity\":0.5}\n");
    feed(k, "KP:03:01\n");      // the KR was lost: 42 now
    CHECK(s.notes.size() == 3 && s.notes[0].note == 66 && s.notes[1].note == 66 && !s.notes[1].on &&
              s.notes[2].note == 42 && s.notes[2].on && s.notes[2].vel == 0.5f,
          "a second press releases the stale note first (%zu notes)", s.notes.size());
    s.clear();
    feed(k, "KR:03:01\n");
    CHECK(s.notes.size() == 1 && s.notes[0].note == 42 && !s.notes[0].on, "one release of 42");
}

void test_ring_full_goes_to_the_ui_unsounded() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    s.ring_full = true;
    feed(k, "KP:03:00\n");
    CHECK(has(s.ui_lines, "\"s\":false"), "ring full: the UI sounds it");
    CHECK(k.counters().ring_full == 1, "counted: %ld", k.counters().ring_full);
    s.ring_full = false;
    s.clear();
    feed(k, "KR:03:00\n");
    CHECK(s.notes.empty(), "its release is the tick's too");
}

// #827 row 1: an off the ring refused is kept, not forgotten, and goes when
// the ring takes it; no press passes it meanwhile.
void test_a_full_ring_keeps_every_off() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    feed(k, "KP:03:00\nKP:03:01\n");
    s.ring_full = true;
    feed(k, "KP:03:01\n");                // the same key again, refused
    s.clear();
    k.ui_drop("the UI went away");
    CHECK(s.notes.empty() && k.offs_due(), "ring full: both offs wait");
    k.retry_offs(0);
    CHECK(s.notes.empty() && k.offs_due(), "still full: still waiting");
    s.ring_full = false;
    k.retry_offs(0);
    CHECK(s.notes.size() == 2 && !s.notes[0].on && !s.notes[1].on && !k.offs_due(),
          "the ring drains: both offs go (%zu)", s.notes.size());
    CHECK(s.notes.size() == 2 && s.notes[0].note == 53 && s.notes[1].note == 54,
          "and they are 53 and 54");

    // A release refused by the ring, then a press of the same note: the off
    // goes first or the press does not go at all.
    k.ui_connect();
    ui(k, "{\"t\":\"snap\",\"sound\":true,\"transpose\":0,\"velocity\":1.0}\n");
    feed(k, "KP:03:00\n");
    s.ring_full = true;
    feed(k, "KR:03:00\n");
    CHECK(k.offs_due(), "the release waits");
    s.clear();
    feed(k, "KP:03:00\n");
    CHECK(s.notes.empty(), "a press behind a waiting off does not go");
    s.ring_full = false;
    feed(k, "KR:03:00\nKP:03:00\n");
    CHECK(s.notes.size() == 2 && !s.notes[0].on && s.notes[0].note == 53 && s.notes[1].on,
          "off, then on (%zu)", s.notes.size());
}

// #827 row 2: NO KEYBOARD, then the ATmega answers the watchdog's PING.
void test_a_pong_after_a_failed_handshake_configures() {
    Sink s;
    KeysReader k(s);
    k.ui_connect();
    k.handshake_begin();
    k.handshake_end(false, 0);
    CHECK(!k.ok() && has(s.ui_lines, "NO KEYBOARD"), "the handshake failed");
    s.clear();
    feed(k, "PONG\r\n", 2001 * kMs);
    CHECK(s.port_bytes == std::string("B\x04L\xffSE", 6), "step 4 sent: %zu bytes",
          s.port_bytes.size());
    CHECK(k.ok() && has(s.ui_lines, "KEYBOARD OK"), "and KEYBOARD OK");
    s.clear();
    feed(k, "PONG\r\n", 4001 * kMs);
    CHECK(s.port_bytes.empty() && s.ui_lines.empty(), "a PONG when well is only a PONG");
}

void test_a_release_goes_only_where_its_press_went() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    feed(k, "KP:00:02\n");             // PLAY, to the first UI
    k.ui_drop("test");
    k.ui_connect();
    ui(k, "{\"t\":\"snap\",\"sound\":true,\"transpose\":0,\"velocity\":1.0}\n");
    s.clear();
    feed(k, "KR:00:02\n");
    CHECK(s.ui_lines.empty(), "a release whose press went to an earlier UI is not forwarded");
}

void test_a_drop_releases_every_held_note() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    feed(k, "KP:03:00\nKP:03:01\nKP:00:02\n");
    s.clear();
    k.ui_drop("the UI went away");
    CHECK(s.notes.size() == 2 && !s.notes[0].on && !s.notes[1].on, "two note-offs (%zu)", s.notes.size());
    CHECK(has(s.logs, "the UI went away — 2 held notes released"), "journalled");
    CHECK(k.counters().ui_drops == 1, "counted");
    CHECK(!k.snapshot().sound, "no UI: nothing sounds");
    s.clear();
    feed(k, "KR:03:00\nKP:03:02\n");
    CHECK(s.notes.empty() && s.ui_lines.empty(), "no UI: the release and a press go nowhere");
}

void test_a_wedged_ui_is_a_drop() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    feed(k, "KP:03:00\n");
    s.clear();
    s.ui_wedged = true;
    feed(k, "KP:03:01\n");
    CHECK(!k.ui_connected(), "a refused write drops the UI");
    // 03:01 sounded, then the drop released both held notes.
    CHECK(s.notes.size() == 3 && !s.notes[1].on && !s.notes[2].on, "and lets go (%zu)", s.notes.size());
    // The thread still reads the dropped UI's socket in the same poll pass: what
    // it queued (an `H` would halt the scan) must never reach the ATmega.
    s.clear();
    ui(k, "{\"t\":\"send\",\"hex\":\"48\"}\n");
    CHECK(s.port_bytes.empty(), "a dropped UI's send is not sent (%zu bytes)", s.port_bytes.size());
}

void test_status_on_connect_and_on_change() {
    Sink s;
    KeysReader k(s);
    k.ui_connect();
    CHECK(s.ui_lines.empty(), "no status before the handshake");
    k.handshake_begin();
    k.handshake_end(false, 0);
    CHECK(has(s.ui_lines, "NO KEYBOARD"), "a failed handshake says so");
    s.clear();
    k.handshake_begin();
    k.handshake_end(true, 0);
    CHECK(s.ui_lines.size() == 1 && has(s.ui_lines, "KEYBOARD OK"), "then OK");
    s.clear();
    k.ui_drop("x");
    k.ui_connect();
    CHECK(s.ui_lines.size() == 1 && has(s.ui_lines, "KEYBOARD OK"), "and on every connect");
}

void test_the_backlight_a_reinit_restores() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    ui(k, "{\"t\":\"send\",\"hex\":\"4c40\"}\n");
    CHECK(s.port_bytes == std::string("L\x40", 2), "send's bytes go to the ATmega");
    s.clear();
    k.handshake_begin();
    k.handshake_end(true, 0);
    CHECK(s.port_bytes == std::string("B\x04L\x40SE", 6), "step 4 with the UI's L: %zu bytes",
          s.port_bytes.size());
    s.clear();
    ui(k, "{\"t\":\"send\",\"hex\":\"4cz0\"}\n");
    CHECK(s.port_bytes.empty(), "not hex: not sent");
}

void test_keyboard_ready_mid_line() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    feed(k, "KP:03:00\n");
    s.clear();
    feed(k, "KP:0KEYBOARD_READY\r\n");
    CHECK(s.notes.size() == 1 && !s.notes[0].on, "the ATmega reset on its own: the held note let go");
    CHECK(has(s.ui_lines, "\"p\":false"), "and the key at the UI");
    CHECK(s.port_bytes == std::string("B\x04L\xffSE", 6), "set-up re-sent");
    CHECK(k.counters().malformed == 0, "not a malformed line");
    s.clear();
    k.handshake_begin();
    feed(k, "KEYBOARD_READY\r\nPONG\r\n");
    CHECK(s.port_bytes.empty() && k.pong(), "inside a handshake it is the handshake's banner");
}

void test_the_watchdog() {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    feed(k, "KP:03:00\n", 0);
    s.clear();
    CHECK(k.tick(1999 * kMs) == KeysReader::Watchdog::None && s.port_bytes.empty(), "quiet < 2 s");
    CHECK(k.tick(2000 * kMs) == KeysReader::Watchdog::None && s.port_bytes == "P", "2 s: P");
    feed(k, "PONG\r\n", 2001 * kMs);
    CHECK(k.tick(3999 * kMs) == KeysReader::Watchdog::None, "a PONG is a line");
    CHECK(k.tick(4001 * kMs) == KeysReader::Watchdog::None, "answered: no miss");
    CHECK(k.tick(6001 * kMs) == KeysReader::Watchdog::None, "miss 1");
    CHECK(k.tick(8001 * kMs) == KeysReader::Watchdog::None, "miss 2");
    CHECK(s.notes.empty(), "nothing released yet");
    CHECK(k.tick(10001 * kMs) == KeysReader::Watchdog::Reinit, "miss 3: re-init");
    CHECK(s.port_bytes == "PPPPP", "five pings: %s", s.port_bytes.c_str());
    CHECK(s.notes.size() == 1 && !s.notes[0].on, "the held note released before the re-init");
    CHECK(has(s.logs, "3 missed PONGs"), "journalled");
    CHECK(k.next_deadline_ns() == 12001 * kMs, "next deadline 2 s on");
}

void test_the_probe() {
    Sink s;
    KeysReader k(s, true);
    ready(k, s);
    feed(k, "KP:03:00\nKR:03:00\n", 3'000'000'123'456);
    const int32_t want = int32_t((3'000'000'123'456 / 1000) % (int64_t(1) << 31));
    CHECK(s.notes.size() == 2 && s.notes[0].probe == want && s.notes[1].probe == -1,
          "a press carries uart_input's stamp, a release none");
    CHECK(has(s.ui_lines, "\"probe\":") && s.ui_lines[0].find("\"probe\":null") == std::string::npos,
          "and the UI hears it");
}

// #1020: `arbutus_os/drivers/key_lines.tsv`, the vectors uart_input.py's tests
// run too: one fresh reader per line, and what came out of it named the way
// the file names it.
std::string unescape(const std::string& in) {
    std::string out;
    for (std::size_t i = 0; i < in.size(); ++i) {
        if (in[i] != '\\' || i + 1 == in.size()) {
            out += in[i];
            continue;
        }
        const char e = in[++i];
        if (e == 'r') out += '\r';
        else if (e == 't') out += '\t';
        else if (e == 'x' && i + 2 < in.size()) {
            out += char(std::strtol(in.substr(i + 1, 2).c_str(), nullptr, 16));
            i += 2;
        } else out += e;
    }
    return out;
}

std::string outcome(const std::string& line, int r, int c, bool down) {
    Sink s;
    KeysReader k(s);
    ready(k, s);
    if (!down) {
        // A release is forwarded only where its press went: press it first.
        char kp[32];
        std::snprintf(kp, sizeof kp, "KP:%02d:%02d\n", r, c);
        feed(k, kp);
        s.clear();
    }
    feed(k, (line + "\n").c_str());
    char out[64];
    if (k.counters().malformed) return "malformed";
    if (k.pong()) return "pong";
    if (has(s.logs, "reset on its own")) return "ready";
    if (s.ui_lines.size() != 1) return s.ui_lines.empty() ? "ignored" : "several events";
    const std::string& u = s.ui_lines[0];
    int a = 0, b = 0;
    if (std::sscanf(u.c_str(), "{\"t\":\"enc\",\"i\":%d,\"d\":%d", &a, &b) == 2) {
        std::snprintf(out, sizeof out, "enc %d %d", a, b);
        return out;
    }
    if (std::sscanf(u.c_str(), "{\"t\":\"key\",\"r\":%d,\"c\":%d", &a, &b) == 2) {
        std::snprintf(out, sizeof out, "key %d %d %s", a, b,
                      u.find("\"p\":true") != std::string::npos ? "down" : "up");
        return out;
    }
    return "a UI line: " + u;
}

void test_the_shared_line_vectors() {
    std::ifstream f(ARB_KEY_LINES);
    CHECK(f.good(), "cannot open %s", ARB_KEY_LINES);
    std::string row;
    int n = 0;
    while (std::getline(f, row)) {
        if (row.empty() || row[0] == '#') continue;
        const std::size_t tab = row.rfind('\t');
        CHECK(tab != std::string::npos, "a row with no tab: %s", row.c_str());
        if (tab == std::string::npos) continue;
        const std::string line = unescape(row.substr(0, tab)), want = row.substr(tab + 1);
        int r = 0, c = 0;
        char dir[8] = "down";
        std::sscanf(want.c_str(), "key %d %d %7s", &r, &c, dir);
        const std::string got = outcome(line, r, c, std::strcmp(dir, "up") != 0);
        CHECK(got == want, "%s: want %s, got %s", row.substr(0, tab).c_str(), want.c_str(), got.c_str());
        ++n;
    }
    CHECK(n >= 30, "only %d vectors read", n);
}

void test_ten_thousand_lines_allocate_nothing() {
    Sink s;
    s.record = false;
    KeysReader k(s, true);
    ready(k, s);
    const char* mix[] = {
        "KP:03:00\r\n", "KR:03:00\r\n", "EN:01:+2\r\n", "KP:00:02\r\n", "KR:00:02\r\n",
        "PONG\r\n", "BL:9\r\n", "EN:00:00-1\r\n", "KP:04:05\r\nKR:04:05\r\n", "KP:0KEYBOARD_READY\r\n",
    };
    const char* cmds[] = {
        "{\"t\":\"snap\",\"sound\":true,\"transpose\":12,\"velocity\":1.0}\n",
        "{\"t\":\"send\",\"hex\":\"4c80\"}\n",
        "{\"t\":\"nonsense\"}\n",
    };
    const long before = g_news.load();
    for (int i = 0; i < 10000; ++i) {
        feed(k, mix[i % 10], int64_t(i) * kMs);
        if (i % 97 == 0) ui(k, cmds[(i / 97) % 3]);
        if (i % 1000 == 0) k.tick(int64_t(i) * kMs + 5000 * kMs);
    }
    k.ui_drop("x");
    k.ui_connect();
    char line[256];
    k.summary(line, sizeof line);
    const long after = g_news.load();
    CHECK(after == before, "the reader called operator new %ld time(s)", after - before);
    CHECK(s.n_notes > 1000 && s.n_ui > 1000, "and it did the work: %ld notes, %ld lines", s.n_notes,
          s.n_ui);
}

}  // namespace

// The counting allocator: see test_events.cpp for why these three and noinline.
[[gnu::noinline]] void* operator new(std::size_t n) {
    g_news.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

int main() {
    test_every_position();
    test_the_ui_line_is_keys_service_s();
    test_lines_split_anywhere();
    test_malformed_lines_are_counted_and_reading_goes_on();
    test_no_sound_before_the_first_snap();
    test_octave_under_a_held_key_and_a_lost_release();
    test_ring_full_goes_to_the_ui_unsounded();
    test_a_full_ring_keeps_every_off();
    test_a_pong_after_a_failed_handshake_configures();
    test_a_release_goes_only_where_its_press_went();
    test_a_drop_releases_every_held_note();
    test_a_wedged_ui_is_a_drop();
    test_status_on_connect_and_on_change();
    test_the_backlight_a_reinit_restores();
    test_keyboard_ready_mid_line();
    test_the_watchdog();
    test_the_probe();
    test_the_shared_line_vectors();
    test_ten_thousand_lines_allocate_nothing();
    if (failures) {
        std::printf("test_keys_reader: %d failure(s)\n", failures);
        return 1;
    }
    std::printf("test_keys_reader: ok\n");
    return 0;
}
