// op1r/op1r-verb — op1r's reverb, unmodified, through the OP-1 adapter (#1230).
//
// The DSP is op1r's `synth_sdk/plugins/reverb/reverb.c` (pcppcp, GPL-3.0-or-later),
// a fixed-point port of the Mutable Instruments Elements reverb (Emilie Gillet,
// MIT), compiled as it is by src/plugin.c. This file only names its knobs: op1r's
// plugin.json carries four default positions and nothing else, so the names and
// what they do come from reverb.c's header comment. Ranges stay 0..1, the knob's
// travel, because the plugin maps them itself (README.md has what each end is).

#include <desc.hpp>

#include "op1r_fx.hpp"

extern "C" const op1_fx_t FX_reverb;

namespace {

// op1r's plugin.json "defaults", in its 0..32767 knob words.
constexpr float q(int word) { return float(word) / 32767.0f; }

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "op1r/op1r-verb");
    arb::set_field(d.version, sizeof d.version, "0.1.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.latency_frames = op1r::kBlock;
    d.num_params = 4;
    // op1r's knob order (blue, green, white, red), so a patch's four words read
    // the same on both instruments.
    d.params[0] = arb::param_lin("time",      "TIME", 0.0f, 1.0f, q(16000), 0.01f, "");
    d.params[1] = arb::param_lin("tone",      "TONE", 0.0f, 1.0f, q(20000), 0.01f, "");
    d.params[2] = arb::param_lin("diffusion", "DIFF", 0.0f, 1.0f, q(16384), 0.01f, "");
    d.params[3] = arb::param_lin("mix",       "MIX",  0.0f, 1.0f, q(12000), 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

using Box = op1r::Box<&FX_reverb, desc>;

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &Box::vtable; }
