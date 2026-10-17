// poly.hpp — arb::PolyHost<Voice, MaxVoices>: the polyphony nobody should have
// to write twice. engine-spec.md §4.
//
// The split, which is the whole point of this file:
//
//   Voice  makes one note's *timbre*. It is handed a note, a velocity and a
//          block, and it writes that block. It never sees an envelope, never
//          counts voices, never decides who gets stolen.
//   PolyHost owns note tracking, stealing, ENV MODE, one ADSR per voice, and
//          the voice's pitch offset: unison detune, glide and pitch_st.
//          It renders each voice into a scratch buffer, multiplies that buffer
//          by *that voice's* envelope, and sums the results into out[2].
//
// The multiply is per voice and after the voice, never over the mix. An
// envelope over the mixed output gates every ringing note whenever a new one
// starts — a volume jump on the fading note and old tails riding along with
// each new one, every step under a sequencer. That bug shipped once already;
// see emulator/CLAUDE.md § Polyphony and the 2026-09-11 session log (private).
//
// Consequences of the split worth knowing before you write a Voice:
//   - render() *writes* its two buffers, it does not accumulate into them.
//   - The envelope is the final gate, so a voice cannot ring past its own
//     release: once the envelope is idle the voice is not rendered at all.
//   - done() answers only about the voice's own tail. A slot is reclaimable
//     when its envelope is idle *and* done() is true.
//   - A voice that follows pitch declares `void set_pitch(float cents)`:
//     PolyHost calls it before every render() with the voice's whole offset
//     from its note -- unison spread + glide + arb_host.pitch_st -- and at
//     note-on just before start(). Read it in render(), not only in start(),
//     or glide and bend will not move a ringing note. Without it a voice plays
//     its note and nothing else.
//   - The older `void set_detune(float cents)` still works: when a voice has
//     it and no set_pitch, PolyHost calls it once at note-on with the unison
//     spread alone. Such a voice gets no glide and no pitch_st.
//   - Every Voice declares `void reset()`: clear the sound's state (phase,
//     filter memory, a tail) and keep what create() handed it, such as the
//     pointer to your parameters. PolyHost::reset() calls it on every slot. It
//     is required because the old fallback, assigning a fresh `V{}`, nulled
//     that pointer and the next render() dereferenced it on the audio thread
//     (#675); a Voice without reset() is now a compile error, not a crash.
//   - In MONO and LEGATO a new note lands on the voice that is already
//     sounding (#646). A voice that declares
//     `void retarget(int note, float vel, const arb_host&, bool retrigger)`
//     gets that call instead of start() when its envelope is not idle: change
//     the note, keep the phase (and filter state), so the change does not
//     click. `retrigger` is false for a legato change: leave any envelope of
//     your own where it is. Without retarget() start() is called, as in poly.
//
// The ADSR is snapshotted at note-on, so turning an ENV encoder does not yank
// notes that are already ringing, and two notes started either side of the turn
// really do release at different rates.
//
// Pitch (DDR-052), which is the other way round -- it moves ringing notes:
//   GLIDE   mono and legato only. A new note starts at the pitch the voice is
//           sounding (a glide in progress included) and slides to its own,
//           linear in semitones, over env_glide seconds taken at that note-on.
//           Any interval takes the same time. It glides whenever the voice is
//           still sounding, release tail included; from silence it does not.
//           Poly and unison ignore it: in poly there is no "previous note" of a
//           voice, and a unison stack sliding as one is not what DETUNE is for.
//   DETUNE  unison only: the kUnisonVoices voices sit evenly across env_detune
//           cents, outermost to outermost. Read every block, so it moves
//           ringing notes (and a route to env.detune is audible on a held chord).
//   pitch_st  every voice, every mode. The host sends one value per block;
//           PolyHost ramps linearly from the last block's value to it across
//           the block, so an LFO on host.pitch does not zipper.
// While a glide or the pitch ramp is moving the block is rendered in runs of
// kPitchStep frames, one set_pitch per run; glide runs sit on a fixed grid of
// the frame count, so a glide is the same whatever the host's block size. When
// nothing moves, a block renders exactly as it did before pitch existed.

#pragma once

#include <algorithm>
#include <cmath>
#include <concepts>
#include <cstddef>
#include <cstdint>

#include <arbutus_engine.h>

namespace arb {

// ---- ENV MODE 3 defaults -------------------------------------------------
// The voice count and gain are fixed; the spread is arb_host.env_detune since
// Stage B2 (DDR-052). kUnisonSpreadCents is the host's default for it: the
// fixed spread unison had before, so a patch without DETUNE sounds the same.
inline constexpr int   kUnisonVoices      = 4;     // stacked per note
inline constexpr float kUnisonSpreadCents = 12.0f; // default env_detune: total, so +/-6 cents

// Frames per set_pitch while a glide or pitch_st is moving: 0.67 ms at 48 kHz,
// a staircase far finer than a 128-frame block. [default chosen here: the
// spec says only that pitch must not zipper]
inline constexpr uint32_t kPitchStep = 32;

// MONO, and a note landing in a release, retarget() a sounding voice at the
// new note's velocity (#828). A voice that applies velocity itself ramps to it
// over this long, linear (arb::Smoothed), rather than stepping its gain: the
// step was 1.6-20x a steady sample-to-sample step. [default chosen here: the
// shortest whole millisecond audio/tests/legato_click.py passes on every synth
// box, MONO 0.2 -> 0.8 held over and into a release]
inline constexpr float kRetargetRampMs = 1.0f;
inline uint32_t retarget_ramp_frames(const arb_host& h) {
    const double sr = h.sample_rate ? double(h.sample_rate) : 48000.0;
    return uint32_t(std::max(1L, std::lround(sr * double(kRetargetRampMs) / 1000.0)));
}

// ---- the control grid inside a Voice (#56) --------------------------------
// The same trick as kPitchStep, for the other direction: a Voice that reads
// its own envelope (or any other running value) *once per render() call* is
// **block-size dependent**, because the host decides how long that call is --
// 128 on the device, whatever a bake or a bench asks for elsewhere
// (engine-spec.md 3.7 rule 2 and 15.8). The value it lands on is then a
// different point of the envelope for every block size, which is audible, not
// roundoff: `arbutus/va` differed by 16 dBFS between 512 and 1.
//
// So a Voice counts its own frames since note-on -- an absolute number,
// because PolyHost splits blocks at event frames, so note-on is on a real
// frame and not on a block boundary -- and recomputes on a fixed grid of it:
//
//     while (done < n) {
//         const uint32_t m = arb::ctrl_run(frame_, n - done);
//         if (arb::ctrl_cell_start(frame_)) recompute();   // NOT every run
//         ... render m frames ...
//         frame_ += m; done += m;
//     }
//
// The `ctrl_cell_start` guard is the half that is easy to miss: a call can end
// mid-cell, so the next call's first run begins mid-cell, and recomputing
// there would put the block size back into the answer. Cache the coefficient
// across calls and only refresh it at a cell's first frame.
//
// 32 frames = 0.67 ms at 48 kHz: finer than the 128-frame block the device
// runs (modulation-spec.md 7's 375 Hz control rate), so nothing gets coarser
// than it was. [default chosen here -- audio/CLAUDE.md]
inline constexpr uint32_t kCtrlStep = 32;

// `n` clipped to the end of the control cell that `frame` falls in.
inline constexpr uint32_t ctrl_run(uint64_t frame, uint32_t n) {
    const uint32_t off = uint32_t(frame % kCtrlStep);
    return n < kCtrlStep - off ? n : kCtrlStep - off;
}

// True on a cell's first frame: the one place a per-cell value is recomputed.
inline constexpr bool ctrl_cell_start(uint64_t frame) { return frame % kCtrlStep == 0; }

// Linear-segment ADSR, same shape as the emulator's (core/synth_engine.py):
// the attack climbs from wherever the envelope sits, so a fast repeat on a
// still-releasing voice does not slam to zero and click.
struct Adsr {
    enum class Stage : uint8_t { Idle, Attack, Decay, Sustain, Release };

    Stage  stage = Stage::Idle;
    float  a = 0.0f, d = 0.0f, s = 1.0f, r = 0.0f;  // snapshot, seconds / level
    float  value = 0.0f;                            // current level
    float  from = 0.0f;                             // level the ramp started at
    double t = 0.0;                                 // seconds into the stage

    bool idle() const { return stage == Stage::Idle; }

    void gate_on(const arb_host& h) {
        a = std::max(0.0f, h.env_a);
        d = std::max(0.0f, h.env_d);
        s = std::clamp(h.env_s, 0.0f, 1.0f);
        r = std::max(0.0f, h.env_r);
        from = value;
        t = 0.0;
        stage = Stage::Attack;
    }

    void gate_off() {
        if (stage == Stage::Idle) return;
        from = value;
        t = 0.0;
        if (r > 0.0f) {
            stage = Stage::Release;
        } else {
            stage = Stage::Idle;
            value = 0.0f;
        }
    }

    void render(float* env, uint32_t n, double dt) {
        for (uint32_t i = 0; i < n; ++i) { step(dt); env[i] = value; }
    }

    // Advance first, then emit: a stage then lasts exactly time*sample_rate
    // samples and decay starts from a real 1.0.
    void step(double dt) {
        switch (stage) {
        case Stage::Idle:    value = 0.0f; return;
        case Stage::Sustain: value = s;    return;
        case Stage::Attack:
            // A zero-length attack emits exactly one sample at full, then
            // decays -- not a jump straight into decay. That is what
            // emulator/core/synth_engine.py does and what tests/golden/
            // adsr_zero.npy froze, and one sample at 1.0 is audible as a click
            // that a half-length attack would not be.
            if (a <= 0.0f) { value = 1.0f; stage = Stage::Decay; t = 0.0; return; }
            t += dt;
            if (t >= a) { value = 1.0f; stage = Stage::Decay; t = 0.0; }
            else        { value = from + float(t / a) * (1.0f - from); }
            return;
        case Stage::Decay:
            t += dt;
            if (d <= 0.0 || t >= d) { value = s; stage = Stage::Sustain; t = 0.0; }
            else                    { value = 1.0f - float(t / d) * (1.0f - s); }
            return;
        case Stage::Release:
            t += dt;
            if (t >= r) { value = 0.0f; stage = Stage::Idle; t = 0.0; }
            else        { value = from * (1.0f - float(t / r)); }
            return;
        }
    }
};

template <class V>
concept VoiceLike = requires(V v, const V cv, const arb_host& h, float* buf, uint32_t n) {
    { v.start(int{}, float{}, h) };
    { v.stop() };
    { v.reset() };                      // keeps what create() set (#675)
    { cv.done() } -> std::convertible_to<bool>;
    { v.render(buf, buf, n, h) };
};

template <VoiceLike V, std::size_t MaxVoices, std::size_t MaxBlock = ARB_MAX_BLOCK>
class PolyHost {
    static_assert(MaxVoices >= 1);
    static_assert(MaxBlock >= 1);

public:
    static constexpr std::size_t max_voices = MaxVoices;
    static constexpr std::size_t max_block  = MaxBlock;

    // Voices are yours between blocks: hand them a pointer to your parameter
    // struct in create(), not per note.
    V&       voice(std::size_t i)       { return slots_[i].voice; }
    const V& voice(std::size_t i) const { return slots_[i].voice; }

    // Inspection, for tests and for engines that draw their own meters.
    float env_value(std::size_t i)  const { return slots_[i].env.value; }
    bool  sounding(std::size_t i)   const { return !slots_[i].env.idle(); }
    int   note_of(std::size_t i)    const { return slots_[i].note; }
    bool  held(std::size_t i)       const { return slots_[i].held; }
    // What set_pitch last received for voice `i`, in cents (0 without set_pitch).
    float pitch_cents(std::size_t i) const { return slots_[i].cents; }
    std::size_t active_count() const {
        std::size_t n = 0;
        for (const Slot& s : slots_) if (!s.env.idle()) ++n;
        return n;
    }

    // ---- per-voice modulation (modulation-spec §8, arb_vtable::set_voice_mod)
    // The default path, and deliberately only storage: PolyHost keeps the depth
    // of each keys.VEL / keys.NOTE route and snapshots depth x source into a
    // voice at note-on, so the voice can read its param at knob + voice_mod()
    // for its own lifetime. Nothing reaches the sound unless a Voice asks for
    // it. Forward the vtable entry for params flagged per_voice only.
    void set_voice_mod(uint32_t idx, uint8_t src, float depth) {
        if (idx >= ARB_MAX_PARAMS || src > ARB_VSRC_NOTE) return;
        mod_depth_[idx][src] = depth;
    }
    float voice_mod_depth(uint32_t idx, uint8_t src) const {
        return (idx < ARB_MAX_PARAMS && src <= ARB_VSRC_NOTE) ? mod_depth_[idx][src] : 0.0f;
    }
    // The offset voice `i` took at its note-on, in normalised units.
    float voice_mod(std::size_t i, uint32_t idx) const {
        return idx < ARB_MAX_PARAMS ? slots_[i].mod[idx] : 0.0f;
    }

    // keys.NOTE as a bipolar source: C4 (60) is 0, 60 semitones either side is
    // +/-1, clamped. [default chosen here: modulation-spec §3.2 calls NOTE
    // bipolar and names no centre or span -- audio/CLAUDE.md]
    static float note_source(int note) {
        return std::clamp(float(note - 60) / 60.0f, -1.0f, 1.0f);
    }

    // Per-voice mix gain. PolyHost sums voices; it does not normalise by the
    // voice count, because how loud eight notes are is the engine's decision.
    void  set_gain(float g) { gain_ = g; }
    float gain() const      { return gain_; }

    void reset() {
        for (Slot& s : slots_) {
            s.env = Adsr{};
            s.note = -1;
            s.held = false;
            s.age = 0;
            s.gain = 1.0f;
            s.unison = 0.0f;
            s.glide_from = 0.0f;
            s.glide_start = 0;
            s.glide_len = 0;
            s.cents = 0.0f;
            for (float& m : s.mod) m = 0.0f;       // route depths are kept, like params
            s.voice.reset();                       // never `= V{}`: that nulls its pointers
        }
        nheld_ = 0;
        next_age_ = 1;
        clock_ = 0;
        pitch_armed_ = false;                      // the next block's pitch_st lands, not ramps
        pitch_from_ = pitch_to_ = pitch_now_ = 0.0f;
    }

    void note_on(int note, float vel, const arb_host& host) {
        last_host_ = &host;
        set_family(host);
        if (!mono_family_) { poly_on(note, vel, host); return; }
        push_held(note, vel);
        mono_retarget(host, /*from_silence=*/nheld_ == 1);
    }

    void note_off(int note) {
        if (mono_family_) {
            const bool was_top = (nheld_ > 0 && held_note_[nheld_ - 1] == note);
            if (!pop_held(note)) return;
            if (nheld_ == 0) {
                for (Slot& s : slots_) if (s.held) { s.held = false; s.voice.stop(); s.env.gate_off(); }
            } else if (was_top && last_host_) {
                mono_retarget(*last_host_, /*from_silence=*/false);
            }
            return;
        }
        for (Slot& s : slots_)
            if (s.held && s.note == note) { s.held = false; s.voice.stop(); s.env.gate_off(); }
    }

    void all_notes_off() {
        nheld_ = 0;
        for (Slot& s : slots_) if (s.held) { s.held = false; s.voice.stop(); s.env.gate_off(); }
    }

    // One block. Events split the block, so a note lands on its own frame and
    // the output does not depend on how the host chopped the stream up.
    void process(const arb_host& host, arb_io& io) {
        last_host_ = &host;
        set_family(host);

        const uint32_t frames = io.frames;
        for (uint32_t i = 0; i < frames; ++i) { io.out[0][i] = 0.0f; io.out[1][i] = 0.0f; }

        // pitch_st ramps from last block's value to this one's. The first block
        // after create/reset takes it as it is, like arb::Smoothed: a patch
        // load does not slide in from 0. A 0-frame block leaves the pitch state
        // alone, so inserting one changes nothing (engine-spec §3.7.8, #674).
        if (frames > 0) {
            if (!pitch_armed_) {
                pitch_from_ = pitch_to_ = host.pitch_st;
                pitch_armed_ = true;
            } else {
                pitch_from_ = pitch_to_;
                pitch_to_ = host.pitch_st;
            }
            pitch_now_ = pitch_from_;
            block_frames_ = frames;
        }

        const arb_event* ev = io.ev_in;
        const uint32_t nev = ev ? io.n_ev_in : 0u;
        uint32_t pos = 0, ei = 0;
        for (;;) {
            while (ei < nev && std::min(ev[ei].frame, frames) <= pos) handle(ev[ei++], host);
            if (pos >= frames) break;
            uint32_t next = (ei < nev) ? std::min(frames, ev[ei].frame) : frames;
            next = std::min(next, pos + uint32_t(MaxBlock));
            render_segment(io, pos, next - pos, host);
            pos = next;
        }
    }

private:
    struct Slot {
        V        voice{};
        Adsr     env{};
        int      note = -1;
        bool     held = false;
        uint64_t age = 0;
        float    gain = 1.0f;
        float    mod[ARB_MAX_PARAMS]{};           // set_voice_mod offsets, fixed at note-on
        float    unison = 0.0f;                   // place in the stack, -0.5..0.5; x env_detune
        float    glide_from = 0.0f;               // semitones from the note when the glide began
        uint64_t glide_start = 0;                 // clock_ at that note-on
        uint32_t glide_len = 0;                   // frames; 0 = not gliding
        float    cents = 0.0f;                    // what set_pitch last received
    };

    // ENV MODE moved between poly and the mono family: the two keep their
    // notes differently (held slots vs the held-note stack), so neither can
    // release what the other started. Release everything and start clean (#674).
    void set_family(const arb_host& host) {
        const bool mono = (host.env_mode != ARB_ENV_POLY);
        if (mono != mono_family_) all_notes_off();
        mono_family_ = mono;
    }

    void handle(const arb_event& e, const arb_host& host) {
        switch (e.type) {
        case ARB_EV_NOTE_ON:
            if (e.b > 0.0f) note_on(int(e.a), e.b, host);
            else            note_off(int(e.a));      // velocity 0 is a note-off
            break;
        case ARB_EV_NOTE_OFF: note_off(int(e.a)); break;
        default: break;                               // clock/transport/softkey: not ours
        }
    }

    // Semitones this slot's glide still has to travel, at clock frame `at`.
    // Evaluated at the start of the kPitchStep cell `at` falls in, so where the
    // host cut its blocks cannot change the value.
    static float glide_st(const Slot& s, uint64_t at) {
        if (s.glide_len == 0) return 0.0f;
        const uint64_t cell = at - at % kPitchStep;
        if (cell < s.glide_start) return s.glide_from;
        const uint64_t e = cell - s.glide_start;
        if (e >= s.glide_len) return 0.0f;
        return s.glide_from * (1.0f - float(double(e) / double(s.glide_len)));
    }

    // The whole offset from the slot's note. With no glide and pitch_st 0 the
    // two additions are of +0.0 and leave the unison spread bit-exact.
    float offset_cents(const Slot& s, const arb_host& host) const {
        return s.unison * host.env_detune + glide_st(s, clock_) * 100.0f + pitch_now_ * 100.0f;
    }

    // Retires finished glides; true while any sounding voice is still gliding.
    bool gliding() {
        bool any = false;
        for (Slot& s : slots_) {
            if (s.glide_len == 0) continue;
            const uint64_t cell = clock_ - clock_ % kPitchStep;
            if (s.env.idle() || (cell >= s.glide_start && cell - s.glide_start >= s.glide_len))
                s.glide_len = 0;
            else
                any = true;
        }
        return any;
    }

    // Splits a segment into kPitchStep runs on the clock grid while pitch
    // moves; otherwise one run, which is the segment exactly as before.
    void render_segment(arb_io& io, uint32_t offset, uint32_t n, const arb_host& host) {
        while (n > 0) {
            uint32_t m = n;
            if (pitch_from_ != pitch_to_ || gliding())
                m = std::min(n, kPitchStep - uint32_t(clock_ % kPitchStep));
            render_run(io, offset, m, host);
            offset += m;
            n -= m;
            clock_ += m;
        }
    }

    void render_run(arb_io& io, uint32_t offset, uint32_t n, const arb_host& host) {
        const uint32_t end = offset + n;
        if (pitch_from_ == pitch_to_ || end >= block_frames_)
            pitch_now_ = pitch_to_;
        else
            pitch_now_ = pitch_from_ + (pitch_to_ - pitch_from_) * (float(end) / float(block_frames_));

        const double dt = (host.sample_rate > 0) ? 1.0 / double(host.sample_rate) : 0.0;
        float* outl = io.out[0] + offset;
        float* outr = io.out[1] + offset;
        for (Slot& s : slots_) {
            if (s.env.idle()) continue;
            if constexpr (requires(V& v) { v.set_pitch(float{}); }) {
                s.cents = offset_cents(s, host);
                s.voice.set_pitch(s.cents);
            }
            s.voice.render(scratch_[0], scratch_[1], n, host);
            s.env.render(env_, n, dt);
            const float g = gain_ * s.gain;
            for (uint32_t i = 0; i < n; ++i) {
                const float e = env_[i] * g;
                outl[i] += scratch_[0][i] * e;
                outr[i] += scratch_[1][i] * e;
            }
        }
    }

    // ---- allocation: free, then quietest-in-release, then oldest ---------
    std::size_t allocate() {
        for (std::size_t i = 0; i < MaxVoices; ++i)
            if (slots_[i].env.idle() && slots_[i].voice.done()) return i;

        std::size_t best = MaxVoices;
        float quietest = 0.0f;
        for (std::size_t i = 0; i < MaxVoices; ++i) {
            if (slots_[i].held) continue;
            if (best == MaxVoices || slots_[i].env.value < quietest) {
                best = i;
                quietest = slots_[i].env.value;
            }
        }
        if (best != MaxVoices) return best;

        std::size_t oldest = 0;
        for (std::size_t i = 1; i < MaxVoices; ++i)
            if (slots_[i].age < slots_[oldest].age) oldest = i;
        return oldest;
    }

    void poly_on(int note, float vel, const arb_host& host) {
        const std::size_t i = allocate();
        start_slot(slots_[i], note, vel, host, /*unison=*/0.0f, /*gain=*/1.0f, /*retrigger=*/true);
    }

    void start_slot(Slot& s, int note, float vel, const arb_host& host, float unison, float gain,
                    bool retrigger, float glide_from = 0.0f, uint32_t glide_len = 0,
                    bool keep_phase = false) {
        s.unison = unison;
        s.glide_from = glide_from;
        s.glide_start = clock_;
        s.glide_len = glide_len;
        if constexpr (requires(V& v) { v.set_pitch(float{}); }) {
            s.cents = offset_cents(s, host);
            s.voice.set_pitch(s.cents);
        } else if constexpr (requires(V& v) { v.set_detune(float{}); }) {
            s.voice.set_detune(unison * host.env_detune);
        }
        for (std::size_t k = 0; k < ARB_MAX_PARAMS; ++k)
            s.mod[k] = mod_depth_[k][ARB_VSRC_VEL] * vel +
                       mod_depth_[k][ARB_VSRC_NOTE] * note_source(note);
        bool retargeted = false;
        if constexpr (requires(V& v) { v.retarget(int{}, float{}, host, bool{}); }) {
            if (keep_phase && !s.env.idle()) {
                s.voice.retarget(note, vel, host, retrigger);
                retargeted = true;
            }
        }
        if (!retargeted) s.voice.start(note, vel, host);
        s.note = note;
        s.held = true;
        s.gain = gain;
        s.age = next_age_++;
        if (retrigger || s.env.idle()) s.env.gate_on(host);
    }

    // mono / legato / unison all play one note at a time off a held-note stack.
    void mono_retarget(const arb_host& host, bool from_silence) {
        if (nheld_ == 0) return;
        const int note = held_note_[nheld_ - 1];
        const float vel = held_vel_[nheld_ - 1];
        const bool legato = (host.env_mode == ARB_ENV_LEGATO) && !from_silence;

        if (host.env_mode == ARB_ENV_UNISON) {
            const int u = int(std::min<std::size_t>(std::size_t(kUnisonVoices), MaxVoices));
            const float g = 1.0f / std::sqrt(float(u));
            for (int i = 0; i < u; ++i) {
                const float spread = (u > 1) ? (float(i) / float(u - 1) - 0.5f) : 0.0f;
                start_slot(slots_[std::size_t(i)], note, vel, host, spread, g, !legato);
            }
            for (std::size_t i = std::size_t(u); i < MaxVoices; ++i)
                if (slots_[i].held) { slots_[i].held = false; slots_[i].voice.stop(); slots_[i].env.gate_off(); }
            return;
        }

        // Glide from wherever voice 0 is sounding now, a glide in flight included.
        Slot& s0 = slots_[0];
        float from = 0.0f;
        uint32_t len = 0;
        if (host.env_glide > 0.0f && s0.note >= 0 && !s0.env.idle()) {
            from = float(s0.note - note) + glide_st(s0, clock_);
            len = uint32_t(std::lround(double(host.env_glide) * double(host.sample_rate)));
            if (from == 0.0f) len = 0;
        }
        // A note change on a voice still sounding keeps its phase (#646): a reset
        // oscillator is a click on every MONO or LEGATO step.
        start_slot(s0, note, vel, host, 0.0f, 1.0f, !legato, from, len, /*keep_phase=*/true);
        for (std::size_t i = 1; i < MaxVoices; ++i)
            if (slots_[i].held) { slots_[i].held = false; slots_[i].voice.stop(); slots_[i].env.gate_off(); }
    }

    static constexpr std::size_t kMaxHeld = 32;

    void push_held(int note, float vel) {
        pop_held(note);                       // re-press moves it to the top
        if (nheld_ == kMaxHeld) {             // drop the oldest; 32 fingers is enough
            for (std::size_t i = 1; i < kMaxHeld; ++i) {
                held_note_[i - 1] = held_note_[i];
                held_vel_[i - 1]  = held_vel_[i];
            }
            --nheld_;
        }
        held_note_[nheld_] = note;
        held_vel_[nheld_]  = vel;
        ++nheld_;
    }

    bool pop_held(int note) {
        for (std::size_t i = 0; i < nheld_; ++i) {
            if (held_note_[i] != note) continue;
            for (std::size_t j = i + 1; j < nheld_; ++j) {
                held_note_[j - 1] = held_note_[j];
                held_vel_[j - 1]  = held_vel_[j];
            }
            --nheld_;
            return true;
        }
        return false;
    }

    Slot     slots_[MaxVoices]{};
    float    scratch_[2][MaxBlock]{};
    float    env_[MaxBlock]{};
    float    gain_ = 1.0f;
    float    mod_depth_[ARB_MAX_PARAMS][2]{};   // [idx][arb_voice_src]
    uint64_t next_age_ = 1;
    uint64_t clock_ = 0;                    // frames rendered since reset: the glide grid
    float    pitch_from_ = 0.0f, pitch_to_ = 0.0f, pitch_now_ = 0.0f;   // pitch_st, semitones
    uint32_t block_frames_ = 0;
    bool     pitch_armed_ = false;

    int         held_note_[kMaxHeld]{};
    float       held_vel_[kMaxHeld]{};
    std::size_t nheld_ = 0;
    bool        mono_family_ = false;
    const arb_host* last_host_ = nullptr;   // note_off() outside process() needs the ENV page
};

}  // namespace arb
