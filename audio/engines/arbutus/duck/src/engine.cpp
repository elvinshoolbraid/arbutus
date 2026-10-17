// arbutus/duck — a sidechain ducker, and the first box to use side[].
//
// An envelope follower on the sidechain port drives a gain on the audio port:
// whatever is on tape track 1 pushes everything else out of its way. §6.6 is
// the contract -- the *source* is a rack setting (`tape:1..4` or `input`), not
// a parameter of this engine, so there is no source knob here.
//
// DEPTH is the fraction of the overshoot that is removed, which makes it one
// knob instead of the usual ratio/knee pair:
//
//     gain = (threshold / envelope) ^ depth      when envelope > threshold
//
// DEPTH 1 pins the sidechain's own level at the threshold (infinite ratio),
// DEPTH 0 does nothing, and every position between is a usable amount of pump
// (DDR-044). The reduction stops at -48 dB so the duck is a duck, not a gate.
//
// With no sidechain source selected the host passes side[] as NULL and this
// engine is a wire. That is deliberate: an effect that silences itself because
// a source was never picked is worse than one that does nothing visible.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr float kMaxCutDb = -48.0f;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/duck");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT | ARB_CAP_SIDECHAIN;
    d.honours_env = 0;
    d.latency_frames = 0;          // no lookahead: §12 says let latency happen
    d.num_params = 4;
    d.params[0] = arb::param_lin("threshold", "THRESH", -60.0f, 0.0f, -26.0f, 0.5f, "dB");
    d.params[1] = arb::param_lin("depth",     "DEPTH",    0.0f, 1.0f,  0.85f, 0.01f, "");
    // LOG on both times: the difference between 1 ms and 5 ms is the whole
    // character, and the difference between 800 ms and 1 s is nothing.
    d.params[2] = arb::param_log("attack",    "ATK",      0.5f, 100.0f,   5.0f, 0.0f, "ms");
    d.params[3] = arb::param_log("release",   "REL",     10.0f, 1000.0f, 150.0f, 0.0f, "ms");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    float  norm[4]{};
    float  thresh_db = -26.0f, depth = 0.85f, atk_ms = 5.0f, rel_ms = 150.0f;
    float  env = 0.0f;                  // sidechain envelope, linear

    Engine() {
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case 0: thresh_db = v; break;
        case 1: depth = v; break;
        case 2: atk_ms = v; break;
        default: rel_ms = v; break;
        }
    }

    void reset() { env = 0.0f; }

    void process(const arb_host& host, arb_io& io) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const float ka = float(1.0 - std::exp(-1000.0 / (double(atk_ms) * sr)));
        const float kr = float(1.0 - std::exp(-1000.0 / (double(rel_ms) * sr)));
        const float thr = std::pow(10.0f, thresh_db / 20.0f);
        const float floor_gain = std::pow(10.0f, kMaxCutDb / 20.0f);

        const float* sl = io.side[0];
        const float* sr_ = io.side[1];

        for (uint32_t i = 0; i < io.frames; ++i) {
            float g = 1.0f;
            if (sl) {
                const float mag = std::max(std::fabs(sl[i]), std::fabs(sr_ ? sr_[i] : sl[i]));
                env += (mag - env) * (mag > env ? ka : kr);
                if (env > thr && depth > 0.0f) {
                    // (thr/env)^depth, which is -depth * overshoot in dB.
                    g = std::max(floor_gain, std::pow(thr / env, depth));
                }
            }
            for (int c = 0; c < 2; ++c) {
                const float* in = io.in[c];
                io.out[c][i] = in ? in[i] * g : 0.0f;    // §3.7.3: both, always
            }
        }
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
    nullptr,                 // load_asset: takes no assets (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    nullptr,                 // format_param: the host draws every readout
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
