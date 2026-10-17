// arbutus/live-input — the world, in the ENGINE slot (DDR-063, §15.2).
//
// Loading this box is how input reaches the MOD and FX racks, the tape tap and
// the mixer. There is no capture mode anywhere in the instrument because this
// box makes one unnecessary: record it like any other engine.
//
// It has no `audio_in`. The host hands the selected input to the ENGINE slot on
// **side[]** (`PROTOCOL.md` § `input_source`) exactly as it hands a sidechain to
// an FX tab, so this box is a source with a port, not a special case in the
// graph. With no input at all the host passes side[] as NULL and this is
// silence — an engine that invents a signal when the microphone is missing is
// worse than one that says nothing.
//
// SOURCE is an ENUM and is **mirrored by the host**: turning it here sends
// `input_source`, and `input_source` writes it back, so the knob on SYNTH ›
// ENGINE and the cell on INPUT are one value. The box never routes anything
// itself; it could not — the input stream is the host's device.
//
// GAIN is in dB, not a 0..8 multiplier, because this is a trim on a live
// microphone and a player thinks in dB there. +24 is enough to lift a quiet
// dynamic mic into the tape; −24 is enough to stop a line input clipping.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <new>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

enum : uint32_t { kSource = 0, kGain = 1 };

// Must outlive the descriptor (desc.hpp): file scope, not a local.
const char* const kSourceLabels[] = {"MIC", "LINE", "USB", "RSMP"};

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/live-input");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    // events_in is declared and ignored (§15.2): the keys must not be swallowed
    // by the instrument holding the input, and a box that refuses events would
    // be the odd one out in the ENGINE slot.
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN | ARB_CAP_SIDECHAIN;
    d.honours_env = 0;             // the input is not a note; ENV must not gate it
    d.latency_frames = 0;
    d.num_params = 2;
    d.params[kSource] = arb::param_enum("source", "SOURCE", kSourceLabels, 4, 0);
    d.params[kGain] = arb::param_lin("gain", "GAIN", -24.0f, 24.0f, 0.0f, 0.5f, "dB");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

struct Engine {
    float norm[2]{};
    arb::Smoothed<float> gain{1.0f};      // linear; rule 7 (§15.7)

    Engine() {
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    void prepare(const arb_host& h) { gain.prepare(h); }

    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        if (idx == kGain) gain.set(std::pow(10.0f, arb::denorm(desc().params[kGain], norm[kGain]) / 20.0f));
        // SOURCE is the host's routing, not this box's: nothing to do here but
        // remember the knob so `state` and a patch carry it.
    }

    // §3.7.5: parameters keep their values through a reset. Smoothed::reset()
    // ends an unfinished ramp *at its target*, which is the difference between
    // a panic mid-turn leaving GAIN where it was going and leaving it where the
    // ramp had got to.
    void reset() { gain.reset(); }

    void process(const arb_host&, arb_io& io) {
        const float* sl = io.side[0];
        const float* sr = io.side[1];
        for (uint32_t i = 0; i < io.frames; ++i) {
            const float g = gain.next();
            const float l = sl ? sl[i] * g : 0.0f;
            io.out[0][i] = l;                                   // §3.7.3: both, always
            io.out[1][i] = sl ? (sr ? sr[i] : sl[i]) * g : 0.0f;
        }
    }

    // Pure in (idx, normalised), as the ABI requires of format_param.
    static int format(uint32_t idx, float n, char* buf, uint32_t cap) {
        if (!buf || cap == 0) return 0;
        if (idx == kGain) {
            std::snprintf(buf, cap, "%+.1f dB", double(arb::denorm(desc().params[kGain], n)));
            return 1;
        }
        return 0;                       // SOURCE draws from the descriptor's labels
    }
};

void* e_create(const arb_host* h, const char*) {
    Engine* e = new (std::nothrow) Engine();
    if (e && h) e->prepare(*h);
    return e;
}
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    return idx < desc().num_params ? static_cast<Engine*>(h)->norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->process(*host, *io);
}
int   e_format_param(void*, uint32_t idx, float n, char* buf, uint32_t cap) {
    return Engine::format(idx, n, buf, cap);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: the input is not content (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    e_format_param,          // GAIN in dB
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
