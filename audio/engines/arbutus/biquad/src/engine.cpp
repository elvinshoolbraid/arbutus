// arbutus/biquad — RBJ two-pole lowpass, the first stock box.
//
// A direct rewrite of emulator/engines/effects.py BiquadLowpass, kept
// arithmetic-for-arithmetic identical: same coefficient expressions in the same
// order, state in double, output rounded to float. That is what lets
// audio/tests/golden/python/biquad.npy — captured from the Python — stand as the
// cross-implementation conformance golden (engine-spec.md §9.4).
//
// The Python filter is the most expensive single item in the emulator's graph
// and the one place a C extension would have earned its keep; here it is, in C.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr double kPi = 3.141592653589793;

// arb_describe() is pure and callable before create(), so the descriptor cannot
// know the run's sample rate. 0.49*sr at the 48 kHz the system fixes
// (engine-spec.md §12); process() re-clamps to the rate it is actually given,
// exactly as the Python set_params does.
constexpr float kNyquistRef = 48000.0f * 0.49f;   // 23520 Hz

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/biquad");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;                 // an effect: the ENV page is greyed (§3.3)
    d.latency_frames = 0;
    d.num_params = 2;
    d.params[0] = arb::param_log("cutoff", "CUTOFF", 20.0f, kNyquistRef, 8000.0f, 0.0f, "Hz");
    d.params[1] = arb::param_lin("q", "Q", 0.5f, 10.0f, 1.0f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Biquad {
    double b0 = 0, b1 = 0, b2 = 0, a1 = 0, a2 = 0;
    double x1 = 0, x2 = 0, y1 = 0, y2 = 0;

    void coeffs(double cutoff, double q, double sr) {
        const double w0 = 2.0 * kPi * cutoff / sr;
        const double alpha = std::sin(w0) / (2.0 * q);
        const double cos_w0 = std::cos(w0);
        const double a0 = 1.0 + alpha;
        b0 = (1.0 - cos_w0) / 2.0 / a0;
        b1 = (1.0 - cos_w0) / a0;
        b2 = (1.0 - cos_w0) / 2.0 / a0;
        a1 = -2.0 * cos_w0 / a0;
        a2 = (1.0 - alpha) / a0;
    }

    void clear() { x1 = x2 = y1 = y2 = 0.0; }

    // Serial by nature — each output feeds the next — so this is a loop and
    // always will be.
    void run(const float* in, float* out, uint32_t n) {
        double lx1 = x1, lx2 = x2, ly1 = y1, ly2 = y2;
        for (uint32_t i = 0; i < n; ++i) {
            const double x = in[i];
            const double y = b0 * x + b1 * lx1 + b2 * lx2 - a1 * ly1 - a2 * ly2;
            lx2 = lx1; lx1 = x;
            ly2 = ly1; ly1 = y;
            out[i] = float(y);
        }
        x1 = lx1; x2 = lx2; y1 = ly1; y2 = ly2;
    }
};

struct Engine {
    float  norm[2]{};
    double cutoff = 8000.0, q = 1.0;
    double sr = 0.0;
    bool   dirty = true;
    Biquad ch[2];

    Engine() {
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        if (idx == 0) cutoff = v; else q = v;
        dirty = true;
    }

    void reset() {
        ch[0].clear();
        ch[1].clear();
    }

    void process(const arb_host& host, arb_io& io) {
        const double rate = host.sample_rate ? double(host.sample_rate) : 48000.0;
        if (dirty || rate != sr) {
            sr = rate;
            const double fc = std::clamp(cutoff, 20.0, sr * 0.49);
            const double qq = std::clamp(q, 0.5, 10.0);
            ch[0].coeffs(fc, qq, sr);
            ch[1].coeffs(fc, qq, sr);
            dirty = false;
        }
        for (int c = 0; c < 2; ++c) {
            if (io.in[c]) ch[c].run(io.in[c], io.out[c], io.frames);
            else          std::fill_n(io.out[c], io.frames, 0.0f);   // §3.7.3
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
