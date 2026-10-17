// The OP-1 adapter: an unmodified op1r effect, run as an Arbutus box (#1230).
//
// op1r (github.com/pcppcp/op1r-src) is the community firmware mod that adds
// engines to the Teenage Engineering OP-1. Its effects are C files that fill an
// `op1_fx_t`: init(pos) and render(pos, int16 buf[64], params), mono, Q15,
// 44.1 kHz, state per FX position 0..2 (synth, drum, master). op1r compiles them
// for the PC with its own harness (`harness/op1_host.h`, -DOP1_HOST), which turns
// every firmware address into a variable. This file is the other half of that
// harness: it fills those variables and speaks arbutus_engine.h on the outside.
//
// How the OP-1's shape maps onto a box, and why:
//   * 64-frame blocks: the box buffers 64 frames and renders them whole, so the
//     output is the same at any host block size (rule 8) and is 64 frames late;
//     latency_frames says so.
//   * Mono: the effect runs on (L+R)/2 and its change, y - m, is added to both
//     channels -- what op1r's dispatcher does at the OP-1's master position. MIX
//     at dry is then the input, bit for bit (64 frames late).
//   * Q15: float -> int16 rounds to nearest and clips at the boundary, as the
//     OP-1's own converters would.
//   * Three FX positions: op1r keeps its state in `static` arrays indexed by
//     position, shared by every instance in the .so. An instance takes a free
//     position at create(); a fourth instance is refused (create() returns NULL,
//     the host's refusal), not run on top of another's state.
//   * The FX scratch memory the firmware lends each position (176,460 bytes) is
//     allocated at create(), outside process() (§3.7.1).
//   * Knobs: Arbutus 0..1 -> op1r 0..32767. Param i is params word i for the four
//     knobs, word i+1 after them (word 4 is op1r's engine selector), so an effect
//     with page-2 words (clouds' 5..7) maps without a table.
//   * 44.1 kHz: op1r's constants are compile-time and the box runs at the host's
//     rate (48 kHz on the device). Nothing here rescales them; each box's README
//     says what moves.
//
// A box using it: a C file that includes the plugin source, and an engine.cpp
// that names the plugin's op1_fx_t and describes its knobs
// (audio/engines/op1r/README.md).

#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <new>

#include <arbutus_engine.h>

extern "C" {
#include "op1_plugin.h"
}

namespace op1r {

constexpr uint32_t kBlock = OP1_FX_BLOCK;                 // 64
constexpr int kPositions = 3;
constexpr uint32_t kScratchWords = OP1_FX_SCRATCH_BYTES / 4;

// Which positions are taken. create() and destroy() run on the control thread
// only (arbutus_engine.h's conventions), so a plain array is enough.
inline bool g_used[kPositions];

inline int16_t to_q15(float x) {
    const float s = std::floor(x * 32768.0f + 0.5f);
    return int16_t(std::clamp(s, -32768.0f, 32767.0f));
}

inline int16_t knob_q15(float v) {
    return int16_t(std::lround(std::clamp(v, 0.0f, 1.0f) * 32767.0f));
}

// Params word for Arbutus param i (see the header comment).
inline uint32_t word_of(uint32_t i) { return i < 4 ? i : i + 1; }

struct Fx {
    const op1_fx_t* fx;
    const arb_engine_desc* d;
    int pos = -1;
    int32_t* scratch = nullptr;
    float norm[ARB_MAX_PARAMS] = {};
    int16_t p[8] = {};
    // The block being filled (mono, Q15) and the one rendered before it, with
    // the dry input it came from: out[k] = dry[k] + (y[k] - m[k]).
    int16_t buf[kBlock] = {};
    int16_t y[kBlock] = {};
    int16_t m[kBlock] = {};
    float dry_l[kBlock] = {}, dry_r[kBlock] = {};
    uint32_t fill = 0;

    void set_param(uint32_t idx, float v) {
        if (idx >= d->num_params) return;
        norm[idx] = std::clamp(v, 0.0f, 1.0f);
        p[word_of(idx)] = knob_q15(norm[idx]);
    }

    void reset() {
        std::memset(buf, 0, sizeof buf);
        std::memset(y, 0, sizeof y);
        std::memset(m, 0, sizeof m);
        std::memset(dry_l, 0, sizeof dry_l);
        std::memset(dry_r, 0, sizeof dry_r);
        fill = 0;
        host_fx_scratch[pos] = scratch;
        fx->init(pos);                     // the plugin clears its own state
    }

    void process(arb_io& io) {
        const float* inl = io.in[0];
        const float* inr = io.in[1] ? io.in[1] : io.in[0];
        for (uint32_t i = 0; i < io.frames; ++i) {
            const float xl = inl ? inl[i] : 0.0f, xr = inr ? inr[i] : 0.0f;
            const uint32_t k = fill;
            const float wet = float(int32_t(y[k]) - int32_t(m[k])) * (1.0f / 32768.0f);
            // out may alias in: both inputs are read above, before these writes.
            io.out[0][i] = dry_l[k] + wet;
            io.out[1][i] = dry_r[k] + wet;
            dry_l[k] = xl;
            dry_r[k] = xr;
            m[k] = buf[k] = to_q15(0.5f * (xl + xr));
            if (++fill == kBlock) {
                fx->render(pos, buf, p);
                std::memcpy(y, buf, sizeof y);
                fill = 0;
            }
        }
    }
};

inline void* create(const op1_fx_t* fx, const arb_engine_desc* d) {
    int pos = 0;
    while (pos < kPositions && g_used[pos]) ++pos;
    if (pos == kPositions) return nullptr;        // all three OP-1 FX positions in use
    Fx* e = new (std::nothrow) Fx{fx, d};
    if (!e) return nullptr;
    e->scratch = new (std::nothrow) int32_t[kScratchWords]();
    if (!e->scratch) { delete e; return nullptr; }
    e->pos = pos;
    g_used[pos] = true;
    for (uint32_t i = 0; i < d->num_params; ++i) {
        const arb_param_desc& pd = d->params[i];
        e->set_param(i, pd.max > pd.min ? (pd.def - pd.min) / (pd.max - pd.min) : 0.0f);
    }
    e->reset();
    return e;
}

inline void destroy(void* h) {
    Fx* e = static_cast<Fx*>(h);
    g_used[e->pos] = false;
    host_fx_scratch[e->pos] = nullptr;
    delete[] e->scratch;
    delete e;
}

// The vtable for one plugin: `fx` and `desc` are the box's own (engine.cpp).
template <const op1_fx_t* fx, const arb_engine_desc& (*desc)()>
struct Box {
    static void* e_create(const arb_host*, const char*) { return create(fx, &desc()); }
    static void e_reset(void* h) { static_cast<Fx*>(h)->reset(); }
    static void e_set_param(void* h, uint32_t i, float v) { static_cast<Fx*>(h)->set_param(i, v); }
    static float e_get_param(void* h, uint32_t i) {
        return i < desc().num_params ? static_cast<Fx*>(h)->norm[i] : 0.0f;
    }
    static void e_process(void* h, const arb_host*, arb_io* io) { static_cast<Fx*>(h)->process(*io); }

    static constexpr arb_vtable vtable = {
        e_create, destroy, e_reset, e_set_param, e_get_param, e_process,
        nullptr,             // load_asset: takes no assets (§3.2)
        nullptr,             // set_note_param: no per_note params (§15.3)
        nullptr,             // format_param: the host draws every readout
        nullptr,             // set_voice_mod: no per_voice params
        {}
    };
};

}  // namespace op1r
