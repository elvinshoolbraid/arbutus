// arbutus/chorus — modulated short delays: chorus, flanger and ensemble on
// one knob each.
//
// Chorus and flanger are the same circuit twice. Both are a delay line of a few
// milliseconds whose length an LFO moves; the only thing that separates them is
// how long "a few" is, and whether the output is fed back in. So DELAY is a
// knob here rather than a mode switch: at 0.5 ms this box is a flanger, at
// 8 ms it is a chorus, at 30 ms it is the slow doubling everyone calls
// "ensemble", and the settings in between are real places rather than the gap
// between two presets (DDR-044).
//
// Stereo is made by *phase*, not by detuning the two sides: the right channel's
// LFO runs SPREAD degrees ahead of the left, so at 180 deg the two sides sweep
// in opposite directions and the image opens without either channel drifting
// out of tune with the other. VOICES stacks 1-3 taps per channel at different
// points of the same LFO cycle, which is what makes three of them sound like a
// section instead of one loud voice.
//
// FDBK is bipolar because the two signs are two different effects: positive
// resonates on the delay's harmonic series, negative notches on it, and the
// hollow one is the flanger everybody actually means.
//
// Ranges are the author's job (DDR-044); README.md argues every one of them.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>
#include <vector>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr double kPi = 3.14159265358979324;

enum Param : uint32_t {
    kRate, kDepth, kDelay, kMix, kFeedback, kSpread, kVoices, kTone, kNumParams
};

constexpr int    kMaxVoices = 3;
// The voices are spaced 0.37 of an LFO cycle apart rather than evenly around
// it. Evenly spaced taps are a *set* that SPREAD can rotate onto itself -- two
// voices at 180 deg, or three at 120 deg, give the two channels the identical
// tap set and the stereo image collapses to mono at exactly the settings that
// should be widest. 0.37 has no such period at one, two or three voices.
constexpr double kVoiceOffset = 0.37;
constexpr double kDelayMinMs = 0.5, kDelayMaxMs = 30.0;
// The sweep reaches +/-90 % of the base delay, so the line has to hold the
// longest base at the top of its swing with room for interpolation.
constexpr double kSweep = 0.9;
constexpr double kLineMs = kDelayMaxMs * (1.0 + kSweep) + 2.0;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/chorus");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.latency_frames = 0;
    d.num_params = kNumParams;
    d.params[kRate]     = arb::param_log("rate",   "RATE",  0.02f, 8.0f, 0.55f, 0.0f, "Hz");
    d.params[kDepth]    = arb::param_lin("depth",  "DEPTH", 0.0f, 1.0f, 0.45f, 0.01f, "");
    d.params[kDelay]    = arb::param_log("delay",  "DELAY", float(kDelayMinMs),
                                         float(kDelayMaxMs), 8.0f, 0.0f, "ms");
    d.params[kMix]      = arb::param_lin("mix",    "MIX",   0.0f, 1.0f, 0.4f, 0.01f, "");
    d.params[kFeedback] = arb::param_lin("feedback", "FDBK", -0.9f, 0.9f, 0.0f, 0.01f, "");
    d.params[kSpread]   = arb::param_lin("spread", "SPREAD", 0.0f, 180.0f, 120.0f, 5.0f, "deg");
    d.params[kVoices]   = arb::param("voices", "VOICES", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                                     1.0f, float(kMaxVoices), 2.0f, 1.0f, "");
    d.params[kTone]     = arb::param_lin("tone",   "TONE",  0.0f, 1.0f, 0.5f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Line {
    std::vector<float> buf;
    int   wpos = 0;
    float lp = 0.0f;                        // TONE, on the wet sum only
    float hp_x1 = 0.0f, hp_y1 = 0.0f;

    void clear() {
        std::fill(buf.begin(), buf.end(), 0.0f);
        wpos = 0;
        lp = hp_x1 = hp_y1 = 0.0f;
    }

    float read(double delay) const {
        const int cap = int(buf.size());
        double rp = double(wpos) - delay;
        while (rp < 0.0) rp += double(cap);
        const int i0 = int(rp) % cap;
        const int i1 = (i0 + 1) % cap;
        const float f = float(rp - std::floor(rp));
        return buf[std::size_t(i0)] * (1.0f - f) + buf[std::size_t(i1)] * f;
    }

    void write(float v) {
        buf[std::size_t(wpos)] = v;
        wpos = (wpos + 1) % int(buf.size());
    }
};

struct Engine {
    float  norm[kNumParams]{};
    double sr = 48000.0;
    float  rate = 0.55f, depth = 0.45f, delay_ms = 8.0f, mix = 0.4f;
    float  fb = 0.0f, spread = 120.0f, tone = 0.5f;
    int    voices = 2;
    Line   ch[2];
    double phase = 0.0;
    float  fb_state[2]{};                   // last wet tap, fed back in

    explicit Engine(double rate_hz) : sr(rate_hz > 0.0 ? rate_hz : 48000.0) {
        const std::size_t cap = std::size_t(sr * kLineMs / 1000.0) + 8;
        for (Line& l : ch) {
            l.buf.assign(cap, 0.0f);        // create() owns every allocation
            l.clear();
        }
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kRate:     rate = v; break;
        case kDepth:    depth = v; break;
        case kDelay:    delay_ms = v; break;
        case kMix:      mix = v; break;
        case kFeedback: fb = v; break;
        case kSpread:   spread = v; break;
        case kVoices:   voices = std::clamp(int(v), 1, kMaxVoices); break;
        default:        tone = v; break;
        }
    }

    void reset() {
        for (Line& l : ch) l.clear();
        phase = 0.0;
        fb_state[0] = fb_state[1] = 0.0f;
    }

    void process(const arb_host& host, arb_io& io) {
        const double rt = host.sample_rate ? double(host.sample_rate) : sr;
        const double cap = double(ch[0].buf.size());
        const double base = std::clamp(double(delay_ms) * rt / 1000.0, 1.0, cap - 4.0);
        const double inc = double(rate) / rt;
        const double ch_off = double(spread) / 360.0;
        // The taps are decorrelated, so they sum as powers, not as amplitudes:
        // 1/sqrt(VOICES) keeps the wet level where it was as they stack.
        const float  vscale = 1.0f / float(std::sqrt(double(voices)));

        // TONE: one knob, two one-poles on the wet sum, both bypassed at the
        // centre so 0.5 is bit-exact.
        const bool use_lp = tone < 0.5f, use_hp = tone > 0.5f;
        float a_lp = 1.0f, a_hp = 0.0f;
        if (use_lp) {
            const double hz = 16000.0 * std::pow(500.0 / 16000.0, double(1.0f - tone * 2.0f));
            a_lp = float(1.0 - std::exp(-2.0 * kPi * hz / rt));
        }
        if (use_hp) {
            const double hz = 20.0 * std::pow(1200.0 / 20.0, double((tone - 0.5f) * 2.0f));
            a_hp = float(std::exp(-2.0 * kPi * hz / rt));
        }

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
            phase += inc;
            if (phase >= 1.0) phase -= 1.0;

            const float x[2] = {il[i], ir[i]};       // in and out may alias
            for (int c = 0; c < 2; ++c) {
                Line& l = ch[c];
                l.write(x[c] + fb * fb_state[c]);

                float wet = 0.0f;
                float first = 0.0f;
                for (int v = 0; v < voices; ++v) {
                    const double p = phase + double(v) * kVoiceOffset + (c ? ch_off : 0.0);
                    const double lfo = std::sin(2.0 * kPi * (p - std::floor(p)));
                    const double d = std::clamp(base * (1.0 + kSweep * double(depth) * lfo),
                                                1.0, cap - 4.0);
                    const float r = l.read(d);
                    if (v == 0) first = r;
                    wet += r;
                }
                wet *= vscale;
                // The feedback tap is the first voice alone: feeding the mean
                // back makes VOICES change the resonance as well as the width,
                // and then one knob moves two things nobody asked it to.
                fb_state[c] = first;

                if (use_lp) { l.lp += a_lp * (wet - l.lp); wet = l.lp; }
                if (use_hp) {
                    const float y = a_hp * (l.hp_y1 + wet - l.hp_x1);
                    l.hp_x1 = wet;
                    l.hp_y1 = y;
                    wet = y;
                }
                io.out[c][i] = dry_g * x[c] + wet_g * wet;
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
