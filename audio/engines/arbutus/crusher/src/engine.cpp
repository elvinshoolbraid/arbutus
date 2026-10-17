// arbutus/crusher — the two halves of a bad converter, and nothing else.
//
// A digital signal has exactly two resolutions: how finely it is measured
// (BITS) and how often (RATE). Everything a "lo-fi" box does is one of those
// two being wrong, so those are the two knobs, and they are deliberately
// *separate* -- 12 bits at 48 kHz and 16 bits at 6 kHz are different sounds
// and a single "amount" knob would only ever walk one line through them.
//
// DRIVE is the third thing that matters and it is not a gain: the pre-gain is
// undone exactly on the way out, so the knob moves the signal against the
// quantiser's grid and the clipper, and nothing else. Turn it up and the
// quantisation noise falls away under the clipping; turn it down and the
// signal lives on fewer and fewer steps.
//
// Six parameters, slots 6 and 7 empty on purpose. A crusher with eight knobs
// is six knobs and two apologies -- the same argument `arbutus/osc` makes for
// having two (§14, DDR-044).
//
// Everything here aliases. That is what it is for: the sample-and-hold's
// images fold back into the band and *are* the sound of a cheap sampler. TONE
// is the one place to buy some of that back.
//
// Ranges are the author's job (DDR-044); README.md argues every one of them.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr double kPi = 3.14159265358979324;

enum Param : uint32_t { kBits, kRate, kDrive, kMix, kTone, kJitter, kNumParams };

constexpr float kToneMaxHz = 20000.0f;     // the top of TONE reads OFF

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/crusher");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.latency_frames = 0;
    d.num_params = kNumParams;               // slots 6-7 deliberately empty
    // The bottom is 2 bits, not 1: a mid-tread quantiser with one level
    // rounds everything under half scale to zero, so 1 bit is a mute for
    // anything but a peak -- "the bottom of the knob is silence", which is
    // what DDR-044 forbids (arbutus/va's CUTOFF makes the same argument).
    d.params[kBits]   = arb::param_lin("bits",   "BITS",   2.0f, 16.0f, 8.0f, 0.1f, "");
    d.params[kRate]   = arb::param_log("rate",   "RATE",   200.0f, 48000.0f, 8000.0f, 0.0f, "Hz");
    d.params[kDrive]  = arb::param_lin("drive",  "DRIVE",  0.0f, 24.0f, 0.0f, 0.5f, "dB");
    d.params[kMix]    = arb::param_lin("mix",    "MIX",    0.0f, 1.0f, 1.0f, 0.01f, "");
    d.params[kTone]   = arb::param_log("tone",   "TONE",   500.0f, kToneMaxHz, kToneMaxHz, 0.0f, "Hz");
    d.params[kJitter] = arb::param_lin("jitter", "JITTER", 0.0f, 1.0f, 0.0f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    float    norm[kNumParams]{};
    double   sr = 48000.0;
    float    bits = 8.0f, rate_hz = 8000.0f, drive_db = 0.0f, mix = 1.0f;
    float    tone_hz = kToneMaxHz, jitter = 0.0f;
    // One hold clock for both channels: a converter samples them together, and
    // two clocks would smear the stereo image into two different signals.
    double   acc = 1.0;                      // >= 1: the first sample holds
    double   period_scale = 1.0;             // this hold's jittered length
    float    held[2]{};
    float    lp[2]{};
    uint32_t rng = 1, seed = 1;

    explicit Engine(const arb_host* host) {
        if (host && host->sample_rate) sr = double(host->sample_rate);
        seed = (host && host->rng_seed) ? host->rng_seed : 0x9e3779b9u;
        rng = seed;
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kBits:  bits = v; break;
        case kRate:  rate_hz = v; break;
        case kDrive: drive_db = v; break;
        case kMix:   mix = v; break;
        case kTone:  tone_hz = v; break;
        default:     jitter = v; break;
        }
    }

    void reset() {
        acc = 1.0;
        period_scale = 1.0;
        held[0] = held[1] = 0.0f;
        lp[0] = lp[1] = 0.0f;
        rng = seed;
    }

    // xorshift32: deterministic from host.rng_seed, so a golden holds.
    float next_uniform() {
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        return float(rng >> 8) * (1.0f / 16777216.0f);        // 0 .. 1
    }

    void process(const arb_host& host, arb_io& io) {
        const double rt = host.sample_rate ? double(host.sample_rate) : sr;

        // BITS is continuous: 8.5 bits is a real grid, half-way between two
        // whole ones, and a macro sweeping it must not step.
        const double levels = std::pow(2.0, double(bits) - 1.0);
        const float  pre = std::pow(10.0f, drive_db / 20.0f);
        const float  post = 1.0f / pre;      // DRIVE is not a level control
        // Clamped to one: at or above the run's own rate the hold is every
        // sample, which is the "off" end of the knob.
        const double step = std::min(1.0, double(rate_hz) / rt);

        const bool use_lp = tone_hz < kToneMaxHz - 0.5f;
        const float a_lp = use_lp
            ? float(1.0 - std::exp(-2.0 * kPi * double(tone_hz) / rt)) : 1.0f;

        const float dry_g = 1.0f - mix;
        const float wet_g = mix;

        const float* il = io.in[0];
        const float* ir = io.in[1] ? io.in[1] : io.in[0];
        if (!il) {                       // §3.7.3: write both channels, always
            std::fill_n(io.out[0], io.frames, 0.0f);
            std::fill_n(io.out[1], io.frames, 0.0f);
            return;
        }

        for (uint32_t i = 0; i < io.frames; ++i) {
            const float x[2] = {il[i], ir[i]};      // in and out may alias

            acc += step / period_scale;
            if (acc >= 1.0) {
                acc -= std::floor(acc);      // JITTER may make a step > 1
                // JITTER lengthens and shortens the hold rather than dropping
                // samples: the pitch of the alias hash wanders instead of the
                // signal gaining clicks. +-60 % at the top, which is a whole
                // octave of wobble and still a recognisable rate.
                period_scale = jitter > 0.0f
                    ? 1.0 + 1.2 * double(jitter) * (double(next_uniform()) - 0.5) : 1.0;
                for (int c = 0; c < 2; ++c) {
                    const float v = std::clamp(x[c] * pre, -1.0f, 1.0f);
                    held[c] = float(std::round(double(v) * levels) / levels) * post;
                }
            }

            for (int c = 0; c < 2; ++c) {
                float w = held[c];
                if (use_lp) { lp[c] += a_lp * (w - lp[c]); w = lp[c]; }
                io.out[c][i] = dry_g * x[c] + wet_g * w;
            }
        }
    }
};

// TONE's top detent is off, and "20000 Hz" would not say so.
int format_tone(float n, char* buf, uint32_t size) {
    const float hz = arb::denorm(desc().params[kTone], n);
    int w;
    if (hz >= kToneMaxHz - 0.5f) w = std::snprintf(buf, size, "OFF");
    else if (hz < 1000.0f)       w = std::snprintf(buf, size, "%.0f Hz", double(hz));
    else                         w = std::snprintf(buf, size, "%.1f kHz", double(hz) / 1000.0);
    return w > 0;
}

void* e_create(const arb_host* host, const char*) {
    return new (std::nothrow) Engine(host);
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
int   e_format_param(void*, uint32_t idx, float n, char* buf, uint32_t size) {
    return (idx == kTone && buf && size) ? format_tone(n, buf, size) : 0;
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: takes no assets (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    e_format_param,          // TONE's OFF detent (§15.1)
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
