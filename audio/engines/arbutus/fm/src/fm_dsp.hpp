// fm_dsp.hpp — the two-operator FM voice, on its own so a test can drive it.
//
// A transcription of emulator/engines/fm_synth.py FMVoice.process. Both phases
// advance linearly, so the block is one expression in the Python and one loop
// here; sin() is periodic in the phase, so only the carried phase is wrapped.
//
// The core takes a *raw* ratio and index. The box above exposes a bounded,
// musical subset of them (DDR-044) and the golden test drives this struct
// directly at the Python's ratio 2.5 / index 3.0, which the box's ENUM cannot
// reach. That split is the whole reason this header exists.
#pragma once

#include <cmath>
#include <cstdint>

namespace arbfm {

inline constexpr double kTwoPi = 6.283185307179586;

struct Voice {
    double carrier_phase = 0.0, mod_phase = 0.0;
    double freq = 440.0;
    double ratio = 2.0, index = 2.0;
    float  vel = 0.0f;

    void reset() { carrier_phase = mod_phase = 0.0; vel = 0.0f; }

    void start(int note, float velocity, double mod_ratio, double mod_index) {
        freq = 440.0 * std::pow(2.0, (note - 69) / 12.0);
        ratio = mod_ratio;
        index = mod_index;
        vel = velocity;
        carrier_phase = mod_phase = 0.0;
    }

    // A new note without resetting either phase (MONO/LEGATO, #646).
    void retarget(int note) { freq = 440.0 * std::pow(2.0, (note - 69) / 12.0); }

    // Writes n samples, velocity applied, no envelope: the amp envelope is
    // PolyHost's and is applied after the voice, never inside it.
    void render(double sr, float* out, uint32_t n) {
        const double cinc = (sr > 0.0) ? freq / sr : 0.0;
        const double minc = cinc * ratio;
        for (uint32_t i = 0; i < n; ++i) {
            const double k = double(i);
            const double m = std::sin(kTwoPi * (mod_phase + minc * k));
            out[i] = float(std::sin(kTwoPi * (carrier_phase + cinc * k + index * m)) * double(vel));
        }
        carrier_phase = std::fmod(carrier_phase + cinc * double(n), 1.0);
        mod_phase = std::fmod(mod_phase + minc * double(n), 1.0);
    }
};

}  // namespace arbfm
