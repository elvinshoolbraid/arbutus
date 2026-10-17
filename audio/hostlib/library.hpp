// library.hpp — the box library: an id -> folder index (engine-spec.md §3.9).
//
// `library/engines/<author>/<name>/engine.json` is the one tree (§2). The index
// is built by scanning it once at start and on demand; nothing here loads a
// binary, because `requires` is a set comparison that must happen *before* any
// loading starts (§6.7, §7.4).
#pragma once

#include <map>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace arb::box {

struct Entry {
    std::string id;        // "author/name"
    std::string version;   // semver, from the manifest
    std::string dir;       // the box folder
    // What it is for the last-of-a-kind rule (D106): "synth", "drum",
    // "effect", "sequencer" or "modulator", from `caps` and `per_note`
    // (`kind_of`); "" when the manifest says nothing usable.
    std::string kind;
};

// The kind a manifest's caps say: `sequencer` first, then `audio_in` (an
// effect), then `audio_out` (an instrument: a drum when a param is `per_note`,
// §15.3, else a synth), then `modulator`.
std::string kind_of(const nlohmann::json& manifest);

// The record of the stock engines copied onto the card (D106, D125), relative
// to the writable root; `arbutus_os/engines.py` writes it.
inline constexpr const char* kStockRecord = "engines/.stock.json";

// -1 / 0 / +1. Missing components are 0, so "1.2" < "1.2.1" and "1.2" == "1.2.0".
int semver_cmp(const std::string& a, const std::string& b);

class Library {
public:
    void add_root(std::string root);
    // D125: the firmware slot's stock boxes, searched only for an id no root
    // has and the card's stock record does not name (installed once, so a
    // missing copy was uninstalled; or removed). A card away on USB has no
    // record, so the slot's copies are all there is.
    void add_fallback(std::string root);
    void rescan();

    // The roots, never the fallbacks: the last is the writable one.
    const std::vector<std::string>& roots() const { return roots_; }

    // Highest version >= min_version, or nullptr. min_version "" means any.
    const Entry* find(const std::string& id, const std::string& min_version = "") const;
    bool has(const std::string& id) const;
    // Highest version present for an id, "" when the id is unknown.
    std::string newest(const std::string& id) const;

    std::vector<Entry> all() const;

private:
    std::vector<std::string> roots_;
    std::vector<std::string> fallbacks_;
    std::map<std::string, std::vector<Entry>> by_id_;
};

}  // namespace arb::box
