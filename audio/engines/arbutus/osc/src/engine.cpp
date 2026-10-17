// arbutus/osc — the basic oscillator synth, on PolyHost.
//
// emulator/engines/basic_oscillator.py, rewritten. That engine exposes exactly
// one timbre parameter -- Wave -- because the amp shape belongs to the ENV page
// and putting A/S/R on the engine as well meant two controls writing one
// envelope (emulator/CLAUDE.md § Polyphony). Nothing is invented here to fill
// the other slots: slots 2-7 stay empty and become fine-tune of 0-1
// (parameter-framework.md), which is the honest answer.
//
// The oscillator itself is src/osc_dsp.hpp, shared with
// audio/tests/test_voice_golden.cpp, which replays it against
// audio/tests/golden/python/osc_*.npy.

#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>
#include <poly.hpp>

#include "osc_dsp.hpp"

namespace {

constexpr std::size_t kVoices = 8;

struct Params {
    int   wave  = arbosc::kSine;
    float level = 0.7f;
};

struct Voice {
    const Params* p = nullptr;
    arbosc::Osc osc;
    double freq = 440.0;
    arb::Smoothed<float> vel;           // a retrigger ramps to its velocity (#828)
    float  pitch_cents = 0.0f;          // PolyHost: unison + glide + pitch_st

    void start(int note, float velocity, const arb_host&) {
        freq = arbosc::note_hz(note);
        vel.snap(velocity);
        osc.reset();                    // determinism: a golden must repeat
    }
    // MONO/LEGATO on a sounding voice (#646): new note, same phase. A legato
    // change keeps the phrase's velocity; a retrigger glides to the new one.
    void retarget(int note, float velocity, const arb_host& host, bool retrigger) {
        freq = arbosc::note_hz(note);
        if (retrigger) { vel.set_ramp_frames(arb::retarget_ramp_frames(host)); vel.set(velocity); }
    }
    void stop() {}                      // the per-voice envelope is the gate
    bool done() const { return true; }  // no tail of its own

    void render(float* l, float* r, uint32_t n, const arb_host& host) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        const double f = freq * std::pow(2.0, double(pitch_cents) / 1200.0);
        osc.generate(p->wave, f, sr, l, n);
        // 0.5 of headroom, as the template does: one note at LEVEL 0.7 peaks
        // at 0.35 and a fistful of them still has somewhere to go.
        if (vel.settled()) {
            const float g = 0.5f * vel.advance(n) * p->level;
            for (uint32_t i = 0; i < n; ++i) { l[i] *= g; r[i] = l[i]; }   // mono voice (§3.7.3)
        } else {
            for (uint32_t i = 0; i < n; ++i) { l[i] *= 0.5f * vel.next() * p->level; r[i] = l[i]; }
        }
    }

    void set_pitch(float cents) { pitch_cents = cents; }     // before every render
    void reset() { osc.reset(); vel.snap(0.0f); pitch_cents = 0.0f; }
};

const char* const kWaveLabels[] = {"SINE", "SQR", "SAW", "TRI"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/osc");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;                  // PolyHost applies the ENV page
    d.latency_frames = 0;
    d.num_params = 2;
    d.params[0] = arb::param_enum("wave", "WAVE", kWaveLabels, arbosc::kNumWaves,
                                  arbosc::kSine);
    d.params[1] = arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 0.7f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    Params params;
    float  norm[2]{};
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
        if (idx == 0) params.wave = int(v); else params.level = v;
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
