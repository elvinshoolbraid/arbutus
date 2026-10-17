// arbutus-test/seqblockbox — the negative control for `tests/ragged_events.sh`
// (#631), as `blockbox` is for ragged.sh (#56).
//
// A sequencer's clock must run on frames, never on process() calls. This one
// runs on calls: it plays a note at frame 0 of every non-empty block, so at
// 512 frames it emits a sixteenth of what it emits at 32. `ctest -R
// ragged_events_catches_block_dependence` requires the script to fail on it.
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
    arb::set_field(d.id, sizeof d.id, "arbutus-test/seqblockbox");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_EVENTS_IN | ARB_CAP_EVENTS_OUT | ARB_CAP_SEQUENCER;
    d.num_params = 0;
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    void process(arb_io& io) {
        for (int ch = 0; ch < 2; ++ch)
            if (io.out[ch])
                for (uint32_t i = 0; i < io.frames; ++i) io.out[ch][i] = 0.0f;
        // One note per call: the whole bug in a line.
        if (!io.frames || !io.ev_out || !io.n_ev_out || *io.n_ev_out >= io.ev_out_cap) return;
        arb_event& e = io.ev_out[(*io.n_ev_out)++];
        e = arb_event{};
        e.type = ARB_EV_NOTE_ON;
        e.a = 60;
        e.b = 1.0f;
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void*) {}
void  e_set_param(void*, uint32_t, float) {}
float e_get_param(void*, uint32_t) { return 0.0f; }
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
