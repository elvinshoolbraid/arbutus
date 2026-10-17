#include "library.hpp"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <set>

namespace arb::box {
namespace fs = std::filesystem;

int semver_cmp(const std::string& a, const std::string& b) {
    auto part = [](const std::string& s, std::size_t& p) -> long {
        long v = 0;
        bool any = false;
        while (p < s.size() && s[p] >= '0' && s[p] <= '9') { v = v * 10 + (s[p++] - '0'); any = true; }
        while (p < s.size() && s[p] != '.') ++p;    // ignore -rc1 and friends
        if (p < s.size()) ++p;
        return any ? v : 0;
    };
    std::size_t pa = 0, pb = 0;
    for (int i = 0; i < 3; ++i) {
        const long x = part(a, pa), y = part(b, pb);
        if (x != y) return x < y ? -1 : 1;
    }
    return 0;
}

std::string kind_of(const nlohmann::json& j) {
    std::vector<std::string> caps;
    if (auto c = j.find("caps"); c != j.end() && c->is_array())
        for (const auto& x : *c)
            if (x.is_string()) caps.push_back(x.get<std::string>());
    auto has = [&](const char* k) { return std::find(caps.begin(), caps.end(), k) != caps.end(); };
    if (has("sequencer")) return "sequencer";
    if (has("audio_in")) return "effect";
    if (has("audio_out")) {
        bool per_note = false;
        if (auto ps = j.find("params"); ps != j.end() && ps->is_array())
            for (const auto& p : *ps)
                if (p.is_object() && p.value("per_note", false) == true) per_note = true;
        return per_note ? "drum" : "synth";
    }
    if (has("modulator")) return "modulator";
    return "";
}

void Library::add_root(std::string root) {
    if (std::find(roots_.begin(), roots_.end(), root) == roots_.end())
        roots_.push_back(std::move(root));
}

void Library::add_fallback(std::string root) {
    if (std::find(fallbacks_.begin(), fallbacks_.end(), root) == fallbacks_.end())
        fallbacks_.push_back(std::move(root));
}

namespace {

using Index = std::map<std::string, std::vector<Entry>>;

void scan(const std::string& root, Index& by_id) {
    std::error_code ec;
    // <root>/<author>/<name>/ is the shape (§3.9); a root that *is* a box
    // folder also indexes, so --library can point straight at one.
    fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec);
    if (ec) return;
    auto take = [&](const fs::path& dir) {
        std::error_code e2;
        const fs::path m = dir / "engine.json";
        if (!fs::exists(m, e2)) return;
        std::ifstream f(m);
        nlohmann::json j;
        try { f >> j; } catch (const std::exception&) { return; }
        // Typed reads that cannot throw: `j.value("version", ...)` on a
        // number aborted the host at start-up (#673). A box with a string
        // id is indexed whatever else is wrong, so loading it is refused
        // with ENGINE BROKEN by box::open rather than silently missing.
        auto str = [&](const char* k, const char* d) {
            auto f = j.find(k);
            return (f != j.end() && f->is_string()) ? f->get<std::string>() : std::string(d);
        };
        // `kind` is not read here: a missing or wrong one is refused at
        // load like any other fault (#786), not hidden as NEEDS ENGINE.
        if (!j.is_object()) return;
        Entry en{str("id", ""), str("version", "0.0.0"), dir.string(), kind_of(j)};
        if (en.id.empty()) return;
        std::vector<Entry>& v = by_id[en.id];
        if (std::none_of(v.begin(), v.end(), [&](const Entry& x) { return x.dir == en.dir; }))
            v.push_back(std::move(en));
    };
    take(root);
    for (; it != fs::recursive_directory_iterator(); it.increment(ec)) {
        if (ec) break;
        // A dot-prefixed folder is not content: `.originals/` holds the
        // masters a sample was made from, and `.trash/` is where a browser
        // LIFT that was never dropped puts an item (DDR-055). Neither may
        // be offered, so the scan does not descend into them either.
        const std::string leaf = it->path().filename().string();
        if (!leaf.empty() && leaf[0] == '.') { it.disable_recursion_pending(); continue; }
        if (it.depth() >= 2) it.disable_recursion_pending();
        if (it->is_directory(ec)) take(it->path());
    }
}

// The ids the card's stock record names, installed or removed: none of them
// may come from the slot while the card is here (D125). Unreadable: none.
std::set<std::string> recorded(const std::string& write_root) {
    std::set<std::string> ids;
    std::ifstream f(fs::path(write_root) / kStockRecord);
    if (!f) return ids;
    nlohmann::json j;
    try { f >> j; } catch (const std::exception&) { return ids; }
    if (!j.is_object()) return ids;
    if (auto in = j.find("installed"); in != j.end() && in->is_object())
        for (const auto& [id, _] : in->items()) ids.insert(id);
    if (auto rm = j.find("removed"); rm != j.end() && rm->is_array())
        for (const auto& id : *rm)
            if (id.is_string()) ids.insert(id.get<std::string>());
    return ids;
}

}  // namespace

void Library::rescan() {
    by_id_.clear();
    for (const std::string& root : roots_) scan(root, by_id_);
    if (!fallbacks_.empty()) {
        Index slot;
        for (const std::string& root : fallbacks_) scan(root, slot);
        const std::set<std::string> hidden =
            roots_.empty() ? std::set<std::string>{} : recorded(roots_.back());
        for (auto& [id, v] : slot)
            if (!by_id_.count(id) && !hidden.count(id)) by_id_[id] = std::move(v);
    }
    // Stable, so of two equal versions the one found first wins by rule: the
    // roots are scanned in order. The service passes one root since D125 (the
    // slot is a fallback), so this keeps the emulator's [BOXES, LIBRARY] order.
    // std::sort did that only for short lists, by accident of its insertion
    // sort (#675).
    for (auto& [id, v] : by_id_)
        std::stable_sort(v.begin(), v.end(),
                  [](const Entry& a, const Entry& b) { return semver_cmp(a.version, b.version) > 0; });
}

const Entry* Library::find(const std::string& id, const std::string& min_version) const {
    auto it = by_id_.find(id);
    if (it == by_id_.end()) return nullptr;
    for (const Entry& e : it->second)                      // sorted newest first
        if (min_version.empty() || semver_cmp(e.version, min_version) >= 0) return &e;
    return nullptr;
}

bool Library::has(const std::string& id) const { return by_id_.count(id) != 0; }

std::string Library::newest(const std::string& id) const {
    auto it = by_id_.find(id);
    return (it == by_id_.end() || it->second.empty()) ? std::string{} : it->second.front().version;
}

std::vector<Entry> Library::all() const {
    std::vector<Entry> v;
    for (const auto& [id, es] : by_id_) v.insert(v.end(), es.begin(), es.end());
    return v;
}

}  // namespace arb::box
