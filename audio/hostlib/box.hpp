// box.hpp — open a box folder the way the host will (engine-spec.md §3.9, §7.4).
//
// Every refusal is one of the §7.4 strings and nothing loads. Never bypass an
// engine silently: a bypassed node plays a sound that is not the shared sound,
// under the shared name.
#pragma once

#include <string>

#include <nlohmann/json.hpp>

#include <arbutus_engine.h>

namespace arb::box {

struct Box {
    nlohmann::json manifest;
    std::string    dir;
    std::string    binary;
    void*          lib = nullptr;
    const arb_engine_desc* desc = nullptr;
    const arb_vtable*      vt = nullptr;
};

struct Refusal {
    std::string code;     // the §7.4 message, verbatim
    std::string detail;   // why, for the author

    bool ok() const { return code.empty(); }
};

Refusal open(const std::string& box_dir, Box& out);

// What open() checks about the binary itself once it has a descriptor and a
// vtable, before comparing the manifest: counts in range, every flag backed by
// the vtable entry it needs, the modulator role's shape. Split out so it can be
// tested without building a box per mistake.
Refusal check(const arb_engine_desc& d, const arb_vtable& vt);
void close(Box& b);

}  // namespace arb::box
