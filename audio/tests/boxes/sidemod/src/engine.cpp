// arbtest/sidemod — a modulator that emits what the host handed it. Not an
// instrument: FIRST is side[0][0], LAST is side[1][frames-1], AX is
// motion[0] (clamped to the output's range), HAS is 1 when side[] is wired. A
// host test sets a DC bus and reads back which block reached the sidechain.

#include <algorithm>
#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>

namespace {

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbtest/sidemod");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_MODULATOR | ARB_CAP_SIDECHAIN;
    d.num_params = 0;
    arb::set_outputs(d, {arb::output("first", "FIRST", true), arb::output("last", "LAST", true),
                         arb::output("ax", "AX", true), arb::output("has", "HAS", false)});
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void*) {}
void  e_set_param(void*, uint32_t, float) {}
float e_get_param(void*, uint32_t) { return 0.0f; }
void  e_process(void*, const arb_host* host, arb_io* io) {
    if (io->side[0] && io->side[1] && io->frames) {
        io->ctl_out[0] = io->side[0][0];
        io->ctl_out[1] = io->side[1][io->frames - 1];
        io->ctl_out[3] = 1.0f;
    }
    io->ctl_out[2] = std::clamp(host->motion[0], -1.0f, 1.0f);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr, nullptr, nullptr, nullptr, {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
