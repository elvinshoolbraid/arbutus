// arbutus/noise — white and pink noise with a tracking lowpass.
//
// emulator/engines/noise_engine.py, rewritten on PolyHost. The DSP is
// src/noise_dsp.hpp and is the Python's, including its RNG stream.
//
// Two re-ranges (DDR-044):
//   CUTOFF was linear 100 .. 10000 Hz. Linear puts nine tenths of the knob
//     above 1 kHz, where noise stops changing character; it is LOG now, over
//     100 .. 12000, so the bottom of the range -- where a filtered noise turns
//     into a drum -- gets the detents it deserves.
//   The default was 5000 Hz. 4000 Hz sits where the pitch tracking is most
//     obviously musical across the keyboard.
//
// One fix: the Python's lowpass restarts from zero on every block, so its
// output depends on the block size. This one keeps its state (ctest -R
// ragged_noise). See noise_dsp.hpp.

#include <algorithm>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>
#include <poly.hpp>

#include "noise_dsp.hpp"

namespace {

constexpr std::size_t kVoices = 8;

struct Params {
    int   type = arbnoise::kWhite;
    float cutoff = 4000.0f;
    float level = 0.8f;
};

struct Voice {
    const Params* p = nullptr;
    arbnoise::Voice v;

    float cents = 0.0f;           // PolyHost's pitch offset for this voice (#647)

    void start(int note, float velocity, const arb_host&) {
        v.start(note, velocity, p->type, double(p->cutoff));
    }
    void stop() {}
    bool done() const { return true; }
    void set_pitch(float c) { cents = c; }

    void render(float* l, float* r, uint32_t n, const arb_host& host) {
        const double sr = host.sample_rate ? double(host.sample_rate) : 48000.0;
        v.track(p->type, double(p->cutoff), double(cents));
        v.render(sr, l, n);
        const float g = v.vel * p->level;
        for (uint32_t i = 0; i < n; ++i) { l[i] *= g; r[i] = l[i]; }
    }

    void reset() { v.reset(); cents = 0.0f; }
};

const char* const kTypeLabels[] = {"WHITE", "PINK"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/noise");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;
    d.latency_frames = 0;
    d.num_params = 3;
    d.params[0] = arb::param_enum("type", "TYPE", kTypeLabels, arbnoise::kNumTypes,
                                  arbnoise::kWhite);
    d.params[1] = arb::param_log("cutoff", "CUTOFF", 100.0f, 12000.0f, 4000.0f, 0.0f, "Hz");
    d.params[2] = arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 0.8f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

// One stream per voice, not the Python's single global one. A shared stream
// hands out its draws in the order the voices happen to be rendered, so how
// many samples each voice takes per segment -- that is, where the host chopped
// the block -- would change the noise. Per voice, a stream advances once per
// frame that voice sounds and nothing else, which is what makes `ctest -R
// ragged_noise` possible. Voice 0's stream is seeded with rng_seed itself, so
// it is bit-for-bit the Python's stream and the conformance golden still holds.
inline uint32_t voice_seed(uint32_t seed, std::size_t i) {
    return seed + uint32_t(i) * 2654435761u;      // Knuth's golden-ratio step
}

struct Engine {
    Params params;
    float  norm[3]{};
    arbnoise::NpRandom rng[kVoices];
    uint32_t seed = 0;
    arb::PolyHost<Voice, kVoices> poly;

    explicit Engine(uint32_t rng_seed) : seed(rng_seed) {
        for (std::size_t i = 0; i < kVoices; ++i) {
            rng[i].seed(voice_seed(seed, i));
            poly.voice(i).p = &params;
            poly.voice(i).v.rng = &rng[i];
        }
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        switch (idx) {
        case 0: params.type = std::clamp(int(v), 0, arbnoise::kNumTypes - 1); break;
        case 1: params.cutoff = v; break;
        default: params.level = v; break;
        }
    }

    void reset() {
        poly.reset();
        // §3.7.5: reset() clears state, and an RNG's position is state.
        for (std::size_t i = 0; i < kVoices; ++i) rng[i].seed(voice_seed(seed, i));
    }
};

void* e_create(const arb_host* host, const char*) {
    return new (std::nothrow) Engine(host ? host->rng_seed : 0u);
}
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
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
