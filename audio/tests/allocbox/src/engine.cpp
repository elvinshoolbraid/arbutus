// arbutus-test/allocbox — the negative control for `--rt-guard` (#52).
//
// Every other box in the tree proves the guard says nothing when there is
// nothing to say, which is exactly what a *broken* guard also does. This one
// breaks engine-spec §3.7.1 on purpose: one `new` per `process()` call, kept
// alive in a leaked list so the live-block count really rises and cannot be
// optimised away. `ctest -R rt_guard_catches_allocation` requires the guard to
// refuse it, so the day `live_blocks()` stops counting — on Linux, on macOS, or
// on whatever the CM4 turns into — the gate says so instead of going quietly
// green (#52: before it, every Linux run printed "unavailable on this platform"
// and passed).
//
// It is built into <build>/rtguard/, NOT the box tree, so the host never
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
    arb::set_field(d.id, sizeof d.id, "arbutus-test/allocbox");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT;
    d.num_params = 1;
    d.params[0] = arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 0.0f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

// A leaked singly-linked list. The engine never walks or frees it: the point is
// that the block is still live when the guard takes its second sample.
struct Crumb {
    Crumb* next;
    float pad[8];
};

struct Engine {
    Crumb* head = nullptr;
    float  level = 0.0f;

    void process(arb_io& io) {
        // The violation, on every call including the 0-frame one.
        Crumb* c = new (std::nothrow) Crumb{head, {}};
        if (c) head = c;
        for (int ch = 0; ch < 2; ++ch)
            if (io.out[ch])
                for (uint32_t i = 0; i < io.frames; ++i) io.out[ch][i] = 0.0f;
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void*) {}
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
