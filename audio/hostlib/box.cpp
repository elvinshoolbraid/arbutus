#include "box.hpp"

#include <dlfcn.h>

#include <fstream>

#include <manifest.hpp>

namespace arb::box {
namespace {

Refusal no(const char* code, std::string detail) { return Refusal{code, std::move(detail)}; }

Refusal open_unguarded(const std::string& box_dir, Box& out) {
    out.dir = box_dir;
    const std::string mpath = box_dir + "/engine.json";

    std::ifstream in(mpath);
    if (!in) return no("ENGINE BROKEN", "no engine.json at " + mpath);
    try {
        in >> out.manifest;
    } catch (const std::exception& e) {
        return no("ENGINE BROKEN", std::string("engine.json: ") + e.what());
    }

    const nlohmann::json& m = out.manifest;
    if (!m.is_object()) return no("ENGINE BROKEN", "engine.json is not an object");
    // Whole numbers only: `"format": 1.5` or `"abi": 2.5` truncated to a
    // supported integer and loaded (#786).
    uint32_t format = 0, abi = 0;
    if (!m.contains("format")) return no("ENGINE BROKEN", "engine.json has no \"format\"");
    if (!manifest::whole(m["format"], format) || format < 1)
        return no("ENGINE BROKEN", "\"format\" is not a whole number from 1");
    if (format > 1) return no("NEEDS FIRMWARE UPDATE", "manifest format is newer than 1");
    if (m.value("kind", std::string{}) != "engine") return no("ENGINE BROKEN", "kind is not \"engine\"");
    if (m.contains("abi") && !manifest::whole(m["abi"], abi))
        return no("ENGINE BROKEN", "\"abi\" is not a whole number");
    if (abi != uint32_t(ARB_ABI))
        return no("NEEDS FIRMWARE UPDATE",
                  "box is ABI " + std::to_string(abi) + ", host is " + std::to_string(ARB_ABI));

    const nlohmann::json arch = m.value("arch", nlohmann::json::object());
    if (!arch.contains(ARB_HOST_ARCH))
        return no("NO BUILD FOR THIS DEVICE", "no \"" ARB_HOST_ARCH "\" in arch");
    out.binary = box_dir + "/" + arch[ARB_HOST_ARCH].get<std::string>();
    if (!std::ifstream(out.binary))
        return no("NO BUILD FOR THIS DEVICE", "missing " + out.binary);

    out.lib = dlopen(out.binary.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!out.lib) return no("ENGINE BROKEN", std::string("dlopen: ") + dlerror());

    auto describe = reinterpret_cast<const arb_engine_desc* (*)()>(dlsym(out.lib, "arb_describe"));
    auto entry = reinterpret_cast<const arb_vtable* (*)()>(dlsym(out.lib, "arb_entry"));
    if (!describe || !entry) return no("ENGINE BROKEN", "box exports no arb_describe/arb_entry");

    out.desc = describe();
    out.vt = entry();
    if (!out.desc || !out.vt) return no("ENGINE BROKEN", "arb_describe/arb_entry returned NULL");
    if (out.desc->abi != uint32_t(ARB_ABI))
        return no("NEEDS FIRMWARE UPDATE", "binary is ABI " + std::to_string(out.desc->abi));
    if (Refusal r = check(*out.desc, *out.vt); !r.ok()) return r;

    // The one duplication in the system, made unable to drift (§3.8).
    if (std::string why = manifest::validate(m, *out.desc); !why.empty())
        return no("ENGINE BROKEN", why);

    return Refusal{};
}

}  // namespace

// A wrongly typed field (`"format": "1"`, `"version": 1`) makes nlohmann's
// typed reads throw; uncaught, that aborted the audio process at start-up and
// on every load (#673). Whatever the manifest holds, the answer is a refusal.
Refusal open(const std::string& box_dir, Box& out) {
    try {
        return open_unguarded(box_dir, out);
    } catch (const nlohmann::json::exception& e) {
        return no("ENGINE BROKEN", std::string("engine.json: ") + e.what());
    }
}

Refusal check(const arb_engine_desc& d, const arb_vtable& vt) {
    // The six the SDK calls required: the host calls set_param right after
    // create() and reset on the audio thread, neither checked for NULL (#673).
    if (!vt.create || !vt.destroy || !vt.reset || !vt.set_param || !vt.get_param || !vt.process)
        return no("ENGINE BROKEN", "vtable is missing one of create/destroy/reset/set_param/get_param/process");
    if (d.num_params > ARB_MAX_PARAMS) return no("ENGINE BROKEN", "num_params > 8");
    if (d.num_softkeys > ARB_MAX_SOFTKEYS) return no("ENGINE BROKEN", "num_softkeys > 4");
    if (d.num_assets > ARB_MAX_ASSETS) return no("ENGINE BROKEN", "num_assets > 3");
    if (d.num_outputs > ARB_MAX_OUTPUTS) return no("ENGINE BROKEN", "num_outputs > 4");
    // A flag without the entry that serves it is an author error that would
    // otherwise surface as a patch that silently does nothing: an asset that
    // never loads, a pad knob that never moves, a velocity route with no effect.
    if (d.num_assets > 0 && !vt.load_asset)
        return no("ENGINE BROKEN", "declares assets but the vtable has no load_asset");
    for (uint32_t i = 0; i < d.num_params; ++i) {
        const std::string id = "param " + std::to_string(i) + " (" +
                               manifest::fixed(d.params[i].id, sizeof d.params[i].id) + ")";
        if (d.params[i].per_note && !vt.set_note_param)
            return no("ENGINE BROKEN", id + " is per_note but the vtable has no set_note_param");
        if (d.params[i].per_voice && !vt.set_voice_mod)
            return no("ENGINE BROKEN", id + " is per_voice but the vtable has no set_voice_mod");
    }
    // modulation-spec §3.1: a modulator is ctl_out only, with 1..4 outputs, and
    // nothing else declares outputs -- the MOD page would draw sources that
    // are never written.
    const bool mod = (d.caps & ARB_CAP_MODULATOR) != 0;
    if (mod && d.num_outputs == 0)
        return no("ENGINE BROKEN", "a modulator declares no outputs");
    if (!mod && d.num_outputs > 0)
        return no("ENGINE BROKEN", "declares outputs but not ARB_CAP_MODULATOR");
    if (mod && (d.caps & (ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT)))
        return no("ENGINE BROKEN", "a modulator has no audio ports (modulation-spec §3.1)");
    return Refusal{};
}

void close(Box& b) {
    if (b.lib) dlclose(b.lib);
    b.lib = nullptr;
    b.desc = nullptr;
    b.vt = nullptr;
}

}  // namespace arb::box
