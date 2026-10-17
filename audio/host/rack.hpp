// rack.hpp — the FX rack as host code (engine-spec.md §6) and the chain that
// renders one instrument (§5).
//
// Three things live here and they are deliberately separated:
//
//   RackDoc   the §6.7 file, pure data, what the UI edits and reads back.
//   Chain     the loaded engines: SEQ, ENGINE, and the rack's tabs. Built on
//             the control thread, rendered on the audio thread, never mutated
//             structurally once handed over.
//   RackCtl   everything about the rack that can change without reloading an
//             engine — macro targets, tab bypass, sidechain sources — with
//             every string already resolved to an index. Swapped as a pointer
//             so a range edit does not restart a reverb.
//
// Macro values are neither: they are one float per slot, written by the UI at
// any time, read every block.
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include <arbutus_engine.h>

#include <box.hpp>
#include <library.hpp>

namespace arb::host {

using json = nlohmann::json;
using Refusal = arb::box::Refusal;

// ---- macro curves (§6.2) ---------------------------------------------------
// lin/exp/log/s. The spec names them and does not define them; these are the
// cheapest shapes that behave: exp bunches at the bottom, log at the top, s is
// smoothstep. [default chosen here, see audio/CLAUDE.md]
enum class Curve : uint8_t { Lin = 0, Exp, Log, S };
Curve curve_from(const std::string& s);
const char* curve_name(Curve c);
float curve_apply(Curve c, float x);

// ---- sidechain sources (§6.6) ---------------------------------------------
inline constexpr int kSideNone = -1;   // 0..7 = tape 1..8 (DDR-060)
inline constexpr int kSideInput = 8;   // the live input
// modulation-spec §4: this instrument's post-rack bus and the master bus, both
// one host block late (the host has only just rendered them). Host-wide ids,
// so FX tabs take them too.
inline constexpr int kSideSelf = 9;
inline constexpr int kSideMaster = 10;
int side_from(const std::string& s);
std::string side_name(int src);

// ---- the §6.7 document -----------------------------------------------------
struct MacroTarget {
    std::string instance, param;
    float lo = 0.0f, hi = 1.0f;
    Curve curve = Curve::Lin;
    float depth = 1.0f;
};

struct MacroDoc {
    int slot = 0;
    std::string name;
    std::vector<MacroTarget> targets;
};

struct TabDoc {
    std::string instance, engine, min_version;
    std::vector<std::pair<std::string, float>> params;   // id -> normalised 0..1
    int sidechain = kSideNone;
    bool bypass = false;                  // FX/MOD node bypass (DDR-049)
    // key -> path, resolved absolute once loaded (§3.2 load_asset): a MOD
    // tab's (arbutus/stepmod's pattern) or an FX tab's. No stock FX box
    // declares one yet; the plumbing is here for the ones that will.
    std::vector<std::pair<std::string, std::string>> assets;
};

// Shared by the FX rack and the MOD rack: both are a list of named tabs.
int tabs_index(const std::vector<TabDoc>& tabs, const std::string& instance);
std::string tabs_unique_instance(const std::vector<TabDoc>& tabs, const std::string& stem);

struct RackDoc {
    std::string name = "RACK";
    std::vector<TabDoc> tabs;
    std::vector<MacroDoc> macros;
    bool bypass = false;                  // whole-rack bypass on the master tab

    json to_json() const;                       // exactly §6.7
    std::vector<std::string> requires_ids() const;
    int tab_index(const std::string& instance) const;
    std::string unique_instance(const std::string& stem) const;
};

Refusal rack_from_json(const json& j, RackDoc& out);

// ---- the MOD rack document (modulation-spec §2, §6.1, §11) -----------------
// Four modulators and four routes, both on purpose: the MOD page is one tab per
// modulator and the master tab is one row per encoder (§2.2). `keys` is a
// source that is always there and is not a tab (§3.2).
inline constexpr int kMaxModTabs = 4;
inline constexpr int kMaxRoutes = 4;

struct RouteDoc {
    std::string source, output, target;   // "lfo_a" "sine" "engine.cutoff"
    float depth = 0.25f;                  // §6.2 default for a new route
    Curve curve = Curve::Lin;
    bool has_lo = false, has_hi = false;  // unset = the target's whole range
    float lo = 0.0f, hi = 1.0f;
};

struct ModDoc {
    std::vector<TabDoc> tabs;
    std::vector<RouteDoc> routes;
    bool bypass = false;

    json to_json() const;                       // exactly §11
    std::vector<std::string> requires_ids() const;
    int tab_index(const std::string& instance) const { return tabs_index(tabs, instance); }
};

Refusal mod_from_json(const json& j, ModDoc& out);

// A route target (§5), parsed but not resolved against any engine.
enum class ModTarget : uint8_t { None, Engine, Fx, Macro, Env, Level, Pan, Pitch };

struct TargetAddr {
    ModTarget kind = ModTarget::None;
    std::string instance, param;          // Fx: both; Engine: param
    int index = 0;                        // Macro: slot; Env: kEnvA..kEnvDetune
    std::string not_yet;                  // non-empty: a §5 address this host does not serve yet
};
TargetAddr parse_target(const std::string& s);

// The ENV page's ranges, in seconds / level / cents, so env.* routes work in
// the same normalised 0..1 as every other target (§6.1). [default chosen here:
// the spec gives the ENV page no ranges; A D S R are the emulator's ENV
// encoders, pages/synth.py; GLIDE 0-2 s and DETUNE 0-50 ct are new with
// DDR-052 -- audio/CLAUDE.md]
enum : int { kEnvA = 0, kEnvD, kEnvS, kEnvR, kEnvGlide, kEnvDetune, kEnvTargets };
inline constexpr float kEnvMin[kEnvTargets] = {0.001f, 0.001f, 0.0f, 0.005f, 0.0f, 0.0f};
inline constexpr float kEnvMax[kEnvTargets] = {2.0f, 2.0f, 1.0f, 4.0f, 2.0f, 50.0f};
// BEND range, which is not a route target but is clamped the same way.
inline constexpr float kBendRangeMax = 24.0f;
// host.pitch: normalised 0..1 onto +/-24 st, knob 0.5 = no offset (modulation-spec §5).
inline constexpr float kPitchSpanSt = 48.0f;

// requires-before-loading (§6.7, §7.4). "" detail on success.
// Turns a refusal into the §7.4 line the player sees.
std::string refusal_message(const Refusal& r);

Refusal check_requires(const arb::box::Library& lib, const std::vector<std::string>& ids,
                       const std::vector<TabDoc>* tabs);

// ---- loaded engines --------------------------------------------------------
struct Instance {
    arb::box::Box box;
    void* h = nullptr;
    std::string instance_id, engine_id, version;
    float authored[ARB_MAX_PARAMS] = {};

    ~Instance();
    Instance() = default;
    Instance(const Instance&) = delete;
    Instance& operator=(const Instance&) = delete;

    const arb_engine_desc& desc() const { return *box.desc; }
    int param_index(const std::string& id) const;
    void set_authored(uint32_t idx, float n);
};
using InstancePtr = std::unique_ptr<Instance>;

// One entry of a patch's or kit's "assets" map: a key the engine declared and
// an absolute path. Content, not parameters (DDR-037) -- see PROTOCOL.md
// "Assets".
struct AssetRef {
    std::string key, path;
};

// Loads a box, calls create(), then load_asset() once per asset. Every failure
// is a §7.4 refusal and nothing is left half-built. load_asset is control
// thread only and runs before the instance ever reaches the graph (§3.2).
Refusal make_instance(const arb::box::Library& lib, const std::string& id,
                      const std::string& min_version, const arb_host& hb,
                      const std::vector<AssetRef>& assets, InstancePtr& out);

// ---- the rendered chain ----------------------------------------------------
struct Chain {
    InstancePtr seq, engine;
    std::vector<InstancePtr> tabs;
    std::vector<InstancePtr> mods;                 // MOD rack tabs, run first (§3.1)

    // MOD rack working state, all fixed-size (§3.7.1).
    float    mod_out[kMaxModTabs][ARB_MAX_OUTPUTS]{};  // this block's ctl_out per tab
    arb_host hb_mod{};                             // the host block with env.* applied
    uint8_t  eng_mod_mask = 0;                     // engine params a route wrote last block
    uint8_t  fx_mod_mask[8]{};                     // per FX tab, the same
    float    vmod_sent[ARB_MAX_PARAMS][2]{};       // set_voice_mod depths the engine holds

    void alloc(uint32_t max_block);
    float* buf(int i, int ch) { return scratch_.data() + (std::size_t(i) * 2 + std::size_t(ch)) * mb_; }
    uint32_t max_block() const { return mb_; }

    // Pre-allocated event working space; §3.7.1 forbids growing these later.
    static constexpr uint32_t kEvCap = 512;
    arb_event ev_seq_out[kEvCap]{};
    arb_event ev_merge[kEvCap]{};
    uint32_t  ev_sink = 0;   // where an engine without EVENTS_OUT writes its count

private:
    std::vector<float> scratch_;
    uint32_t mb_ = 0;
};
using ChainPtr = std::unique_ptr<Chain>;

// ---- resolved, hot-swappable rack control ---------------------------------
struct CtlTarget { int tab = 0, param = 0; float lo = 0, hi = 1; Curve curve = Curve::Lin; float depth = 1; };
struct CtlMacro { int slot = 0; std::vector<CtlTarget> targets; };

struct RackCtl {
    std::vector<CtlMacro> macros;
    std::vector<int> side;       // one per tab
    std::vector<bool> tab_bypass;
    bool bypass = false;
};
using RackCtlPtr = std::unique_ptr<RackCtl>;

// Resolves instance and parameter names to indices. `tab_pids` is one list of
// parameter ids per tab, descriptor order — the control thread's mirror of what
// each loaded engine has. Unknown names are dropped rather than refused: the
// loaded engines are the authority on what exists, not the document.
RackCtlPtr build_ctl(const RackDoc& doc, const std::vector<std::vector<std::string>>& tab_pids);

// ---- resolved, hot-swappable MOD rack control -----------------------------
// Every string of the §11 document resolved to an index against the chain it
// is swapped in with. Plain data, read-only on the audio thread.
struct CtlRoute {
    int       doc = 0;            // index into ModDoc::routes, for the live readout
    int       tab = -1;           // MOD tab, -1 = keys
    int       output = 0;         // keys: 0 = VEL, 1 = NOTE
    bool      bipolar = true;
    ModTarget kind = ModTarget::None;
    int       a = 0, b = 0;       // Engine: a = param; Fx: a = tab, b = param; Macro/Env: a
    float     depth = 0.25f;
    Curve     curve = Curve::Lin;
    float     lo = 0.0f, hi = 1.0f;
    bool      per_voice = false;  // keys -> a per_voice engine param: set_voice_mod, not an offset (§8)
};

struct ModCtl {
    std::vector<CtlRoute> routes;
    bool tab_bypass[kMaxModTabs]{};
    int  side[kMaxModTabs]{kSideNone, kSideNone, kSideNone, kSideNone};
    bool bypass = false;
    float vmod[ARB_MAX_PARAMS][2]{};   // per_voice depths to forward, [idx][arb_voice_src]
    uint32_t gen = 0;                  // matches live values to the document that made them
};
using ModCtlPtr = std::unique_ptr<ModCtl>;

// What the control thread knows about the loaded engines, for resolving routes.
struct ModInputs {
    std::vector<std::string> engine_pids;
    std::vector<bool> engine_per_voice;
    const RackDoc* rack = nullptr;
    std::vector<std::vector<std::string>> fx_pids;                     // per FX tab
    std::vector<std::vector<std::pair<std::string, bool>>> outputs;   // per MOD tab: id, bipolar
};

// Resolves one route. "" on success; otherwise a refusal whose code is
// "not_found", "not_yet" (a §5 address this host does not serve yet) or
// "bad_request", and whose detail says why.
Refusal resolve_route(const ModDoc& doc, const RouteDoc& r, const ModInputs& in, CtlRoute& out);

// Unresolvable routes are carried in the document and inert, like macro
// targets (build_ctl): the loaded engines are the authority on what exists.
ModCtlPtr build_mod_ctl(const ModDoc& doc, const ModInputs& in, uint32_t gen);

// ---- rendering -------------------------------------------------------------
// Per instrument, owned by the audio thread, and surviving chain swaps so a
// patch reload does not forget the last velocity.
struct ModRun {
    float key_vel = 0.0f;         // keys.VEL: last note-on velocity; 0 before any note
    float key_note = 0.0f;        // keys.NOTE: (note - 60) / 60 of the same note
    float host_knob[3] = {1.0f, 0.5f, 0.5f};   // host.level, host.pan, host.pitch knobs, normalised
    // Written by the current chain each block, read by render_instrument and
    // published to the control thread.
    float level = 1.0f, pan = 0.5f;      // effective, normalised
    float live[kMaxRoutes]{};            // each document route's target value
    uint8_t live_mask = 0;               // which of live[] were written
    uint32_t gen = 0;
};

struct SideSources {
    const float* tape[8][2] = {};
    const float* input[2] = {};
    const float* self[2] = {};            // previous block of this instrument's bus
    const float* master[2] = {};          // previous block of the master bus
};

struct ChainInput {
    // Everything, sorted: what the SEQ slot sees.
    const arb_event* ev = nullptr;        uint32_t n_ev = 0;
    // The same minus soft-keys, which reach sequencer engines only (§3.6):
    // what the ENGINE slot sees when the SEQ slot is empty.
    const arb_event* ev_engine = nullptr; uint32_t n_engine = 0;
    // Clock and transport alone; merged back after a sequencer (§3.4).
    const arb_event* ev_clock = nullptr;  uint32_t n_clock = 0;
    SideSources sides;
    // Audio *into* the chain, where the ENGINE slot's output would be. Null on
    // an instrument (the engine makes its own sound); the master rack (§15.5,
    // DDR-062) is the same rack with the mix fed in instead of an engine.
    const float* bus[2] = {nullptr, nullptr};
    // The ENGINE slot's side[] (DDR-063): the selected live input, for a box
    // that declares ARB_CAP_SIDECHAIN in the ENGINE slot. `arbutus/live-input`
    // is the one that does; every other engine leaves the port NULL because it
    // never asked for it.
    const float* engine_side[2] = {nullptr, nullptr};
};

// Renders one instrument: [SEQ] -> [MOD rack] -> [ENGINE] -> [FX rack] -> out[2].
// Writes both channels always (§3.7.3); an empty slot is passthrough/silence.
// `mod` may be null (no MOD state at all). `publish` is false for a chain that
// is only fading out: it renders with the same routes but does not update the
// keys source or the live values.
void render_chain(Chain& c, const RackCtl* ctl, const float* macro_values,
                  const ModCtl* mctl, ModRun* mod, bool publish,
                  const arb_host& hb, const ChainInput& in, float* out[2], uint32_t frames);

}  // namespace arb::host
