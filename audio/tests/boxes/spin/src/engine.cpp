// arbtest/spin — a box that deliberately misses the block budget.
//
// §6.8 says the device raises the CPU flag when xruns pile up, and the only
// honest way to test that is an engine that really is too slow. This one
// passes audio through and then burns LOAD * 100k float operations. It is the
// one box in the tree that is *meant* to break the realtime rules, and it lives
// under tests/ so it can never be mistaken for a stock engine.

#include <algorithm>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbtest/spin");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.num_params = 1;
    d.params[0] = arb::param("load", "LOAD", ARB_PARAM_INTEGER, ARB_CURVE_LIN,
                             0.0f, 2000.0f, 0.0f, 1.0f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    float norm = 0.0f;
    int   load = 0;

    void set_param(uint32_t idx, float n) {
        if (idx) return;
        norm = arb::clamp01(n);
        load = int(arb::denorm(desc().params[0], norm));
    }

    void process(arb_io& io) {
        for (int c = 0; c < 2; ++c) {
            if (io.in[c]) std::copy_n(io.in[c], io.frames, io.out[c]);
            else          std::fill_n(io.out[c], io.frames, 0.0f);
        }
        volatile double acc = 0.0;
        for (int i = 0; i < load; ++i)
            for (int k = 0; k < 100000; ++k) acc = acc + 1.0e-9;
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void*) {}
void  e_set_param(void* h, uint32_t i, float n) { static_cast<Engine*>(h)->set_param(i, n); }
float e_get_param(void* h, uint32_t i) { return i ? 0.0f : static_cast<Engine*>(h)->norm; }
void  e_process(void* h, const arb_host*, arb_io* io) { static_cast<Engine*>(h)->process(*io); }

const arb_vtable kVtable = {e_create, e_destroy, e_reset, e_set_param, e_get_param,
                           e_process, nullptr, nullptr, nullptr, nullptr, {}};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
