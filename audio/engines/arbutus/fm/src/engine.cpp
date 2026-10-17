// arbutus/fm — two-operator FM, re-ranged so it is playable (DDR-044).
//
// The DSP is emulator/engines/fm_synth.py, unchanged; the *descriptor* is the
// deliverable. Elvin on the Python one:
//
//   "initial parameters are completely unlistenable, index and ratio ranges
//    are so large with such small bliss points that finding them is difficult,
//    and the knobs don't have the resolution to vary in the space that sounds
//    okay"
//
// That is DDR-044's stated failure case, so:
//
//   RATIO was continuous 0.5 .. 8.0. Almost all of that is inharmonic mush;
//     the sounds people want live on a handful of simple ratios. It is now an
//     ENUM of eight of them, one per encoder detent, so every position is a
//     usable timbre and none of them can be missed between detents.
//   INDEX was 0.0 .. 10.0, and that number is not the modulation index. The
//     Python adds `mod_index * modulator` to a phase measured in *cycles*, so
//     the textbook index is beta = 2*pi*that -- its 10.0 is beta = 62.8, and
//     everything that sounds like an instrument lives below about 1.0 on that
//     knob. That is precisely the "small bliss points" complaint. This knob is
//     beta, in the units every FM reference uses, over 0 .. 6; the engine
//     divides by 2*pi on the way into the untouched core.
//   The default was ratio 2.0 / index 2.0 -- beta 12.6, eight sidebands wide,
//     a clangy metallic wash. It is now ratio 1 / beta 2.2, which with the ENV
//     page's default is a soft electric-piano-ish tone: the fundamental on top
//     with two or three sidebands of reed under it.
//
// src/fm_dsp.hpp is the untouched core, so tests/golden/fm_voice.npy still
// applies at the raw ratio 2.5 / index 3.0 the Python golden used --
// audio/tests/test_voice_golden.cpp drives the struct directly.

#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>
#include <poly.hpp>

#include "fm_dsp.hpp"

namespace {

constexpr std::size_t kVoices = 8;

// Eight ratios, one per detent. 1 is the reed/EP family, 2 and 3 are the brass
// and clav end, 1.5 and 3.5 are the bell family (a non-integer ratio gives
// inharmonic partials, which is what a bell is), 7 is the metallic top.
constexpr float kRatios[] = {0.5f, 1.0f, 1.5f, 2.0f, 3.0f, 3.5f, 5.0f, 7.0f};
constexpr int kNumRatios = int(sizeof kRatios / sizeof kRatios[0]);
const char* const kRatioLabels[] = {"0.5", "1", "1.5", "2", "3", "3.5", "5", "7"};

struct Params {
    int   ratio_idx = 1;      // 1.0
    float beta = 2.2f;        // the textbook modulation index
    float level = 0.8f;
};

// The core takes the Python's phase deviation in cycles; the knob is beta.
inline double to_cycles(float beta) { return double(beta) / arbfm::kTwoPi; }

struct Voice {
    const Params* p = nullptr;
    arbfm::Voice v;
    arb::Smoothed<float> vel;        // a retrigger ramps to its velocity (#828)
    float  pitch_cents = 0.0f;       // PolyHost: unison + glide + pitch_st
    double base_freq = 440.0;        // the note's own frequency

    void start(int note, float velocity, const arb_host&) {
        v.start(note, velocity, double(kRatios[p->ratio_idx]), to_cycles(p->beta));
        vel.snap(velocity);
        base_freq = v.freq;
        retune();
    }
    // MONO/LEGATO on a sounding voice (#646): new note, both phases kept; a
    // legato change keeps the phrase's velocity; a retrigger glides to the new one.
    void retarget(int note, float velocity, const arb_host& host, bool retrigger) {
        v.retarget(note);
        if (retrigger) { vel.set_ramp_frames(arb::retarget_ramp_frames(host)); vel.set(velocity); }
        base_freq = v.freq;
        retune();
    }
    // Exactly the note's frequency at offset 0, so the goldens do not move.
    void retune() {
        v.freq = pitch_cents != 0.0f ? base_freq * std::pow(2.0, double(pitch_cents) / 1200.0)
                                     : base_freq;
    }
    void stop() {}
    bool done() const { return true; }

    void render(float* l, float* r, uint32_t n, const arb_host& host) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        // Live, not latched at note-on: turning RATIO or INDEX while a note is
        // held has to be audible, or the knob feels broken.
        v.ratio = double(kRatios[p->ratio_idx]);
        v.index = to_cycles(p->beta);
        retune();
        const float g = 0.5f * p->level;
        if (vel.settled()) {
            v.vel = vel.advance(n);              // the core applies it, as the golden did
            v.render(sr, l, n);
            for (uint32_t i = 0; i < n; ++i) { l[i] *= g; r[i] = l[i]; }
        } else {
            v.vel = 1.0f;                        // the ramp is applied here instead
            v.render(sr, l, n);
            for (uint32_t i = 0; i < n; ++i) { l[i] *= g * vel.next(); r[i] = l[i]; }
        }
    }

    void set_pitch(float cents) { pitch_cents = cents; }     // before every render
    void reset() { v.reset(); vel.snap(0.0f); pitch_cents = 0.0f; }
};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/fm");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;
    d.latency_frames = 0;
    d.num_params = 3;
    d.params[0] = arb::param_enum("ratio", "RATIO", kRatioLabels, kNumRatios, 1);
    d.params[1] = arb::param_lin("index", "INDEX", 0.0f, 6.0f, 2.2f, 0.05f, "");   // beta
    d.params[2] = arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 0.8f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    Params params;
    float  norm[3]{};
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
        case 0: params.ratio_idx = std::clamp(int(v), 0, kNumRatios - 1); break;
        case 1: params.beta = v; break;
        default: params.level = v; break;
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
    nullptr,                 // format_param: the host draws every readout
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
