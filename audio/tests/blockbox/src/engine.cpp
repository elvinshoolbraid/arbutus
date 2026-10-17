// arbutus-test/blockbox — the negative control for `tests/ragged.sh` (#56).
//
// The 24 ragged_* tests all printed "all block sizes agree" and passed for as
// long as they had existed, because the script's condition was a pipeline
// ending in `sed` and a pipeline's status is its *last* command's. Two stock
// boxes were 16 and 60 dBFS out the whole time. Twenty-four green tests that
// could not go red look exactly like twenty-four green tests that can, which is
// why this file exists: `ctest -R ragged_catches_block_dependence` requires the
// script to **fail** on a box that really is block-size dependent, so the next
// time the harness loses its grip the gate says so.
//
// The violation is the smallest one that is still the real bug: a ramp whose
// step is read once per `process()` call rather than once per frame, which is
// what "read the envelope once per block" was in `arbutus/va` and
// `arbutus/wavetable`. At 512 frames it emits a different signal than at 1.
//
// It is built into <build>/raggedfix/, NOT the box tree, so the host never
// indexes it and the UI never offers it.

#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus-test/blockbox");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT;
    d.num_params = 1;
    d.params[0] = arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 1.0f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    double phase = 0.0;
    float  level = 1.0f;

    void process(arb_io& io) {
        // One value per call, held across the block: the whole bug in a line.
        const float v = float(phase) * level;
        phase += 1.0 / 4096.0;
        if (phase >= 1.0) phase -= 1.0;
        for (int ch = 0; ch < 2; ++ch)
            if (io.out[ch])
                for (uint32_t i = 0; i < io.frames; ++i) io.out[ch][i] = v;
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->phase = 0.0; }
void  e_set_param(void* h, uint32_t idx, float n) {
    if (idx == 0) static_cast<Engine*>(h)->level = arb::clamp01(n);
}
float e_get_param(void* h, uint32_t idx) {
    return idx == 0 ? static_cast<Engine*>(h)->level : 0.0f;
}
void  e_process(void* h, const arb_host*, arb_io* io) {
    static_cast<Engine*>(h)->process(*io);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr, nullptr, nullptr, nullptr, {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
