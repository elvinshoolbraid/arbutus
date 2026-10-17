// arbutus/transient — a transient shaper, as a rack box (DDR-053).
//
// The Field's drum envelope had attack/gain/release/timing on the drum voice.
// DDR-053 moves the shaping into the rack, because a transient shaper is useful
// on anything, and leaves the drum voice an AD.
//
// Envelope difference, no threshold: three peak followers on the stereo-linked
// level max(|L|, |R|), each a one-pole with separate attack and release:
//
//   F   fast:          attack 0.5 ms, release SPEED
//   S   slow attack:   attack SPEED,  release SPEED
//   T   slow release:  attack 0.5 ms, release 4 x SPEED
//
// At an onset F jumps and S lags, so a = (F - S) / F rises toward 1 and falls
// back to 0 within about SPEED. As a note decays F falls and T holds, so
// t = (T - F) / T rises toward 1 through the tail. Both are ratios of the
// signal to itself, so
//
//   gain dB = 12 x (ATTACK x a + SUSTAIN x t)       ATTACK, SUSTAIN in -1..+1
//
// is level-independent -- a quiet hit is shaped like a loud one, and there is
// no threshold knob to set before anything happens (DDR-044) -- and bounded:
// +/-100 % moves the level at most 12 dB, reached only at the front of an
// onset from silence (or deep in a tail). A ratio F/S would be unbounded
// there. One gain for both channels, so the stereo image does not move.
//
// ATTACK 0 and SUSTAIN 0 make the gain exactly 1.0f without evaluating a pow,
// MIX 100 % and OUTPUT 0 dB are exact too: the default is a wire, sample for
// sample, which is what lets a rack carry the box before anyone touches it.
//
// ATTACK, SUSTAIN, MIX and OUTPUT are per-frame gain terms, so they glide over
// one host block (realtime rule 7, §15.7); at rest a Smoothed returns the stored
// value untouched, so the wire stays exact. SPEED sets filter coefficients read
// once per block, where a step is inaudible.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr int kNumParams = 5;
enum { kAttack = 0, kSustain, kSpeed, kMix, kOutput };

constexpr double kFastAttackMs = 0.5;   // fast enough for a stick, slow enough not to follow a 100 Hz cycle
constexpr double kTailFactor = 4.0;     // T releases this many times slower than F
constexpr float  kRangeDb = 12.0f;      // what +/-100 % can move: a shaper, not a gate
constexpr float  kFloor = 1.0e-9f;      // silence: every ratio is 0, not 0/0

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/transient");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.latency_frames = 0;               // no lookahead: §12, let latency not happen
    d.num_params = kNumParams;
    d.params[kAttack]  = arb::param_lin("attack",  "ATTACK",  -100.0f, 100.0f, 0.0f, 1.0f, "%");
    d.params[kSustain] = arb::param_lin("sustain", "SUSTAIN", -100.0f, 100.0f, 0.0f, 1.0f, "%");
    // LOG: 5 ms is a click, 30 ms a snare's crack, 200 ms a whole kick. The
    // useful difference is at the bottom.
    d.params[kSpeed]   = arb::param_log("speed",   "SPEED",      5.0f, 200.0f, 30.0f, 0.0f, "ms");
    d.params[kMix]     = arb::param_lin("mix",     "MIX",        0.0f, 100.0f, 100.0f, 1.0f, "%");
    // SHIFT row. Trim for the level a big ATTACK adds; +/-12 dB covers it.
    d.params[kOutput]  = arb::param_lin("output",  "OUTPUT",   -12.0f, 12.0f, 0.0f, 0.1f, "dB");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

float coeff(double ms, double sr) { return float(1.0 - std::exp(-1000.0 / (ms * sr))); }

struct Follower {
    float v = 0.0f;
    float step(float x, float ka, float kr) {
        v += (x - v) * (x > v ? ka : kr);
        return v;
    }
};

struct Engine {
    float norm[kNumParams]{};
    float val[kNumParams]{};
    arb::Smoothed<float> atk, sus, mix{1.0f}, out_g{1.0f};
    Follower f, s, t;

    explicit Engine(const arb_host* host) {
        if (host) for (arb::Smoothed<float>* p : {&atk, &sus, &mix, &out_g}) p->prepare(*host);
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = val[idx] = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kAttack:  atk.set(v / 100.0f); break;
        case kSustain: sus.set(v / 100.0f); break;
        case kMix:     mix.set(v / 100.0f); break;
        case kOutput:  out_g.set(v == 0.0f ? 1.0f : std::pow(10.0f, v / 20.0f)); break;
        default: break;                          // SPEED: read per block
        }
    }

    void reset() {
        f = s = t = Follower{};
        for (arb::Smoothed<float>* p : {&atk, &sus, &mix, &out_g}) p->reset();
    }

    void process(const arb_host& host, arb_io& io) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const double speed = double(val[kSpeed]);
        const float kfa = coeff(kFastAttackMs, sr);
        const float kspd = coeff(speed, sr);
        const float ktail = coeff(speed * kTailFactor, sr);

        const float* inl = io.in[0];
        const float* inr = io.in[1] ? io.in[1] : io.in[0];
        for (uint32_t i = 0; i < io.frames; ++i) {
            const float xl = inl ? inl[i] : 0.0f;
            const float xr = inr ? inr[i] : 0.0f;
            // The followers always run, so turning ATTACK up mid-hit finds them
            // already tracking rather than starting from zero.
            const float x = std::max(std::fabs(xl), std::fabs(xr));
            const float ef = f.step(x, kfa, kspd) + kFloor;
            const float es = s.step(x, kspd, kspd) + kFloor;
            const float et = t.step(x, kfa, ktail) + kFloor;
            const float ka = atk.next(), ks = sus.next(), m = mix.next(), og = out_g.next();
            float g = 1.0f;
            if (ka != 0.0f || ks != 0.0f) {
                const float a = std::max(0.0f, (ef - es) / ef);
                const float t_ = std::max(0.0f, (et - ef) / et);
                g = std::pow(10.0f, kRangeDb * (ka * a + ks * t_) / 20.0f);
            }
            // Read before write: out may alias in (arbutus_engine.h).
            const float wl = xl * g, wr = xr * g;
            io.out[0][i] = (m == 1.0f ? wl : xl + (wl - xl) * m) * og;
            io.out[1][i] = (m == 1.0f ? wr : xr + (wr - xr) * m) * og;
        }
    }
};

void* e_create(const arb_host* host, const char*) { return new (std::nothrow) Engine(host); }
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
    nullptr,                 // load_asset: takes no assets (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    nullptr,                 // format_param: the host draws every readout
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
