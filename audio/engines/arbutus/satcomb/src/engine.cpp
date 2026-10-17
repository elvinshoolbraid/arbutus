// arbutus/satcomb — the saturating comb. The one that was unaffordable.
//
// arbutus/comb with a nonlinearity *inside* the feedback loop:
//
//     y = drive*x + fb*d ;  y = y / (1 + |y|)
//
// which is the whole difference. A linear comb at FDBK 0.95 either rings
// forever or it does not; this one compresses as it rings, so the note blooms,
// squashes, and dies with a changing timbre. That is the "colourbass" class --
// a resonator that is also a limiter -- and it is exactly what a per-sample
// Python loop could not buy at any useful voice count (board_bench.py
// --effects put serial DSP at ~30x a vectorised form, which is why there is no
// Python original to conform to). See README.md for the measured cost here.
//
// The saturator has unity slope at zero, so DRIVE changes how hard the loop is
// hit, not how much of it comes back: the loop gain is still FDBK and FDBK
// alone. It bounds the line to +/-1 by construction, which is why FDBK is
// allowed all the way to 1.0 here and stops at 0.98 on the linear comb.

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <new>
#include <vector>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr double kBaseHz = 110.0;
constexpr double kMinHz  = 27.5;
constexpr double kPi     = 3.14159265358979324;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/satcomb");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.latency_frames = 0;
    d.num_params = 5;
    d.params[0] = arb::param_lin("tune",     "TUNE", -24.0f, 24.0f, 0.0f, 0.5f, "st");
    d.params[1] = arb::param_lin("feedback", "FDBK",   0.0f, 1.0f,  0.9f, 0.01f, "");
    d.params[2] = arb::param_lin("damp",     "DAMP",   0.0f, 1.0f,  0.35f, 0.01f, "");
    // LOG: the audible half of a drive control lives in its bottom third.
    d.params[3] = arb::param_log("drive",    "DRIVE",  0.5f, 12.0f, 2.0f, 0.0f, "");
    d.params[4] = arb::param_lin("mix",      "MIX",    0.0f, 1.0f,  0.6f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

// A feedback comb has a DC gain of 1/(1-fb); here it sits inside the loop, so
// neither the input's offset nor the saturator's own can pile up in the line.
struct DcBlock {
    static constexpr float kPole = 0.9995f;         // ~4 Hz at 48 kHz
    float x1 = 0.0f, y1 = 0.0f;
    void clear() { x1 = y1 = 0.0f; }
    float step(float x) {
        const float y = x - x1 + kPole * y1;
        x1 = x;
        y1 = y;
        return y;
    }
};

struct Line {
    std::vector<float> buf;
    int   wpos = 0;
    float lp = 0.0f;
    DcBlock dc;

    void clear() {
        std::fill(buf.begin(), buf.end(), 0.0f);
        wpos = 0;
        lp = 0.0f;
        dc.clear();
    }

    float step(float x, double delay, float fb, float a, float drive) {
        const int cap = int(buf.size());
        double rp = double(wpos) - delay;
        while (rp < 0.0) rp += double(cap);
        const int i0 = int(rp) % cap;
        const int i1 = (i0 + 1) % cap;
        const float frac = float(rp - std::floor(rp));
        const float wet = buf[std::size_t(i0)] * (1.0f - frac) + buf[std::size_t(i1)] * frac;

        lp += a * (wet - lp);
        const float y = drive * x + fb * lp;
        // The saturator is odd, but a transient hits it asymmetrically and the
        // little DC step that comes out is then multiplied by the loop's DC
        // gain of 1/(1-FDBK) -- 20x at 0.95. Blocking inside the loop, not just
        // at the input, is what keeps the line centred.
        buf[std::size_t(wpos)] = dc.step(y / (1.0f + std::fabs(y)));
        wpos = (wpos + 1) % cap;
        return wet;
    }
};

struct Engine {
    float  norm[5]{};
    double sr = 48000.0;
    float  tune = 0.0f, fb = 0.9f, damp = 0.35f, drive = 2.0f, mix = 0.6f;
    Line   ch[2];

    explicit Engine(double rate) : sr(rate > 0.0 ? rate : 48000.0) {
        const std::size_t cap = std::size_t(sr / kMinHz) + 4;
        for (Line& l : ch) { l.buf.assign(cap, 0.0f); l.clear(); }
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case 0: tune = v; break;
        case 1: fb = v; break;
        case 2: damp = v; break;
        case 3: drive = v; break;
        default: mix = v; break;
        }
    }

    void reset() { for (Line& l : ch) l.clear(); }

    void process(const arb_host& host, arb_io& io) {
        const double rate = host.sample_rate ? double(host.sample_rate) : sr;
        const double hz = kBaseHz * std::pow(2.0, double(tune) / 12.0);
        const double cap = double(ch[0].buf.size());
        // The loop's lowpass and DC blocker delay (and advance) the ring:
        // take their phase delay at the note off the line, or TUNE lands on
        // the note only at DAMP 0 and sits sharp there (#630: -86 ct at +24,
        // DAMP 0.9; +10 ct at TUNE 0, DAMP 0).
        const float a = 1.0f - damp * 0.95f;
        const double w = 2.0 * kPi * hz / rate;
        const std::complex<double> z1 = std::polar(1.0, -w);     // e^-jw
        const std::complex<double> h = double(a) / (1.0 - double(1.0f - a) * z1) *
                                       (1.0 - z1) / (1.0 - double(DcBlock::kPole) * z1);
        const double lag = -std::arg(h) / w;
        const double delay = std::clamp(rate / hz - lag, 1.0, cap - 2.0);
        const float wetg = mix;
        const float dryg = 1.0f - mix;

        for (int c = 0; c < 2; ++c) {
            const float* in = io.in[c];
            float* out = io.out[c];
            if (!in) { std::fill_n(out, io.frames, 0.0f); ch[c].clear(); continue; }
            Line& l = ch[c];
            for (uint32_t i = 0; i < io.frames; ++i) {
                const float x = in[i];
                out[i] = dryg * x + wetg * l.step(x, delay, fb, a, drive);
            }
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
