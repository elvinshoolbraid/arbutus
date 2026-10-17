#include "rack.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>

#include <manifest.hpp>
#include <params.hpp>

namespace arb::host {
namespace {

Refusal no(const char* code, std::string detail) { return Refusal{code, std::move(detail)}; }

std::string fixed(const char* p, std::size_t n) { return arb::manifest::fixed(p, n); }

}  // namespace

// ---- curves ----------------------------------------------------------------
Curve curve_from(const std::string& s) {
    if (s == "exp") return Curve::Exp;
    if (s == "log") return Curve::Log;
    if (s == "s") return Curve::S;
    return Curve::Lin;
}

const char* curve_name(Curve c) {
    switch (c) {
    case Curve::Exp: return "exp";
    case Curve::Log: return "log";
    case Curve::S:   return "s";
    default:         return "lin";
    }
}

float curve_apply(Curve c, float x) {
    x = arb::clamp01(x);
    switch (c) {
    case Curve::Exp: return x * x;                    // slow at the bottom
    case Curve::Log: return std::sqrt(x);             // fast at the bottom
    case Curve::S:   return x * x * (3.0f - 2.0f * x);
    default:         return x;
    }
}

// ---- sidechain -------------------------------------------------------------
int side_from(const std::string& s) {
    if (s == "input") return kSideInput;
    if (s == "self") return kSideSelf;
    if (s == "master") return kSideMaster;
    if (s.rfind("tape:", 0) == 0) {
        const int n = std::atoi(s.c_str() + 5);
        if (n >= 1 && n <= 8) return n - 1;
    }
    return kSideNone;
}

std::string side_name(int src) {
    if (src == kSideInput) return "input";
    if (src == kSideSelf) return "self";
    if (src == kSideMaster) return "master";
    if (src >= 0 && src < 8) return "tape:" + std::to_string(src + 1);
    return "none";
}

// ---- RackDoc ---------------------------------------------------------------
std::vector<std::string> RackDoc::requires_ids() const {
    std::vector<std::string> v;
    for (const TabDoc& t : tabs)
        if (std::find(v.begin(), v.end(), t.engine) == v.end()) v.push_back(t.engine);
    std::sort(v.begin(), v.end());
    return v;
}

int tabs_index(const std::vector<TabDoc>& tabs, const std::string& instance) {
    for (std::size_t i = 0; i < tabs.size(); ++i)
        if (tabs[i].instance == instance) return int(i);
    return -1;
}

std::string tabs_unique_instance(const std::vector<TabDoc>& tabs, const std::string& stem) {
    std::string base = stem;
    if (const std::size_t sl = base.rfind('/'); sl != std::string::npos) base = base.substr(sl + 1);
    for (int n = 1;; ++n) {
        const std::string cand = base + "_" + std::to_string(n);
        if (tabs_index(tabs, cand) < 0) return cand;
    }
}

int RackDoc::tab_index(const std::string& instance) const { return tabs_index(tabs, instance); }

std::string RackDoc::unique_instance(const std::string& stem) const {
    return tabs_unique_instance(tabs, stem);
}

json RackDoc::to_json() const {
    json tabs_j = json::array();
    for (const TabDoc& t : tabs) {
        json p = json::object();
        for (const auto& [id, v] : t.params) p[id] = arb::manifest::nice(v);
        json tj{{"instance", t.instance}, {"engine", t.engine},
                {"min_version", t.min_version}, {"params", p},
                {"bypass", t.bypass}};
        if (t.sidechain != kSideNone) tj["sidechain"] = side_name(t.sidechain);
        if (!t.assets.empty()) {                  // absent when none, so old racks read back unchanged
            json aj = json::object();
            for (const auto& [k, path] : t.assets) aj[k] = path;
            tj["assets"] = aj;
        }
        tabs_j.push_back(std::move(tj));
    }

    json macs = json::array();
    for (const MacroDoc& m : macros) {
        json ts = json::array();
        for (const MacroTarget& t : m.targets)
            ts.push_back(json{{"instance", t.instance}, {"param", t.param},
                              {"lo", arb::manifest::nice(t.lo)}, {"hi", arb::manifest::nice(t.hi)},
                              {"curve", curve_name(t.curve)},
                              {"depth", arb::manifest::nice(t.depth)}});
        macs.push_back(json{{"slot", m.slot}, {"name", m.name}, {"targets", ts}});
    }

    json req = json::array();
    for (const std::string& id : requires_ids()) req.push_back(id);

    return json{{"format", 1}, {"kind", "rack"}, {"name", name}, {"requires", req},
                {"tabs", tabs_j}, {"macros", macs},
                {"bypass", bypass}};
}

Refusal rack_from_json(const json& j, RackDoc& out) {
    if (!j.is_object()) return no("ENGINE BROKEN", "rack is not an object");
    const int fmt = j.value("format", 0);
    if (fmt < 1) return no("ENGINE BROKEN", "rack has no \"format\"");
    if (fmt > 1) return no("NEEDS FIRMWARE UPDATE", "rack format " + std::to_string(fmt));
    if (j.value("kind", std::string{}) != "rack") return no("ENGINE BROKEN", "kind is not \"rack\"");

    RackDoc d;
    d.name = j.value("name", std::string("RACK"));
    d.bypass = j.value("bypass", false);

    for (const json& t : j.value("tabs", json::array())) {
        TabDoc td;
        td.engine = t.value("engine", std::string{});
        if (td.engine.empty()) return no("ENGINE BROKEN", "a rack tab has no \"engine\"");
        td.instance = t.value("instance", std::string{});
        if (td.instance.empty()) td.instance = d.unique_instance(td.engine);
        if (d.tab_index(td.instance) >= 0)
            return no("ENGINE BROKEN", "duplicate instance \"" + td.instance + "\"");
        td.min_version = t.value("min_version", std::string{});
        const json pj = t.value("params", json::object());
        for (auto it = pj.begin(); it != pj.end(); ++it)
            if (it.value().is_number()) td.params.emplace_back(it.key(), it.value().get<float>());
        td.sidechain = side_from(t.value("sidechain", std::string{}));
        td.bypass = t.value("bypass", false);
        const json aj = t.value("assets", json::object());
        if (aj.is_object())
            for (auto it = aj.begin(); it != aj.end(); ++it)
                if (it.value().is_string()) td.assets.emplace_back(it.key(), it.value().get<std::string>());
        d.tabs.push_back(std::move(td));
    }
    if (d.tabs.size() > 8) return no("ENGINE BROKEN", "a rack holds at most 8 engines (§12)");

    // "parallel" (a span of tabs summed at a MIX point) was cut from v1
    // (engine-spec §6.5, 2026-10-02): an old rack that still carries it loads
    // as flat tabs in series, so the key is not read at all.

    for (const json& m : j.value("macros", json::array())) {
        MacroDoc md;
        md.slot = m.value("slot", 0);
        if (md.slot < 0 || md.slot > 7) return no("ENGINE BROKEN", "macro slot must be 0..7");
        md.name = m.value("name", std::string{});
        for (const json& t : m.value("targets", json::array())) {
            MacroTarget mt;
            mt.instance = t.value("instance", std::string{});
            mt.param = t.value("param", std::string{});
            if (mt.instance.empty() || mt.param.empty())
                return no("ENGINE BROKEN", "macro target needs \"instance\" and \"param\"");
            mt.lo = t.value("lo", 0.0f);
            mt.hi = t.value("hi", 1.0f);
            mt.curve = curve_from(t.value("curve", std::string("lin")));
            mt.depth = t.value("depth", 1.0f);
            md.targets.push_back(std::move(mt));
        }
        d.macros.push_back(std::move(md));
    }
    if (d.macros.size() > 8) return no("ENGINE BROKEN", "a rack has at most 8 macros (§6.2)");

    out = std::move(d);
    return Refusal{};
}

// ---- ModDoc (modulation-spec §11) -----------------------------------------
std::vector<std::string> ModDoc::requires_ids() const {
    std::vector<std::string> v;
    for (const TabDoc& t : tabs)
        if (std::find(v.begin(), v.end(), t.engine) == v.end()) v.push_back(t.engine);
    std::sort(v.begin(), v.end());
    return v;
}

json ModDoc::to_json() const {
    json tabs_j = json::array();
    for (const TabDoc& t : tabs) {
        json p = json::object();
        for (const auto& [id, v] : t.params) p[id] = arb::manifest::nice(v);
        json tj{{"instance", t.instance}, {"engine", t.engine},
                {"min_version", t.min_version}, {"params", p}, {"bypass", t.bypass}};
        if (t.sidechain != kSideNone) tj["sidechain"] = side_name(t.sidechain);
        if (!t.assets.empty()) {
            json aj = json::object();
            for (const auto& [k, path] : t.assets) aj[k] = path;
            tj["assets"] = aj;
        }
        tabs_j.push_back(std::move(tj));
    }
    json routes_j = json::array();
    for (const RouteDoc& r : routes) {
        json rj{{"source", r.source}, {"output", r.output}, {"target", r.target},
                {"depth", arb::manifest::nice(r.depth)}, {"curve", curve_name(r.curve)}};
        // §11 omits an unset window; §6.1 writes null. Omitted here, and both
        // read back as unset.
        if (r.has_lo) rj["lo"] = arb::manifest::nice(r.lo);
        if (r.has_hi) rj["hi"] = arb::manifest::nice(r.hi);
        routes_j.push_back(std::move(rj));
    }
    return json{{"format", 1}, {"kind", "modrack"}, {"tabs", tabs_j},
                {"routes", routes_j}, {"bypass", bypass}};
}

Refusal mod_from_json(const json& j, ModDoc& out) {
    if (!j.is_object()) return no("ENGINE BROKEN", "mod is not an object");
    const int fmt = j.value("format", 0);
    if (fmt < 1) return no("ENGINE BROKEN", "mod has no \"format\"");
    if (fmt > 1) return no("NEEDS FIRMWARE UPDATE", "mod format " + std::to_string(fmt));
    if (j.value("kind", std::string{}) != "modrack") return no("ENGINE BROKEN", "kind is not \"modrack\"");

    ModDoc d;
    for (const json& t : j.value("tabs", json::array())) {
        if (!t.is_object()) return no("ENGINE BROKEN", "a MOD tab is not an object");
        TabDoc td;
        td.engine = t.value("engine", std::string{});
        if (td.engine.empty()) return no("ENGINE BROKEN", "a MOD tab has no \"engine\"");
        td.instance = t.value("instance", std::string{});
        if (td.instance.empty()) td.instance = tabs_unique_instance(d.tabs, td.engine);
        // "keys" is the host's own source (§3.2); a tab by that name would be
        // unreachable from a route.
        if (td.instance == "keys" || d.tab_index(td.instance) >= 0)
            return no("ENGINE BROKEN", "duplicate or reserved MOD instance \"" + td.instance + "\"");
        td.min_version = t.value("min_version", std::string{});
        const json pj = t.value("params", json::object());
        for (auto it = pj.begin(); it != pj.end(); ++it)
            if (it.value().is_number()) td.params.emplace_back(it.key(), it.value().get<float>());
        td.sidechain = side_from(t.value("sidechain", std::string{}));
        td.bypass = t.value("bypass", false);
        const json aj = t.value("assets", json::object());
        if (aj.is_object())
            for (auto it = aj.begin(); it != aj.end(); ++it)
                if (it.value().is_string()) td.assets.emplace_back(it.key(), it.value().get<std::string>());
        d.tabs.push_back(std::move(td));
    }
    if (d.tabs.size() > std::size_t(kMaxModTabs))
        return no("ENGINE BROKEN", "a MOD rack holds at most 4 modulators (modulation-spec §2)");

    for (const json& r : j.value("routes", json::array())) {
        if (!r.is_object()) return no("ENGINE BROKEN", "a route is not an object");
        RouteDoc rd;
        rd.source = r.value("source", std::string{});
        rd.output = r.value("output", std::string{});
        rd.target = r.value("target", std::string{});
        if (rd.source.empty() || rd.output.empty() || rd.target.empty())
            return no("ENGINE BROKEN", "a route needs \"source\", \"output\" and \"target\"");
        if (rd.source != "keys" && d.tab_index(rd.source) < 0)
            return no("ENGINE BROKEN", "a route names no MOD tab \"" + rd.source + "\"");
        // A §5 address this firmware does not serve yet is the §7.4 case of a
        // file newer than the firmware, not a route to drop quietly: a patch
        // that plays without its pitch route is not the shared sound.
        if (const TargetAddr ta = parse_target(rd.target); !ta.not_yet.empty())
            return no("NEEDS FIRMWARE UPDATE", ta.not_yet);
        if (r.contains("depth") && r["depth"].is_number()) rd.depth = r["depth"].get<float>();
        rd.curve = curve_from(r.value("curve", std::string("lin")));
        if (r.contains("lo") && r["lo"].is_number()) { rd.has_lo = true; rd.lo = r["lo"].get<float>(); }
        if (r.contains("hi") && r["hi"].is_number()) { rd.has_hi = true; rd.hi = r["hi"].get<float>(); }
        d.routes.push_back(std::move(rd));
    }
    if (d.routes.size() > std::size_t(kMaxRoutes))
        return no("ENGINE BROKEN", "a MOD rack holds at most 4 routes (modulation-spec §2.2)");
    d.bypass = j.value("bypass", false);

    out = std::move(d);
    return Refusal{};
}

// ---- route targets (modulation-spec §5) -------------------------------------
TargetAddr parse_target(const std::string& s) {
    TargetAddr t;
    auto after = [&](const char* prefix) -> const char* {
        const std::size_t n = std::strlen(prefix);
        return s.compare(0, n, prefix) == 0 ? s.c_str() + n : nullptr;
    };
    if (const char* p = after("engine.")) {
        if (*p) { t.kind = ModTarget::Engine; t.param = p; }
    } else if (const char* p = after("env.")) {
        const std::string e = p;
        static const char* const kEnv[kEnvTargets] = {"a", "d", "s", "r", "glide", "detune"};
        for (int i = 0; i < kEnvTargets; ++i)
            if (e == kEnv[i]) { t.kind = ModTarget::Env; t.index = i; }
    } else if (const char* p = after("fx.macro.")) {
        char* end = nullptr;
        const long slot = std::strtol(p, &end, 10);
        if (*p && end && !*end && slot >= 0 && slot <= 7) { t.kind = ModTarget::Macro; t.index = int(slot); }
    } else if (const char* p = after("fx.")) {
        const std::string rest = p;
        const std::size_t dot = rest.rfind('.');
        if (dot != std::string::npos && dot > 0 && dot + 1 < rest.size()) {
            t.kind = ModTarget::Fx;
            t.instance = rest.substr(0, dot);
            t.param = rest.substr(dot + 1);
        }
    } else if (s == "host.level") {
        t.kind = ModTarget::Level;
    } else if (s == "host.pan") {
        t.kind = ModTarget::Pan;
    } else if (s == "host.pitch") {
        // ±24 st, added into arb_host.pitch_st, which every PolyHost voice
        // follows (ringing ones included).
        t.kind = ModTarget::Pitch;
    }
    return t;
}

Refusal resolve_route(const ModDoc& doc, const RouteDoc& r, const ModInputs& in, CtlRoute& out) {
    CtlRoute c;
    // --- source --------------------------------------------------------------
    if (r.source == "keys") {
        c.tab = -1;
        if (r.output == "vel") { c.output = 0; c.bipolar = false; }
        else if (r.output == "note") { c.output = 1; c.bipolar = true; }
        else return no("not_found", "keys has outputs vel and note, not \"" + r.output + "\"");
    } else {
        const int t = doc.tab_index(r.source);
        if (t < 0 || t >= int(in.outputs.size()))
            return no("not_found", "no MOD tab named \"" + r.source + "\"");
        const auto& outs = in.outputs[std::size_t(t)];
        int oi = -1;
        for (std::size_t k = 0; k < outs.size(); ++k)
            if (outs[k].first == r.output) oi = int(k);
        if (oi < 0) return no("not_found", r.source + " has no output \"" + r.output + "\"");
        c.tab = t;
        c.output = oi;
        c.bipolar = outs[std::size_t(oi)].second;
    }

    // --- target --------------------------------------------------------------
    if (r.target.rfind("mod.", 0) == 0)
        return no("bad_request", "a modulator's params are not a target (no mod->mod, DDR-050)");
    const TargetAddr ta = parse_target(r.target);
    if (!ta.not_yet.empty()) return no("not_yet", ta.not_yet);
    c.kind = ta.kind;
    switch (ta.kind) {
    case ModTarget::Engine: {
        const auto it = std::find(in.engine_pids.begin(), in.engine_pids.end(), ta.param);
        if (it == in.engine_pids.end())
            return no("not_found", "the ENGINE slot has no parameter \"" + ta.param + "\"");
        c.a = int(it - in.engine_pids.begin());
        c.per_voice = c.tab < 0 && std::size_t(c.a) < in.engine_per_voice.size() &&
                      in.engine_per_voice[std::size_t(c.a)];
        break;
    }
    case ModTarget::Fx: {
        const int t = in.rack ? in.rack->tab_index(ta.instance) : -1;
        if (t < 0 || t >= int(in.fx_pids.size()))
            return no("not_found", "no FX tab named \"" + ta.instance + "\"");
        const auto& pids = in.fx_pids[std::size_t(t)];
        const auto it = std::find(pids.begin(), pids.end(), ta.param);
        if (it == pids.end()) return no("not_found", ta.instance + " has no parameter \"" + ta.param + "\"");
        c.a = t;
        c.b = int(it - pids.begin());
        break;
    }
    case ModTarget::Macro:
    case ModTarget::Env:
        c.a = ta.index;
        break;
    case ModTarget::Level:
    case ModTarget::Pan:
    case ModTarget::Pitch:
        break;
    default:
        return no("bad_request", "\"" + r.target + "\" is not a route target (modulation-spec §5)");
    }

    c.depth = std::clamp(r.depth, -1.0f, 1.0f);
    c.curve = r.curve;
    c.lo = r.has_lo ? arb::clamp01(r.lo) : 0.0f;
    c.hi = r.has_hi ? arb::clamp01(r.hi) : 1.0f;
    if (c.lo > c.hi) std::swap(c.lo, c.hi);   // a window has no direction; depth's sign does that
    out = c;
    return Refusal{};
}

ModCtlPtr build_mod_ctl(const ModDoc& doc, const ModInputs& in, uint32_t gen) {
    auto c = std::make_unique<ModCtl>();
    c->gen = gen;
    c->bypass = doc.bypass;
    for (std::size_t t = 0; t < doc.tabs.size() && t < std::size_t(kMaxModTabs); ++t) {
        c->tab_bypass[t] = doc.tabs[t].bypass;
        c->side[t] = doc.tabs[t].sidechain;
    }
    for (std::size_t i = 0; i < doc.routes.size() && i < std::size_t(kMaxRoutes); ++i) {
        CtlRoute r;
        if (!resolve_route(doc, doc.routes[i], in, r).ok()) continue;
        r.doc = int(i);
        // §8: a keys route to a per_voice param is the engine's to apply, per
        // voice. A bypassed rack forwards nothing, so the depth stays 0.
        // Curve and window do not cross set_voice_mod -- it carries a depth
        // only (engine-spec §15.9 leaves a curve open). [default chosen here]
        if (r.per_voice && !c->bypass)
            c->vmod[r.a][r.output] += r.depth;
        c->routes.push_back(r);
    }
    return c;
}

std::string refusal_message(const Refusal& r) {
    if (r.code == "NEEDS ENGINE") return "NEEDS ENGINE " + r.detail;
    if (r.code == "NEEDS VERSION") return "NEEDS " + r.detail;   // detail is "id \u2265 1.2"
    // The asset refusals name the key and nothing else; the rest of `detail`
    // is for the author (a §7.4 message is one short line on a 320x240 screen).
    if (r.code == "UNKNOWN ASSET" || r.code == "ASSET NOT FOUND")
        return r.code + " " + r.detail.substr(0, r.detail.find(' '));
    return r.code;
}

Refusal check_requires(const arb::box::Library& lib, const std::vector<std::string>& ids,
                       const std::vector<TabDoc>* tabs) {
    for (const std::string& id : ids)
        if (!lib.has(id)) return no("NEEDS ENGINE", id);
    if (tabs)
        for (const TabDoc& t : *tabs)
            if (!t.min_version.empty() && !lib.find(t.engine, t.min_version))
                return Refusal{"NEEDS VERSION", t.engine + " ≥ " + t.min_version};
    return Refusal{};
}

// ---- Instance --------------------------------------------------------------
Instance::~Instance() {
    if (h && box.vt) box.vt->destroy(h);
    h = nullptr;
    arb::box::close(box);
}

int Instance::param_index(const std::string& id) const {
    if (!box.desc) return -1;
    for (uint32_t i = 0; i < box.desc->num_params; ++i)
        if (fixed(box.desc->params[i].id, sizeof box.desc->params[i].id) == id) return int(i);
    return -1;
}

void Instance::set_authored(uint32_t idx, float n) {
    if (!box.desc || idx >= box.desc->num_params) return;
    authored[idx] = arb::clamp01(n);
    box.vt->set_param(h, idx, authored[idx]);
}

Refusal make_instance(const arb::box::Library& lib, const std::string& id,
                      const std::string& min_version, const arb_host& hb,
                      const std::vector<AssetRef>& assets, InstancePtr& out) {
    const arb::box::Entry* e = lib.find(id, min_version);
    if (!e) {
        if (!lib.has(id)) return Refusal{"NEEDS ENGINE", id};
        return Refusal{"NEEDS VERSION", id + " ≥ " + min_version};
    }

    auto inst = std::make_unique<Instance>();
    if (Refusal r = arb::box::open(e->dir, inst->box); !r.ok()) return r;

    inst->h = inst->box.vt->create(&hb, inst->box.dir.c_str());
    if (!inst->h) return no("ENGINE BROKEN", id + ": create() returned NULL");

    // Assets, on the control thread, after create() and before this instance is
    // anywhere near the graph (§3.2). Every failure refuses the whole load: a
    // drum machine with half a kit is not the shared sound (DDR-043).
    for (const AssetRef& a : assets) {
        if (!arb::manifest::declares_asset(*inst->box.desc, a.key))
            return no("UNKNOWN ASSET", a.key + " -- " + id + " declares no such asset");
        std::error_code ec;
        if (!std::filesystem::exists(a.path, ec))
            return no("ASSET NOT FOUND", a.key + " -- no file at " + a.path);
        if (inst->box.vt->load_asset(inst->h, a.key.c_str(), a.path.c_str()) != 0)
            return no("ENGINE BROKEN", id + ": load_asset(\"" + a.key + "\") refused " + a.path);
    }

    inst->instance_id = id;
    inst->engine_id = id;
    inst->version = e->version;
    for (uint32_t i = 0; i < inst->box.desc->num_params; ++i)
        inst->authored[i] = arb::norm(inst->box.desc->params[i], inst->box.desc->params[i].def);

    out = std::move(inst);
    return Refusal{};
}

// ---- Chain -----------------------------------------------------------------
namespace {
constexpr int kNumBufs = 5;
constexpr int kBufEngine = 0, kBufPing = 1, kBufPong = 2, kBufSeq = 3,
              kBufMod = 4;   // a modulator's out[]: valid by contract, never read
}  // namespace

void Chain::alloc(uint32_t max_block) {
    mb_ = max_block;
    scratch_.assign(std::size_t(kNumBufs) * 2 * max_block, 0.0f);
}

RackCtlPtr build_ctl(const RackDoc& doc, const std::vector<std::vector<std::string>>& tab_pids) {
    const int ntabs = int(tab_pids.size());
    auto c = std::make_unique<RackCtl>();
    c->side.assign(std::size_t(ntabs), kSideNone);
    c->tab_bypass.assign(std::size_t(ntabs), false);
    c->bypass = doc.bypass;
    for (std::size_t i = 0; i < std::size_t(ntabs) && i < doc.tabs.size(); ++i) {
        c->side[i] = doc.tabs[i].sidechain;
        c->tab_bypass[i] = doc.tabs[i].bypass;
    }

    for (const MacroDoc& m : doc.macros) {
        CtlMacro cm;
        cm.slot = m.slot;
        for (const MacroTarget& t : m.targets) {
            const int ti = doc.tab_index(t.instance);
            if (ti < 0 || ti >= ntabs) continue;
            const std::vector<std::string>& pids = tab_pids[std::size_t(ti)];
            const auto pit = std::find(pids.begin(), pids.end(), t.param);
            if (pit == pids.end()) continue;
            const int pi = int(pit - pids.begin());
            cm.targets.push_back(CtlTarget{ti, pi, t.lo, t.hi, t.curve, t.depth});
        }
        if (!cm.targets.empty()) c->macros.push_back(std::move(cm));
    }
    return c;
}

// ---- rendering -------------------------------------------------------------
namespace {

uint32_t merge_events(const arb_event* a, uint32_t na, const arb_event* b, uint32_t nb,
                      arb_event* out, uint32_t cap) {
    uint32_t i = 0, j = 0, k = 0;
    while (k < cap && (i < na || j < nb)) {
        if (j >= nb || (i < na && a[i].frame <= b[j].frame)) out[k++] = a[i++];
        else                                                 out[k++] = b[j++];
    }
    return k;
}

void run(Instance& inst, const arb_host& hb, const float* in_l, const float* in_r,
         const float* side_l, const float* side_r, float* out_l, float* out_r,
         const arb_event* ev, uint32_t n_ev, arb_event* ev_out, uint32_t ev_cap,
         uint32_t* n_ev_out, uint32_t frames, float* ctl = nullptr) {
    const uint32_t caps = inst.desc().caps;
    arb_io io{};
    io.frames = frames;
    if ((caps & ARB_CAP_AUDIO_IN) && in_l) { io.in[0] = in_l; io.in[1] = in_r; }
    if ((caps & ARB_CAP_SIDECHAIN) && side_l) { io.side[0] = side_l; io.side[1] = side_r; }
    io.out[0] = out_l;
    io.out[1] = out_r;
    if (caps & ARB_CAP_EVENTS_IN) { io.ev_in = n_ev ? ev : nullptr; io.n_ev_in = n_ev; }
    *n_ev_out = 0;                                // the host zeroes it (ABI note)
    // ABI 2: ctl_out is zero on entry for every role, so a modulator that
    // writes nothing reads as 0 and a box can never see last block's values.
    for (float& v : io.ctl_out) v = 0.0f;
    if (caps & ARB_CAP_EVENTS_OUT) {
        io.ev_out = ev_out;
        io.ev_out_cap = ev_cap;
    }
    io.n_ev_out = n_ev_out;
    inst.box.vt->process(inst.h, &hb, &io);
    if (ctl) std::copy_n(io.ctl_out, ARB_MAX_OUTPUTS, ctl);   // the MOD rack reads it after (§3.1)
}

// One place maps a source id to its buffers, for FX and MOD tabs alike (§6.6,
// modulation-spec §4). kSideNone leaves both NULL: the box sees no side[].
void side_ptrs(const SideSources& s, int src, const float** l, const float** r) {
    *l = *r = nullptr;
    const float* const* p = nullptr;
    if (src == kSideInput) p = s.input;
    else if (src == kSideSelf) p = s.self;
    else if (src == kSideMaster) p = s.master;
    else if (src >= 0 && src < 8) p = s.tape[src];
    if (p) { *l = p[0]; *r = p[1]; }
}

// §6.2's curves made odd, so a bipolar output keeps its sign: for m >= 0 they
// are exactly the macro curves.
float curve_odd(Curve c, float m) {
    const float a = std::fabs(m);
    const float sg = m < 0.0f ? -1.0f : 1.0f;
    switch (c) {
    case Curve::Exp: return sg * a * a;
    case Curve::Log: return sg * std::sqrt(a);
    case Curve::S:   return sg * a * a * (3.0f - 2.0f * a);
    default:         return m;
    }
}

// §6.2: the macro at 0 sends lo, at 1 sends hi, lo > hi inverts. `depth`
// scales the excursion about the centre of [lo, hi], so depth -1 inverts about
// that centre and depth 0 parks the target there. [default chosen here: the
// spec says "negative = invert around centre" and nothing else]
float macro_map(const CtlTarget& t, float v) {
    const float x = curve_apply(t.curve, v);
    const float mapped = t.lo + x * (t.hi - t.lo);
    const float centre = 0.5f * (t.lo + t.hi);
    return arb::clamp01(centre + t.depth * (mapped - centre));
}

bool same_target(const CtlRoute& x, const CtlRoute& y) {
    return x.kind == y.kind && x.a == y.a && x.b == y.b;
}

float env_norm(int i, float v) { return arb::clamp01((v - kEnvMin[i]) / (kEnvMax[i] - kEnvMin[i])); }
float env_denorm(int i, float n) { return kEnvMin[i] + arb::clamp01(n) * (kEnvMax[i] - kEnvMin[i]); }
float& env_field(arb_host& h, int i) {
    switch (i) {
    case kEnvA:      return h.env_a;
    case kEnvD:      return h.env_d;
    case kEnvS:      return h.env_s;
    case kEnvGlide:  return h.env_glide;
    case kEnvDetune: return h.env_detune;
    default:         return h.env_r;
    }
}

}  // namespace

void render_chain(Chain& c, const RackCtl* ctl, const float* macro_values,
                  const ModCtl* mctl, ModRun* mod, bool publish,
                  const arb_host& hb, const ChainInput& in, float* out[2], uint32_t frames) {
    // --- SEQ (§5): events in, events out; its audio port is not wired -------
    const arb_event* eng_ev = in.ev_engine;
    uint32_t eng_n = in.n_engine;
    if (c.seq) {
        uint32_t n_out = 0;
        run(*c.seq, hb, nullptr, nullptr, nullptr, nullptr, c.buf(kBufSeq, 0), c.buf(kBufSeq, 1),
            in.ev, in.n_ev, c.ev_seq_out, Chain::kEvCap, &n_out, frames);
        // Clock and transport are the host's, not the sequencer's, so they are
        // merged back in rather than depending on a box to forward them.
        eng_n = merge_events(c.ev_seq_out, n_out, in.ev_clock, in.n_clock, c.ev_merge,
                             Chain::kEvCap);
        eng_ev = c.ev_merge;
    }

    // --- keys (modulation-spec §3.2): the notes the ENGINE is about to get --
    if (mod && publish)
        for (uint32_t k = 0; k < eng_n; ++k)
            if (eng_ev[k].type == ARB_EV_NOTE_ON && eng_ev[k].b > 0.0f) {
                mod->key_vel = arb::clamp01(eng_ev[k].b);
                // The same mapping as PolyHost::note_source, so the global and
                // per-voice readings of NOTE agree.
                mod->key_note = std::clamp(float(int(eng_ev[k].a) - 60) / 60.0f, -1.0f, 1.0f);
            }

    // --- MOD rack: modulators run before anything they modulate, so this
    // block's ctl_out moves this block (§7). A bypassed tab, or any tab of a
    // bypassed rack, is not run at all and reads 0. [default chosen here: the
    // spec says a bypassed modulator's routes hold at the knob; not running it
    // also costs nothing, and it resumes where it stopped]
    const bool mod_on = mctl && !mctl->bypass;
    for (std::size_t t = 0; t < c.mods.size() && t < std::size_t(kMaxModTabs); ++t) {
        float* co = c.mod_out[t];
        if (!mod_on || mctl->tab_bypass[t]) {
            std::fill_n(co, ARB_MAX_OUTPUTS, 0.0f);
            continue;
        }
        const float* sl = nullptr;
        const float* sr = nullptr;
        side_ptrs(in.sides, mctl->side[t], &sl, &sr);
        run(*c.mods[t], hb, nullptr, nullptr, sl, sr, c.buf(kBufMod, 0), c.buf(kBufMod, 1),
            eng_ev, eng_n, nullptr, 0, &c.ev_sink, frames, co);
    }

    // --- routes (§6.2), at block rate, in normalised space ------------------
    float mv[8];
    for (int k = 0; k < 8; ++k) mv[k] = macro_values ? arb::clamp01(macro_values[k]) : 0.0f;
    c.hb_mod = hb;
    uint8_t eng_mask = 0;
    uint8_t fx_mask[8]{};
    float level = mod ? mod->host_knob[0] : 1.0f;
    float pan = mod ? mod->host_knob[1] : 0.5f;
    float pitch = mod ? mod->host_knob[2] : 0.5f;
    float live[kMaxRoutes]{};
    uint8_t live_mask = 0;

    const std::size_t nr = mod_on ? std::min(mctl->routes.size(), std::size_t(kMaxRoutes)) : 0;
    float contrib[kMaxRoutes]{};
    bool active[kMaxRoutes]{};
    for (std::size_t i = 0; i < nr; ++i) {
        const CtlRoute& r = mctl->routes[i];
        float m = 0.0f;
        if (r.tab < 0) {
            m = mod ? (r.output == 0 ? mod->key_vel : mod->key_note) : 0.0f;
        } else {
            // A bypassed tab's routes are not there at all: the target sits at
            // its knob, window included (§2.3).
            if (r.tab >= int(c.mods.size()) || mctl->tab_bypass[r.tab]) continue;
            m = c.mod_out[r.tab][r.output & (ARB_MAX_OUTPUTS - 1)];
        }
        // An output outside its declared range is the box's bug; the route
        // does not amplify it.
        m = r.bipolar ? std::clamp(m, -1.0f, 1.0f) : arb::clamp01(m);
        contrib[i] = r.depth * curve_odd(r.curve, m);
        active[i] = true;
    }

    // Every route to one target sums, then clamps once to the intersection of
    // their windows (an unset window is [0, 1]); disjoint windows land on the
    // highest lo. `with_pv` includes keys routes the engine applies per voice,
    // which is what the live readout shows and not what set_param receives.
    // [default chosen here: §6.2 defines one route; audio/CLAUDE.md]
    auto combine = [&](std::size_t i, float k, bool with_pv, bool* any) {
        float sum = 0.0f, lo = 0.0f, hi = 1.0f;
        bool hit = false;
        for (std::size_t j = 0; j < nr; ++j) {
            const CtlRoute& rj = mctl->routes[j];
            if (!active[j] || !same_target(mctl->routes[i], rj)) continue;
            if (rj.per_voice && !with_pv) continue;
            sum += contrib[j];
            lo = std::max(lo, rj.lo);
            hi = std::min(hi, rj.hi);
            hit = true;
        }
        if (any) *any = hit;
        return std::clamp(k + sum, lo, std::max(lo, hi));
    };
    auto first = [&](std::size_t i) {
        for (std::size_t j = 0; j < i; ++j)
            if (active[j] && same_target(mctl->routes[i], mctl->routes[j])) return false;
        return true;
    };
    auto publish_live = [&](std::size_t i, float v) {
        for (std::size_t j = i; j < nr; ++j)
            if (active[j] && same_target(mctl->routes[i], mctl->routes[j])) {
                const int d = mctl->routes[j].doc;
                if (d >= 0 && d < kMaxRoutes) { live[d] = v; live_mask |= uint8_t(1u << d); }
            }
    };

    // fx.macro.<slot> first: a modulated macro then drives its targets.
    for (std::size_t i = 0; i < nr; ++i) {
        const CtlRoute& r = mctl->routes[i];
        if (!active[i] || r.kind != ModTarget::Macro || !first(i)) continue;
        mv[r.a & 7] = combine(i, mv[r.a & 7], false, nullptr);
        publish_live(i, mv[r.a & 7]);
    }

    // --- macros (§6.2) --------------------------------------------------------
    if (ctl && macro_values) {
        for (const CtlMacro& m : ctl->macros) {
            const float v = mv[m.slot & 7];
            for (const CtlTarget& t : m.targets) {
                if (t.tab < 0 || t.tab >= int(c.tabs.size())) continue;
                Instance& inst = *c.tabs[std::size_t(t.tab)];
                inst.box.vt->set_param(inst.h, uint32_t(t.param), macro_map(t, v));
            }
        }
    }
    // The knob an FX param route swings around: the macro's output when a
    // macro owns the param (a direct knob would not stick), else the knob.
    auto fx_bound = [&](int tab, int param, float* v) {
        bool bound = false;
        if (ctl && macro_values)
            for (const CtlMacro& m : ctl->macros)
                for (const CtlTarget& t : m.targets)
                    if (t.tab == tab && t.param == param) { *v = macro_map(t, mv[m.slot & 7]); bound = true; }
        return bound;
    };

    // --- every other target -----------------------------------------------------
    for (std::size_t i = 0; i < nr; ++i) {
        const CtlRoute& r = mctl->routes[i];
        if (!active[i] || r.kind == ModTarget::Macro || !first(i)) continue;
        bool applied = false;
        switch (r.kind) {
        case ModTarget::Engine: {
            if (!c.engine || r.a < 0 || r.a >= int(c.engine->desc().num_params)) break;
            // The knob is `authored`, which a route never writes: that is what
            // lets a knob turn move the whole excursion (DDR-046).
            const float k = c.engine->authored[r.a];
            const float v = combine(i, k, false, &applied);
            if (applied) {
                c.engine->box.vt->set_param(c.engine->h, uint32_t(r.a), v);
                eng_mask |= uint8_t(1u << r.a);
            }
            publish_live(i, combine(i, k, true, nullptr));
            break;
        }
        case ModTarget::Fx: {
            if (r.a < 0 || r.a >= int(c.tabs.size()) || r.a >= 8) break;
            Instance& inst = *c.tabs[std::size_t(r.a)];
            if (r.b < 0 || r.b >= int(inst.desc().num_params)) break;
            float k = inst.authored[r.b];
            fx_bound(r.a, r.b, &k);
            const float v = combine(i, k, false, nullptr);
            inst.box.vt->set_param(inst.h, uint32_t(r.b), v);
            fx_mask[r.a] |= uint8_t(1u << r.b);
            publish_live(i, v);
            break;
        }
        case ModTarget::Env: {
            // §5: the host re-sends the host block. PolyHost snapshots the ADSR
            // and the glide time at note-on, so those move new notes only;
            // DETUNE is read every block and moves a held unison stack.
            const int e = std::clamp(r.a, 0, kEnvTargets - 1);
            const float v = combine(i, env_norm(e, env_field(c.hb_mod, e)), false, nullptr);
            env_field(c.hb_mod, e) = env_denorm(e, v);
            publish_live(i, v);
            break;
        }
        case ModTarget::Level:
            level = combine(i, level, false, nullptr);
            publish_live(i, level);
            break;
        case ModTarget::Pan:
            pan = combine(i, pan, false, nullptr);
            publish_live(i, pan);
            break;
        case ModTarget::Pitch:
            pitch = combine(i, pitch, false, nullptr);
            publish_live(i, pitch);
            break;
        default:
            break;
        }
    }

    // A route that stopped writing hands its target back to the knob. Env and
    // host targets are recomputed from the knob every block and need nothing.
    if (c.engine) {
        const uint8_t gone = uint8_t(c.eng_mod_mask & ~eng_mask);
        for (uint32_t p = 0; p < ARB_MAX_PARAMS && p < c.engine->desc().num_params; ++p)
            if (gone & (1u << p)) c.engine->box.vt->set_param(c.engine->h, p, c.engine->authored[p]);
    }
    for (std::size_t t = 0; t < 8; ++t) {
        const uint8_t gone = uint8_t(c.fx_mod_mask[t] & ~fx_mask[t]);
        if (gone && t < c.tabs.size()) {
            Instance& inst = *c.tabs[t];
            for (uint32_t p = 0; p < ARB_MAX_PARAMS && p < inst.desc().num_params; ++p) {
                float bound = 0.0f;
                if ((gone & (1u << p)) && !fx_bound(int(t), int(p), &bound))   // a macro already rewrote it
                    inst.box.vt->set_param(inst.h, p, inst.authored[p]);
            }
        }
        c.fx_mod_mask[t] = fx_mask[t];
    }
    c.eng_mod_mask = eng_mask;

    // host.pitch adds to the bend the host already resolved into pitch_st. At
    // the centre knob with no route this adds +0.0: pitch_st is untouched.
    c.hb_mod.pitch_st = hb.pitch_st + (pitch - 0.5f) * kPitchSpanSt;

    // §8: keys routes to per_voice params cross as set_voice_mod, only when the
    // depth changed. A fresh chain's engine holds zeros, and so does vmod_sent.
    if (c.engine && c.engine->box.vt->set_voice_mod) {
        const uint32_t np = std::min<uint32_t>(c.engine->desc().num_params, ARB_MAX_PARAMS);
        for (uint32_t p = 0; p < np; ++p)
            for (uint8_t src = 0; src < 2; ++src) {
                const float want = mctl ? mctl->vmod[p][src] : 0.0f;
                if (want != c.vmod_sent[p][src]) {
                    c.engine->box.vt->set_voice_mod(c.engine->h, p, src, want);
                    c.vmod_sent[p][src] = want;
                }
            }
    }

    if (mod && publish) {
        mod->level = level;
        mod->pan = pan;
        std::copy_n(live, kMaxRoutes, mod->live);
        mod->live_mask = live_mask;
        mod->gen = mctl ? mctl->gen : 0;
    }

    // --- ENGINE -------------------------------------------------------------
    const arb_host& hbm = c.hb_mod;
    float* eng_l = c.buf(kBufEngine, 0);
    float* eng_r = c.buf(kBufEngine, 1);
    if (c.engine) {
        // DDR-063: an ENGINE-slot box that declares a sidechain port is asking
        // for the live input -- that is what `arbutus/live-input` is. Nothing
        // else in the ENGINE slot declares it, so the port stays NULL there.
        const bool wants_side = (c.engine->desc().caps & ARB_CAP_SIDECHAIN) != 0;
        run(*c.engine, hbm, nullptr, nullptr, wants_side ? in.engine_side[0] : nullptr,
            wants_side ? in.engine_side[1] : nullptr, eng_l, eng_r, eng_ev, eng_n,
            nullptr, 0, &c.ev_sink, frames);
    } else if (in.bus[0] && in.bus[1]) {
        // The master rack: the mix arrives where an engine's output would be,
        // so the tabs, macros and bypass are the same code.
        std::copy_n(in.bus[0], frames, eng_l);
        std::copy_n(in.bus[1], frames, eng_r);
    } else {
        std::fill_n(eng_l, frames, 0.0f);      // empty slot = silence (§5)
        std::fill_n(eng_r, frames, 0.0f);
    }

    // --- FX rack ------------------------------------------------------------
    auto side_of = [&](int tab, const float** l, const float** r) {
        *l = *r = nullptr;
        if (!ctl || tab >= int(ctl->side.size())) return;
        side_ptrs(in.sides, ctl->side[std::size_t(tab)], l, r);
    };

    int cur = kBufEngine;
    if (ctl && ctl->bypass) {
        std::copy_n(c.buf(cur, 0), frames, out[0]);
        std::copy_n(c.buf(cur, 1), frames, out[1]);
        return;
    }
    auto tab_bypassed = [&](int tab) {
        return ctl && tab >= 0 && tab < int(ctl->tab_bypass.size()) &&
               ctl->tab_bypass[std::size_t(tab)];
    };
    for (std::size_t i = 0; i < c.tabs.size(); ++i) {
        if (tab_bypassed(int(i))) continue;
        const float* sl;
        const float* sr;
        side_of(int(i), &sl, &sr);
        const int nxt = (cur == kBufPing) ? kBufPong : kBufPing;
        run(*c.tabs[i], hbm, c.buf(cur, 0), c.buf(cur, 1), sl, sr, c.buf(nxt, 0), c.buf(nxt, 1),
            eng_ev, eng_n, nullptr, 0, &c.ev_sink, frames);
        cur = nxt;
    }

    std::copy_n(c.buf(cur, 0), frames, out[0]);
    std::copy_n(c.buf(cur, 1), frames, out[1]);
}

}  // namespace arb::host
