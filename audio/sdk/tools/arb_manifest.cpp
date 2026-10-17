// arb-manifest — write a box's engine.json from its built binary.
//
// add_arbutus_engine() runs this after every link, so the §3.8 "params"
// snapshot is generated, never typed. What the author writes by hand is the
// prose half (author, description); what the binary knows (id, version, abi,
// caps, params, softkeys, assets, outputs) is read out of arb_describe().
//
// Build-time only. Nothing here runs on the device.

#include <dlfcn.h>

#include <cstdio>
#include <fstream>
#include <string>

#include <arbutus_engine.h>

#include "../manifest.hpp"

namespace {

using json = nlohmann::json;

int fail(const std::string& msg) {
    std::fprintf(stderr, "arb-manifest: %s\n", msg.c_str());
    return 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 7) {
        std::fprintf(stderr,
                     "usage: arb-manifest <binary> <source engine.json> <out engine.json>"
                     " <arch> <binary-rel-path> <author/name>\n");
        return 2;
    }
    const std::string binary = argv[1], src = argv[2], out = argv[3];
    const std::string arch = argv[4], rel = argv[5], want_id = argv[6];

    void* lib = dlopen(binary.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!lib) return fail("dlopen " + binary + ": " + dlerror());

    auto describe = reinterpret_cast<const arb_engine_desc* (*)()>(dlsym(lib, "arb_describe"));
    if (!describe) return fail(binary + " exports no arb_describe");
    if (!dlsym(lib, "arb_entry")) return fail(binary + " exports no arb_entry");

    const arb_engine_desc* d = describe();
    if (!d) return fail("arb_describe() returned NULL");
    if (d->abi != ARB_ABI)
        return fail("engine is ABI " + std::to_string(d->abi) + ", this SDK is " +
                    std::to_string(ARB_ABI));
    if (d->num_params > ARB_MAX_PARAMS) return fail("num_params > ARB_MAX_PARAMS");
    if (d->num_softkeys > ARB_MAX_SOFTKEYS) return fail("num_softkeys > ARB_MAX_SOFTKEYS");
    if (d->num_outputs > ARB_MAX_OUTPUTS) return fail("num_outputs > ARB_MAX_OUTPUTS");
    if (arb::manifest::fixed(d->id, sizeof d->id).find('/') == std::string::npos)
        return fail("engine id must be \"author/name\"");
    // §3.9: the id is the folder the box lands in. A copied template whose id
    // was not renamed would otherwise build a second `arbutus/example`, and
    // the library, which indexes by id, would offer only one of the two (#675).
    if (const std::string id = arb::manifest::fixed(d->id, sizeof d->id); id != want_id)
        return fail("engine id \"" + id + "\" is not its folder \"" + want_id +
                    "\": rename the id in arb_describe(), or the target or author");

    json authored = json::object();
    if (std::ifstream in{src}) {
        try {
            in >> authored;
        } catch (const std::exception& e) {
            return fail(src + ": " + e.what());
        }
    } else {
        return fail("cannot read " + src);
    }

    // Generated fields win; anything else the author wrote is carried through.
    json m = authored;
    json gen = arb::manifest::from_desc(*d, arch, rel);
    if (authored.contains("arch") && authored["arch"].is_object()) {
        json merged = authored["arch"];        // a native build only knows its own arch
        merged[arch] = rel;
        gen["arch"] = merged;
    }
    m.update(gen);

    if (const std::string why = arb::manifest::validate(m, *d); !why.empty())
        return fail("generated manifest does not validate: " + why);

    std::ofstream os{out};
    if (!os) return fail("cannot write " + out);
    os << m.dump(2) << "\n";
    return 0;
}
