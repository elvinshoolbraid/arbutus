// arbutus/envelope — the aim-anywhere envelope (modulation-spec §3.2,
// DDR-047). A modulator, not the amp envelope: one global ADSR, triggered by
// notes (NOTE), by the sidechain's level (GATE-IN) or by nothing at all
// (FREE). README.md says what every range is for.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <iterator>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

enum Param : uint32_t { kAttack, kDecay, kSustain, kRelease, kTrig, kLoop, kCurve, kNumParams };
enum Output : uint32_t { kEnv, kInv };
enum Trig : int { kNote = 0, kGateIn, kFree };

const char* const kTrigLabels[] = {"NOTE", "GATE-IN", "FREE"};
const char* const kOffOn[] = {"OFF", "ON"};

// GATE-IN's detector: instant attack, 50 ms release so a waveform's zero
// crossings do not close the gate, opening at -30 dBFS and closing 3 dB lower
// so a level hovering at the threshold is one gate. [default chosen here: §3.2
// says "sidechain follower" and no numbers -- audio/CLAUDE.md]
constexpr double kGateOpen = 0.031623;    // -30 dBFS
constexpr double kGateClose = 0.022387;   // -33 dBFS
constexpr double kGateReleaseS = 0.050;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/envelope");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    // events for NOTE; sidechain for GATE-IN (§4). A tab with no source set in
    // GATE-IN mode simply never opens.
    d.caps = ARB_CAP_MODULATOR | ARB_CAP_EVENTS_IN | ARB_CAP_SIDECHAIN;
    d.num_params = kNumParams;                         // slot 7 empty
    d.params[kAttack]  = arb::param_log("attack", "ATTACK", 0.001f, 10.0f, 0.01f, 0.0f, "s");
    d.params[kDecay]   = arb::param_log("decay", "DECAY", 0.001f, 10.0f, 0.3f, 0.0f, "s");
    d.params[kSustain] = arb::param_lin("sustain", "SUSTAIN", 0.0f, 1.0f, 0.5f, 0.01f, "");
    d.params[kRelease] = arb::param_log("release", "RELEASE", 0.001f, 20.0f, 0.5f, 0.0f, "s");
    d.params[kTrig]    = arb::param_enum("trig", "TRIG", kTrigLabels, 3, kNote);
    d.params[kLoop]    = arb::param_enum("loop", "LOOP", kOffOn, 2, 0);
    d.params[kCurve]   = arb::param_lin("curve", "CURVE", -1.0f, 1.0f, 0.0f, 0.05f, "");
    arb::set_outputs(d, {arb::output("env", "ENV", false), arb::output("inv", "INV", true)});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

// CURVE shapes progress through a segment, x in 0..1 of its time. Positive
// bends toward the analog shape (fast start, slow finish, on the way up and
// down alike); negative the reverse; 0 is a straight line.
double shape(double x, double c) {
    if (std::fabs(c) < 1e-3) return x;
    const double k = 6.0 * std::fabs(c);
    return c > 0.0 ? (1.0 - std::exp(-k * x)) / (1.0 - std::exp(-k))
                   : (std::exp(k * x) - 1.0) / (std::exp(k) - 1.0);
}

struct Engine {
    float  norm[kNumParams]{};
    double a_s = 0.01, d_s = 0.3, s_lvl = 0.5, r_s = 0.5, curve = 0.0;
    int    trig = kNote;
    bool   loop = false;

    enum Stage : uint8_t { Idle, Attack, Decay, Sustain, Release } stage = Idle;
    double level = 0.0, from = 0.0, t = 0.0;   // t: seconds into the stage
    bool   gate = false;                       // held: a note, the sidechain, or FREE+LOOP
    bool   oneshot = false;                    // FREE without LOOP: A, D, then R unasked
    bool   held[128]{};
    int    n_held = 0;
    double det = 0.0;                          // GATE-IN detector
    int    trig_seen = -1;                     // mode last block, to catch a change

    Engine() {
        for (uint32_t i = 0; i < kNumParams; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void reset() {
        stage = Idle;
        level = from = t = 0.0;
        gate = oneshot = false;
        std::fill(std::begin(held), std::end(held), false);
        n_held = 0;
        det = 0.0;
        trig_seen = -1;               // FREE starts again "on load"
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= kNumParams) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        // No smoothing (rule 7): times and levels are read by the segment
        // maths every block, and the target smooths the output.
        switch (idx) {
        case kAttack:  a_s = v; break;
        case kDecay:   d_s = v; break;
        case kSustain: s_lvl = v; break;
        case kRelease: r_s = v; break;
        case kTrig:    trig = std::clamp(int(v), 0, 2); break;
        case kLoop:    loop = v >= 0.5f; break;
        case kCurve:   curve = v; break;
        default: break;
        }
    }

    void enter(Stage s) {
        stage = s;
        from = level;
        t = 0.0;
    }

    void gate_on() {
        gate = true;
        enter(Attack);           // every note-on retriggers, from where it is: no click
    }

    void gate_off() {
        gate = false;
        if (stage != Idle) enter(Release);
    }

    // Runs the stages for dt seconds. Attack takes A·(1 − start), so a
    // retrigger from high up is quick and LOOP from SUSTAIN keeps A's slope;
    // decay and release take their full time from wherever they start.
    void advance(double dt) {
        for (int guard = 0; guard < 4096 && dt > 0.0; ++guard) {
            double dur = 0.0, target = 0.0;
            switch (stage) {
            case Idle:    level = 0.0; return;
            case Sustain: level = s_lvl; return;
            case Attack:  dur = a_s * (1.0 - from); target = 1.0; break;
            case Decay:   dur = d_s; target = s_lvl; break;
            case Release: dur = r_s; target = 0.0; break;
            }
            const double left = dur - t;
            if (dt < left) {
                t += dt;
                level = from + (target - from) * shape(t / dur, curve);
                return;
            }
            dt -= std::max(0.0, left);
            level = target;
            switch (stage) {
            case Attack: enter(Decay); break;
            case Decay:
                if (loop && gate) enter(Attack);
                else if (oneshot) { oneshot = false; enter(Release); }
                else enter(Sustain);
                break;
            case Release: enter(Idle); break;
            default: break;
            }
        }
    }

    void process(const arb_host& host, arb_io& io) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const uint32_t n = io.frames;

        // A change of TRIG takes effect at the block: into FREE starts it (as
        // on load); out of FREE or GATE-IN lets go unless the new source is
        // holding. GATE-IN was missing here, so an open sidechain gate switched
        // to NOTE held SUSTAIN until some key came and went (#631).
        if (trig != trig_seen) {
            if (trig == kFree) {
                oneshot = !loop;
                gate_on();
                gate = loop;
            } else if (trig_seen == kFree || (trig_seen == kGateIn && gate)) {
                if (trig == kNote && n_held > 0) gate = true;
                else gate_off();
            }
            trig_seen = trig;
        }
        // FREE follows LOOP live: on starts cycling (again, if it had run
        // out), off finishes the current pass and releases.
        if (trig == kFree) {
            if (loop) {
                if (!gate || stage == Idle || stage == Release) gate_on();
                oneshot = false;
            } else if (gate) {
                gate = false;
                if (stage == Attack || stage == Decay) oneshot = true;
                else if (stage == Sustain) enter(Release);
            }
        }

        uint32_t pos = 0;
        auto run_to = [&](uint32_t f) {
            f = std::min(f, n);
            if (f > pos) advance(double(f - pos) / sr);
            pos = std::max(pos, f);
        };

        // Notes are always tracked, so switching to NOTE with a key down holds.
        const uint32_t nev = io.ev_in ? io.n_ev_in : 0u;
        const double rel = std::exp(-1.0 / (kGateReleaseS * sr));
        const bool side = io.side[0] && io.side[1];
        uint32_t k = 0;
        for (uint32_t f = 0; f < n; ++f) {
            for (; k < nev && io.ev_in[k].frame <= f; ++k) {
                const arb_event& e = io.ev_in[k];
                const bool on = e.type == ARB_EV_NOTE_ON && e.b > 0.0f;
                const bool off = e.type == ARB_EV_NOTE_OFF || (e.type == ARB_EV_NOTE_ON && e.b <= 0.0f);
                if (!on && !off) continue;
                const int note = e.a & 127;
                if (on) {
                    if (!held[note]) { held[note] = true; ++n_held; }
                    if (trig == kNote) { run_to(f); gate_on(); }
                } else if (held[note]) {
                    held[note] = false;
                    // Global, not per voice: the last key up releases (§3.2).
                    if (--n_held == 0 && trig == kNote) { run_to(f); gate_off(); }
                }
            }
            if (trig == kGateIn) {
                const double x = side ? std::max(std::fabs(double(io.side[0][f])),
                                                 std::fabs(double(io.side[1][f])))
                                      : 0.0;
                det = x > det ? x : det * rel;
                if (!gate && det > kGateOpen) { run_to(f); gate_on(); }
                else if (gate && det < kGateClose) { run_to(f); gate_off(); }
            }
        }
        // Events stamped past the block's end are the host's bug; still count them.
        for (; k < nev; ++k) {
            const arb_event& e = io.ev_in[k];
            const int note = e.a & 127;
            if (e.type == ARB_EV_NOTE_ON && e.b > 0.0f && !held[note]) { held[note] = true; ++n_held; }
            if ((e.type == ARB_EV_NOTE_OFF || (e.type == ARB_EV_NOTE_ON && e.b <= 0.0f)) && held[note]) {
                held[note] = false;
                --n_held;
            }
        }
        if (det < 1e-12) det = 0.0;
        run_to(n);

        // The block's value is its last frame, so a note-on anywhere in the
        // block is already moving the target this block.
        const float e = float(std::clamp(level, 0.0, 1.0));
        io.ctl_out[kEnv] = e;
        io.ctl_out[kInv] = 1.0f - 2.0f * e;
    }
};

// CURVE: "LIN", "EXP 40", "LOG 40" (percent of full bend). Pure in (idx, n).
int format_curve(float n, char* buf, uint32_t size) {
    const float c = arb::denorm(desc().params[kCurve], n);
    const int pct = int(std::lround(std::fabs(c) * 100.0f));
    const int w = pct == 0 ? std::snprintf(buf, size, "LIN")
                           : std::snprintf(buf, size, "%s %d", c > 0.0f ? "EXP" : "LOG", pct);
    return w > 0;
}

// ---- ABI shims -------------------------------------------------------------
void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    return idx < kNumParams ? static_cast<Engine*>(h)->norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->process(*host, *io);
}
int   e_format_param(void*, uint32_t idx, float n, char* buf, uint32_t size) {
    return (idx == kCurve && buf && size) ? format_curve(n, buf, size) : 0;
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: takes no assets
    nullptr,                 // set_note_param: no per_note params
    e_format_param,          // CURVE's readout
    nullptr,                 // set_voice_mod: a modulator has no voices
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
