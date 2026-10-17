// arbutus/drive — the rack's distortion, and the only box that oversamples.
//
// The MASTER strip already has a DRIVE (`host/mixer.hpp` DriveStage): one
// `tanh`, at the run's own rate, on the mix. That is the right thing there --
// it is a finishing knob and it must cost nothing. This is the other one: the
// distortion you put *on a sound*, where the whole point is to run out of
// headroom, and where the difference between a good one and a bad one is
// almost entirely aliasing.
//
// So the shaper runs at **2x** behind a pair of 15-tap half-band filters. A
// waveshaper multiplies bandwidth: a hard clipper fed 7 kHz makes harmonics at
// 21, 35, 49 kHz, and at 48 kHz every one of those folds back into the band as
// an inharmonic whistle that moves the wrong way when you play a scale.
// Oversampling does not remove them -- nothing does -- but it moves the first
// fold an octave up and lets the decimation filter throw most of it away.
// README § Tests has the measured floor.
//
// MODE is four shapers rather than four boxes because the *curve* is the one
// thing about a distortion that a knob cannot interpolate: SOFT and HARD are
// the two ends of "clipping", FOLD is not clipping at all (it turns round and
// comes back, so more level is a different timbre rather than a louder one),
// and FUZZ is asymmetric on purpose -- it is the even harmonics that make a
// fuzz sound like a fuzz.
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
    kMode, kDrive, kTone, kMix, kBias, kHp, kSag, kOut, kNumParams
};
enum Mode : int { kSoft = 0, kHard, kFold, kFuzz };

const char* const kModeLabels[] = {"SOFT", "HARD", "FOLD", "FUZZ"};

// +30 dB at the top of DRIVE: enough that a −30 dBFS tail is at full scale.
constexpr float kMaxGain = 32.0f;
// The tilt frequency is MASTER's (host/mixer.hpp kDriveTiltHz), so the two
// DRIVE knobs on the instrument mean the same thing by "tone".
constexpr float kTiltHz = 700.0f;
constexpr float kHpOffHz = 20.0f;

// ---- the 2x oversampler -----------------------------------------------------
// A windowed-sinc half-band, the filter `arbutus/wavetable` uses for its mip
// chain with the length raised from 15 to 31 taps. Half-band means every even
// offset from the centre is exactly zero, so the polyphase split is 16 taps
// for one phase and a single centre tap -- a pure delay -- for the other; 31
// taps therefore cost 16 multiplies a sample each way, not 31.
//
// 15 was tried first and measured: it dropped 20 kHz by 3.8 dB and left the
// worst fold-back only 8.5 dB better than no oversampling at all. 31 is the
// length that makes the claim true (README § Tests). Group delay is kHalf
// taps at 2x for each filter, so the box's latency is kHalf frames and it says
// so in the descriptor (§3.2 latency_frames: reported, not compensated).
constexpr int kTaps = 31;
constexpr int kHalf = kTaps / 2;               // 15, and odd, which is what
                                               // makes the even offsets zero
constexpr int kPoly = (kTaps + 1) / 2;         // 16 even-index taps

struct HalfBand {
    float he[kPoly]{};                         // even-index taps, h[0] h[2] ...
    float hc = 0.5f;                           // the centre tap, h[kHalf]

    void design() {
        float h[kTaps], sum = 0.0f;
        for (int i = 0; i < kTaps; ++i) {
            const double x = double(i - kHalf);
            const double sinc = x == 0.0 ? 0.5 : std::sin(kPi * x * 0.5) / (kPi * x);
            const double win = 0.5 - 0.5 * std::cos(2.0 * kPi * double(i) / double(kTaps - 1));
            h[i] = float(sinc * win);
            sum += h[i];
        }
        for (int i = 0; i < kTaps; ++i) h[i] /= sum;
        for (int k = 0; k < kPoly; ++k) he[k] = h[2 * k];
        hc = h[kHalf];
    }
};

// One channel's histories. hx: input, he/ho: the two 2x phases coming back.
struct Over {
    float hx[kPoly]{};
    float se[kPoly]{};
    float so[kPoly]{};

    void clear() {
        for (int i = 0; i < kPoly; ++i) hx[i] = se[i] = so[i] = 0.0f;
    }
    static void push(float* z, float v) {
        for (int i = kPoly - 1; i > 0; --i) z[i] = z[i - 1];
        z[0] = v;
    }
};

// ---- the shapers ------------------------------------------------------------
// f(t) is bounded and has unit slope at the origin; `norm` puts f(g) back at 1
// so the loudest thing in still comes out at the loudest thing out.
inline float shaper(int mode, float t) {
    switch (mode) {
    case kHard: return std::clamp(t, -1.0f, 1.0f);
    // A sine folder: past +-1 it turns round instead of flattening, so more
    // level is a different timbre rather than a louder one.
    case kFold: return std::sin(float(kPi) * 0.5f * t);
    // Asymmetric on purpose: the negative half saturates sooner and softer,
    // which is the even-harmonic content that makes a fuzz sound like one.
    case kFuzz: return t >= 0.0f ? std::tanh(t) : 0.55f * std::tanh(t * 0.6f);
    default:    return std::tanh(t);
    }
}

inline float shaper_norm(int mode, float g) {
    switch (mode) {
    case kHard: return 1.0f;                   // g >= 1, so f(g) is already 1
    case kFold: return 1.0f;                   // bounded by construction
    default:    return 1.0f / std::tanh(g);
    }
}

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/drive");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.latency_frames = kHalf;                // 3.5 frames each way, 7 in all
    d.num_params = kNumParams;
    d.params[kMode]  = arb::param_enum("mode", "MODE", kModeLabels, 4, kSoft);
    d.params[kDrive] = arb::param_lin("drive", "DRIVE", 0.0f, 1.0f, 0.3f, 0.01f, "");
    d.params[kTone]  = arb::param_lin("tone",  "TONE",  0.0f, 1.0f, 0.5f, 0.01f, "");
    d.params[kMix]   = arb::param_lin("mix",   "MIX",   0.0f, 1.0f, 1.0f, 0.01f, "");
    d.params[kBias]  = arb::param_lin("bias",  "BIAS",  0.0f, 1.0f, 0.0f, 0.01f, "");
    d.params[kHp]    = arb::param_log("hp",    "HP",    kHpOffHz, 400.0f, kHpOffHz, 0.0f, "Hz");
    d.params[kSag]   = arb::param_lin("sag",   "SAG",   0.0f, 1.0f, 0.0f, 0.01f, "");
    d.params[kOut]   = arb::param_lin("out",   "OUT",   -24.0f, 12.0f, 0.0f, 0.5f, "dB");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    float  norm[kNumParams]{};
    double sr = 48000.0;
    int    mode = kSoft;
    float  drive = 0.3f, tone = 0.5f, mix = 1.0f, bias = 0.0f;
    float  hp_hz = kHpOffHz, sag = 0.0f, out_db = 0.0f;

    HalfBand fir;
    Over   ov[2];
    float  tilt[2]{};                          // TONE's one-pole low state
    float  hp_x1[2]{}, hp_y1[2]{};             // HP before the shaper
    float  dc_x1[2]{}, dc_y1[2]{};             // DC blocker on the shaped path
    float  env[2]{};                           // SAG's follower
    float  dry[2][kTaps]{};                    // the dry, delayed by the latency
    int    dry_pos = 0;

    explicit Engine(double rate) : sr(rate > 0.0 ? rate : 48000.0) {
        fir.design();
        clear();
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void clear() {
        for (int c = 0; c < 2; ++c) {
            ov[c].clear();
            tilt[c] = hp_x1[c] = hp_y1[c] = dc_x1[c] = dc_y1[c] = env[c] = 0.0f;
            for (int i = 0; i < kTaps; ++i) dry[c][i] = 0.0f;
        }
        dry_pos = 0;
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kMode:  mode = std::clamp(int(v), 0, 3); break;
        case kDrive: drive = v; break;
        case kTone:  tone = v; break;
        case kMix:   mix = v; break;
        case kBias:  bias = v; break;
        case kHp:    hp_hz = v; break;
        case kSag:   sag = v; break;
        default:     out_db = v; break;
        }
    }

    void reset() { clear(); }

    void process(const arb_host& host, arb_io& io) {
        const double rt = host.sample_rate ? double(host.sample_rate) : sr;

        const float g = 1.0f + (kMaxGain - 1.0f) * drive;
        const float nrm = shaper_norm(mode, g);
        // BIAS is an offset in the *shaper's* units, not in the signal's: at
        // DRIVE 1 an input-side offset of 0.7 would be 22 into a tanh, which
        // pins the shaper and makes the top of the knob a mute -- measured, and
        // exactly what DDR-044 forbids.
        const float off = bias * 0.8f;
        // Silence stays silence: the shaper's answer to the offset alone is
        // subtracted. It is taken at g, not at SAG's moving gain -- the DC
        // blocker below mops up the difference and this stays one call.
        const float bias_dc = shaper(mode, off) * nrm;
        // MASTER's tilt, same frequency and the same exactly-reconstructing
        // pair of gains: at TONE 0.5 what reaches the shaper is the input.
        const float a_tilt = float(std::clamp(1.0 - std::exp(-2.0 * kPi * double(kTiltHz) / rt),
                                              1e-5, 1.0));
        const float gl = 2.0f * (1.0f - tone), gh = 2.0f * tone;
        const bool use_hp = hp_hz > kHpOffHz + 0.01f;
        const float a_hp = use_hp ? float(std::exp(-2.0 * kPi * double(hp_hz) / rt)) : 0.0f;
        const float a_dc = float(std::exp(-2.0 * kPi * 12.0 / rt));
        const float atk = float(1.0 - std::exp(-1000.0 / (5.0 * rt)));
        const float rel = float(1.0 - std::exp(-1000.0 / (150.0 * rt)));
        const float out_g = std::pow(10.0f, out_db / 20.0f);
        const float dry_g = 1.0f - mix, wet_g = mix;

        const float* il = io.in[0];
        const float* ir = io.in[1] ? io.in[1] : io.in[0];
        if (!il) {                       // §3.7.3: write both channels, always
            std::fill_n(io.out[0], io.frames, 0.0f);
            std::fill_n(io.out[1], io.frames, 0.0f);
            return;
        }

        for (uint32_t i = 0; i < io.frames; ++i) {
            const float x[2] = {il[i], ir[i]};      // in and out may alias
            for (int c = 0; c < 2; ++c) {
                // --- the dry, delayed by the box's own latency ---------------
                // Everything downstream is 7 frames late, so the crossfade and
                // MIX must see a dry that is late by exactly the same amount or
                // the box comb-filters itself.
                const float xd = dry[c][(dry_pos + kTaps - kHalf) % kTaps];

                // --- HP, then the tilt, then into the shaper ----------------
                float u = x[c];
                if (use_hp) {
                    const float y = a_hp * (hp_y1[c] + u - hp_x1[c]);
                    hp_x1[c] = u;
                    hp_y1[c] = y;
                    u = y;
                }
                tilt[c] += a_tilt * (u - tilt[c]);
                const float v = tilt[c] * gl + (u - tilt[c]) * gh;

                // --- 2x up, shape, 2x down ----------------------------------
                Over& o = ov[c];
                Over::push(o.hx, v);
                float up_e = 0.0f;
                for (int k = 0; k < kPoly; ++k) up_e += fir.he[k] * o.hx[k];
                up_e *= 2.0f;
                const float up_o = 2.0f * fir.hc * o.hx[kHalf / 2];   // the pure-delay phase

                Over::push(o.se, shaper(mode, g * up_e + off) * nrm - bias_dc);
                Over::push(o.so, shaper(mode, g * up_o + off) * nrm - bias_dc);
                float s = 0.0f;
                for (int k = 0; k < kPoly; ++k) s += fir.he[k] * o.se[k];
                s += fir.hc * o.so[kHalf / 2 + 1];

                // SAG: the shaped level dips while the shaper is being driven
                // and takes 150 ms to come back, the way an under-specified
                // supply does. It is an *output* dip: pulling the drive down
                // instead changes nothing audible once the shaper is saturated
                // (measured -- a square wave stays a square wave).
                const float mag = std::fabs(s);
                env[c] += (mag - env[c]) * (mag > env[c] ? atk : rel);
                if (sag > 0.0f) s /= 1.0f + 3.0f * sag * env[c];

                // FUZZ and BIAS are asymmetric, so the shaped path carries DC
                // on signal as well as at rest. 12 Hz, on the wet only.
                const float d = s - dc_x1[c] + a_dc * dc_y1[c];
                dc_x1[c] = s;
                dc_y1[c] = d;
                s = d;

                const float p = (xd + drive * (s - xd)) * out_g;
                io.out[c][i] = dry_g * xd + wet_g * p;
                dry[c][dry_pos] = x[c];
            }
            dry_pos = (dry_pos + 1) % kTaps;
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
