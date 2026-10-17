// mixer.hpp — the host's own DSP between the tape and the output (§15.5):
// per-track EQ (DDR-062) and the MASTER strip's drive (DDR-062's
// LEVEL · COMP · DRIVE · BAL).
//
//   tape 1..8 -> per-track EQ -> mixer -> master FX rack -> drive -> comp
//             -> level -> out
//
// Everything here is a *host* effect, not a box: an EQ per track is the one
// thing a rack cannot do (DDR-062), and the master drive is one knob on the
// last page rather than a box the player has to load. Both are written so that
// their defaults are **bit-exact bypass** — the host must not colour the sound
// (audio/CLAUDE.md), and `ctest -R host_pipeline` proves it.
//
// No allocation anywhere: coefficients are recomputed in place when a knob
// moves, state is five biquads per track per channel, fixed at compile time.
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace arb::host {

// ---- one RBJ biquad, transposed direct form II ------------------------------
struct Biquad {
    float b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0;
    float z1[2]{}, z2[2]{};                    // state per channel

    void reset() { z1[0] = z1[1] = z2[0] = z2[1] = 0.0f; }

    void set(double db0, double db1, double db2, double da0, double da1, double da2) {
        const double inv = da0 == 0.0 ? 1.0 : 1.0 / da0;
        b0 = float(db0 * inv);
        b1 = float(db1 * inv);
        b2 = float(db2 * inv);
        a1 = float(da1 * inv);
        a2 = float(da2 * inv);
    }

    inline float step(int ch, float x) {
        const float y = b0 * x + z1[ch];
        z1[ch] = b1 * x - a1 * y + z2[ch];
        z2[ch] = b2 * x - a2 * y;
        return y;
    }

    void run(int ch, float* x, uint32_t n) {
        for (uint32_t k = 0; k < n; ++k) x[k] = step(ch, x[k]);
    }
};

// ---- the per-track EQ's knobs (DDR-062) ------------------------------------
// LOW / MID / HIGH are dB (the sketch's +/-12), MID-F and the two filters are
// Hz, MID-Q is a Q. The shelf corners are not knobs on the page, so they are
// fixed here. [defaults chosen here: the sketch gives the shape and the +/-12,
// not the frequencies -- audio/CLAUDE.md]
inline constexpr float kEqLowHz = 200.0f;      // LOW shelf corner
inline constexpr float kEqHighHz = 3000.0f;    // HIGH shelf corner
inline constexpr float kEqMaxDb = 12.0f;
inline constexpr float kEqMidFMin = 100.0f, kEqMidFMax = 8000.0f;
inline constexpr float kEqMidQMin = 0.2f, kEqMidQMax = 8.0f;
// HP at its bottom and LP at its top are **off**, not a filter at the edge of
// the band: a flat EQ has to be bit-exact bypass.
inline constexpr float kEqHpOff = 20.0f, kEqHpMax = 2000.0f;
inline constexpr float kEqLpOff = 20000.0f, kEqLpMin = 200.0f;
inline constexpr float kFilterQ = 0.70710678f;  // one biquad, Butterworth

struct EqSettings {
    float low = 0.0f, mid = 0.0f, high = 0.0f;      // dB
    float mid_f = 1000.0f, mid_q = 0.7f;
    float hp = kEqHpOff, lp = kEqLpOff;             // Hz

    void clamp() {
        low = std::clamp(low, -kEqMaxDb, kEqMaxDb);
        mid = std::clamp(mid, -kEqMaxDb, kEqMaxDb);
        high = std::clamp(high, -kEqMaxDb, kEqMaxDb);
        mid_f = std::clamp(mid_f, kEqMidFMin, kEqMidFMax);
        mid_q = std::clamp(mid_q, kEqMidQMin, kEqMidQMax);
        hp = std::clamp(hp, kEqHpOff, kEqHpMax);
        lp = std::clamp(lp, kEqLpMin, kEqLpOff);
    }
    bool flat() const {
        return low == 0.0f && mid == 0.0f && high == 0.0f && hp <= kEqHpOff && lp >= kEqLpOff;
    }
};

// ---- the five filters of one track -----------------------------------------
struct TrackEq {
    Biquad hp, lp, low, mid, high;
    bool use_hp = false, use_lp = false, use_low = false, use_mid = false, use_high = false;

    void reset() { hp.reset(); lp.reset(); low.reset(); mid.reset(); high.reset(); }

    bool active() const { return use_hp || use_lp || use_low || use_mid || use_high; }

    void set(const EqSettings& s, double sr) {
        const double pi = 3.14159265358979323846;
        auto shelf = [&](Biquad& f, double hz, double db, bool high_shelf) {
            const double A = std::pow(10.0, db / 40.0);
            const double w = 2.0 * pi * hz / sr;
            const double cs = std::cos(w), sn = std::sin(w);
            // S = 1: the gentlest slope that has no gain ripple.
            const double alpha = sn * 0.5 * std::sqrt(2.0);
            const double tw = 2.0 * std::sqrt(A) * alpha;
            if (high_shelf)
                f.set(A * ((A + 1) + (A - 1) * cs + tw), -2 * A * ((A - 1) + (A + 1) * cs),
                      A * ((A + 1) + (A - 1) * cs - tw), (A + 1) - (A - 1) * cs + tw,
                      2 * ((A - 1) - (A + 1) * cs), (A + 1) - (A - 1) * cs - tw);
            else
                f.set(A * ((A + 1) - (A - 1) * cs + tw), 2 * A * ((A - 1) - (A + 1) * cs),
                      A * ((A + 1) - (A - 1) * cs - tw), (A + 1) + (A - 1) * cs + tw,
                      -2 * ((A - 1) + (A + 1) * cs), (A + 1) + (A - 1) * cs - tw);
        };
        auto peak = [&](Biquad& f, double hz, double db, double q) {
            const double A = std::pow(10.0, db / 40.0);
            const double w = 2.0 * pi * hz / sr;
            const double cs = std::cos(w), alpha = std::sin(w) / (2.0 * q);
            f.set(1 + alpha * A, -2 * cs, 1 - alpha * A, 1 + alpha / A, -2 * cs, 1 - alpha / A);
        };
        auto pass = [&](Biquad& f, double hz, bool highpass) {
            const double w = 2.0 * pi * hz / sr;
            const double cs = std::cos(w), alpha = std::sin(w) / (2.0 * double(kFilterQ));
            if (highpass)
                f.set((1 + cs) * 0.5, -(1 + cs), (1 + cs) * 0.5, 1 + alpha, -2 * cs, 1 - alpha);
            else
                f.set((1 - cs) * 0.5, 1 - cs, (1 - cs) * 0.5, 1 + alpha, -2 * cs, 1 - alpha);
        };

        EqSettings c = s;
        c.clamp();
        const double ny = sr * 0.5;
        use_low = c.low != 0.0f;
        use_mid = c.mid != 0.0f;
        use_high = c.high != 0.0f;
        use_hp = c.hp > kEqHpOff;
        use_lp = c.lp < kEqLpOff && double(c.lp) < ny;
        if (use_low) shelf(low, std::min(double(kEqLowHz), ny * 0.9), c.low, false);
        if (use_high) shelf(high, std::min(double(kEqHighHz), ny * 0.9), c.high, true);
        if (use_mid) peak(mid, std::min(double(c.mid_f), ny * 0.9), c.mid, c.mid_q);
        if (use_hp) pass(hp, std::min(double(c.hp), ny * 0.9), true);
        if (use_lp) pass(lp, std::min(double(c.lp), ny * 0.9), false);
    }

    // In place, one channel at a time. Order is the signal's: filters, then the
    // three bands, which is what the EQ page draws.
    void process(int ch, float* x, uint32_t n) {
        if (use_hp) hp.run(ch, x, n);
        if (use_lp) lp.run(ch, x, n);
        if (use_low) low.run(ch, x, n);
        if (use_mid) mid.run(ch, x, n);
        if (use_high) high.run(ch, x, n);
    }
};

// ---- MASTER DRIVE / DRIVE TONE ---------------------------------------------
// One knob of saturation with a tone tilt in front of it. The spec names the
// two knobs and nothing else, so: TONE tilts what is driven around 700 Hz
// (0 = only the lows reach the shaper, 1 = only the highs, 0.5 = flat and
// exactly reconstructing), the shaper is tanh normalised to unity peak, and
// DRIVE crossfades dry to shaped so that 0 is bit-exact bypass.
// [defaults chosen here -- audio/CLAUDE.md]
inline constexpr float kDriveTiltHz = 700.0f;
inline constexpr float kDriveMaxGain = 10.0f;   // small-signal boost at DRIVE 1

struct DriveStage {
    float tilt[2]{};                            // one-pole low state per channel

    void reset() { tilt[0] = tilt[1] = 0.0f; }

    // `amount` and `tone` are 0..1. Bypassed (and untouched) at amount 0.
    void process(int ch, float* x, uint32_t n, float amount, float tone, double sr) {
        if (amount <= 0.0f) return;
        const double w = 2.0 * 3.14159265358979323846 * double(kDriveTiltHz) / sr;
        const float a = float(std::clamp(1.0 - std::exp(-w), 1e-5, 1.0));
        const float gl = 2.0f * (1.0f - tone), gh = 2.0f * tone;
        const float g = 1.0f + (kDriveMaxGain - 1.0f) * amount;
        const float norm = 1.0f / std::tanh(g);
        float y = tilt[ch];
        for (uint32_t k = 0; k < n; ++k) {
            const float in = x[k];
            y += a * (in - y);
            const float shaped = std::tanh(g * (y * gl + (in - y) * gh)) * norm;
            x[k] = in + amount * (shaped - in);
        }
        tilt[ch] = y;
    }
};

// ---- MASTER BAL -------------------------------------------------------------
// A balance, not a pan: unity at centre, the far side cos-shaped down, the same
// law as host.pan (modulation-spec §5) so the two agree. -1..+1, 0 = untouched.
inline void bal_gains(float bal, float* gl, float* gr) {
    const float b = std::clamp(bal, -1.0f, 1.0f);
    constexpr float kHalfPi = 1.5707963267948966f;
    *gl = b > 0.0f ? std::cos(b * kHalfPi) : 1.0f;
    *gr = b < 0.0f ? std::cos(-b * kHalfPi) : 1.0f;
}

// ---- mute / solo (DDR-061) --------------------------------------------------
// Solo wins: with any track soloed, the un-soloed ones are silent whatever
// their mute says. Returns the track's gate, 1 or 0.
inline float mix_gate(const bool* mute, const bool* solo, int tracks, int t) {
    bool any_solo = false;
    for (int i = 0; i < tracks; ++i) any_solo = any_solo || solo[i];
    if (any_solo) return solo[t] ? 1.0f : 0.0f;
    return mute[t] ? 0.0f : 1.0f;
}

}  // namespace arb::host
