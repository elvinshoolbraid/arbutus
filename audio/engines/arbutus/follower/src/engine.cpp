// arbutus/follower — an input follower (modulation-spec §3.2, §4). A
// modulator that is a sidechain consumer: rectify, smooth, emit. The source
// (`input`, `tape:n`, `self`, `master`) is a rack setting, not a knob, exactly
// as for arbutus/duck (§6.6), so no slot is spent on it. README.md says what
// every range is for.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

enum Param : uint32_t { kAttack, kRelease, kGain, kThresh, kHold, kNumParams };
enum Output : uint32_t { kEnv, kFast, kInv, kGate };

// FAST's fixed times: short enough to see a drum hit inside one 2.7 ms block,
// long enough on release not to ripple at a bass note's period.
constexpr double kFastAttackS = 0.0005, kFastReleaseS = 0.030;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/follower");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_MODULATOR | ARB_CAP_SIDECHAIN;
    d.num_params = kNumParams;                         // slots 5-7 empty
    // LOG times, as on duck: 1 ms against 5 ms is the character, 400 against
    // 500 is nothing.
    d.params[kAttack]  = arb::param_log("attack", "ATTACK", 0.1f, 500.0f, 5.0f, 0.0f, "ms");
    d.params[kRelease] = arb::param_log("release", "RELEASE", 5.0f, 5000.0f, 150.0f, 0.0f, "ms");
    d.params[kGain]    = arb::param_lin("gain", "GAIN", -12.0f, 36.0f, 0.0f, 0.5f, "dB");
    d.params[kThresh]  = arb::param_lin("thresh", "THRESH", -60.0f, 0.0f, -30.0f, 0.5f, "dB");
    d.params[kHold]    = arb::param_lin("hold", "HOLD", 0.0f, 1000.0f, 50.0f, 5.0f, "ms");
    arb::set_outputs(d, {arb::output("env", "ENV", false), arb::output("fast", "FAST", false),
                         arb::output("inv", "INV", true), arb::output("gate", "GATE", false)});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

// One-pole coefficient for a time constant; 0 s is a wire.
double coef(double seconds, double sr) { return seconds > 0.0 ? std::exp(-1.0 / (seconds * sr)) : 0.0; }

struct Engine {
    float  norm[kNumParams]{};
    double atk_s = 0.005, rel_s = 0.15, gain = 1.0, thresh = 0.0316, hold_s = 0.05;

    double env = 0.0, fast = 0.0;     // linear, after GAIN
    double hold_left = 0.0;           // frames the gate stays open below THRESH
    bool   gate = false;

    // Coefficients depend on the sample rate, which only process() is told
    // reliably; cached per rate so a knob turn costs one exp().
    double sr_seen = 0.0, a_atk = 0.0, a_rel = 0.0, f_atk = 0.0, f_rel = 0.0;

    Engine() {
        for (uint32_t i = 0; i < kNumParams; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void reset() {
        env = fast = 0.0;
        hold_left = 0.0;
        gate = false;
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= kNumParams) return;
        norm[idx] = arb::clamp01(n);
        const double v = arb::denorm(desc().params[idx], norm[idx]);
        // No smoothing (rule 7): ATTACK and RELEASE *are* smoothing, and the
        // rest are read once per block, the output's own resolution.
        switch (idx) {
        case kAttack:  atk_s = v / 1000.0; break;
        case kRelease: rel_s = v / 1000.0; break;
        case kGain:    gain = std::pow(10.0, v / 20.0); break;
        case kThresh:  thresh = std::pow(10.0, v / 20.0); break;
        case kHold:    hold_s = v / 1000.0; break;
        default: break;
        }
        sr_seen = 0.0;                // recompute coefficients next block
    }

    void process(const arb_host& host, arb_io& io) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        if (sr != sr_seen) {
            a_atk = coef(atk_s, sr);
            a_rel = coef(rel_s, sr);
            f_atk = coef(kFastAttackS, sr);
            f_rel = coef(kFastReleaseS, sr);
            sr_seen = sr;
        }
        const double hold_frames = hold_s * sr;
        // No source selected: the host passes side[] as NULL, and the follower
        // hears silence and lets go, rather than holding its last value.
        const bool has = io.side[0] && io.side[1];
        for (uint32_t k = 0; k < io.frames; ++k) {
            const double x = has ? gain * std::max(std::fabs(double(io.side[0][k])),
                                                   std::fabs(double(io.side[1][k])))
                                 : 0.0;
            env = x + (x > env ? a_atk : a_rel) * (env - x);
            fast = x + (x > fast ? f_atk : f_rel) * (fast - x);
            // HOLD counts from the last frame above THRESH, so a gate that
            // chatters at the threshold stays one gate.
            if (env > thresh) {
                gate = true;
                hold_left = hold_frames;
            } else if (hold_left > 0.0) {
                hold_left -= 1.0;
            } else {
                gate = false;
            }
        }
        if (env < 1e-12) env = 0.0;      // no denormals in a silent tail
        if (fast < 1e-12) fast = 0.0;

        // The block's value is its last frame: the newest level there is, and
        // `self`/`master` are already a block old (§4).
        const float e = float(std::min(env, 1.0));
        io.ctl_out[kEnv] = e;
        io.ctl_out[kFast] = float(std::min(fast, 1.0));
        io.ctl_out[kInv] = 1.0f - 2.0f * e;
        io.ctl_out[kGate] = gate ? 1.0f : 0.0f;
    }
};

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

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: takes no assets
    nullptr,                 // set_note_param: no per_note params
    nullptr,                 // format_param: every readout is a number and a unit
    nullptr,                 // set_voice_mod: a modulator has no voices
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
