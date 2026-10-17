// desc.hpp — filling in arb_engine_desc without a page of memcpy.
//
// The ABI is C and fixed-size char arrays; these builders keep an engine's
// describe() readable so the parameter list stays the thing you look at.
#pragma once

#include <cstddef>
#include <cstring>
#include <initializer_list>

#include <arbutus_engine.h>

namespace arb {

// Truncating copy; a name that does not fit is an author error the screen
// would show anyway, so it is not worth an error path in the audio process.
inline void set_field(char* dst, std::size_t n, const char* s) {
    std::memset(dst, 0, n);
    if (!s) return;
    const std::size_t len = std::strlen(s);
    std::memcpy(dst, s, len < n ? len : n);
}

inline arb_param_desc param(const char* id, const char* name, arb_param_type type,
                            arb_curve curve, float min, float max, float def,
                            float step = 0.0f, const char* unit = "",
                            const char* const* labels = nullptr) {
    arb_param_desc p{};
    set_field(p.id, sizeof p.id, id);
    set_field(p.name, sizeof p.name, name);
    p.type = type;
    p.curve = curve;
    p.min = min;
    p.max = max;
    p.def = def;
    p.step = step;
    set_field(p.unit, sizeof p.unit, unit);
    p.labels = labels;
    return p;
}

inline arb_param_desc param_lin(const char* id, const char* name, float min, float max,
                                float def, float step = 0.0f, const char* unit = "") {
    return param(id, name, ARB_PARAM_CONTINUOUS, ARB_CURVE_LIN, min, max, def, step, unit);
}

inline arb_param_desc param_log(const char* id, const char* name, float min, float max,
                                float def, float step = 0.0f, const char* unit = "") {
    return param(id, name, ARB_PARAM_CONTINUOUS, ARB_CURVE_LOG, min, max, def, step, unit);
}

// labels must outlive the descriptor: a static array, not a local.
inline arb_param_desc param_enum(const char* id, const char* name,
                                 const char* const* labels, int count, int def) {
    return param(id, name, ARB_PARAM_ENUM, ARB_CURVE_LIN, 0.0f, float(count - 1),
                 float(def), 1.0f, "", labels);
}

// The asset keys an engine accepts (§3.2 `load_asset`). Content -- a sample, a
// kit -- is not a parameter, so it arrives by key and path, not as a float.
// Declaring the keys is what lets the host refuse a patch that names one this
// engine has never heard of, before anything loads (§7.4).
inline void set_assets(arb_engine_desc& d, std::initializer_list<const char*> keys) {
    d.num_assets = 0;
    for (const char* k : keys) {
        if (d.num_assets >= ARB_MAX_ASSETS) break;
        set_field(d.asset_keys[d.num_assets], sizeof d.asset_keys[0], k);
        ++d.num_assets;
    }
}

// Flags, wrapped around a builder: arb::per_voice(arb::param_lin(...)).
// per_voice: a keys.VEL / keys.NOTE route reaches this param per voice through
// set_voice_mod (modulation-spec §8), so the engine needs that vtable entry.
// per_note: the value is per pad and arrives through set_note_param (§15.3).
inline arb_param_desc per_voice(arb_param_desc p) { p.per_voice = 1; return p; }
inline arb_param_desc per_note(arb_param_desc p)  { p.per_note = 1; return p; }

// A modulator's control outputs (modulation-spec §3.1). Bipolar outputs write
// -1..1 into ctl_out, unipolar 0..1; the flag is what the route's depth means.
inline arb_output_desc output(const char* id, const char* name, bool bipolar) {
    arb_output_desc o{};
    set_field(o.id, sizeof o.id, id);
    set_field(o.name, sizeof o.name, name);
    o.bipolar = bipolar ? 1 : 0;
    return o;
}

inline void set_outputs(arb_engine_desc& d, std::initializer_list<arb_output_desc> outs) {
    d.num_outputs = 0;
    for (const arb_output_desc& o : outs) {
        if (d.num_outputs >= ARB_MAX_OUTPUTS) break;
        d.outputs[d.num_outputs++] = o;
    }
}

inline arb_softkey_desc softkey(const char* id, const char* label) {
    arb_softkey_desc k{};
    set_field(k.id, sizeof k.id, id);
    set_field(k.label, sizeof k.label, label);
    return k;
}

}  // namespace arb
