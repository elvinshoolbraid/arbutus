// arbtest/constmod — a modulator that emits its knobs. Not an instrument: it
// is how host_mod.py puts an exact m into modulation-spec §6.2's formula
// instead of reverse-engineering an LFO's phase. BI is bipolar (-1..1), UNI
// unipolar (0..1); both hold for as long as the knob does.

#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbtest/constmod");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_MODULATOR;
    d.num_params = 2;
    d.params[0] = arb::param_lin("bi", "BI", -1.0f, 1.0f, 0.0f, 0.01f, "");
    d.params[1] = arb::param_lin("uni", "UNI", 0.0f, 1.0f, 0.0f, 0.01f, "");
    arb::set_outputs(d, {arb::output("bi", "BI", true), arb::output("uni", "UNI", false)});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    float norm[2]{0.5f, 0.0f};
    float out[2]{};
    void set_param(uint32_t idx, float n) {
        if (idx >= 2) return;
        norm[idx] = arb::clamp01(n);
        out[idx] = arb::denorm(desc().params[idx], norm[idx]);
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void*) {}
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) { return idx < 2 ? static_cast<Engine*>(h)->norm[idx] : 0.0f; }
void  e_process(void* h, const arb_host*, arb_io* io) {
    const Engine* e = static_cast<Engine*>(h);
    io->ctl_out[0] = e->out[0];
    io->ctl_out[1] = e->out[1];
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr, nullptr, nullptr, nullptr, {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
