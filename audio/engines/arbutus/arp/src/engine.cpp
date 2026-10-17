// arbutus/arp — the arpeggiator (engine-spec.md §15.4).
//
// The chord is the pattern. Hold notes; the box plays them one at a time on
// the clock, in the order MODE names, across OCT octaves. Nothing is entered
// and nothing is stored, which is the whole difference from `endless`: this
// box has no memory at all, so it has no soft-keys either (see README.md).
//
// Shared with `endless` by design, not by accident: the clock is frames
// against `host.tempo_bpm` (an ARB_EV_CLOCK tick at ppqn 24 is coarser than a
// 1/32 triplet), steps fire sample-accurately inside the block (§3.4), and
// SWING is the same even/odd stretch so the two boxes swing alike.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>
#include <random>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr int kMaxHeld = 16;        // ten fingers, and room for a sustain pedal
constexpr int kMaxOct = 4;
constexpr int kStepGuard = 256;     // ratchets make the unit short; never spin

// The same division list as `endless`, so a phrase moved between the two boxes
// keeps its rhythm.
constexpr float kDivBeats[] = {1.0f, 0.5f, 1.0f / 3.0f, 0.25f, 1.0f / 6.0f,
                               0.125f, 1.0f / 12.0f};
const char* const kDivLabels[] = {"1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32", "1/32T"};
constexpr int kNumDivs = int(sizeof kDivBeats / sizeof kDivBeats[0]);

const char* const kModeLabels[] = {"UP", "DOWN", "UPDN", "DNUP", "ORDER", "RAND"};
enum Mode { kUp = 0, kDown, kUpDn, kDnUp, kOrder, kRand, kNumModes };

const char* const kOffOn[] = {"OFF", "ON"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/arp");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_EVENTS_IN | ARB_CAP_EVENTS_OUT | ARB_CAP_SEQUENCER;
    d.honours_env = 0;               // it makes no sound, so it has no envelope
    d.latency_frames = 0;
    d.num_params = 7;
    d.params[0] = arb::param_enum("div", "DIV", kDivLabels, kNumDivs, 3);   // 1/16
    d.params[1] = arb::param_enum("mode", "MODE", kModeLabels, kNumModes, kUp);
    d.params[2] = arb::param("oct", "OCT", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             1.0f, float(kMaxOct), 1.0f, 1.0f, "");
    d.params[3] = arb::param("gate", "GATE", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             10.0f, 100.0f, 80.0f, 1.0f, "%");
    // ---- the SHIFT row ----
    d.params[4] = arb::param("swing", "SWING", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             50.0f, 75.0f, 50.0f, 1.0f, "%");
    d.params[5] = arb::param_enum("hold", "HOLD", kOffOn, 2, 0);
    d.params[6] = arb::param("ratchet", "RATCHET", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             1.0f, 4.0f, 1.0f, 1.0f, "");
    // No soft-keys: every gesture this box has is a knob (README.md).
    d.num_softkeys = 0;
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    // ---- parameters ----
    float norm[ARB_MAX_PARAMS]{};
    int   div = 3;
    int   mode = kUp;
    int   oct = 1;
    int   gate = 80;
    int   swing = 50;
    bool  hold = false;
    int   ratchet = 1;

    // ---- the chord, in both orders ----
    int  sorted[kMaxHeld]{};         // ascending: UP/DOWN/UPDN/DNUP/RAND
    int  order[kMaxHeld]{};          // as played: ORDER
    int  n_held = 0;                 // the chord being played
    int  n_down = 0;                 // keys physically down
    bool latched = false;            // HOLD is keeping a released chord alive

    // ---- playback ----
    int64_t step_count = 0;
    int     sub = 0;                 // which ratchet repeat inside the step
    int     r_cur = 1;               // RATCHET snapshotted at the step boundary
    double  acc = 0.0;
    bool    gate_done = true;
    bool    first = true;
    int     sounding = -1;

    std::mt19937 rng{0};
    uint32_t seed = 0;

    // ---- emission ----
    arb_event* ev_out = nullptr;
    uint32_t   ev_cap = 0;
    uint32_t*  n_ev = nullptr;
    int        deferred[kMaxHeld]{};   // note-offs waiting for the next block
    int        n_deferred = 0;

    explicit Engine(uint32_t rng_seed) : seed(rng_seed) {
        rng.seed(seed);
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case 0: div = std::clamp(int(v), 0, kNumDivs - 1); break;
        case 1: mode = std::clamp(int(v), 0, kNumModes - 1); break;
        case 2: oct = std::clamp(int(v), 1, kMaxOct); break;
        case 3: gate = std::clamp(int(v), 10, 100); break;
        case 4: swing = int(v); break;
        case 5: {
            const bool on = v >= 0.5f;
            // Turning HOLD off drops a chord nobody is holding any more.
            if (!on && hold && latched) drop_chord(0);
            hold = on;
            break;
        }
        default: ratchet = std::clamp(int(v), 1, 4); break;
        }
    }

    // ---- emission ------------------------------------------------------
    //
    // A note-off asked for outside process() (HOLD turned off between blocks)
    // has no buffer to go to, so it waits for frame 0 of the next block.
    // Dropping it left the note stuck in the ENGINE (#631).
    void emit(uint32_t frame, uint16_t type, int note, float vel) {
        if (!ev_out || !n_ev) {
            if (type == ARB_EV_NOTE_OFF && n_deferred < kMaxHeld) deferred[n_deferred++] = note;
            return;
        }
        if (*n_ev >= ev_cap) return;
        arb_event& e = ev_out[(*n_ev)++];
        e = arb_event{};
        e.frame = frame;
        e.type = type;
        e.a = uint16_t(note);
        e.b = vel;
    }

    void flush_deferred() {
        const int n = n_deferred;
        n_deferred = 0;
        for (int i = 0; i < n; ++i) emit(0, ARB_EV_NOTE_OFF, deferred[i], 0.0f);
    }

    void silence(uint32_t frame) {
        if (sounding >= 0) emit(frame, ARB_EV_NOTE_OFF, sounding, 0.0f);
        sounding = -1;
    }

    // ---- the chord -----------------------------------------------------
    //
    // Two counts, deliberately: `n_held` is the chord being played and
    // `n_down` is how many keys are physically down. HOLD is the difference --
    // with it on, the last key up latches the chord instead of ending it, and
    // the next key down starts a fresh one.
    void press(int note) {
        if (latched) { n_held = 0; latched = false; }   // a new chord, not an addition
        ++n_down;
        for (int i = 0; i < n_held; ++i) if (order[i] == note) return;
        if (n_held >= kMaxHeld) return;
        order[n_held] = note;
        // Insertion sort: sixteen notes, and it keeps `sorted` allocation-free.
        int i = n_held;
        while (i > 0 && sorted[i - 1] > note) { sorted[i] = sorted[i - 1]; --i; }
        sorted[i] = note;
        ++n_held;
        if (first) { step_count = 0; sub = 0; acc = 0.0; }
    }

    void lift(int note, uint32_t frame) {
        if (n_down > 0) --n_down;
        if (hold) {
            if (n_down == 0 && n_held > 0) latched = true;
            return;
        }
        int k = -1;
        for (int i = 0; i < n_held; ++i) if (order[i] == note) { k = i; break; }
        if (k < 0) return;
        for (int i = k; i + 1 < n_held; ++i) order[i] = order[i + 1];
        int j = -1;
        for (int i = 0; i < n_held; ++i) if (sorted[i] == note) { j = i; break; }
        if (j >= 0) for (int i = j; i + 1 < n_held; ++i) sorted[i] = sorted[i + 1];
        --n_held;
        if (n_held == 0) stop(frame);
    }

    void drop_chord(uint32_t frame) {
        n_held = 0;
        n_down = 0;
        latched = false;
        stop(frame);
    }

    // ---- the sequence --------------------------------------------------
    int seq_len() const { return n_held * oct; }

    // Position i of the arpeggio: the chord, then the chord an octave up, ...
    int seq_note(int i) const {
        const int n = n_held;
        if (n <= 0) return -1;
        const int base = (mode == kOrder) ? order[i % n] : sorted[i % n];
        const int note = base + 12 * (i / n);
        return note <= 127 ? note : -1;
    }

    // Which position the n-th step plays. Pure in `n` apart from RAND, so the
    // count alone says where the arpeggio is.
    int position(int64_t n) {
        const int len = seq_len();
        if (len <= 0) return -1;
        switch (mode) {
        case kDown:  return int(len - 1 - (n % len));
        case kUpDn:
        case kDnUp: {
            if (len == 1) return 0;
            const int64_t r = n % (2 * len - 2);      // ends not repeated
            const int up = int(r < len ? r : 2 * len - 2 - r);
            return mode == kUpDn ? up : len - 1 - up;
        }
        case kRand:  return int(rng() % uint32_t(len));
        default:     return int(n % len);             // UP and ORDER
        }
    }

    // ---- clock ---------------------------------------------------------
    double step_samples(const arb_host& h, int64_t index) const {
        const double bpm = std::max(1.0, h.tempo_bpm);
        const double sr = h.sample_rate ? double(h.sample_rate) : 48000.0;
        const double base = double(kDivBeats[div]) * 60.0 / bpm * sr;
        const double sw = double(swing - 50) / 100.0;        // 0 .. 0.25
        if (sw <= 0.0) return base;
        return (index % 2 == 0) ? base * (1.0 + sw) : base * (1.0 - sw);
    }

    bool running() const { return n_held > 0; }

    void fire(uint32_t frame) {
        silence(frame);
        const int p = position(step_count);
        if (p < 0) return;
        const int note = seq_note(p);
        if (note < 0) return;
        emit(frame, ARB_EV_NOTE_ON, note, 1.0f);
        sounding = note;
    }

    void advance(const arb_host& h, uint32_t off, uint32_t n) {
        if (!running()) return;
        if (first) {
            first = false;
            step_count = 0;
            sub = 0;
            r_cur = ratchet;
            fire(off);
            acc = 0.0;
            gate_done = false;
        }
        // An event's frame is where the clock stands in this call, rounded down
        // once. Adding up each step's truncated length instead lost a frame per
        // step whenever several fell in one block, so the timing moved with the
        // block size (#631, found by ragged_events at 2048).
        double remaining = double(n);
        uint32_t at = off;
        for (int guard = 0; guard < kStepGuard; ++guard) {
            const double slen = step_samples(h, step_count) / double(r_cur);
            if (!gate_done) {
                const double g = slen * double(gate) / 100.0;
                if (gate < 100 && g < slen) {
                    const double need = std::max(0.0, g - acc);
                    if (need >= remaining) { acc += remaining; return; }
                    remaining -= need;
                    at = off + uint32_t(double(n) - remaining);
                    acc = std::max(acc, g);
                    silence(at);
                }
                gate_done = true;
            }
            // `>=`, not `>`: a boundary exactly at the block's end is frame 0 of the
            // next block. Firing it here stamped it `frame == frames`, which the
            // header forbids (#631).
            const double need = std::max(0.0, slen - acc);
            if (need >= remaining) { acc += remaining; return; }
            remaining -= need;
            at = off + uint32_t(double(n) - remaining);
            acc = 0.0;
            gate_done = false;
            if (++sub >= r_cur) { sub = 0; ++step_count; r_cur = ratchet; }
            fire(at);
        }
        acc += remaining;
    }

    void stop(uint32_t frame) {
        silence(frame);
        step_count = 0;
        sub = 0;
        acc = 0.0;
        gate_done = true;
        first = true;
    }

    // ---- events --------------------------------------------------------
    void handle(const arb_event& e, uint32_t frame) {
        switch (e.type) {
        case ARB_EV_NOTE_ON:
            if (e.b <= 0.0f) { lift(int(e.a), frame); break; }
            press(int(e.a));
            break;
        case ARB_EV_NOTE_OFF:
            lift(int(e.a), frame);
            break;
        case ARB_EV_TRANSPORT:
            if (e.a == 0) drop_chord(frame);     // STOP silences a running arp
            break;
        default:
            break;
        }
    }

    void reset() {
        drop_chord(0);
        rng.seed(seed);                          // §3.7.5: an RNG's position is state
    }

    void process(const arb_host& host, arb_io& io) {
        // A sequencer fills no audio port, but §3.7.3 is unconditional.
        for (int c = 0; c < 2; ++c)
            if (io.out[c]) std::fill_n(io.out[c], io.frames, 0.0f);

        ev_out = io.ev_out;
        ev_cap = io.ev_out ? io.ev_out_cap : 0u;
        n_ev = io.n_ev_out;
        flush_deferred();

        const uint32_t frames = io.frames;
        const arb_event* ev = io.ev_in;
        const uint32_t nev = ev ? io.n_ev_in : 0u;
        uint32_t pos = 0, ei = 0;
        for (;;) {
            while (ei < nev && std::min(ev[ei].frame, frames) <= pos) handle(ev[ei++], pos);
            if (pos >= frames) break;
            const uint32_t next = (ei < nev) ? std::min(frames, ev[ei].frame) : frames;
            advance(host, pos, next - pos);
            pos = next;
        }
        ev_out = nullptr;
        n_ev = nullptr;
    }
};

void* e_create(const arb_host* host, const char*) {
    return new (std::nothrow) Engine(host ? host->rng_seed : 0u);
}
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    return idx < desc().num_params ? static_cast<Engine*>(h)->norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->process(*host, *io);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: the chord is the pattern (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    nullptr,                 // format_param: the host draws every readout
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
