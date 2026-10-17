// osc_dsp.hpp — the oscillator core, on its own so a test can drive it.
//
// A transcription of emulator/core/synth_engine.py Oscillator.generate, kept
// expression-for-expression: the block is a *phase ramp* from the carried
// phase, each sample's phase wrapped before it is shaped, and the carried
// phase wrapped once at the end. Accumulating `phase += inc` instead would
// drift by ~1e-13 over a block, which is inaudible everywhere except at a
// square wave's edge, where one sample flips by 2.0.
//
// This header is the reason the goldens work: audio/tests/test_voice_golden.cpp
// runs it against audio/tests/golden/python/osc_*.npy directly, at the raw
// parameters that test used, while the box above exposes the tuned ones
// (DDR-044).
#pragma once

#include <cmath>
#include <cstdint>

namespace arbosc {

// Declaration order of emulator/core/synth_engine.py Waveform, which is also
// the order BasicOscillatorSynth maps its Wave parameter onto. Keeping it means
// a patch written against the emulator picks the same shape.
enum Wave { kSine = 0, kSquare, kSaw, kTriangle, kNumWaves };

inline constexpr double kTwoPi = 6.283185307179586;

inline float shape(int wave, double ph) {
    switch (wave) {
    case kSine:   return float(std::sin(kTwoPi * ph));
    case kSquare: return ph < 0.5 ? 1.0f : -1.0f;
    case kSaw:    return float(2.0 * ph - 1.0);
    default:      return float(ph < 0.5 ? 4.0 * ph - 1.0 : 3.0 - 4.0 * ph);
    }
}

struct Osc {
    double phase = 0.0;

    void reset() { phase = 0.0; }

    // Writes n samples at `freq`; does not accumulate into `out`.
    void generate(int wave, double freq, double sr, float* out, uint32_t n) {
        const double inc = (sr > 0.0) ? freq / sr : 0.0;
        for (uint32_t i = 0; i < n; ++i) {
            double ph = std::fmod(phase + inc * double(i), 1.0);
            if (ph < 0.0) ph += 1.0;
            out[i] = shape(wave, ph);
        }
        phase = std::fmod(phase + inc * double(n), 1.0);
        if (phase < 0.0) phase += 1.0;
    }
};

inline double note_hz(int note) { return 440.0 * std::pow(2.0, (note - 69) / 12.0); }

}  // namespace arbosc
