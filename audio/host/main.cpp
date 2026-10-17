// arbutus-audio — the audio process (engine-spec.md §8).
//
// Newline-delimited JSON over a Unix socket in, audio out. `--offline` drops
// the device and runs the same pipeline from `render` messages, which is how
// the whole thing is tested without audio hardware.
//
// The message reference is host/PROTOCOL.md and it is public API from message
// one; anything added there is added, never changed.

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <deque>
#include <string>
#include <vector>

#include <arpa/inet.h>
#include <fcntl.h>
#include <fstream>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <nlohmann/json.hpp>

#include <rt_alloc_count.hpp>

#include "audio_host.hpp"
#include "device.hpp"
#include "imu_lsm6dso.hpp"
#include "keys_thread.hpp"

using nlohmann::json;
using arb::host::AudioHost;

namespace {

volatile std::sig_atomic_t g_stop = 0;
void on_signal(int) { g_stop = 1; }

std::string default_socket() {
    const char* tmp = std::getenv("TMPDIR");
    std::string dir = tmp && *tmp ? tmp : "/tmp";
    if (dir.back() != '/') dir += '/';
    return dir + "arbutus-audio.sock";
}

int usage(int code) {
    std::fprintf(code ? stderr : stdout,
        "arbutus-audio [options]\n"
        "  --socket PATH     control socket (default $TMPDIR/arbutus-audio.sock)\n"
        "  --library PATH    box library root; repeatable\n"
        "  --library-fallback PATH  the slot's stock boxes, for an id no root has\n"
        "                    and the card's stock record does not name (D125)\n"
        "  --offline         no audio device; drive it with render/bake\n"
        "  --no-input        output only (no capture device)\n"
        "  --device PCM      open this ALSA PCM by name (P3: hw:wm8904, § 6.3);\n"
        "                    without it, the backend's default device\n"
        "  --audio-core N    run the audio thread on core N alone and every other\n"
        "                    thread off it (#637; the device: 3). Default: no pinning\n"
        "  --sr N            sample rate (default 48000)\n"
        "  --block N         block size (default 128, §15.8)\n"
        "  --max-block N     largest block an engine will see (default = --block)\n"
        "  --tape-seconds S  tape length per track (default 480 = 8 min)\n"
        "  --tape-dir DIR    write takes behind onto DIR/tape-1..8.wav (C1 step 2a);\n"
        "                    without it the tape is RAM only, as before\n"
        "  --tape-pool-seconds S  track-seconds of tape resident in RAM (default 120);\n"
        "                    only bites with --tape-dir, since a range is fetched\n"
        "                    back from there\n"
        "  --no-tape-prealloc  do not keep a fallocate lead ahead of the write\n"
        "                    head (#158's *before* switch; the lead is on)\n"
        "  --no-tape-peaks   do not keep the peaks sidecar TRACK draws from\n"
        "                    (#163's *before* switch; the sidecar is on)\n"
        "  --tape-store posix|fake   TESTS ONLY: `fake` keeps the media in RAM\n"
        "                    inside this process and writes nothing to the card,\n"
        "                    so a full or stalling card is reachable from a test\n"
        "  --tape-free-bytes N   TESTS ONLY: believe the tape's filesystem has at\n"
        "                    most N bytes free (REC is refused under 2x the pool)\n"
        "  --tape-readahead-seconds S  read-ahead horizon in seconds of wall\n"
        "                    clock (default 4); 0 turns read-ahead off\n"
        "  --tape-behind-seconds S  how far behind the playhead stays resident\n"
        "                    (default 1, for a REV hold)\n"
        "  --seed N          host.rng_seed (default 0)\n"
        "  --run-seconds S   quit after S seconds; 0 = run until told (default 0)\n"
        "  --imu PATH        LSM6DSO on this i2c-dev bus (device: /dev/i2c-1)\n"
        "  --imu-mount NAME  how the part sits on the board: p3 (default; the\n"
        "                    bottom side), top, or chip (raw axes, bring-up)\n"
        "  --keys PORT       read the ATmega's keys on PORT and serve keys.sock\n"
        "                    (#638, device-os-spec § 1.3.4; P3: /dev/ttyAMA5).\n"
        "                    Empty or absent: no key thread\n"
        "  --keys-socket PATH  where keys.sock is (default: beside --socket)\n"
        "  --key-probe       bench only: a key press carries the probe stamp\n"
        "  --tcp-port N      also serve the protocol on TCP (device-os-spec § 9.4)\n"
        "  --token-file PATH the pairing token; without one, TCP is not bound\n"
        "  --feed-window-ms N  TESTS ONLY: how old a D17 action may be and still\n"
        "                    be answered (default 30000)\n"
        "  --quiet           no startup banner\n"
        "  --rt-guard        offline: count heap blocks gained inside render_block\n");
    return code;
}

struct Client {
    int fd = -1;
    std::string in, out;
    // device-os-spec.md § 9.4. The Unix socket is the instrument talking to
    // itself and needs no token; a TCP connection is a paired phone and has to
    // say `hello` with one before anything else is read as a message.
    bool tcp = false;
    bool authed = false;
    int refusals = 0;
    bool closing = false;           // drop once `out` has gone out
    // D17 (#276): this connection asked for `elements` and `action` pushes
    // with `actions {on:true}`. Nobody else gets them -- the UI, which sends
    // them, would only read its own key presses back.
    bool feed = false;
    // #853: this connection asked for the live stream with `live {on:true}`:
    // `situation` patches, `frame` pushes, and the D17 pushes above.
    bool live = false;
    // #412: which connection this is (from 1), and whether it said `hello` --
    // a UI session does; a one-shot publisher (`mock --ui`, a test) does not.
    long long id = 0;
    bool hello = false;
};

void queue(Client& c, const json& j) { c.out += j.dump() + "\n"; }

// Whether a client is allowed to be told things. An unauthenticated TCP peer
// gets its own refusals and nothing else -- `state` is the whole instrument.
bool listening(const Client& c) { return !c.tcp || c.authed; }

// Length-independent compare, so the wire cannot be used to learn the token a
// character at a time. It is a LAN and the radio is off otherwise
// (companion-app-spec §3.1), but this costs one loop.
bool token_eq(const std::string& a, const std::string& b) {
    unsigned char diff = a.size() == b.size() ? 0 : 1;
    const std::size_t n = std::min(a.size(), b.size());
    for (std::size_t i = 0; i < n; ++i)
        diff = static_cast<unsigned char>(diff | (a[i] ^ b[i]));
    return diff == 0 && a.size() == b.size();
}

json refusal(const json& msg, const char* code, const std::string& message) {
    json e{{"type", "error"}, {"code", code}, {"message", message}};
    if (msg.is_object()) {
        if (msg.contains("type") && msg["type"].is_string()) e["for"] = msg["type"];
        if (msg.contains("req")) e["req"] = msg["req"];
    }
    return e;
}

// § 9.4's three transport rules, in one place: nothing before an accepted
// `hello {token}`; `shutdown` and `render` are the local instrument's, not a
// phone's; `meters` is capped at 10 Hz over the network. Returns the refusal
// to send instead of handling the message, or null to let it through.
json tcp_gate(Client& c, json& msg, const std::string& token) {
    if (!c.tcp) return nullptr;
    if (!msg.is_object()) return nullptr;      // `bad_request` is the host's answer
    auto str = [&](const char* k) -> std::string {
        const auto it = msg.find(k);
        return it != msg.end() && it->is_string() ? it->get<std::string>() : std::string{};
    };
    const std::string type = str("type");
    if (!c.authed) {
        if (type == "hello" && msg.contains("token") && token_eq(str("token"), token)) {
            c.authed = true;
            return nullptr;
        }
        // Three tries, then the connection goes -- a wrong token is either a
        // stale phone or someone guessing, and both should have to reconnect.
        if (++c.refusals >= 3) c.closing = true;
        return refusal(msg, "unauthorized", "NOT PAIRED");
    }
    // `ui_elements` / `ui_action` are the UI telling what it shows and what
    // the player did (D17); a phone that could send them could put arrows
    // and lessons on a screen that is not there (companion-app § 10.1).
    if (type == "shutdown" || type == "render" || type == "ui_elements" ||
        type == "ui_action" || type == "ui_situation" || type == "ui_frame")
        return refusal(msg, "bad_request", type + " is local only");
    if (type == "meters") {
        const auto hz = msg.find("hz");
        if (hz != msg.end() && hz->is_number() && hz->get<double>() > 10.0) *hz = 10.0;
    }
    return nullptr;
}

// The token file (device-os-spec.md § 3.3, written by `arbutus-net`). Trailing
// whitespace is not part of it: a file a human wrote with an editor ends in a
// newline and the phone never sent one.
std::string read_token(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return {};
    std::string t((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    while (!t.empty() && (t.back() == '\n' || t.back() == '\r' || t.back() == ' ' || t.back() == '\t'))
        t.pop_back();
    return t;
}

// What a client may have waiting before it is dropped. Every push goes to
// every client, so one that stops reading (a phone on a Wi-Fi that went away
// without a reset, a bench script asleep) grows its own backlog and nobody
// else's; this is where that stops. 8 MiB is minutes of 30 Hz meters.
constexpr std::size_t kMaxPending = std::size_t(8) << 20;

// #596: the same bound on what a client may send without a newline. A line
// that grows past it is never going to be a message, and without a cap a
// peer that never sends '\n' holds as much of the instrument's memory as it
// likes. A TCP peer that has not passed the token check gets far less: the
// only thing it may send is a `hello`, which is a few hundred bytes.
constexpr std::size_t kMaxLine = std::size_t(8) << 20;
constexpr std::size_t kMaxLineUnauthed = std::size_t(64) << 10;

// Accepted sockets are non-blocking (P9, #89): a blocking write to a client
// whose buffer is full stalled this whole loop -- and so the UI, whose
// `note` got no reply until the stalled client read again.
void nonblocking(int fd) { ::fcntl(fd, F_SETFL, ::fcntl(fd, F_GETFL, 0) | O_NONBLOCK); }

void flush(Client& c) {
    while (!c.out.empty()) {
        const ssize_t n = ::write(c.fd, c.out.data(), c.out.size());
        if (n <= 0) {
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                if (c.out.size() > kMaxPending) {
                    std::fprintf(stderr, "arbutus-audio: dropping a client that "
                                         "stopped reading (%zu bytes waiting)\n",
                                 c.out.size());
                    c.out.clear();
                    c.closing = true;
                }
                return;
            }
            c.out.clear();
            return;
        }
        c.out.erase(0, std::size_t(n));
    }
}

// -- D17 (#276): the screen's element map and the recent actions ----------
// companion-app-spec.md § 10.6 / § 10.8. The UI is this process's client and
// the phone is too, so the host is the one place both already reach: the UI
// publishes, the host keeps the latest map and a short ring of actions and
// relays them. None of it touches the audio thread or `AudioHost` -- it is a
// fact about the screen, held beside the sockets, not in the engine.
// PROTOCOL.md § The screen and the player's actions.
struct UiFeed {
    json page = nullptr;                    // null until the UI has published
    json elements = json::array();
    // #412: the connection (`Client::id`) that published the map, when it
    // was a UI session; 0 otherwise. Its close takes the map with it.
    long long owner = 0;
    std::deque<json> ring;                  // newest at the back
    long long seq = 0;
    std::chrono::steady_clock::time_point t0 = std::chrono::steady_clock::now();
    std::vector<json> pushes;               // for the clients with `feed` on
    // #334: the paired phone's light or dark, which the instrument's screen
    // follows. Held so a UI that connects later can ask; null until a client
    // has said. Its push goes to **every** client, not only `feed` ones: the
    // UI never subscribes to the feed (it would read its own actions back).
    json appearance = nullptr;
    std::vector<json> broadcast;            // for every listening client

    static constexpr std::size_t kRing = 10;        // § 10.6: "the last ~10"
    long long window_ms = 30000;                    // "... or ~30 s"; a test shortens it
    static constexpr std::size_t kMaxElements = 64;
    static constexpr std::size_t kMaxActions = 64;  // per `ui_action`

    long long now_ms() const {
        return std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - t0).count();
    }
};

json named(const json& msg, const char* type) {
    json r{{"type", type}, {"for", type}};
    if (msg.contains("req")) r["req"] = msg["req"];
    return r;
}

json ok_for(const json& msg) {
    json r{{"type", "ok"}, {"for", msg["type"]}};
    if (msg.contains("req")) r["req"] = msg["req"];
    return r;
}

// The four D17 messages and `appearance` (#334), or null when `msg` is
// none of them. Transport
// rules (`ui_*` local only) have already been applied by `tcp_gate`.
json ui_feed_handle(Client& c, const json& msg, UiFeed& f) {
    if (!msg.is_object() || !msg.contains("type") || !msg["type"].is_string())
        return nullptr;
    const std::string type = msg["type"].get<std::string>();

    if (type == "ui_elements") {
        const auto pg = msg.find("page");
        const auto el = msg.find("elements");
        if (pg == msg.end() || !pg->is_string() || el == msg.end() ||
            !el->is_array() || el->size() > UiFeed::kMaxElements)
            return refusal(msg, "bad_request", "BAD MESSAGE");
        for (const json& e : *el)
            if (!e.is_object() || !e.contains("id") || !e["id"].is_string())
                return refusal(msg, "bad_request", "BAD MESSAGE");
        // Pushed on a change only: the UI may republish an unchanged map
        // (after a reconnect it must), and a phone redraws on every push.
        if (*pg != f.page || *el != f.elements) {
            f.page = *pg;
            f.elements = *el;
            f.pushes.push_back(json{{"type", "elements"}, {"page", f.page},
                                    {"elements", f.elements}});
        }
        f.owner = c.hello ? c.id : 0;
        return ok_for(msg);
    }
    if (type == "ui_action") {
        const auto acts = msg.find("actions");
        if (acts == msg.end() || !acts->is_array() ||
            acts->size() > UiFeed::kMaxActions)
            return refusal(msg, "bad_request", "BAD MESSAGE");
        for (const json& a : *acts)
            if (!a.is_object() || !a.contains("kind") || !a["kind"].is_string())
                return refusal(msg, "bad_request", "BAD MESSAGE");
        for (const json& a : *acts) {
            json e = a;
            e.erase("type");                // the host's fields, not the UI's
            e["seq"] = ++f.seq;
            e["t_ms"] = f.now_ms();
            f.ring.push_back(e);
            if (f.ring.size() > UiFeed::kRing) f.ring.pop_front();
            json p = e;
            p["type"] = "action";
            f.pushes.push_back(std::move(p));
        }
        return ok_for(msg);
    }
    if (type == "appearance") {
        // PROTOCOL.md § The phone's appearance (#334). With `mode` it is a
        // set (any client: the phone sends it, a test or the bench may);
        // without, a question. Either way the reply names the mode held.
        if (const auto m = msg.find("mode"); m != msg.end()) {
            if (!m->is_string() || (*m != "light" && *m != "dark"))
                return refusal(msg, "bad_request", "BAD MESSAGE");
            if (*m != f.appearance) {
                f.appearance = *m;
                f.broadcast.push_back(json{{"type", "appearance"}, {"mode", f.appearance}});
            }
        }
        json r = named(msg, "appearance");
        r["mode"] = f.appearance;
        return r;
    }
    if (type == "elements") {
        json r = named(msg, "elements");
        r["page"] = f.page;
        r["elements"] = f.elements;
        return r;
    }
    if (type == "actions") {
        if (const auto on = msg.find("on"); on != msg.end() && on->is_boolean())
            c.feed = on->get<bool>();
        long long last = static_cast<long long>(UiFeed::kRing);
        if (const auto l = msg.find("last"); l != msg.end() && l->is_number())
            // clamped as a double first: casting 1e300 is undefined (#412)
            last = static_cast<long long>(
                std::clamp(l->get<double>(), 0.0, static_cast<double>(last)));
        const long long now = f.now_ms();
        json list = json::array();
        for (const json& e : f.ring)
            if (now - e["t_ms"].get<long long>() <= f.window_ms) list.push_back(e);
        while (static_cast<long long>(list.size()) > last) list.erase(list.begin());
        json r = named(msg, "actions");
        r["on"] = c.feed;
        r["now_ms"] = now;
        r["actions"] = std::move(list);
        return r;
    }
    return nullptr;
}

// #412: a UI session that leaves takes its map with it -- a phone must not
// point arrows at a screen nobody is drawing (companion-app § 10.1), and
// `GET /screen.png` already answers 503 at that moment. A one-shot publisher
// (no `hello`) owns nothing, so `mock --ui` still leaves its map standing.
void ui_feed_gone(UiFeed& f, const Client& c) {
    if (f.owner == 0 || f.owner != c.id) return;
    f.owner = 0;
    if (f.page.is_null() && f.elements.empty()) return;
    f.page = nullptr;
    f.elements = json::array();
    f.pushes.push_back(json{{"type", "elements"}, {"page", f.page},
                            {"elements", f.elements}});
}

// -- #853: the live stream --------------------------------------------------
// PROTOCOL.md § The live stream. One subscription (`live {on:true}`) carries
// what the instrument is doing (the *situation* document, as RFC 7396 merge
// patches numbered by `rev`), the player's actions (D17's pushes) and when the
// screen changed (`frame`). The document is the host's half (`live_doc`)
// with the UI's half (`ui_situation`) merged over it. Like D17 it is a fact
// held beside the sockets: nothing here touches the audio thread, and with
// nobody subscribed none of it is even built.
struct Live {
    json ui = json::object();               // the UI's half, as last published
    long long ui_owner = 0;                 // #412's rule: a UI session owns it
    json sent = json::object();             // the document at `rev`
    long long rev = 0;
    std::chrono::steady_clock::time_point checked{};
    long long frame = 0;                    // frames announced since the start
    long long frame_sent = 0;
    std::chrono::steady_clock::time_point frame_at{};
    int watchers = 0;                       // as last told to every client
    // ~20 patches a second at most, and a frame push at most every 200 ms:
    // the phone fetches `GET /screen.png` on one, and the UI encodes one at
    // most that often anyway (screenshare.MIN_PERIOD_S).
    static constexpr auto kPeriod = std::chrono::milliseconds(50);
    static constexpr auto kFramePeriod = std::chrono::milliseconds(200);
};

bool has_null(const json& j) {
    if (j.is_null()) return true;
    if (j.is_object() || j.is_array())
        for (const json& v : j) if (has_null(v)) return true;
    return false;
}

// The RFC 7396 patch that turns `a` into `b`: changed and new members as they
// are in `b`, removed ones as null, objects recursed into, anything else
// (arrays included) replaced whole. Empty when the two are equal.
json merge_diff(const json& a, const json& b) {
    json p = json::object();
    for (auto it = a.begin(); it != a.end(); ++it)
        if (!b.contains(it.key())) p[it.key()] = nullptr;
    for (auto it = b.begin(); it != b.end(); ++it) {
        const auto o = a.find(it.key());
        if (o == a.end()) p[it.key()] = *it;
        else if (*o != *it)
            p[it.key()] = o->is_object() && it->is_object() ? merge_diff(*o, *it) : *it;
    }
    return p;
}

json live_current(const AudioHost& host, const Live& l) {
    json d = host.live_doc();
    d.merge_patch(l.ui);                    // the UI's half over the host's
    return d;
}

// Brings `sent` up to the document now and pushes the difference to every
// subscriber, as the next `rev`. Nothing when nothing changed.
void live_refresh(Live& l, const AudioHost& host, std::vector<Client>& clients) {
    json d = live_current(host, l);
    json p = merge_diff(l.sent, d);
    if (p.empty()) return;
    l.sent = std::move(d);
    ++l.rev;
    const json push{{"type", "situation"}, {"rev", l.rev}, {"patch", std::move(p)}};
    for (Client& c : clients) if (listening(c) && c.live) queue(c, push);
}

// `live`, `situation`, `ui_situation`, `ui_frame`; null for anything else.
json live_handle(Client& c, const json& msg, Live& l, const AudioHost& host,
                 std::vector<Client>& clients) {
    if (!msg.is_object() || !msg.contains("type") || !msg["type"].is_string())
        return nullptr;
    const std::string type = msg["type"].get<std::string>();
    if (type == "ui_situation") {
        const auto s = msg.find("situation");
        if (s == msg.end() || !s->is_object() || has_null(*s))
            return refusal(msg, "bad_request", "BAD MESSAGE");
        // A one-shot publisher (no `hello`: the mock's stand-in screen, its
        // `--ui`) does not replace the half a UI session owns -- the session
        // republishes only on a change, so its half would be lost until then.
        if (c.hello || l.ui_owner == 0 || l.ui_owner == c.id) {
            l.ui = *s;
            l.ui_owner = c.hello ? c.id : 0;
        }
        json r = ok_for(msg);
        r["watchers"] = l.watchers;         // so a UI knows at once (frames)
        return r;
    }
    if (type == "ui_frame") {
        // The host numbers the frames, not the publisher: two publishers (the
        // mock's stand-in screen, then an emulator attaching) would each start
        // at 1, and a repeated number reads as no new frame. A `rev` the UI
        // sends is its own count, checked for type and otherwise unused.
        if (const auto rv = msg.find("rev"); rv != msg.end() && !rv->is_number_integer())
            return refusal(msg, "bad_request", "BAD MESSAGE");
        ++l.frame;
        return ok_for(msg);
    }
    if (type == "live" || type == "situation") {
        if (type == "live")
            if (const auto on = msg.find("on"); on != msg.end() && on->is_boolean())
                c.live = on->get<bool>();
        const bool whole = type == "situation" || c.live;
        if (whole) {
            // Whoever is subscribed is brought to the document now first, so
            // the `rev` answered here means the same thing to everyone. This
            // connection is not sent that patch: it gets the whole document.
            // That push counts as the tick's: within kPeriod of the last one,
            // with anyone else subscribed, the answer is the document at the
            // last push (at most 50 ms old) and the next tick pushes the rest
            // (#1179: a client asking between changes flooded the others).
            const bool was = c.live;
            c.live = false;
            bool others = false;
            for (const Client& k : clients) others = others || (listening(k) && k.live);
            const auto now = std::chrono::steady_clock::now();
            if (!others || now - l.checked >= Live::kPeriod) {
                if (others) l.checked = now;
                live_refresh(l, host, clients);
            }
            c.live = was;
        }
        json r = named(msg, type.c_str());
        if (type == "live") {
            r["on"] = c.live;
            int n = 0;
            for (const Client& k : clients) n += (listening(k) && k.live) ? 1 : 0;
            r["watchers"] = n;
        }
        r["rev"] = l.rev;
        if (whole) r["situation"] = l.sent;
        return r;
    }
    return nullptr;
}

// One turn of the loop: the watcher count to every client when it moved, the
// situation's patch at most every kPeriod, the newest frame at most every
// kFramePeriod. A frame the UI announced while nobody watched is dropped.
void live_tick(Live& l, const AudioHost& host, std::vector<Client>& clients) {
    int n = 0;
    for (const Client& c : clients) n += (listening(c) && c.live) ? 1 : 0;
    if (n != l.watchers) {
        l.watchers = n;
        const json push{{"type", "live"}, {"watchers", n}};
        for (Client& c : clients) if (listening(c)) queue(c, push);
    }
    if (n == 0) { l.frame_sent = l.frame; return; }
    const auto now = std::chrono::steady_clock::now();
    if (now - l.checked >= Live::kPeriod) {
        l.checked = now;
        live_refresh(l, host, clients);
    }
    if (l.frame != l.frame_sent && now - l.frame_at >= Live::kFramePeriod) {
        l.frame_sent = l.frame;
        l.frame_at = now;
        const json push{{"type", "frame"}, {"rev", l.frame}};
        for (Client& c : clients) if (listening(c) && c.live) queue(c, push);
    }
}

// #412's rule for the UI's half: a UI session that leaves takes it with it,
// and the next patch says so; a one-shot publisher's half stands.
void live_gone(Live& l, const Client& c) {
    if (l.ui_owner == 0 || l.ui_owner != c.id) return;
    l.ui_owner = 0;
    l.ui = json::object();
}

}  // namespace

int main(int argc, char** argv) {
    arb::host::Options o;
    std::string sock_path = default_socket();
    bool capture = true;
    std::string pcm;   // --device: an ALSA PCM by name (§ 6.3, #543)
    bool quiet = false;
    double run_seconds = 0.0;
    bool max_block_set = false;
    std::string imu_path;
    const arb::host::ImuMount* imu_mount = &arb::host::lsm6dso::kMountP3;
    int tcp_port = 0;                       // 0 = Unix socket only
    int audio_core = -1;                    // #637: -1 = no pinning
    std::string token_path, token;
    arb::host::KeysThread::Options keys_opt;   // #638: --keys
    long long feed_window_ms = 30000;       // D17's "~30 s"; tests shorten it

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&]() -> std::string { return i + 1 < argc ? argv[++i] : std::string{}; };
        if (a == "--socket") sock_path = next();
        else if (a == "--library") o.library_roots.push_back(next());
        else if (a == "--library-fallback") o.library_fallbacks.push_back(next());
        else if (a == "--offline") o.offline = true;
        else if (a == "--no-input") capture = false;
        else if (a == "--device") pcm = next();
        else if (a == "--audio-core") audio_core = std::stoi(next());
        else if (a == "--sr") o.sr = uint32_t(std::stoul(next()));
        else if (a == "--block") o.block = uint32_t(std::stoul(next()));
        else if (a == "--max-block") { o.max_block = uint32_t(std::stoul(next())); max_block_set = true; }
        else if (a == "--tape-seconds") o.tape_seconds = std::stod(next());
        else if (a == "--tape-dir") o.tape_dir = next();
        else if (a == "--tape-pool-seconds") o.tape_pool_seconds = std::stod(next());
        else if (a == "--tape-store") o.tape_store_fake = next() == "fake";
        else if (a == "--tape-free-bytes") o.tape_free_bytes = std::stoll(next());
        else if (a == "--no-tape-prealloc") o.tape_prealloc = false;
        else if (a == "--no-tape-peaks") o.tape_peaks = false;
        else if (a == "--tape-readahead-seconds") o.tape_readahead_seconds = std::stod(next());
        else if (a == "--tape-behind-seconds") o.tape_behind_seconds = std::stod(next());
        else if (a == "--seed") o.seed = uint32_t(std::stoul(next()));
        else if (a == "--run-seconds") run_seconds = std::stod(next());
        else if (a == "--imu") imu_path = next();
        else if (a == "--imu-mount") {
            const std::string n = next();
            imu_mount = arb::host::imu_mount(n.c_str());
            if (!imu_mount) {
                std::fprintf(stderr, "arbutus-audio: unknown --imu-mount %s\n", n.c_str());
                return usage(2);
            }
        }
        else if (a == "--keys") keys_opt.port = next();
        else if (a == "--keys-socket") keys_opt.socket = next();
        else if (a == "--key-probe") keys_opt.probe = true;
        else if (a == "--tcp-port") tcp_port = std::stoi(next());
        else if (a == "--token-file") token_path = next();
        else if (a == "--feed-window-ms") feed_window_ms = std::stoll(next());
        else if (a == "--quiet") quiet = true;
        else if (a == "--rt-guard") o.rt_guard = true;
        else if (a == "-h" || a == "--help") return usage(0);
        else { std::fprintf(stderr, "arbutus-audio: unknown option %s\n", a.c_str()); return usage(2); }
    }
    if (!max_block_set) o.max_block = o.block;

    // Arm the heap counter before the host exists, so the Linux counting
    // allocator is live for every allocation the run makes (#52). A no-op off
    // glibc, and never armed unless --rt-guard was asked for.
    if (o.rt_guard) arb::rt::enable_alloc_counting();

    // #637: off the audio core before `init` starts the tape I/O thread and
    // `device.start` miniaudio's worker, so both inherit a set without it; the
    // worker alone is put back on it, in `go_realtime`.
    const std::string core_report = o.offline ? std::string() : arb::host::leave_core(audio_core);

    AudioHost host;
    std::string err;
    if (!host.init(o, err)) {
        std::fprintf(stderr, "arbutus-audio: %s\n", err.c_str());
        return 1;
    }

    // #638 (§ 1.3.4 part 2): the key thread, right after init so it
    // handshakes while the audio device opens. It is off the audio core
    // already: it inherits the set `leave_core` left this thread.
    arb::host::KeysThread keys(host);
    if (!keys_opt.port.empty()) {
        if (keys_opt.socket.empty()) {
            const auto slash = sock_path.rfind('/');
            keys_opt.socket = (slash == std::string::npos ? std::string(".") : sock_path.substr(0, slash)) +
                              "/keys.sock";
        }
        if (!keys.start(keys_opt, err)) {
            std::fprintf(stderr, "arbutus-audio: %s\n", err.c_str());
            return 1;
        }
    }

    arb::host::Device device;
    device.set_pcm(pcm);
    device.set_audio_core(audio_core);
    if (!o.offline) {
        if (!device.start(host, capture, err)) {
            if (capture && device.start(host, false, err)) {
                std::fprintf(stderr, "arbutus-audio: no capture device, output only\n");
            } else {
                std::fprintf(stderr, "arbutus-audio: %s\n", err.c_str());
                return 1;
            }
        }
    }

    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);
    std::signal(SIGPIPE, SIG_IGN);

    ::unlink(sock_path.c_str());
    const int listen_fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (listen_fd < 0) { std::perror("socket"); return 1; }
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (sock_path.size() >= sizeof addr.sun_path) {
        std::fprintf(stderr, "arbutus-audio: socket path is too long\n");
        return 1;
    }
    std::strncpy(addr.sun_path, sock_path.c_str(), sizeof addr.sun_path - 1);
    if (::bind(listen_fd, reinterpret_cast<sockaddr*>(&addr), sizeof addr) < 0) {
        std::perror("bind");
        return 1;
    }
    if (::listen(listen_fd, 8) < 0) { std::perror("listen"); return 1; }

    // -- the TCP control listener (device-os-spec.md § 9.4) -----------------
    // The same message set over TCP, for the companion app. Bound **only when
    // a token exists**: a device nobody has paired with listens on nothing, so
    // the network surface of an unpaired instrument is zero rather than "one
    // port that always refuses".
    int tcp_fd = -1;
    if (tcp_port > 0) {
        if (!token_path.empty()) token = read_token(token_path);
        if (token.empty()) {
            std::fprintf(stderr, "arbutus-audio: no pairing token, TCP not bound\n");
        } else if ((tcp_fd = ::socket(AF_INET, SOCK_STREAM, 0)) < 0) {
            std::perror("socket(tcp)");
        } else {
            int one = 1;
            ::setsockopt(tcp_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
            sockaddr_in in{};
            in.sin_family = AF_INET;
            in.sin_addr.s_addr = htonl(INADDR_ANY);   // § 9.2: Wi-Fi or Ethernet
            in.sin_port = htons(uint16_t(tcp_port));
            if (::bind(tcp_fd, reinterpret_cast<sockaddr*>(&in), sizeof in) < 0 ||
                ::listen(tcp_fd, 8) < 0) {
                std::perror("bind(tcp)");
                ::close(tcp_fd);
                tcp_fd = -1;
            }
        }
    }

    if (!quiet) {
        std::printf("%s on %s, %u Hz / %u frames%s\n", arb::host::kFirmware,
                    sock_path.c_str(), o.sr, o.block,
                    o.offline ? " (offline)" : (" -> " + device.name()).c_str());
        if (tcp_fd >= 0) std::printf("control also on tcp/%d (token)\n", tcp_port);
        if (!device.realtime().empty()) std::printf("%s\n", device.realtime().c_str());
        if (!core_report.empty()) std::printf("%s\n", core_report.c_str());
        std::fflush(stdout);
    }

    // -- the IMU (device-os-spec.md § 6.5) ---------------------------------
    // On the control thread, not a thread of its own: § 1.2 adds no thread to
    // this process, and the read is six registers over a bus that is idle.
    // Absent hardware is not an error — `motion[]` stays zero, `arbutus/motion`
    // says NO SENSOR and SYSTEM hides KEY VEL, all of which already exist.
    arb::host::LinuxI2c imu_bus;
    arb::host::Lsm6dso imu;
    bool imu_on = false;
    imu.set_mount(*imu_mount);
    if (!imu_path.empty()) {
        const char* why = "";
        if (imu_bus.open(imu_path.c_str(), arb::host::lsm6dso::kAddress, &why) &&
            imu.init(imu_bus, &why)) {
            imu_on = true;
            std::printf("imu: LSM6DSO on %s at 0x%02x, mount %s\n",
                        imu_path.c_str(), arb::host::lsm6dso::kAddress,
                        imu_mount->name);
        } else {
            std::fprintf(stderr, "arbutus-audio: no IMU on %s (%s)\n",
                         imu_path.c_str(), why);
        }
        std::fflush(stdout);
    }
    // What the UI draws `NO SENSOR` from (#116). Settled here, before the
    // first client can be accepted -- the accept loop is below -- so no
    // handshake can ever see the value mid-decision.
    host.set_motion_sensor(imu_on);
    // 208 Hz ODR (§ 6.5), so the poll has to come round faster than that or
    // samples are dropped; 5 ms is the poll timeout below.
    const int poll_ms = imu_on ? 5 : 20;
    // The state document is pushed after any edit, but not after each one of
    // a burst (#74): building it costs ~2-3 ms on the CM4 and the client then
    // parses ~14 KB, and a STOP or a SAVE is five to ten messages in a row,
    // each of which waited for the document before its reply left. So the
    // push waits until no request has come for kStateSettle -- longer than the
    // UI's own gaps inside one gesture (its take adoption polls every 5 ms;
    // 1 ms was measured too short) -- and a client that never stops asking
    // still gets one kStateMax after the document first went dirty. Offline a
    // `render` is the clock -- the UI sends one a block, back to back -- so a
    // turn that answered one lets a pending push go: time has passed, and the
    // UI follows another client's transport from that push (#99).
    using clock = std::chrono::steady_clock;
    constexpr auto kStateSettle = std::chrono::milliseconds(8);
    constexpr auto kStateMax = std::chrono::milliseconds(50);
    clock::time_point last_asked{};
    clock::time_point dirty_since{};
    bool dirty_seen = false;
    auto imu_retry_at = std::chrono::steady_clock::now();

    std::vector<Client> clients;
    UiFeed ui_feed;
    ui_feed.window_ms = feed_window_ms;
    Live live;
    long long next_client = 0;
    const auto t_start = std::chrono::steady_clock::now();

    while (!g_stop && !host.should_quit()) {
        std::vector<pollfd> fds;
        fds.push_back(pollfd{listen_fd, POLLIN, 0});
        fds.push_back(pollfd{tcp_fd, tcp_fd >= 0 ? short(POLLIN) : short(0), 0});
        for (const Client& c : clients)
            fds.push_back(pollfd{c.fd, short(POLLIN | (c.out.empty() ? 0 : POLLOUT)), 0});

        int wait_ms = poll_ms;
        if (dirty_seen) {                // wake for the push when it falls due
            const auto due = std::min(last_asked + kStateSettle, dirty_since + kStateMax);
            const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(
                due - clock::now()).count();
            wait_ms = int(std::clamp<long long>(left + 1, 0, poll_ms));
        }
        if (host.bouncing() && !o.offline) wait_ms = 0;   // #920: render, do not sleep
        ::poll(fds.data(), nfds_t(fds.size()), wait_ms);
        bool rendered = false;           // offline: a block of time went by

        if (imu_on) {
            if (imu.healthy()) {
                float m[6];
                if (imu.feed(m)) host.set_motion(m);   // zeros once on a failure
            } else if (std::chrono::steady_clock::now() >= imu_retry_at) {
                // § 6.5: a bus error mid-run is reported once and retried
                // every 5 s. Reported once is what `reported` is for — a
                // journal line per 5 ms would be the loudest thing on the box.
                static bool reported = false;
                if (!reported) {
                    reported = true;
                    std::fprintf(stderr, "arbutus-audio: IMU bus error; "
                                         "motion is zero, retrying\n");
                }
                imu_retry_at = std::chrono::steady_clock::now() +
                    std::chrono::milliseconds(
                        int(arb::host::lsm6dso::kRetrySeconds * 1000));
                const char* why = "";
                imu.init(imu_bus, &why);
            }
            // § 6.5 gives "no WHO_AM_I" and "a bus error mid-run" the same
            // answer on screen, so the flag follows the driver's health and
            // not just whether the part was there at boot (#116). The setter
            // only pushes when the value actually moves.
            host.set_motion_sensor(imu.healthy());
        }

        // `fds` was built from the clients that existed before these
        // accepts, so anyone who arrives now has no entry in it and is read
        // on the next turn of the loop.
        const std::size_t polled = clients.size();
        if (fds[0].revents & POLLIN) {
            const int fd = ::accept(listen_fd, nullptr, nullptr);
            if (fd >= 0) {
                nonblocking(fd);
                Client c;
                c.fd = fd;
                c.id = ++next_client;
                clients.push_back(std::move(c));
            }
        }
        if (tcp_fd >= 0 && (fds[1].revents & POLLIN)) {
            const int fd = ::accept(tcp_fd, nullptr, nullptr);
            if (fd >= 0) {
                nonblocking(fd);
                int one = 1;
                ::setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
                Client c;
                c.fd = fd;
                c.id = ++next_client;
                c.tcp = true;
                clients.push_back(std::move(c));
            }
        }

        for (std::size_t k = 0; k < polled; ++k) {
            Client& c = clients[k];
            if (!(fds[k + 2].revents & (POLLIN | POLLHUP | POLLERR))) continue;
            char buf[8192];
            const ssize_t n = ::read(c.fd, buf, sizeof buf);
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) continue;
            if (n <= 0) { ui_feed_gone(ui_feed, c); live_gone(live, c); ::close(c.fd); c.fd = -1; continue; }
            // A connection already being closed is read only so its hang-up is seen.
            if (c.closing) continue;
            c.in.append(buf, std::size_t(n));

            std::size_t nl;
            while ((nl = c.in.find('\n')) != std::string::npos) {
                const std::string line = c.in.substr(0, nl);
                c.in.erase(0, nl + 1);
                if (line.find_first_not_of(" \t\r") == std::string::npos) continue;
                json msg;
                try {
                    msg = json::parse(line);
                } catch (const std::exception& e) {
                    // Malformed JSON is an error message, not a dropped
                    // connection: the UI must be able to recover (§8.2).
                    queue(c, json{{"type", "error"}, {"code", "bad_json"},
                                  {"message", "MALFORMED MESSAGE"}, {"detail", e.what()}});
                    continue;
                }
                // #596: valid JSON with a field of the wrong type ("proto":"1")
                // makes the JSON library throw from whichever accessor reads it.
                // Every client message gets exactly one reply (PROTOCOL.md), so
                // that is a `bad_request` here and never an abort of the process
                // the instrument's sound runs in.
                try {
                    // § 9.4's transport rules first: a TCP peer that has not
                    // said `hello` with the token never reaches `handle`.
                    if (json refused = tcp_gate(c, msg, token); !refused.is_null()) {
                        queue(c, refused);
                        if (c.closing) { c.in.clear(); break; }
                        continue;
                    }
                    if (const auto t = msg.find("type");
                        msg.is_object() && t != msg.end() && *t == "hello")
                        c.hello = true;
                    if (json r = live_handle(c, msg, live, host, clients); !r.is_null()) {
                        queue(c, r);
                        continue;
                    }
                    if (json r = ui_feed_handle(c, msg, ui_feed); !r.is_null()) {
                        queue(c, r);
                        continue;
                    }
                    queue(c, host.handle(msg));
                } catch (const json::exception& e) {
                    json r{{"type", "error"}, {"code", "bad_request"},
                           {"message", "BAD MESSAGE"}, {"detail", e.what()}};
                    if (msg.is_object()) {
                        if (const auto t = msg.find("type"); t != msg.end() && t->is_string())
                            r["for"] = *t;
                        if (const auto q = msg.find("req"); q != msg.end()) r["req"] = *q;
                    }
                    queue(c, r);
                }
                last_asked = clock::now();
                if (const auto t = msg.find("type");
                    msg.is_object() && t != msg.end() && *t == "render")
                    rendered = true;
            }
            // #596: what is left has no newline yet. Past the cap it is not a
            // message in the making; say so once and close the connection.
            if (!c.closing && c.in.size() > (c.tcp && !c.authed ? kMaxLineUnauthed : kMaxLine)) {
                queue(c, json{{"type", "error"}, {"code", "bad_request"},
                              {"message", "MESSAGE TOO LONG"},
                              {"detail", std::to_string(c.in.size()) + " bytes without a newline"}});
                c.in.clear();
                c.closing = true;
            }
        }
        clients.erase(std::remove_if(clients.begin(), clients.end(),
                                     [](const Client& c) { return c.fd < 0; }),
                      clients.end());

        host.poll();
        const auto now = clock::now();
        if (host.state_dirty() && !dirty_seen) { dirty_seen = true; dirty_since = now; }
        if (dirty_seen && (rendered || now - last_asked >= kStateSettle ||
                           now - dirty_since >= kStateMax)) {
            host.take_state_dirty();
            dirty_seen = false;
            const json s = host.state_doc();
            for (Client& c : clients) if (listening(c)) queue(c, s);
        }
        for (const json& e : host.drain_events())
            for (Client& c : clients) if (listening(c)) queue(c, e);
        for (const json& e : ui_feed.pushes)
            for (Client& c : clients) if (listening(c) && (c.feed || c.live)) queue(c, e);
        ui_feed.pushes.clear();
        for (const json& e : ui_feed.broadcast)
            for (Client& c : clients) if (listening(c)) queue(c, e);
        ui_feed.broadcast.clear();
        live_tick(live, host, clients);
        for (Client& c : clients) {
            flush(c);
            if (c.closing) { ui_feed_gone(ui_feed, c); live_gone(live, c); ::close(c.fd); c.fd = -1; }
        }
        clients.erase(std::remove_if(clients.begin(), clients.end(),
                                     [](const Client& c) { return c.fd < 0; }),
                      clients.end());

        if (run_seconds > 0.0 &&
            std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count() >=
                run_seconds)
            break;
    }

    for (Client& c : clients) { flush(c); ::close(c.fd); }
    ::close(listen_fd);
    if (tcp_fd >= 0) ::close(tcp_fd);
    ::unlink(sock_path.c_str());
    if (!keys_opt.port.empty()) {
        keys.stop();                     // joined before the device stops
        std::printf("%s\n", keys.summary().c_str());
    }
    device.stop();
    // A SIGTERM is how the device stops this process (`systemctl stop`): a
    // take still open then ends here, as STOP would end it, instead of
    // leaving its header a range behind the audio on the card (#612).
    host.end_open_take();
    if (imu_on)
        std::printf("imu: %ld samples, %ld bus errors\n", imu.samples(),
                    imu.errors());
    imu_bus.close();
    return 0;
}
