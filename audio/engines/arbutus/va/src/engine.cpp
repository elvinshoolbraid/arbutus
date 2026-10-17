// arbutus/va — the subtractive voice: two band-limited oscillators, a sub, a
// noise source and a four-pole resonant ladder with its own envelope.
//
// This is the box the instrument did not have. `arbutus/osc` is one naive
// waveform and a level -- correct as a rewrite of the Python, and useless as
// *the* analogue synth, because everything that makes a subtractive patch is
// downstream of the oscillator: the filter, its envelope, and a second
// detuned copy. Putting a `biquad` on the FX rack is not the same instrument:
// the rack's filter is one per patch, after the mix, with no per-voice
// envelope on it, which is exactly the bug `audio/CLAUDE.md` § The envelope
// rule is about, one layer up.
//
// Ranges are the author's job (DDR-044) and this box is where that bites
// hardest, because a subtractive voice has more knobs that can be set to
// silence than any other. Every range below is bounded so it cannot be:
//   - CUTOFF stops at 80 Hz, not at 20: below about 80 Hz a four-pole ladder
//     on a saw is a thud and then nothing, and "the bottom of the knob is
//     mute" is the failure DDR-044 names.
//   - RES stops short of self-oscillation (k = 3.6 of a possible 4.0). A
//     self-oscillating ladder is a sine generator, not a filter, and it is
//     loud; the last detent of a knob should not be a different instrument.
//   - ENV-F is bipolar and worth +/-5 octaves, which is the whole useful
//     sweep; beyond that the envelope just parks the filter open or shut.
//   - DRIVE is crossfaded, so 0 is bit-exact bypass and the knob cannot add
//     colour you did not ask for.
//
// The filter envelope is the **ENV page's own ADSR** (`arb::Adsr` over
// `arb_host.env_*`), not four more knobs. DDR-044 allows eight parameters and
// a second ADSR would eat half of them for a shape the player already set;
// tying the two together is what a one-envelope synth (the SH-101, the Bass
// Station) does, and ENV-F is the depth knob that makes it an instrument.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>
#include <poly.hpp>

namespace {

constexpr std::size_t kVoices = 8;
constexpr int kNumParams = 8;
enum Idx { kWave = 0, kCutoff, kRes, kEnvF, kSub, kNoise, kSpread, kDrive };

// Waveforms. PULSE is a 25 % pulse and SQR a 50 % one: two pulse timbres
// without a ninth knob for the width, which would only be live on two of the
// four settings.
enum Wave { kSaw = 0, kPulse, kSquare, kTri, kNumWaves };
const char* const kWaveLabels[] = {"SAW", "PULSE", "SQR", "TRI"};
constexpr double kPulseWidth[kNumWaves] = {0.0, 0.25, 0.5, 0.5};

// Resonance at the top of the knob. 4.0 is where a ladder self-oscillates;
// 3.6 is a hard squelch that still needs an input. [default chosen here]
constexpr float kMaxK = 3.6f;

// How far ENV-F at +/-100 % moves the cutoff.
constexpr float kEnvOctaves = 5.0f;

// Voice trim: the headroom `arbutus/osc` and `arbutus/fm` take, adjusted for
// a source mix that is normalised rather than summed (see render()). One note
// at full velocity peaks around 0.4 and a fistful still has somewhere to go.
constexpr float kTrim = 0.4f;

// ---- band-limited oscillator ---------------------------------------------
// polyBLEP: the discontinuity of a saw or a pulse is replaced by a two-sample
// polynomial that has the step's low frequencies and not its aliases. It is
// the cheapest correction that is audibly right up to the top of the keyboard,
// which is what a CM4 with eight voices and a ladder per voice can afford.
inline double poly_blep(double t, double dt) {
    if (t < dt) { t /= dt; return t + t - t * t - 1.0; }
    if (t > 1.0 - dt) { t = (t - 1.0) / dt; return t * t + t + t + 1.0; }
    return 0.0;
}

struct Blep {
    double phase = 0.0;
    double inc = 0.0;
    double tri = 0.0;               // the leaky integrator behind TRI

    // The triangle is an integrated square, so it starts where an ideal one
    // does (-1 at phase 0) rather than walking up to it over the leak's time
    // constant, which is audible as a thump and measurable as DC.
    void reset() { phase = 0.0; tri = -1.0; }
    void set_freq(double hz, double sr) { inc = std::clamp(hz / sr, 0.0, 0.45); }

    // Always -1..1 and centred: a 25 % pulse has a mean of -0.5, and a filter
    // with resonance on an off-centre input produces a DC term in the output.
    // Removing the mean makes the peak 1.5, so it is scaled back as well --
    // every wave then arrives at the mixer at the same size.
    double next(int wave) {
        const double dt = inc;
        double y;
        if (wave == kSaw) {
            y = 2.0 * phase - 1.0 - poly_blep(phase, dt);
        } else {
            const double w = kPulseWidth[wave];
            y = phase < w ? 1.0 : -1.0;
            y += poly_blep(phase, dt);
            double t = phase + 1.0 - w;
            if (t >= 1.0) t -= 1.0;
            y -= poly_blep(t, dt);
            const double dc = 2.0 * w - 1.0;
            y = (y - dc) / std::max(2.0 - 2.0 * w, 2.0 * w);
            if (wave == kTri) {
                // Integrating the square gives a triangle with no aliasing of
                // its own: the square's blep is integrated with it. 4*inc per
                // sample is exactly +/-1 over a half period; the leak is what
                // keeps a stray DC term from walking away.
                tri = tri * 0.9995 + 4.0 * dt * y;
                y = std::clamp(tri, -1.5, 1.5);
            }
        }
        phase += dt;
        if (phase >= 1.0) phase -= 1.0;
        return y;
    }
};

// ---- zero-delay four-pole ladder -----------------------------------------
// Four one-pole TPT sections with the feedback solved rather than delayed, so
// the resonant peak sits where the knob says at every cutoff instead of
// drifting sharp near Nyquist (which a naive z^-1 in the loop does).
struct Ladder {
    float s[4]{};

    void reset() { for (float& v : s) v = 0.0f; }

    // g = tan(pi*fc/sr) precomputed by the caller; k = 0..4 resonance.
    void run(float* x, uint32_t n, float G, float k) {
        const float a = 1.0f - G;               // (1 - G) in the cascade below
        const float G2 = G * G, G3 = G2 * G, G4 = G3 * G;
        const float denom = 1.0f / (1.0f + k * G4);
        for (uint32_t i = 0; i < n; ++i) {
            const float S = a * (G3 * s[0] + G2 * s[1] + G * s[2] + s[3]);
            const float y4 = (G4 * x[i] + S) * denom;
            float u = x[i] - k * y4;
            for (int j = 0; j < 4; ++j) {
                const float v = G * (u - s[j]);     // TPT one-pole: v, then y, then s
                const float y = v + s[j];
                s[j] = y + v;
                u = y;
            }
            x[i] = u;
        }
    }
};

struct Params {
    int   wave = kSaw;
    float cutoff = 2200.0f;       // Hz
    float res = 0.2f;             // 0..1
    float env_f = 0.35f;          // -1..1
    float sub = 0.25f;
    float noise = 0.0f;
    float spread = 8.0f;          // cents, total
    float drive = 0.15f;
};

struct Voice {
    const Params* p = nullptr;
    Blep a, b, sub;
    Ladder filt;
    arb::Adsr fenv;               // the ENV page's shape, used on the filter
    uint32_t rng = 1;
    double base = 440.0;
    arb::Smoothed<float> vel;     // a retrigger ramps to its velocity (#828)
    float  cents = 0.0f;
    uint64_t frame = 0;           // frames since note-on: the control grid (#56)
    float  G = 0.0f;              // the ladder coefficient, one per control cell

    void start(int note, float velocity, const arb_host& host) {
        base = 440.0 * std::pow(2.0, double(note - 69) / 12.0);
        vel.snap(velocity);
        a.reset();
        b.reset();
        sub.reset();
        filt.reset();
        // Seeded from the note and the host, so a golden repeats and two notes
        // do not share a noise stream (§3.7 determinism).
        rng = 0x9e3779b9u ^ (uint32_t(note) * 2654435761u) ^ host.rng_seed;
        if (rng == 0) rng = 1;
        frame = 0;                // the first render() is a cell start
        fenv.gate_on(host);
    }
    // MONO/LEGATO on a sounding voice (#646): the oscillators, the ladder and
    // the noise run on; MONO restarts the filter envelope from where it is,
    // a legato change leaves it and the phrase's velocity alone; a retrigger
    // glides to the new velocity.
    void retarget(int note, float velocity, const arb_host& host, bool retrigger) {
        base = 440.0 * std::pow(2.0, double(note - 69) / 12.0);
        if (retrigger) {
            vel.set_ramp_frames(arb::retarget_ramp_frames(host));
            vel.set(velocity);
            fenv.gate_on(host);
        }
    }
    void stop() { fenv.gate_off(); }
    bool done() const { return true; }        // the amp envelope is the gate

    void set_pitch(float c) { cents = c; }
    void reset() {
        a.reset(); b.reset(); sub.reset(); filt.reset();
        fenv = arb::Adsr{};
        vel.snap(0.0f);
        cents = 0.0f;
        frame = 0;
        G = 0.0f;
    }

    float white() {
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        return float(int32_t(rng)) * (1.0f / 2147483648.0f);
    }

    void render(float* l, float* r, uint32_t n, const arb_host& host) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const double f = base * std::pow(2.0, double(cents) / 1200.0);
        const double half = double(p->spread) * 0.5;
        a.set_freq(f * std::pow(2.0, -half / 1200.0), sr);
        b.set_freq(f * std::pow(2.0, +half / 1200.0), sr);
        sub.set_freq(f * 0.5, sr);

        const double dt = sr > 0.0 ? 1.0 / sr : 0.0;
        const float k = p->res * kMaxK;
        // Resonance in a ladder takes the bottom out; a little makeup keeps a
        // bass patch a bass patch as RES comes up.
        const float in_gain = 1.0f + 0.3f * k;
        const float dg = 1.0f + 9.0f * p->drive;
        const float dnorm = p->drive > 0.0f ? 1.0f / std::tanh(dg) : 0.0f;
        // SUB and NOISE are a **balance**, not a gain: the mix is divided by
        // what they add, so turning them up changes the timbre and not the
        // level. Three sources summed raw would be 3x hotter at the top of
        // three knobs, which is a volume control nobody asked for.
        const float mix_norm = 1.0f / (1.0f + 0.7f * (p->sub + p->noise));
        const bool ramp = !vel.settled();
        const float g = kTrim * (ramp ? 1.0f : vel.advance(n));

        // The filter envelope is read on the SDK's control grid, not once per
        // call (#56). Once per call is one cutoff per *block*, and the block is
        // the host's to choose -- this box moved by 16 dBFS between 512 frames
        // and 1, which `tests/ragged.sh` exists to catch and could not, because
        // its own exit status was `sed`'s. A per-sample tan() is still not
        // worth it on a CM4; kCtrlStep is 0.67 ms, finer than the 128-frame
        // block the device runs, so the sweep is no coarser than it was.
        for (uint32_t done = 0; done < n; ) {
            const uint32_t m = arb::ctrl_run(frame, n - done);
            if (arb::ctrl_cell_start(frame)) {
                const float sweep = p->env_f * kEnvOctaves * fenv.value;
                const double fc = std::clamp(double(p->cutoff) * std::pow(2.0, double(sweep)),
                                             20.0, sr * 0.45);
                const double g_ = std::tan(3.14159265358979324 * fc / sr);
                G = float(g_ / (1.0 + g_));
            }

            float* lm = l + done;
            for (uint32_t i = 0; i < m; ++i) {
                float x = float(0.5 * (a.next(p->wave) + b.next(p->wave)));
                if (p->sub > 0.0f)   x += p->sub * float(sub.next(kSquare));
                if (p->noise > 0.0f) x += p->noise * white();
                x *= mix_norm;
                if (p->drive > 0.0f) {
                    const float wet = std::tanh(dg * x) * dnorm;
                    x += p->drive * (wet - x);      // 0 = bit-exact bypass
                }
                lm[i] = x * in_gain;
            }
            filt.run(lm, m, G, k);
            if (ramp) for (uint32_t i = 0; i < m; ++i) { lm[i] *= g * vel.next(); r[done + i] = lm[i]; }
            else      for (uint32_t i = 0; i < m; ++i) { lm[i] *= g; r[done + i] = lm[i]; }  // mono (§3.7.3)

            for (uint32_t i = 0; i < m; ++i) fenv.step(dt);
            frame += m;
            done += m;
        }
    }
};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/va");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;                  // PolyHost applies it; so does the filter
    d.latency_frames = 0;
    d.num_params = kNumParams;
    d.params[kWave]   = arb::param_enum("wave", "WAVE", kWaveLabels, kNumWaves, kSaw);
    d.params[kCutoff] = arb::param_log("cutoff", "CUTOFF", 80.0f, 12000.0f, 2200.0f, 0.0f, "Hz");
    d.params[kRes]    = arb::param_lin("res", "RES", 0.0f, 1.0f, 0.2f, 0.01f, "");
    d.params[kEnvF]   = arb::param_lin("env", "ENV-F", -100.0f, 100.0f, 35.0f, 1.0f, "%");
    d.params[kSub]    = arb::param_lin("sub", "SUB", 0.0f, 1.0f, 0.25f, 0.01f, "");
    d.params[kNoise]  = arb::param_lin("noise", "NOISE", 0.0f, 1.0f, 0.0f, 0.01f, "");
    d.params[kSpread] = arb::param_lin("spread", "SPREAD", 0.0f, 30.0f, 8.0f, 0.5f, "ct");
    d.params[kDrive]  = arb::param_lin("drive", "DRIVE", 0.0f, 1.0f, 0.15f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    Params params;
    float  norm[kNumParams]{};
    arb::PolyHost<Voice, kVoices> poly;

    Engine() {
        for (std::size_t i = 0; i < kVoices; ++i) poly.voice(i).p = &params;
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case kWave:   params.wave = std::clamp(int(v), 0, kNumWaves - 1); break;
        case kCutoff: params.cutoff = v; break;
        case kRes:    params.res = v; break;
        case kEnvF:   params.env_f = v / 100.0f; break;
        case kSub:    params.sub = v; break;
        case kNoise:  params.noise = v; break;
        case kSpread: params.spread = v; break;
        default:      params.drive = v; break;
        }
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->poly.reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    return idx < desc().num_params ? static_cast<Engine*>(h)->norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->poly.process(*host, *io);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: takes no assets (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    nullptr,                 // format_param: every slot has a unit the host can draw
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
