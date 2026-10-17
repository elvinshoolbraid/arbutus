// arbutus/example — the template box. Copy this folder, rename, change Voice.
//
// A two-oscillator polyphonic synth in about a hundred lines, which is the
// claim engine-spec.md §4 makes for PolyHost: the engine is a Voice struct and
// the ABI shims below. Read sdk/template/README.md first.

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

// Shared between voices, written only between blocks (set_param is not called
// from inside process()), so a plain pointer is all the sharing needed. These
// are the values *this block* renders with; the knobs' targets live in Engine
// as arb::Smoothed and glide into here (realtime rule 7, §15.7).
struct Params {
    int   wave   = 1;       // 0 sine, 1 saw, 2 square, 3 triangle
    float detune = 8.0f;    // cents between the two oscillators
    float cutoff = 4000.0f; // one-pole lowpass, Hz
};

inline float osc(int wave, double phase) {
    switch (wave) {
    case 0:  return float(std::sin(6.283185307179586 * phase));
    case 2:  return phase < 0.5 ? 1.0f : -1.0f;
    case 3:  return float(4.0 * std::abs(phase - 0.5) - 1.0);
    default: return float(2.0 * phase - 1.0);
    }
}

struct Voice {
    const Params* p = nullptr;

    double phase[2]{0.0, 0.0};
    double freq = 440.0;
    float  vel = 0.0f;
    float  lp = 0.0f;
    float  pitch_cents = 0.0f;   // unison + glide + pitch_st, from PolyHost

    // --- the four calls PolyHost makes ---------------------------------
    void start(int note, float velocity, const arb_host&) {
        freq = 440.0 * std::pow(2.0, (note - 69) / 12.0);
        vel = velocity;
        phase[0] = 0.0;      // reset for determinism: goldens must repeat
        phase[1] = 0.5;
    }

    void stop() {}           // nothing to do: the amp envelope is the gate

    // No tail of its own, so the envelope alone decides when this voice is
    // free. A voice with a delay line or a long decay returns false until it
    // has really gone quiet.
    bool done() const { return true; }

    void render(float* l, float* r, uint32_t n, const arb_host& host) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const float cents = p->detune + pitch_cents;
        const double inc0 = freq * std::pow(2.0, (+cents * 0.5) / 1200.0) / sr;
        const double inc1 = freq * std::pow(2.0, (-cents * 0.5) / 1200.0) / sr;
        const float a = float(1.0 - std::exp(-6.283185307179586 * p->cutoff / sr));
        const float g = 0.5f * vel;          // LEVEL is applied per frame, after the mix

        for (uint32_t i = 0; i < n; ++i) {
            const float s = (osc(p->wave, phase[0]) + osc(p->wave, phase[1])) * g;
            lp += a * (s - lp);
            l[i] = lp;
            r[i] = lp;           // mono voice, both channels written (§3.7.3)
            phase[0] += inc0; if (phase[0] >= 1.0) phase[0] -= 1.0;
            phase[1] += inc1; if (phase[1] >= 1.0) phase[1] -= 1.0;
        }
    }

    // --- optional, and PolyHost finds them by name ----------------------
    // Called before every render with the voice's whole pitch offset: unison
    // spread, glide and the host's pitch_st (bend, host.pitch). Declaring it is
    // what makes GLIDE, DETUNE and BEND work on this box.
    void set_pitch(float cents) { pitch_cents = cents; }
    void reset() { phase[0] = phase[1] = 0.0; lp = 0.0f; vel = 0.0f; }
};

const char* const kWaveLabels[] = {"SINE", "SAW", "SQR", "TRI"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/example");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;                       // PolyHost applies the ENV page
    d.latency_frames = 0;
    d.num_params = 4;                        // slots 4-7 empty -> SHIFT is fine-tune
    d.params[0] = arb::param_enum("wave", "WAVE", kWaveLabels, 4, 1);
    d.params[1] = arb::param_lin("detune", "DETUNE", 0.0f, 50.0f, 8.0f, 0.5f, "ct");
    d.params[2] = arb::param_log("tone", "TONE", 100.0f, 12000.0f, 4000.0f, 0.0f, "Hz");
    d.params[3] = arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 0.5f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    Params params;
    float  norm[4]{};
    // Rule 7: a knob glides to its new value over at least one block. Two
    // speeds of the same idea here:
    //   level          is a gain, so it moves per frame -- after the mix, where
    //                  one ramp serves every voice (a gain commutes with the sum)
    //                  -- over the default one block.
    //   detune, cutoff are read by a voice once per block, so they advance a
    //                  block at a time. A one-block ramp read that way is just a
    //                  step one block late, so these ask for 20 ms: several
    //                  blocks at 128 frames, a staircase too fine to hear.
    //                  Its treads are the host's block, so a glide renders a
    //                  little differently at 64 and at 128 frames: rule 8 holds
    //                  for parameters at rest, not for a glide (README § Rule 8;
    //                  read the ramp on arb::kCtrlStep where that matters).
    // WAVE is an ENUM and steps: a waveform halfway between SAW and SQR is not a
    // thing, and "a step is a choice" (§15.7) -- this is the choice.
    static constexpr float kVoiceGlideMs = 20.0f;
    arb::Smoothed<float> detune{8.0f}, cutoff{4000.0f}, level{0.5f};
    arb::PolyHost<Voice, kVoices> poly;

    explicit Engine(const arb_host* host) {
        if (host) {
            level.prepare(*host);
            detune.prepare(*host, kVoiceGlideMs);
            cutoff.prepare(*host, kVoiceGlideMs);
        }
        for (std::size_t i = 0; i < kVoices; ++i) poly.voice(i).p = &params;
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const arb_param_desc& pd = desc().params[idx];
        const float v = arb::denorm(pd, norm[idx]);
        switch (idx) {
        case 0: params.wave = int(v); break;
        case 1: detune.set(v); break;
        case 2: cutoff.set(v); break;
        case 3: level.set(v); break;
        default: break;
        }
    }

    void process(const arb_host& host, arb_io& io) {
        params.detune = detune.advance(io.frames);
        params.cutoff = cutoff.advance(io.frames);
        poly.process(host, io);
        for (uint32_t i = 0; i < io.frames; ++i) {
            const float g = level.next();
            io.out[0][i] *= g;
            io.out[1][i] *= g;
        }
    }

    void reset() {
        poly.reset();
        for (auto* s : {&detune, &cutoff, &level}) s->reset();
    }
};

// ---- ABI shims -------------------------------------------------------------
void* e_create(const arb_host* host, const char*) { return new (std::nothrow) Engine(host); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    Engine* e = static_cast<Engine*>(h);
    return idx < desc().num_params ? e->norm[idx] : 0.0f;
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
