// arbutus/euclid — the Euclidean rhythm generator (engine-spec.md §15.4).
//
// PULSES onsets spread as evenly as STEPS allows, rotated by ROT. One note on
// every onset, on whatever key is held, so the rhythm is the instrument and the
// key is only the pitch. There is nothing to enter and nothing to store: the
// numbers *are* the pattern, which is why this box has no soft-keys.
//
// The spread is the closed form, not Bjorklund's recursion:
//
//     onset(i)  <->  (i * PULSES) mod STEPS  <  PULSES
//
// with no recursion, no allocation and no table -- one multiply per query,
// which is what §3.7.1 wants from a box that answers this on the audio thread.
// It gives Bjorklund's necklace **up to rotation** (E(5,8) comes out x.x.xx.x
// where the textbook writes x.xx.xx.), and rotation is a knob here, so the
// difference is one detent of ROT rather than a different rhythm.
//
// Clock, SWING and GATE are `endless`'s, deliberately: the three sequencer
// boxes swing alike and a division means the same thing in all of them.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr int kMaxSteps = 32;
constexpr int kStepGuard = 64;
constexpr int kLatchNote = 60;      // middle C: what HOLD plays before you do
constexpr int kMaxDown = 16;        // keys down at once: ten fingers, and room

// `endless`'s division list, unchanged.
constexpr float kDivBeats[] = {1.0f, 0.5f, 1.0f / 3.0f, 0.25f, 1.0f / 6.0f,
                               0.125f, 1.0f / 12.0f};
const char* const kDivLabels[] = {"1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32", "1/32T"};
constexpr int kNumDivs = int(sizeof kDivBeats / sizeof kDivBeats[0]);

const char* const kOffOn[] = {"OFF", "ON"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/euclid");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_EVENTS_IN | ARB_CAP_EVENTS_OUT | ARB_CAP_SEQUENCER;
    d.honours_env = 0;               // it makes no sound, so it has no envelope
    d.latency_frames = 0;
    d.num_params = 8;
    d.params[0] = arb::param("steps", "STEPS", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             1.0f, float(kMaxSteps), 16.0f, 1.0f, "");
    d.params[1] = arb::param("pulses", "PULSES", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             0.0f, float(kMaxSteps), 4.0f, 1.0f, "");
    d.params[2] = arb::param("rot", "ROT", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             0.0f, float(kMaxSteps - 1), 0.0f, 1.0f, "");
    d.params[3] = arb::param_enum("div", "DIV", kDivLabels, kNumDivs, 3);   // 1/16
    // ---- the SHIFT row ----
    d.params[4] = arb::param("gate", "GATE", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             10.0f, 100.0f, 60.0f, 1.0f, "%");
    d.params[5] = arb::param("swing", "SWING", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             50.0f, 75.0f, 50.0f, 1.0f, "%");
    d.params[6] = arb::param_enum("hold", "HOLD", kOffOn, 2, 0);
    d.params[7] = arb::param("accent", "ACCENT", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             0.0f, 100.0f, 0.0f, 1.0f, "%");
    // No soft-keys: the numbers are the pattern (README.md).
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
    int   steps = 16;
    int   pulses = 4;
    int   rot = 0;
    int   div = 3;
    int   gate = 60;
    int   swing = 50;
    bool  hold = false;
    int   accent = 0;

    // ---- playback ----
    int64_t step_count = 0;
    double  acc = 0.0;
    bool    gate_done = true;
    bool    first = true;
    int     trigger_note = -1;
    bool    latched = false;
    int     sounding = -1;
    int     down[kMaxDown]{};        // keys physically down, oldest first
    int     n_down = 0;

    // ---- emission ----
    arb_event* ev_out = nullptr;
    uint32_t   ev_cap = 0;
    uint32_t*  n_ev = nullptr;
    int        deferred[kMaxDown]{}; // note-offs waiting for the next block
    int        n_deferred = 0;

    Engine() {
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case 0: steps = std::clamp(int(v), 1, kMaxSteps); break;
        case 1: pulses = std::clamp(int(v), 0, kMaxSteps); break;
        case 2: rot = std::clamp(int(v), 0, kMaxSteps - 1); break;
        case 3: div = std::clamp(int(v), 0, kNumDivs - 1); break;
        case 4: gate = std::clamp(int(v), 10, 100); break;
        case 5: swing = int(v); break;
        case 6: {
            const bool on = v >= 0.5f;
            if (on != hold) { hold = on; hold_changed(0); }
            break;
        }
        default: accent = std::clamp(int(v), 0, 100); break;
        }
    }

    // ---- emission ------------------------------------------------------
    //
    // A note-off asked for outside process() (HOLD turned off between blocks)
    // has no buffer to go to, so it waits for frame 0 of the next block.
    // Dropping it left the note stuck in the ENGINE (#631).
    void emit(uint32_t frame, uint16_t type, int note, float vel) {
        if (!ev_out || !n_ev) {
            if (type == ARB_EV_NOTE_OFF && n_deferred < kMaxDown) deferred[n_deferred++] = note;
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

    // ---- the necklace --------------------------------------------------
    //
    // PULSES above STEPS is every step (the knob does not have to know STEPS).
    bool onset(int64_t n) const {
        if (pulses <= 0) return false;
        if (pulses >= steps) return true;
        const int i = int(((n + rot) % steps + steps) % steps);
        return (int64_t(i) * pulses) % steps < pulses;
    }

    // The **downbeat** -- the first step of each cycle, `n % steps == 0` --
    // keeps full velocity and ACCENT is how far the others drop under it.
    // ROT moves the necklace under the counter, not the counter, so ROT is
    // also what decides whether the downbeat is an onset at all: that is the
    // musical job it does. 100 % leaves the rest at 0.3, not 0 -- a silent
    // off-pulse is what PULSES is for, and a rhythm that vanishes at the top
    // of a knob is a trap.
    float velocity(int64_t n) const {
        if (accent == 0 || n % steps == 0) return 1.0f;
        return 1.0f - 0.7f * float(accent) / 100.0f;
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

    bool running() const { return trigger_note >= 0; }

    void fire(uint32_t frame) {
        silence(frame);
        if (!onset(step_count) || trigger_note < 0) return;
        emit(frame, ARB_EV_NOTE_ON, trigger_note, velocity(step_count));
        sounding = trigger_note;
    }

    void advance(const arb_host& h, uint32_t off, uint32_t n) {
        if (!running()) return;
        if (first) {
            first = false;
            step_count = 0;
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
            const double step = step_samples(h, step_count);
            if (!gate_done) {
                const double g = step * double(gate) / 100.0;
                if (gate < 100 && g < step) {
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
            const double need = std::max(0.0, step - acc);
            if (need >= remaining) { acc += remaining; return; }
            remaining -= need;
            at = off + uint32_t(double(n) - remaining);
            acc = 0.0;
            gate_done = false;
            ++step_count;
            fire(at);
        }
        acc += remaining;
    }

    // ---- transport -----------------------------------------------------
    //
    // A new key while one is already down re-pitches without restarting the
    // necklace: the rhythm is the point, and snapping it back to step 0
    // mid-bar is never what you want (`endless` does the same).
    //
    // Every key down is remembered, so letting go of the newest falls back to
    // the one still held rather than stopping under it (#631); the last key
    // up is the one that stops (or, with HOLD, latches).
    void key_down(int note) {
        key_up(note);
        if (n_down < kMaxDown) down[n_down++] = note;
    }

    bool key_up(int note) {
        for (int i = 0; i < n_down; ++i)
            if (down[i] == note) {
                for (int j = i; j + 1 < n_down; ++j) down[j] = down[j + 1];
                --n_down;
                return true;
            }
        return false;
    }

    void trigger(int note) {
        const bool repitch = (trigger_note >= 0) && !latched;
        if (latched) n_down = 0;
        key_down(note);
        trigger_note = note;
        latched = false;
        if (repitch) return;
        step_count = 0;
        acc = 0.0;
        gate_done = true;
        first = true;
    }

    void release(int note, uint32_t frame) {
        if (!key_up(note)) return;
        if (n_down > 0) { trigger_note = down[n_down - 1]; return; }
        if (hold) { latched = true; return; }
        stop(frame);
    }

    void stop(uint32_t frame) {
        silence(frame);
        trigger_note = -1;
        latched = false;
        n_down = 0;
        step_count = 0;
        acc = 0.0;
        gate_done = true;
        first = true;
    }

    void hold_changed(uint32_t frame) {
        if (hold) {
            // HOLD with nothing played yet starts on middle C, so the knob
            // does something on its own -- this box needs no key to be useful.
            if (trigger_note < 0) { trigger_note = kLatchNote; latched = true; }
        } else if (latched) {
            stop(frame);
        }
    }

    // ---- events --------------------------------------------------------
    void handle(const arb_event& e, uint32_t frame) {
        switch (e.type) {
        case ARB_EV_NOTE_ON:
            if (e.b <= 0.0f) { release(int(e.a), frame); break; }
            trigger(int(e.a));
            break;
        case ARB_EV_NOTE_OFF:
            release(int(e.a), frame);
            break;
        case ARB_EV_TRANSPORT:
            if (e.a == 0) stop(frame);           // STOP silences a running necklace
            break;
        default:
            break;                               // clock: the host's, not ours
        }
    }

    void reset() { stop(0); }

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

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
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
    nullptr,                 // load_asset: the numbers are the pattern (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    nullptr,                 // format_param: the host draws every readout
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
