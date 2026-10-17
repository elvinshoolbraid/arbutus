// #631: what the sequencer and modulator boxes do when something changes
// *between* blocks or *mid-gesture* -- the gap the event goldens cannot see,
// because `arbutus-run --set` fixes every parameter before the render.
//
// Each box is loaded as the host loads it and driven block by block, with
// set_param() between blocks, as the control thread does. Every event a box
// emits is also checked against the header's 0 <= frame < frames (risk 8).
//
//   test_seqmod <box root>      e.g. audio/build/boxes

#include <cmath>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include <arbutus_engine.h>

#include <box.hpp>
#include <manifest.hpp>
#include <params.hpp>

namespace {

std::string g_root;
int g_bad = 0;

void check(bool pass, const std::string& what, const std::string& detail) {
    std::printf("%s  %-50s %s\n", pass ? "ok  " : "FAIL", what.c_str(), detail.c_str());
    if (!pass) ++g_bad;
}

// One box, one block size, a running clock. Events queued with note()/key()
// arrive at frame 0 of the next block; what the box emits is kept with its
// frame rebased onto the run's timeline.
struct Rig {
    arb::box::Box box;
    void* h = nullptr;
    arb_host host{};
    uint32_t block = 64;
    int64_t pos = 0;
    std::vector<arb_event> in, out;
    int out_of_block = 0;            // events stamped frame >= frames
    float side_level = 0.0f;         // a constant sidechain, for GATE-IN
    float ctl[ARB_MAX_OUTPUTS]{};    // a modulator's last block

    bool open(const char* id, uint32_t blk, double bpm = 120.0) {
        block = blk;
        const std::string dir = g_root + "/arbutus/" + id;
        if (arb::box::Refusal r = arb::box::open(dir, box); !r.ok()) {
            check(false, std::string("open ") + id, r.code + ": " + r.detail);
            return false;
        }
        host.sample_rate = 48000;
        host.max_block = blk;
        host.tempo_bpm = bpm;
        host.ppqn = 24;
        host.playing = 1;
        h = box.vt->create(&host, dir.c_str());
        return h != nullptr;
    }

    ~Rig() {
        if (h) box.vt->destroy(h);
        if (box.vt) arb::box::close(box);
    }

    void set(const char* id, float value) {
        for (uint32_t i = 0; i < box.desc->num_params; ++i)
            if (arb::manifest::fixed(box.desc->params[i].id, sizeof box.desc->params[i].id) == id) {
                box.vt->set_param(h, i, arb::norm(box.desc->params[i], value));
                return;
            }
        check(false, std::string("no parameter ") + id, "");
    }

    void push(uint16_t type, int a, float b) {
        arb_event e{};
        e.type = type;
        e.a = uint16_t(a);
        e.b = b;
        in.push_back(e);
    }
    void note(int n, bool on) { push(on ? ARB_EV_NOTE_ON : ARB_EV_NOTE_OFF, n, on ? 1.0f : 0.0f); }
    void key(int k, bool down) { push(ARB_EV_SOFTKEY, k, down ? 1.0f : 0.0f); }

    void blocks(int k) {
        static float scratch[2][ARB_MAX_BLOCK];
        static float side[ARB_MAX_BLOCK];
        std::vector<arb_event> ev(256);
        for (int b = 0; b < k; ++b) {
            for (uint32_t i = 0; i < block; ++i) side[i] = side_level;
            uint32_t n_ev = 0;
            arb_io io{};
            io.frames = block;
            io.out[0] = scratch[0];
            io.out[1] = scratch[1];
            io.side[0] = io.side[1] = side;
            io.ev_in = in.empty() ? nullptr : in.data();
            io.n_ev_in = uint32_t(in.size());
            io.ev_out = ev.data();
            io.ev_out_cap = uint32_t(ev.size());
            io.n_ev_out = &n_ev;
            host.song_frame = pos;
            box.vt->process(h, &host, &io);
            in.clear();
            for (uint32_t i = 0; i < n_ev; ++i) {
                if (ev[i].frame >= block) ++out_of_block;
                arb_event e = ev[i];
                e.frame += uint32_t(pos);
                out.push_back(e);
            }
            std::memcpy(ctl, io.ctl_out, sizeof ctl);
            pos += block;
        }
    }

    // Notes the box turned on and never turned off.
    int hung() const {
        std::map<int, int> on;
        for (const arb_event& e : out) {
            if (e.type == ARB_EV_NOTE_ON && e.b > 0.0f) ++on[e.a];
            else if (e.type == ARB_EV_NOTE_OFF || e.type == ARB_EV_NOTE_ON) --on[e.a];
        }
        int n = 0;
        for (const auto& [note, c] : on) n += c > 0 ? c : 0;
        return n;
    }

    std::vector<arb_event> ons(int64_t from, int64_t to = INT64_MAX) const {
        std::vector<arb_event> v;
        for (const arb_event& e : out)
            if (e.type == ARB_EV_NOTE_ON && e.b > 0.0f && e.frame >= from && e.frame < to)
                v.push_back(e);
        return v;
    }

    // endless and pattern: a phrase entered on the soft-keys, REC held.
    void enter(std::initializer_list<int> notes) {
        key(0, true);
        blocks(1);
        for (int n : notes) { note(n, true); blocks(1); note(n, false); blocks(1); }
        key(0, false);
        blocks(1);
    }
};

std::string num(long long v) { return std::to_string(v); }

// Row 1: HOLD off while a latched sequence sounds must end its note. The
// note-off came from set_param(), outside process(), and had nowhere to go.
void hold_off(const char* id) {
    Rig r;
    if (!r.open(id, 64)) return;
    r.set("gate", 100.0f);
    if (!std::strcmp(id, "euclid")) r.set("pulses", 16.0f);    // every step sounds
    if (!std::strcmp(id, "endless") || !std::strcmp(id, "pattern")) r.enter({60, 64});
    r.set("hold", 1.0f);
    r.note(60, true);
    r.blocks(400);                         // several steps: 6000 frames each
    r.note(60, false);
    r.blocks(400);
    const std::size_t before = r.ons(0).size();
    r.set("hold", 0.0f);
    r.blocks(200);
    check(before > 0 && r.hung() == 0, std::string(id) + ": HOLD off ends the latched note",
          num(before) + " note-ons, " + num(r.hung()) + " left sounding");
    check(r.out_of_block == 0, std::string(id) + ": every event inside its block",
          num(r.out_of_block) + " at frame >= frames");
}

// Row 3: hold A, press B, release B -- the sequence keeps running on A, and
// stops when A comes up. `want` is the pitch set A's run plays.
void last_key_up(const char* id, std::initializer_list<int> want) {
    Rig r;
    if (!r.open(id, 64)) return;
    r.set("gate", 100.0f);
    if (!std::strcmp(id, "euclid")) r.set("pulses", 16.0f);
    if (!std::strcmp(id, "endless") || !std::strcmp(id, "pattern")) r.enter({60, 64});
    const int a = 48, b = 50;
    r.note(a, true);
    r.blocks(100);
    r.note(b, true);
    r.blocks(100);
    r.note(b, false);
    r.blocks(1);
    // A step (6000 frames at 1/16, 120 bpm) after B is up, A's run is playing.
    const int64_t from = r.pos + 6000, to = from + 4 * 6000;
    r.blocks(4 * 6000 / 64 + 100);
    const auto mid = r.ons(from, to);
    bool pitch_ok = !mid.empty();
    for (const arb_event& e : mid) {
        bool hit = false;
        for (int w : want) hit = hit || e.a == w;
        pitch_ok = pitch_ok && hit;
    }
    check(pitch_ok, std::string(id) + ": B up with A held keeps A's run",
          num(long(mid.size())) + " note-ons in the 4 steps after");
    r.note(a, false);
    r.blocks(1);
    const int64_t gone = r.pos;
    r.blocks(400);
    check(r.ons(gone).empty() && r.hung() == 0, std::string(id) + ": A up then stops it",
          num(long(r.ons(gone).size())) + " note-ons after, " + num(r.hung()) + " sounding");
}

// Row 2: endless swings on the step count, so an odd-length phrase keeps the
// pairs of steps on the beat grid: onset 18 is 9 pairs, 9 x 12000 frames in.
void endless_swing() {
    Rig r;
    if (!r.open("endless", 64)) return;
    r.enter({60, 62, 64});
    r.set("swing", 66.0f);
    r.set("hold", 1.0f);
    const int64_t start = r.pos;
    r.blocks(3 * 48000 / 64);
    const auto on = r.ons(start);
    const long long err = on.size() > 18 ? (long long)(on[18].frame - on[0].frame) - 108000 : -1;
    check(on.size() > 18 && std::llabs(err) <= 1, "endless: SWING on a 3-step phrase stays on grid",
          "onset 18 is " + num(err) + " frames off the grid");
}

// Risk 8: a step boundary on a block's end was stamped frame == frames.
// 90 bpm at the device's 64 frames put 48 of 49 arp events there.
void block_end(const char* id) {
    Rig r;
    if (!r.open(id, 64, 90.0)) return;
    if (!std::strcmp(id, "endless") || !std::strcmp(id, "pattern")) r.enter({60, 64});
    r.note(60, true);
    r.blocks(1500);
    check(!r.out.empty() && r.out_of_block == 0, std::string(id) + ": 90 bpm, 64 frames: inside its block",
          num(r.out_of_block) + " of " + num(long(r.out.size())) + " at frame >= frames");
}

// Row 4: GATE-IN open, TRIG switched to NOTE: no key is down, so it releases.
void envelope_trig() {
    Rig r;
    if (!r.open("envelope", 128)) return;
    r.set("attack", 0.01f);
    r.set("decay", 0.05f);
    r.set("sustain", 0.6f);
    r.set("release", 0.1f);
    r.set("trig", 1.0f);                   // GATE-IN
    r.side_level = 0.5f;
    r.blocks(48000 / 128);
    const float held = r.ctl[0];
    r.set("trig", 0.0f);                   // NOTE, with no key ever pressed
    r.blocks(2 * 48000 / 128);
    check(held > 0.5f && r.ctl[0] < 0.001f, "envelope: GATE-IN -> NOTE with no key releases",
          "ENV " + std::to_string(held) + " -> " + std::to_string(r.ctl[0]));
}

// Row 5: RETRIG NOTE draws a new S&H value on every note-on, PHASE or not.
// PHASE 180 and a retrigger at phase 0.6 put the S&H key past the wrap, where
// it read the same before and after the retrigger.
void lfo_retrig() {
    Rig r;
    if (!r.open("lfo", 64)) return;
    r.set("phase", 180.0f);
    r.set("retrig", 1.0f);                 // NOTE; RATE stays 1/4: 2 Hz at 120 bpm
    r.note(60, true);
    r.blocks(1);
    int changed = 0;
    const int kTimes = 20;
    for (int i = 0; i < kTimes; ++i) {
        r.blocks(14400 / 64 - 1);          // to phase 0.6: 0.3 s of a 0.5 s cycle
        const float before = r.ctl[3];
        r.note(60, true);
        r.blocks(1);
        changed += r.ctl[3] != before;
    }
    check(changed == kTimes, "lfo: PHASE 180, every RETRIG draws a new S&H",
          num(changed) + " of " + num(kTimes));
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "usage: test_seqmod <box root>\n"); return 2; }
    g_root = argv[1];
    for (const char* id : {"arp", "euclid", "endless", "pattern"}) hold_off(id);
    last_key_up("euclid", {48});
    last_key_up("endless", {48, 52});      // the phrase 60 64, moved to A
    last_key_up("pattern", {60, 64});      // absolute pitch: the key only runs it
    endless_swing();
    for (const char* id : {"arp", "euclid", "endless", "pattern"}) block_end(id);
    envelope_trig();
    lfo_retrig();
    std::printf("%s\n", g_bad ? "FAIL" : "all passed");
    return g_bad ? 1 : 0;
}
