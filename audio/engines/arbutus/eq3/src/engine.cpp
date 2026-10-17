// arbutus/eq3 — three bands you can move, on a rack tab.
//
// This is *not* the per-track EQ (DDR-062, `host/mixer.hpp`). That one is host
// DSP on the mixer's eight strips: fixed shelves at 200 Hz and 3 kHz, a sweepable
// mid, and HP/LP, edited in real dB and Hz from MIXER › EQ. It is the desk.
// This is the pedal: it goes on any FX tab, on an instrument's rack or on the
// master's, every parameter is macro-bindable and modulatable like any other
// engine parameter, and the two shelves move as well as the mid -- which the
// desk's do not, because a desk's shelves are the same on every channel by
// design and these are per sound.
//
// The three bands are RBJ biquads: a low shelf, a peaking mid and a high shelf.
// There is no HP or LP here on purpose. The track EQ has them, `arbutus/biquad`
// is one, and eight slots spent on five filters would leave nothing to move
// them with.
//
// Ranges are the author's job (DDR-044); README.md argues every one of them.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr double kPi = 3.14159265358979324;

enum Param : uint32_t {
    kLow, kMid, kHigh, kMidF, kLowF, kHighF, kMidQ, kOut, kNumParams
};

// One RBJ biquad, direct form I, per channel state.
struct Biquad {
    float b0 = 1.0f, b1 = 0.0f, b2 = 0.0f, a1 = 0.0f, a2 = 0.0f;
    float x1[2]{}, x2[2]{}, y1[2]{}, y2[2]{};

    void clear() {
        for (int c = 0; c < 2; ++c) x1[c] = x2[c] = y1[c] = y2[c] = 0.0f;
    }

    void set(double b0_, double b1_, double b2_, double a0_, double a1_, double a2_) {
        b0 = float(b0_ / a0_); b1 = float(b1_ / a0_); b2 = float(b2_ / a0_);
        a1 = float(a1_ / a0_); a2 = float(a2_ / a0_);
    }

    // RBJ cookbook, S = 1 for both shelves: the steepest slope that does not
    // overshoot, which is the only shelf shape worth having on a tone control.
    void low_shelf(double f, double db, double sr) {
        const double A = std::pow(10.0, db / 40.0);
        const double w = 2.0 * kPi * f / sr, c = std::cos(w), s = std::sin(w);
        const double al = s / 2.0 * std::sqrt(2.0);
        const double t = 2.0 * std::sqrt(A) * al;
        set(A * ((A + 1) - (A - 1) * c + t), 2 * A * ((A - 1) - (A + 1) * c),
            A * ((A + 1) - (A - 1) * c - t),
            (A + 1) + (A - 1) * c + t, -2 * ((A - 1) + (A + 1) * c),
            (A + 1) + (A - 1) * c - t);
    }

    void high_shelf(double f, double db, double sr) {
        const double A = std::pow(10.0, db / 40.0);
        const double w = 2.0 * kPi * f / sr, c = std::cos(w), s = std::sin(w);
        const double al = s / 2.0 * std::sqrt(2.0);
        const double t = 2.0 * std::sqrt(A) * al;
        set(A * ((A + 1) + (A - 1) * c + t), -2 * A * ((A - 1) + (A + 1) * c),
            A * ((A + 1) + (A - 1) * c - t),
            (A + 1) - (A - 1) * c + t, 2 * ((A - 1) - (A + 1) * c),
            (A + 1) - (A - 1) * c - t);
    }

    void peak(double f, double db, double q, double sr) {
        const double A = std::pow(10.0, db / 40.0);
        const double w = 2.0 * kPi * f / sr, c = std::cos(w), s = std::sin(w);
        const double al = s / (2.0 * q);
        set(1 + al * A, -2 * c, 1 - al * A, 1 + al / A, -2 * c, 1 - al / A);
    }

    // The recursion is written as explicit std::fma, not as one arithmetic
    // expression, because the golden is a bit-exact contract and a plain
    // expression is not portable. A compiler on a machine with a fused
    // multiply-add contracts `b0*x + b1*X1 + ...` into fma chains (arm64 --
    // the Mac and the CM4 -- does, by default); the x86-64 SSE2 baseline has
    // no fma instruction, so it cannot, and rounds once more per term. With
    // the low shelf at 120 Hz the poles sit close enough to z=1 that the two
    // roundings diverge by -92 dBFS, which is over tests/golden's -100 dB
    // tolerance -- eq3 is the only stock box tight enough to cross it (the
    // next-closest golden is satcomb at -125 dB).
    //
    // std::fma is correctly rounded and fully specified by IEEE-754, so this
    // form gives identical bits on every conforming platform. It reproduces
    // the frozen golden exactly, which means it *is* what arm64 was already
    // computing: the device's output is unchanged and x86-64 now matches it.
    // The association below is that contraction, term for term -- the first
    // product is the fma's addend, the rest chain. Changing the order changes
    // the bits (checked: three other orders miss the golden by -91 to -98 dB).
    //
    // Cost: on a machine without an fma instruction this calls libm per term.
    // That is x86-64 development and CI only; arm64 emits fmadd as before.
    void run(int ch, const float* in, float* out, uint32_t n) {
        float X1 = x1[ch], X2 = x2[ch], Y1 = y1[ch], Y2 = y2[ch];
        for (uint32_t i = 0; i < n; ++i) {
            const float x = in[i];
            float y = std::fma(b0, x, b1 * X1);
            y = std::fma(b2, X2, y);
            y = std::fma(-a1, Y1, y);
            y = std::fma(-a2, Y2, y);
            X2 = X1; X1 = x;
            Y2 = Y1; Y1 = y;
            out[i] = y;
        }
        x1[ch] = X1; x2[ch] = X2; y1[ch] = Y1; y2[ch] = Y2;
    }
};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/eq3");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.latency_frames = 0;
    d.num_params = kNumParams;
    d.params[kLow]   = arb::param_lin("low",    "LOW",    -18.0f, 18.0f, 0.0f, 0.5f, "dB");
    d.params[kMid]   = arb::param_lin("mid",    "MID",    -18.0f, 18.0f, 0.0f, 0.5f, "dB");
    d.params[kHigh]  = arb::param_lin("high",   "HIGH",   -18.0f, 18.0f, 0.0f, 0.5f, "dB");
    d.params[kMidF]  = arb::param_log("mid_f",  "MID-F",  100.0f, 8000.0f, 900.0f, 0.0f, "Hz");
    d.params[kLowF]  = arb::param_log("low_f",  "LOW-F",   40.0f,  600.0f, 180.0f, 0.0f, "Hz");
    d.params[kHighF] = arb::param_log("high_f", "HIGH-F", 1200.0f, 12000.0f, 3200.0f, 0.0f, "Hz");
    d.params[kMidQ]  = arb::param_log("mid_q",  "MID-Q",    0.3f,     6.0f,   0.9f, 0.0f, "");
    d.params[kOut]   = arb::param_lin("out",    "OUT",    -12.0f,   12.0f,   0.0f, 0.5f, "dB");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    float  norm[kNumParams]{};
    double sr = 48000.0;
    float  low = 0.0f, mid = 0.0f, high = 0.0f;
    float  mid_f = 900.0f, low_f = 180.0f, high_f = 3200.0f, mid_q = 0.9f, out_db = 0.0f;
    Biquad bq[3];                      // low shelf, mid peak, high shelf

    explicit Engine(double rate) : sr(rate > 0.0 ? rate : 48000.0) {
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kLow:   low = v; break;
        case kMid:   mid = v; break;
        case kHigh:  high = v; break;
        case kMidF:  mid_f = v; break;
        case kLowF:  low_f = v; break;
        case kHighF: high_f = v; break;
        case kMidQ:  mid_q = v; break;
        default:     out_db = v; break;
        }
    }

    void reset() { for (Biquad& b : bq) b.clear(); }

    void process(const arb_host& host, arb_io& io) {
        const double rt = host.sample_rate ? double(host.sample_rate) : sr;
        // A band at exactly 0 dB is skipped, not run as a unity biquad: an
        // identity biquad is not bit-exact in float, and "flat is bypass" is
        // the rule the mixer's EQ already keeps (audio/CLAUDE.md, "EQ off").
        const bool on[3] = {low != 0.0f, mid != 0.0f, high != 0.0f};
        const float out_g = std::pow(10.0f, out_db / 20.0f);

        // Coefficients are recomputed per block, not per change: a macro may
        // drive any of these from the audio thread every block (§6.2) and this
        // is where it is safe to do the trig. No allocation, no locks.
        const double ny = rt * 0.49;
        if (on[0]) bq[0].low_shelf(std::min(double(low_f), ny), double(low), rt);
        if (on[1]) bq[1].peak(std::min(double(mid_f), ny), double(mid), double(mid_q), rt);
        if (on[2]) bq[2].high_shelf(std::min(double(high_f), ny), double(high), rt);
        for (int i = 0; i < 3; ++i)
            if (!on[i]) bq[i].clear();

        for (int c = 0; c < 2; ++c) {
            const float* in = io.in[c] ? io.in[c] : io.in[0];
            float* out = io.out[c];
            if (!in) { std::fill_n(out, io.frames, 0.0f); continue; }
            bool first = true;
            for (int i = 0; i < 3; ++i) {
                if (!on[i]) continue;
                bq[i].run(c, first ? in : out, out, io.frames);
                first = false;
            }
            if (first) {                      // flat: a copy, sample for sample
                if (out != in) std::copy_n(in, io.frames, out);
            }
            if (out_g != 1.0f)
                for (uint32_t k = 0; k < io.frames; ++k) out[k] *= out_g;
        }
    }
};

void* e_create(const arb_host* host, const char*) {
    return new (std::nothrow) Engine(host ? double(host->sample_rate) : 48000.0);
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
    nullptr,                 // load_asset: takes no assets (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    nullptr,                 // format_param: the host draws every readout
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
