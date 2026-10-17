// arbtest/abi2 — the ABI 2 surface, exercised end to end through the host.
//
// Not an instrument. It declares a per_voice param, a per_note param and a
// param with its own readout, implements the three entries those need, and
// turns one host promise into sound: if ctl_out is not zero when process() is
// entered, the box adds a full-scale DC offset from then on, so a host test
// only has to look at the peak. The voice is a constant LEVEL, so whatever
// else comes out is the envelope.
//
// LEVEL is also the per-voice consumer (modulation-spec §8): each voice plays
// LEVEL + its own voice_mod, so a keys.VEL route shows up per note in the sum.
// With no route voice_mod is 0 and the output is what it always was.

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>
#include <poly.hpp>

namespace {

constexpr std::size_t kVoices = 4;

struct Engine;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbtest/abi2");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.honours_env = 1;
    d.num_params = 3;
    d.params[0] = arb::per_voice(arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 0.5f, 0.01f, ""));
    // The bipolar RATE knob of modulation-spec §3.3: the shape format_param is for.
    d.params[1] = arb::param_lin("rate", "RATE", -1.0f, 1.0f, 0.0f, 0.01f, "");
    d.params[2] = arb::per_note(arb::param_lin("tune", "TUNE", -12.0f, 12.0f, 0.0f, 0.1f, "st"));
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Voice {
    const float* level = nullptr;   // normalised knob; LEVEL's range is 0..1, so also units
    const Engine* eng = nullptr;    // for PolyHost::voice_mod; Voice cannot name PolyHost<Voice>
    std::size_t  index = 0;
    void start(int, float, const arb_host&) {}
    void stop() {}
    bool done() const { return true; }
    void render(float* l, float* r, uint32_t n, const arb_host&);
    // Required (#675): no sound state, and the three pointers create() set
    // stay. PolyHost's old `= V{}` fallback nulled them on every host reset.
    void reset() {}
};

const char* const kDivisions[] = {"8 BAR", "4 BAR", "2 BAR", "1 BAR", "1/2",
                                  "1/4",   "1/8",   "1/16",  "1/32",  "1/32T"};
constexpr int kNumDivisions = 10;

struct Engine {
    float norm[3]{};
    float level = 0.5f;
    float note_tune[128]{};
    bool  stale_ctl = false;
    arb::PolyHost<Voice, kVoices> poly;

    Engine() {
        for (std::size_t i = 0; i < kVoices; ++i) {
            poly.voice(i).level = &level;
            poly.voice(i).eng = this;
            poly.voice(i).index = i;
        }
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params || desc().params[idx].per_note) return;   // §15.3
        norm[idx] = arb::clamp01(n);
        if (idx == 0) level = arb::denorm(desc().params[0], norm[0]);
    }

    void process(const arb_host& host, arb_io& io) {
        for (float v : io.ctl_out) if (v != 0.0f) stale_ctl = true;
        poly.process(host, io);
        if (stale_ctl)
            for (int c = 0; c < 2; ++c)
                for (uint32_t i = 0; i < io.frames; ++i) io.out[c][i] += 1.0f;
        // Garbage for the next block: the host must clear it before then.
        for (float& v : io.ctl_out) v = 0.5f;
    }
};

void Voice::render(float* l, float* r, uint32_t n, const arb_host&) {
    // The knob plus this voice's snapshot of depth x velocity (PolyHost, §8).
    const float v = arb::clamp01(*level + eng->poly.voice_mod(index, 0));
    for (uint32_t i = 0; i < n; ++i) { l[i] = v; r[i] = v; }
}

// Centre is STOP, right is tempo divisions, left is free Hz (0.05..50, log).
int format_rate(float n, char* buf, uint32_t size) {
    const float x = arb::denorm(desc().params[1], n);
    if (std::fabs(x) < 0.02f) return std::snprintf(buf, size, "STOP") > 0;
    if (x > 0.0f) {
        const int k = int(std::lround((x - 0.02f) / 0.98f * float(kNumDivisions - 1)));
        return std::snprintf(buf, size, "%s", kDivisions[k < 0 ? 0 : k]) > 0;
    }
    const double hz = 0.05 * std::pow(1000.0, double((-x - 0.02f) / 0.98f));
    return std::snprintf(buf, size, "%.1f Hz", hz) > 0;
}

void* e_create(const arb_host*, const char*) { return new (std::nothrow) Engine(); }
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->poly.reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    return idx < desc().num_params ? static_cast<Engine*>(h)->norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->process(*host, *io);
}
void  e_set_note_param(void* h, uint8_t note, uint32_t idx, float n) {
    if (idx == 2 && note < 128)
        static_cast<Engine*>(h)->note_tune[note] = arb::denorm(desc().params[2], n);
}
int   e_format_param(void*, uint32_t idx, float n, char* buf, uint32_t size) {
    return (idx == 1 && buf && size) ? format_rate(n, buf, size) : 0;   // the rest: host default
}
void  e_set_voice_mod(void* h, uint32_t idx, uint8_t src, float depth) {
    if (idx < desc().num_params && desc().params[idx].per_voice)
        static_cast<Engine*>(h)->poly.set_voice_mod(idx, src, depth);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset
    e_set_note_param, e_format_param, e_set_voice_mod,
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
