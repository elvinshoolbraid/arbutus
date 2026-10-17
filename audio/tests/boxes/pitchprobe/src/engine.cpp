// arbtest/pitchprobe — a PolyHost synth whose voice renders what set_pitch
// last gave it, in semitones, as DC. With a flat envelope and one note the bus
// *is* the voice's pitch offset: glide, bend and host.pitch read back exactly,
// with no frequency estimation in the test. Not an instrument.

#include <cstdint>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>
#include <poly.hpp>

namespace {

constexpr std::size_t kVoices = 8;

struct Voice {
    float cents = 0.0f;
    void start(int, float, const arb_host&) {}
    void stop() {}
    bool done() const { return true; }
    void render(float* l, float* r, uint32_t n, const arb_host&) {
        const float st = cents / 100.0f;
        for (uint32_t i = 0; i < n; ++i) { l[i] = st; r[i] = st; }
    }
    void set_pitch(float c) { cents = c; }
    void reset() { cents = 0.0f; }
};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbtest/pitchprobe");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;
    d.num_params = 0;
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    arb::PolyHost<Voice, kVoices> poly;
};

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->poly.reset(); }
void  e_set_param(void*, uint32_t, float) {}
float e_get_param(void*, uint32_t) { return 0.0f; }
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->poly.process(*host, *io);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr, nullptr, nullptr, nullptr, {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
