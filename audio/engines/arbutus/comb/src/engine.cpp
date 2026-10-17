// arbutus/comb — a feedback comb tuned in semitones.
//
// The cheapest thing in the instrument that has a pitch. A delay line short
// enough to be heard as a note, fed back on itself through a one-pole
// lowpass: struck, it rings; driven, it is a resonator you play with the TUNE
// knob rather than a "delay effect" with a time in milliseconds.
//
// TUNE is semitones around A2 = 110 Hz, so the knob is a keyboard: the delay
// is sr / (110 * 2^(tune/12)) samples and the resonance lands on a note. The
// read is linearly interpolated, so sweeping TUNE glides instead of stepping.
//
// This is the shape emulator/engines/* never had (the Python cost class made a
// per-sample feedback loop unaffordable at any useful count -- board_bench.py
// --effects). There is no Python golden to conform to; the golden here was
// frozen from this box, checked first for peak / RMS / DC / NaN.

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

// The lowest note TUNE reaches, which is what sizes the delay line. A2 two
// octaves down is 27.5 Hz; anything lower stops being a pitch.
constexpr double kBaseHz = 110.0;
constexpr double kMinHz  = 27.5;
constexpr double kPi     = 3.14159265358979324;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/comb");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;                 // an effect: the ENV page is greyed (§3.3)
    d.latency_frames = 0;
    d.num_params = 4;
    // DDR-044: every position on every knob has to sound like something.
    // FDBK stops at 0.98 because 1.0 is an oscillator, not an effect.
    d.params[0] = arb::param_lin("tune",     "TUNE", -24.0f, 24.0f, 0.0f, 0.5f, "st");
    d.params[1] = arb::param_lin("feedback", "FDBK",   0.0f, 0.98f, 0.8f, 0.01f, "");
    d.params[2] = arb::param_lin("damp",     "DAMP",   0.0f, 1.0f,  0.3f, 0.01f, "");
    d.params[3] = arb::param_lin("mix",      "MIX",    0.0f, 1.0f,  0.5f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

// A feedback comb has a DC gain of 1/(1-fb) -- at FDBK 0.9 that is ten times,
// and an input with any offset (a bare impulse has plenty) piles up in the
// line as a slow ramp under the note. Blocking DC on the way *in* keeps the
// loop linear and leaves the dry path alone.
struct DcBlock {
    float x1 = 0.0f, y1 = 0.0f;
    void clear() { x1 = y1 = 0.0f; }
    float step(float x) {
        const float y = x - x1 + 0.9995f * y1;      // ~4 Hz at 48 kHz
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

    // One sample. `delay` is in samples and may be fractional; `a` is the
    // one-pole coefficient inside the loop (1 = no damping).
    float step(float x, double delay, float fb, float a) {
        const int cap = int(buf.size());
        double rp = double(wpos) - delay;
        while (rp < 0.0) rp += double(cap);
        const int i0 = int(rp) % cap;
        const int i1 = (i0 + 1) % cap;
        const float frac = float(rp - std::floor(rp));
        const float wet = buf[std::size_t(i0)] * (1.0f - frac) + buf[std::size_t(i1)] * frac;

        lp += a * (wet - lp);
        buf[std::size_t(wpos)] = x + fb * lp;
        wpos = (wpos + 1) % cap;
        return wet;
    }
};

struct Engine {
    float  norm[4]{};
    double sr = 48000.0;
    float  tune = 0.0f, fb = 0.8f, damp = 0.3f, mix = 0.5f;
    Line   ch[2];

    explicit Engine(double rate) : sr(rate > 0.0 ? rate : 48000.0) {
        const std::size_t cap = std::size_t(sr / kMinHz) + 4;
        for (Line& l : ch) {
            l.buf.assign(cap, 0.0f);      // create() is where allocation lives (§3.7.1)
            l.clear();
        }
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    // Called from the audio thread every block when a macro drives it (§6.2),
    // so it stays arithmetic: nothing here allocates or clears a buffer.
    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case 0: tune = v; break;
        case 1: fb = v; break;
        case 2: damp = v; break;
        default: mix = v; break;
        }
    }

    void reset() { for (Line& l : ch) l.clear(); }

    void process(const arb_host& host, arb_io& io) {
        const double rate = host.sample_rate ? double(host.sample_rate) : sr;
        const double hz = kBaseHz * std::pow(2.0, double(tune) / 12.0);
        const double cap = double(ch[0].buf.size());
        // damp 1 leaves 5% of the loop passing per sample: dark, still alive.
        const float a = 1.0f - damp * 0.95f;
        // The loop's lowpass delays the ring too, more as DAMP rises and on
        // high notes: take its phase delay at the note off the line, or TUNE
        // lands on the note only at DAMP 0 (#630: -88 ct at +24, DAMP 0.9).
        const double w = 2.0 * kPi * hz / rate;
        const std::complex<double> z1 = std::polar(1.0, -w);     // e^-jw
        const double lag = -std::arg(double(a) / (1.0 - double(1.0f - a) * z1)) / w;
        const double delay = std::clamp(rate / hz - lag, 1.0, cap - 2.0);
        const float wetg = mix;
        const float dryg = 1.0f - mix;

        for (int c = 0; c < 2; ++c) {
            const float* in = io.in[c];
            float* out = io.out[c];
            if (!in) { std::fill_n(out, io.frames, 0.0f); ch[c].clear(); continue; }
            Line& l = ch[c];
            for (uint32_t i = 0; i < io.frames; ++i) {
                const float x = in[i];                 // in and out may alias
                out[i] = dryg * x + wetg * l.step(l.dc.step(x), delay, fb, a);
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
