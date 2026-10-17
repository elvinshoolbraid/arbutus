// arbutus-test/nanbox — the negative control for arbutus-run's NaN checks (#626).
//
// The golden comparison was `peak = std::max(peak, |out - ref|)`, and
// `std::max(peak, NaN)` returns `peak`: an engine whose output was all NaN
// passed every golden and every ragged_* test at -inf dBFS. This box is
// silence, except that with NAN at 1 frame kNanFrame of the left channel is a
// quiet NaN -- one bad sample in a run of good ones, which is the shape an
// unstable filter or a divide by zero really takes. `ctest -R
// arbutus_run_catches_nan` requires arbutus-run to refuse it.
//
// Built into <build>/nanfix/, NOT the box tree, so the host never indexes it
// and the UI never offers it.

#include <cstdint>
#include <limits>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr uint64_t kNanFrame = 1000;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus-test/nanbox");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT;
    d.num_params = 1;
    d.params[0] = arb::param_lin("nan", "NAN", 0.0f, 1.0f, 0.0f, 1.0f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    uint64_t frame = 0;   // absolute, so the NaN lands on the same frame at every block size
    bool     nan = false;

    void process(arb_io& io) {
        for (int ch = 0; ch < 2; ++ch)
            if (io.out[ch])
                for (uint32_t i = 0; i < io.frames; ++i) io.out[ch][i] = 0.0f;
        if (nan && io.out[0] && frame <= kNanFrame && kNanFrame < frame + io.frames)
            io.out[0][kNanFrame - frame] = std::numeric_limits<float>::quiet_NaN();
        frame += io.frames;
    }
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->frame = 0; }
void  e_set_param(void* h, uint32_t idx, float n) {
    if (idx == 0) static_cast<Engine*>(h)->nan = n >= 0.5f;
}
float e_get_param(void* h, uint32_t idx) {
    return idx == 0 && static_cast<Engine*>(h)->nan ? 1.0f : 0.0f;
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
