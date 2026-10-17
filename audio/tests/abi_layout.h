/* abi_layout.h — every size and offset of the ABI 2 header, once.
 *
 * abi_c.c expands this list as C99 and test_abi.cpp as C++20; the test fails
 * if the two compilers disagree or if either disagrees with the third column,
 * which is the layout arbutus_engine.h promises on LP64 (both targets: the Mac
 * and the CM4 are 64-bit). A layout change is an ABI change, so changing a
 * number here is a decision, not a fix.
 *
 * X(label, expression, expected on LP64)
 */
#ifndef ARB_ABI_LAYOUT_H
#define ARB_ABI_LAYOUT_H

#include <stddef.h>

#include <arbutus_engine.h>

#define ARB_LAYOUT(X)                                                          \
    X("sizeof arb_event",               sizeof(arb_event),                  16) \
    X("sizeof arb_param_desc",          sizeof(arb_param_desc),             80) \
    X("sizeof arb_softkey_desc",        sizeof(arb_softkey_desc),           24) \
    X("sizeof arb_output_desc",         sizeof(arb_output_desc),            32) \
    X("sizeof arb_engine_desc",         sizeof(arb_engine_desc),          1080) \
    X("sizeof arb_host",                sizeof(arb_host),                  136) \
    X("sizeof arb_io",                  sizeof(arb_io),                    128) \
    X("sizeof arb_vtable",              sizeof(arb_vtable),                144) \
    X("param.type",                     offsetof(arb_param_desc, type),     24) \
    X("param.min",                      offsetof(arb_param_desc, min),      32) \
    X("param.unit",                     offsetof(arb_param_desc, unit),     48) \
    X("param.labels",                   offsetof(arb_param_desc, labels),   56) \
    X("param.per_voice",                offsetof(arb_param_desc, per_voice), 64) \
    X("param.per_note",                 offsetof(arb_param_desc, per_note), 65) \
    X("param._reserved",                offsetof(arb_param_desc, _reserved), 66) \
    X("output.bipolar",                 offsetof(arb_output_desc, bipolar), 24) \
    X("desc.caps",                      offsetof(arb_engine_desc, caps),     4) \
    X("desc.id",                        offsetof(arb_engine_desc, id),       8) \
    X("desc.version",                   offsetof(arb_engine_desc, version), 72) \
    X("desc.num_params",                offsetof(arb_engine_desc, num_params), 88) \
    X("desc.num_softkeys",              offsetof(arb_engine_desc, num_softkeys), 92) \
    X("desc.params",                    offsetof(arb_engine_desc, params),  96) \
    X("desc.softkeys",                  offsetof(arb_engine_desc, softkeys), 736) \
    X("desc.latency_frames",            offsetof(arb_engine_desc, latency_frames), 832) \
    X("desc.num_outputs",               offsetof(arb_engine_desc, num_outputs), 836) \
    X("desc.outputs",                   offsetof(arb_engine_desc, outputs), 840) \
    X("desc.asset_keys",                offsetof(arb_engine_desc, asset_keys), 968) \
    X("desc.num_assets",                offsetof(arb_engine_desc, num_assets), 1016) \
    X("desc.honours_env",               offsetof(arb_engine_desc, honours_env), 1017) \
    X("desc._reserved",                 offsetof(arb_engine_desc, _reserved), 1018) \
    X("host.max_block",                 offsetof(arb_host, max_block),       4) \
    X("host.tempo_bpm",                 offsetof(arb_host, tempo_bpm),       8) \
    X("host.song_frame",                offsetof(arb_host, song_frame),     16) \
    X("host.ppqn",                      offsetof(arb_host, ppqn),           24) \
    X("host.rng_seed",                  offsetof(arb_host, rng_seed),       28) \
    X("host.env_a",                     offsetof(arb_host, env_a),          32) \
    X("host.env_r",                     offsetof(arb_host, env_r),          44) \
    X("host.env_glide",                 offsetof(arb_host, env_glide),      48) \
    X("host.env_detune",                offsetof(arb_host, env_detune),     52) \
    X("host.bend_range",                offsetof(arb_host, bend_range),     56) \
    X("host.pitch_st",                  offsetof(arb_host, pitch_st),       60) \
    X("host.motion",                    offsetof(arb_host, motion),         64) \
    X("host.playing",                   offsetof(arb_host, playing),        88) \
    X("host.env_mode",                  offsetof(arb_host, env_mode),       89) \
    X("host._reserved",                 offsetof(arb_host, _reserved),      90) \
    X("io.side",                        offsetof(arb_io, side),             16) \
    X("io.out",                         offsetof(arb_io, out),              32) \
    X("io.ev_in",                       offsetof(arb_io, ev_in),            48) \
    X("io.ev_out",                      offsetof(arb_io, ev_out),           56) \
    X("io.n_ev_out",                    offsetof(arb_io, n_ev_out),         64) \
    X("io.frames",                      offsetof(arb_io, frames),           72) \
    X("io.n_ev_in",                     offsetof(arb_io, n_ev_in),          76) \
    X("io.ev_out_cap",                  offsetof(arb_io, ev_out_cap),       80) \
    X("io.ctl_out",                     offsetof(arb_io, ctl_out),          84) \
    X("io._reserved",                   offsetof(arb_io, _reserved),       100) \
    X("vtable.process",                 offsetof(arb_vtable, process),      40) \
    X("vtable.load_asset",              offsetof(arb_vtable, load_asset),   48) \
    X("vtable.set_note_param",          offsetof(arb_vtable, set_note_param), 56) \
    X("vtable.format_param",            offsetof(arb_vtable, format_param), 64) \
    X("vtable.set_voice_mod",           offsetof(arb_vtable, set_voice_mod), 72) \
    X("vtable._reserved",               offsetof(arb_vtable, _reserved),    80)

#endif /* ARB_ABI_LAYOUT_H */
