// noise_dsp.hpp — the noise voice, on its own so a test can drive it.
//
// A transcription of emulator/engines/noise_engine.py NoiseVoice: white or
// Kellet pink, a one-pole lowpass whose cutoff tracks the note, and nothing
// else. The amp envelope is PolyHost's.
//
// Two things here look odd and are deliberate.
//
// 1. `NpRandom` reproduces numpy's *legacy* RandomState stream exactly:
//    np.random.seed(s) is MT19937 init_genrand(s), which is also what
//    std::mt19937 does, and np.random.random_sample() is
//    ((a >> 5) * 2^26 + (b >> 6)) / 2^53 from two draws. That is what lets
//    audio/tests/golden/python/noise_voice.npy -- a golden made of *noise* -- be a
//    conformance test at all: same seed, same numbers. The engine seeds from
//    arb_host::rng_seed, which §3.3 put there for exactly this.
//
// 2. `lp_reset_per_render` reproduces a bug. The Python's _apply_lowpass
//    starts from `prev = 0.0` on every call, so its output depends on where
//    the block boundaries fall -- a filter restart every 512 samples. This box
//    keeps the state, which is correct and which the ragged-block test
//    requires; the flag exists so the golden test can ask for the Python's
//    behaviour and prove the rest of the chain matches. Nothing sets it in the
//    shipped engine.
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <random>

namespace arbnoise {

enum Type { kWhite = 0, kPink, kNumTypes };

// numpy.random.RandomState's MT19937 + random_sample, and nothing more.
struct NpRandom {
    std::mt19937 mt{0};

    void seed(uint32_t s) { mt.seed(s); }

    double next_double() {
        const uint32_t a = uint32_t(mt()) >> 5;
        const uint32_t b = uint32_t(mt()) >> 6;
        return (double(a) * 67108864.0 + double(b)) / 9007199254740992.0;
    }
    // np.random.uniform(-1.0, 1.0)
    double uniform_pm1() { return -1.0 + 2.0 * next_double(); }
};

struct Voice {
    NpRandom* rng = nullptr;        // shared: the Python draws one global stream
    int    type = kWhite;
    double cutoff = 5000.0;         // after the per-note scaling
    double s0 = 0.0, s1 = 0.0, s2 = 0.0;   // Kellet pink state
    double lp = 0.0;
    float  vel = 0.0f;
    bool   lp_reset_per_render = false;    // the Python's per-block restart

    void reset() { s0 = s1 = s2 = 0.0; lp = 0.0; vel = 0.0f; }

    int    note = 60;

    void start(int note_in, float velocity, int noise_type, double base_cutoff) {
        note = note_in;
        type = noise_type;
        vel = velocity;
        // "This makes the noise pitched even though it's not harmonic."
        cutoff = base_cutoff * std::pow(2.0, double(note - 60) / 12.0);
        s0 = s1 = s2 = 0.0;
    }

    // #647: CUTOFF and TYPE follow the knob on a note already sounding, and the
    // cutoff follows the voice's pitch offset (bend, glide, host.pitch, the
    // unison spread) as it follows the note. The Python latched both at
    // note-on, so a held drone did not answer the knob -- "a knob that only
    // takes effect on the next note feels broken" (fm's README). With the knob
    // still and no offset this is start()'s value to the bit, so the
    // conformance golden holds.
    void track(int noise_type, double base_cutoff, double cents) {
        if (noise_type != type) {
            type = noise_type;
            s0 = s1 = s2 = 0.0;          // pink starts from rest, as at note-on
        }
        double st = double(note - 60);
        if (cents != 0.0) st += cents / 100.0;
        cutoff = base_cutoff * std::pow(2.0, st / 12.0);
    }

    void render(double sr, float* out, uint32_t n) {
        // The Python rounds the noise to float32 before the filter in both
        // paths: white through .astype(np.float32), pink through
        // np.asarray(out, np.float32) * 0.11.
        if (lp_reset_per_render) lp = 0.0;
        double cn = cutoff / (sr * 0.5);
        cn = std::clamp(cn, 0.01, 0.99);
        const double alpha = cn;
        for (uint32_t i = 0; i < n; ++i) {
            float x;
            if (type == kWhite) {
                x = float(rng->uniform_pm1());
            } else {
                const double w = rng->uniform_pm1();
                s0 = 0.99886 * s0 + w * 0.0555179;
                s1 = 0.99332 * s1 + w * 0.0750759;
                s2 = 0.96900 * s2 + w * 0.1538520;
                x = float(s0 + s1 + s2) * 0.11f;
            }
            lp = lp + alpha * (double(x) - lp);
            out[i] = float(lp);
        }
    }
};

}  // namespace arbnoise
