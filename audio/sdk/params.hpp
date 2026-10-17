// params.hpp — the 0..1 <-> units mapping named by arb_param_desc.curve.
//
// Parameters cross the ABI normalised (engine-spec.md §3.2) and the descriptor
// is the only place that says what a 0.42 means. Host, engine and arbutus-run
// all map through this file so a --set in units and an encoder detent land on
// the same number.
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>

#include "arbutus_engine.h"

namespace arb {

inline float clamp01(float n) { return std::clamp(n, 0.0f, 1.0f); }

// Normalised -> units. LOG needs min > 0; a descriptor that says otherwise is
// an author error, and degrading to LIN beats producing a NaN in the audio.
inline float denorm(const arb_param_desc& p, float n) {
    n = clamp01(n);
    double v;
    if (p.curve == ARB_CURVE_LOG && p.min > 0.0f && p.max > 0.0f)
        v = double(p.min) * std::pow(double(p.max) / double(p.min), double(n));
    else
        v = double(p.min) + double(n) * (double(p.max) - double(p.min));
    if (p.type != ARB_PARAM_CONTINUOUS) v = std::round(v);
    return float(v);
}

// Units -> normalised. The inverse of denorm() to float precision.
inline float norm(const arb_param_desc& p, float value) {
    double v = std::clamp(double(value), double(std::min(p.min, p.max)),
                          double(std::max(p.min, p.max)));
    double n;
    if (p.curve == ARB_CURVE_LOG && p.min > 0.0f && p.max > 0.0f)
        n = std::log(v / double(p.min)) / std::log(double(p.max) / double(p.min));
    else if (p.max != p.min)
        n = (v - double(p.min)) / (double(p.max) - double(p.min));
    else
        n = 0.0;
    return clamp01(float(n));
}

inline int denorm_int(const arb_param_desc& p, float n) { return int(denorm(p, n)); }

// ---- realtime rule 7 (§15.7): a parameter change glides, it does not step ----
//
// A linear ramp toward the target, not a one-pole: it arrives exactly, in a
// known number of frames, so "has it settled" is a comparison rather than a
// threshold, and a box at rest renders the same samples it did before it
// smoothed anything -- which is what keeps the goldens frozen.
//
// Length: exactly one host block (host.max_block) unless the engine asks for
// more. [default chosen here: §15.7 sets one block as the minimum and names no
// length. One block is also the only length that suits modulation: a route
// re-targets every block (modulation-spec §7), and a one-block linear ramp
// joins that staircase into straight lines with one block of delay, where a
// longer ramp becomes a lag filter that eats a fast LFO's depth. A box that
// wants a slower glide for big jumps passes `ms` -- audio/CLAUDE.md]
template <class T>
class Smoothed {
public:
    constexpr Smoothed() = default;
    constexpr explicit Smoothed(T v) : cur_(v), target_(v) {}

    // From create(): one host block, or `ms` if that is longer.
    void prepare(const arb_host& h, float ms = 0.0f) {
        const double f = double(h.sample_rate) * double(ms) / 1000.0;
        set_ramp_frames(std::max(h.max_block, uint32_t(std::lround(f))));
    }
    void set_ramp_frames(uint32_t n) { ramp_ = n ? n : 1u; }

    // A new target. Until the first next()/advance() it lands immediately: the
    // values set before the first block are where the sound starts, not a place
    // to glide from, and a patch load must not fade its own knobs in.
    void set(T target) {
        if (!running_) { snap(target); return; }
        if (target == target_) return;             // a macro re-sends every block
        target_ = target;
        left_ = ramp_;
        step_ = (target_ - cur_) / T(ramp_);
    }

    // The deliberate step (§15.7 "a step is a choice"): ENUMs, note-on values.
    void snap(T v) { cur_ = target_ = v; step_ = T{}; left_ = 0; }

    // One frame. At rest it returns the stored value with no arithmetic.
    T next() {
        running_ = true;
        if (left_ == 0) return cur_;
        if (--left_ == 0) cur_ = target_;
        else              cur_ += step_;
        return cur_;
    }

    // n frames at once, for a value used at block rate. Same end point as n
    // calls to next(), up to float rounding on the way.
    T advance(uint32_t n) {
        running_ = true;
        if (n >= left_) { cur_ = target_; left_ = 0; }
        else            { cur_ += step_ * T(n); left_ -= n; }
        return cur_;
    }

    // For the engine's reset(): parameters keep their values (§3.7.5), so an
    // unfinished glide ends where it was going, and the next set before a block
    // lands immediately again.
    void reset() { snap(target_); running_ = false; }

    T    value() const   { return cur_; }
    T    target() const  { return target_; }
    bool settled() const { return left_ == 0; }
    uint32_t ramp_frames() const { return ramp_; }

private:
    T        cur_{}, target_{}, step_{};
    uint32_t ramp_ = ARB_MAX_BLOCK;   // safe until prepare(): never shorter than a block
    uint32_t left_ = 0;
    bool     running_ = false;
};

}  // namespace arb
