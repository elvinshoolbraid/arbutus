// The ABI 2 descriptor fields through the builders, the engine.json round trip
// and the loader's structural check (§3.8, §7.4, §15.1). No box is built: the
// descriptors are made here, which is what lets every mistake have a case.

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#include <unistd.h>   // mkdtemp: POSIX; macOS declares it only here, glibc in <cstdlib> too

#include <arbutus_engine.h>

#include <box.hpp>
#include <desc.hpp>
#include <library.hpp>
#include <manifest.hpp>

namespace {

int failures = 0;

#define CHECK(cond, ...)                                                        \
    do {                                                                        \
        if (!(cond)) {                                                          \
            std::printf("FAIL %s:%d  " #cond "\n      ", __FILE__, __LINE__);   \
            std::printf(__VA_ARGS__);                                           \
            std::printf("\n");                                                  \
            ++failures;                                                         \
        }                                                                       \
    } while (0)

using json = nlohmann::json;

void* f_create(const arb_host*, const char*) { return nullptr; }
void  f_destroy(void*) {}
void  f_reset(void*) {}
void  f_set_param(void*, uint32_t, float) {}
float f_get_param(void*, uint32_t) { return 0.0f; }
void  f_process(void*, const arb_host*, arb_io*) {}
void  f_set_note_param(void*, uint8_t, uint32_t, float) {}
void  f_set_voice_mod(void*, uint32_t, uint8_t, float) {}

arb_vtable full_vtable() {
    arb_vtable vt{};
    vt.create = f_create;
    vt.destroy = f_destroy;
    vt.reset = f_reset;
    vt.set_param = f_set_param;
    vt.get_param = f_get_param;
    vt.process = f_process;
    vt.set_note_param = f_set_note_param;
    vt.set_voice_mod = f_set_voice_mod;
    return vt;
}

// A modulator: two outputs, a CONTINUOUS RATE, no audio ports.
arb_engine_desc modulator() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbtest/lfo");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_MODULATOR | ARB_CAP_EVENTS_IN;
    d.num_params = 1;
    d.params[0] = arb::param_lin("rate", "RATE", -1.0f, 1.0f, 0.0f, 0.01f, "");
    arb::set_outputs(d, {arb::output("sine", "SINE", true), arb::output("gate", "GATE", false)});
    return d;
}

// A drum-shaped synth: one per_note param, one per_voice param, one both.
arb_engine_desc pads() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbtest/pads");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_OUT | ARB_CAP_EVENTS_IN;
    d.num_params = 3;
    d.params[0] = arb::per_note(arb::param_lin("pitch", "PITCH", -12.0f, 12.0f, 0.0f));
    d.params[1] = arb::per_voice(arb::param_lin("level", "LEVEL", 0.0f, 1.0f, 0.8f));
    d.params[2] = arb::per_voice(arb::per_note(arb::param_lin("pan", "PAN", -1.0f, 1.0f, 0.0f)));
    return d;
}

// The rest of the snapshot (#675, review m-audio-sdk row 8): an ENUM with
// labels, a stepped param with a unit, honours_env, assets and soft-keys.
const char* const kModes[] = {"FWD", "REV", "PNG"};
arb_engine_desc everything() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbtest/all");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_SEQUENCER | ARB_CAP_EVENTS_IN | ARB_CAP_EVENTS_OUT;
    d.honours_env = 1;
    d.num_params = 2;
    d.params[0] = arb::param_enum("dir", "DIR", kModes, 3, 0);
    d.params[1] = arb::param_lin("gate", "GATE", 10.0f, 100.0f, 50.0f, 5.0f, "%");
    arb::set_assets(d, {"pattern"});
    d.num_softkeys = 2;
    d.softkeys[0] = arb::softkey("rest", "REST");
    d.softkeys[1] = arb::softkey("clear", "CLR");
    return d;
}

void test_builders_set_the_flags_and_outputs() {
    const arb_engine_desc m = modulator();
    CHECK(m.num_outputs == 2, "num_outputs %u", m.num_outputs);
    CHECK(m.outputs[0].bipolar == 1 && m.outputs[1].bipolar == 0, "bipolar flags");
    CHECK(arb::manifest::fixed(m.outputs[1].name, 8) == "GATE", "output name");

    const arb_engine_desc p = pads();
    CHECK(p.params[0].per_note == 1 && p.params[0].per_voice == 0, "per_note only");
    CHECK(p.params[1].per_voice == 1 && p.params[1].per_note == 0, "per_voice only");
    CHECK(p.params[2].per_voice == 1 && p.params[2].per_note == 1, "both");

    arb_engine_desc over{};
    arb::set_outputs(over, {arb::output("a", "A", true), arb::output("b", "B", true),
                            arb::output("c", "C", true), arb::output("d", "D", true),
                            arb::output("e", "E", true)});
    CHECK(over.num_outputs == ARB_MAX_OUTPUTS, "set_outputs stops at ARB_MAX_OUTPUTS");
}

void test_round_trip() {
    for (const arb_engine_desc& d : {modulator(), pads()}) {
        const json m = arb::manifest::from_desc(d, "darwin-arm64", "x.dylib");
        const std::string why = arb::manifest::validate(m, d);
        CHECK(why.empty(), "%s: generated manifest does not validate: %s", d.id, why.c_str());
        // What a reader of engine.json sees, and what came back through text.
        const json back = json::parse(m.dump());
        CHECK(arb::manifest::validate(back, d).empty(), "%s: does not survive dump/parse", d.id);
    }

    const json lfo = arb::manifest::from_desc(modulator(), "darwin-arm64", "x.dylib");
    CHECK(lfo["caps"] == json({"events_in", "modulator"}), "caps: %s", lfo["caps"].dump().c_str());
    CHECK(lfo["outputs"].size() == 2 && lfo["outputs"][0]["id"] == "sine" &&
              lfo["outputs"][0]["name"] == "SINE" && lfo["outputs"][0]["bipolar"] == true &&
              lfo["outputs"][1]["bipolar"] == false,
          "outputs: %s", lfo["outputs"].dump().c_str());

    const json pj = arb::manifest::from_desc(pads(), "darwin-arm64", "x.dylib");
    CHECK(pj["params"][0]["per_note"] == true && pj["params"][0]["per_voice"] == false,
          "params[0]: %s", pj["params"][0].dump().c_str());
    CHECK(pj["outputs"] == json::array(), "non-modulator: outputs present and empty");
}

void test_drift_in_a_new_field_is_refused() {
    const arb_engine_desc lfo = modulator();
    const json good = arb::manifest::from_desc(lfo, "darwin-arm64", "x.dylib");
    auto refuses = [&](const arb_engine_desc& d, json m, const char* what) {
        const std::string why = arb::manifest::validate(m, d);
        CHECK(!why.empty(), "%s: drift was accepted", what);
    };
    { json m = good; m["outputs"][0]["bipolar"] = false; refuses(lfo, m, "output bipolar"); }
    { json m = good; m["outputs"][1]["name"] = "TRIG"; refuses(lfo, m, "output name"); }
    { json m = good; m["outputs"].erase(1); refuses(lfo, m, "output count"); }
    { json m = good; m["caps"] = json({"events_in"}); refuses(lfo, m, "modulator cap"); }

    const arb_engine_desc p = pads();
    const json pg = arb::manifest::from_desc(p, "darwin-arm64", "x.dylib");
    { json m = pg; m["params"][1]["per_voice"] = false; refuses(p, m, "per_voice"); }
    { json m = pg; m["params"][0]["per_note"] = false; refuses(p, m, "per_note"); }
    { json m = pg; m["params"][0]["per_note"] = 1; refuses(p, m, "per_note as a number"); }
    { json m = pg; m["params"][1].erase("per_voice"); refuses(p, m, "per_voice missing"); }

    // An absent flag or list means false / empty, so a manifest that predates a
    // field still validates against a binary that does not use it.
    arb_engine_desc plain = pads();
    plain.num_params = 0;
    json m = arb::manifest::from_desc(plain, "darwin-arm64", "x.dylib");
    m.erase("outputs");
    CHECK(arb::manifest::validate(m, plain).empty(), "absent outputs == []");

    // #786: a field present with the wrong type was read as its default, so
    // `"min": "0"` matched a min of 0 and `"type": 42` matched continuous.
    { json m2 = pg; m2["params"][1]["min"] = "0"; refuses(p, m2, "min as a string"); }
    { json m2 = pg; m2["params"][1]["type"] = 42; refuses(p, m2, "type as a number"); }
    { json m2 = pg; m2["params"][1]["curve"] = false; refuses(p, m2, "curve as a bool"); }
    { json m2 = pg; m2["params"][1]["unit"] = json::array(); refuses(p, m2, "unit as a list"); }
    { json m2 = pg; m2["abi"] = ARB_ABI + 0.5; refuses(p, m2, "fractional abi"); }
    { json m2 = pg; m2["latency_frames"] = 0.5; refuses(p, m2, "fractional latency_frames"); }
    // Absent optional fields still read as their defaults.
    { json m2 = pg; m2["params"][1].erase("type"); m2["params"][1].erase("curve");
      CHECK(arb::manifest::validate(m2, p).empty(), "absent type/curve == continuous/lin"); }
    { json m2 = pg; m2["abi"] = double(ARB_ABI);
      CHECK(arb::manifest::validate(m2, p).empty(), "abi %d.0 is a whole number", ARB_ABI); }

    // #675 (row 8): the seven guards the suite could lose without a red. One
    // drift each, against a snapshot that validates as generated.
    const arb_engine_desc all = everything();
    const json ag = arb::manifest::from_desc(all, "darwin-arm64", "x.dylib");
    CHECK(arb::manifest::validate(ag, all).empty(), "the control: everything() validates: %s",
          arb::manifest::validate(ag, all).c_str());
    { json m2 = ag; m2["params"][1]["step"] = 1.0; refuses(all, m2, "step"); }
    { json m2 = ag; m2["params"][1]["unit"] = "ms"; refuses(all, m2, "unit"); }
    { json m2 = ag; m2["params"][0].erase("labels"); refuses(all, m2, "labels missing"); }
    { json m2 = ag; m2["params"][1]["labels"] = json({"A"}); refuses(all, m2, "labels on a non-ENUM"); }
    for (int k = 0; k < 3; ++k) {
        json m2 = ag;
        m2["params"][0]["labels"][std::size_t(k)] = "XXX";
        const std::string what = "label " + std::to_string(k);
        refuses(all, m2, what.c_str());
    }
    { json m2 = ag; m2["honours_env"] = false; refuses(all, m2, "honours_env"); }
    { json m2 = ag; m2["assets"] = json({"sample"}); refuses(all, m2, "assets"); }
    { json m2 = ag; m2["softkeys"][1]["label"] = "CLEAR"; refuses(all, m2, "softkey label"); }
}

// A box folder holding only `m` as its engine.json, under a fresh root.
std::string box_with(const std::string& root, const char* id, const json& m) {
    const std::string dir = root + "/" + id;
    std::filesystem::create_directories(dir);
    std::ofstream(dir + "/engine.json") << m.dump();
    return dir;
}

// #675 (review row 11): two roots holding the same id at the same version.
// The library scans its roots in order, shipped boxes first, so the first
// root's copy must win by rule. std::sort is not stable; it kept that order
// only for short lists. 40 roots, two versions interleaved, so the sort has
// real work to do.
void test_equal_versions_keep_the_root_order() {
    char tmpl[] = "/tmp/arbutus-ties-XXXXXX";
    const std::string top = mkdtemp(tmpl);
    arb::box::Library lib;
    std::vector<std::string> dirs;
    for (int r = 0; r < 40; ++r) {
        const std::string root = top + "/r" + std::to_string(r);
        const json m = {{"format", 1}, {"kind", "engine"}, {"id", "t/same"},
                        {"version", (r % 2) ? "0.9.0" : "1.0.0"}};
        dirs.push_back(box_with(root, "t/same", m));
        lib.add_root(root);
    }
    lib.rescan();
    const arb::box::Entry* e = lib.find("t/same");
    CHECK(e && e->dir == dirs[0], "the first root's 1.0.0 must win, got %s",
          e ? e->dir.c_str() : "(none)");
    std::vector<std::string> order;
    for (const arb::box::Entry& x : lib.all()) order.push_back(x.dir);
    bool kept = order.size() == 40;
    for (std::size_t k = 0; kept && k < 20; ++k)
        kept = order[k] == dirs[2 * k] && order[20 + k] == dirs[2 * k + 1];
    CHECK(kept, "equal versions must stay in root order (%zu entries)", order.size());
    std::filesystem::remove_all(top);
}

// D106, D125 (#839): the card is the one root and the slot's stock boxes are
// its fallback. The card's copy wins whatever its version; the slot's copy is
// offered only for an id the card's stock record does not name, so one the
// owner uninstalled (recorded, then missing) never comes back from the slot.
void test_card_first_slot_fallback() {
    char tmpl[] = "/tmp/arbutus-fallback-XXXXXX";
    const std::string top = mkdtemp(tmpl);
    const std::string slot = top + "/slot", card = top + "/data";
    auto m = [](const char* id, const char* v, json caps, bool per_note = false) {
        json j = {{"format", 1}, {"kind", "engine"}, {"id", id}, {"version", v}, {"caps", caps}};
        if (per_note) j["params"] = json::array({json{{"id", "p"}, {"per_note", true}}});
        return j;
    };
    box_with(slot, "t/kept", m("t/kept", "2.0.0", {"audio_out", "events_in"}));
    box_with(slot, "t/gone", m("t/gone", "1.0.0", {"audio_out", "events_in"}, true));
    box_with(slot, "t/new", m("t/new", "1.0.0", {"audio_in", "audio_out"}));
    box_with(slot, "t/dropped", m("t/dropped", "1.0.0", {"events_in", "events_out", "sequencer"}));
    const std::string kept = box_with(card + "/engines", "t/kept",
                                      m("t/kept", "1.0.0", {"audio_out", "events_in"}));

    arb::box::Library lib;
    lib.add_root(card);
    lib.add_fallback(slot);
    lib.rescan();
    // No record: the card is away, or the first boot has not seeded it.
    const arb::box::Entry* e = lib.find("t/kept");
    CHECK(e && e->dir == kept, "the card's 1.0.0 must win over the slot's 2.0.0, got %s",
          e ? e->dir.c_str() : "(none)");
    CHECK(lib.has("t/gone") && lib.has("t/new"), "with no record the slot's boxes are offered");
    CHECK(lib.roots().size() == 1 && lib.roots().back() == card, "a fallback is not a root");

    std::ofstream(card + "/" + arb::box::kStockRecord)
        << json{{"installed", {{"t/kept", "x"}, {"t/gone", "y"}}}, {"removed", {"t/dropped"}}}.dump();
    lib.rescan();
    CHECK(!lib.has("t/gone"), "a recorded box missing on the card was uninstalled: not from the slot");
    CHECK(!lib.has("t/dropped"), "a removed box never comes back from the slot");
    CHECK(lib.has("t/new"), "a slot box the record never named is still offered");
    CHECK(lib.find("t/kept") && lib.find("t/kept")->dir == kept, "the card's copy still wins");
    CHECK(lib.all().size() == 2, "kept and new only, got %zu", lib.all().size());

    CHECK(lib.find("t/kept")->kind == "synth", "audio_out without per_note is a synth");
    CHECK(lib.find("t/new")->kind == "effect", "audio_in is an effect");
    CHECK(arb::box::kind_of(m("a/b", "1", {"audio_out", "events_in"}, true)) == "drum", "per_note is a drum");
    CHECK(arb::box::kind_of(m("a/b", "1", {"events_in", "events_out", "sequencer"})) == "sequencer",
          "the sequencer cap");
    CHECK(arb::box::kind_of(m("a/b", "1", {"modulator"})) == "modulator", "a modulator");
    std::filesystem::remove_all(top);
}

// #786: the manifest's own fields, before any binary is opened.
void test_open_refuses_bad_header() {
    char tmpl[] = "/tmp/arbutus-manifest-XXXXXX";
    const std::string root = mkdtemp(tmpl);
    const json good = arb::manifest::from_desc(pads(), ARB_HOST_ARCH, "missing.so");
    auto code = [&](const char* id, const json& m) {
        arb::box::Box b;
        return arb::box::open(box_with(root, id, m), b).code;
    };
    // The control: a header that is right gets as far as the missing binary.
    CHECK(code("t/good", good) == "NO BUILD FOR THIS DEVICE", "good header: %s",
          code("t/good", good).c_str());
    { json m = good; m["format"] = 1.5;
      CHECK(code("t/f15", m) == "ENGINE BROKEN", "format 1.5: %s", code("t/f15", m).c_str()); }
    { json m = good; m["format"] = 1.0;
      CHECK(code("t/f10", m) == "NO BUILD FOR THIS DEVICE", "format 1.0: %s", code("t/f10", m).c_str()); }
    { json m = good; m["format"] = 4294967297.0;
      CHECK(code("t/fbig", m) == "ENGINE BROKEN", "format 2^32+1: %s", code("t/fbig", m).c_str()); }
    { json m = good; m["abi"] = ARB_ABI + 0.5;
      CHECK(code("t/a25", m) == "ENGINE BROKEN", "abi +0.5: %s", code("t/a25", m).c_str()); }
    { json m = good; m["abi"] = -1;
      CHECK(code("t/aneg", m) == "ENGINE BROKEN", "abi -1: %s", code("t/aneg", m).c_str()); }

    // Row 1: a box with a string id is indexed whatever its kind, so loading
    // it says ENGINE BROKEN rather than NEEDS ENGINE.
    { json m = good; m.erase("kind"); m["id"] = "t/nokind"; box_with(root, "t/nokind", m); }
    { json m = good; m["kind"] = 7; m["id"] = "t/numkind"; box_with(root, "t/numkind", m); }
    arb::box::Library lib;
    lib.add_root(root);
    lib.rescan();
    CHECK(lib.has("t/nokind"), "a box with no kind is not indexed");
    CHECK(lib.has("t/numkind"), "a box with a numeric kind is not indexed");
    CHECK(code("t/nokind", json::parse(std::ifstream(root + "/t/nokind/engine.json"))) ==
              "ENGINE BROKEN", "no kind is not ENGINE BROKEN");
    std::filesystem::remove_all(root);
}

void test_loader_check() {
    const arb_vtable full = full_vtable();
    CHECK(arb::box::check(modulator(), full).ok(), "a well-formed modulator loads");
    CHECK(arb::box::check(pads(), full).ok(), "per_note + per_voice with both entries loads");

    auto broken = [](const arb_engine_desc& d, const arb_vtable& vt, const char* what) {
        const arb::box::Refusal r = arb::box::check(d, vt);
        CHECK(r.code == "ENGINE BROKEN", "%s: got \"%s\"", what, r.code.c_str());
    };
    // #673: the host calls these without a NULL check, so a box missing one
    // loaded and then segfaulted the audio process.
    { arb_vtable vt = full; vt.reset = nullptr; broken(pads(), vt, "no reset"); }
    { arb_vtable vt = full; vt.set_param = nullptr; broken(pads(), vt, "no set_param"); }
    { arb_vtable vt = full; vt.get_param = nullptr; broken(pads(), vt, "no get_param"); }
    { arb_vtable vt = full; vt.set_note_param = nullptr; broken(pads(), vt, "per_note, no set_note_param"); }
    { arb_vtable vt = full; vt.set_voice_mod = nullptr; broken(pads(), vt, "per_voice, no set_voice_mod"); }
    { arb_engine_desc d = modulator(); d.num_outputs = 0; broken(d, full, "modulator without outputs"); }
    { arb_engine_desc d = modulator(); d.num_outputs = 5; broken(d, full, "num_outputs > 4"); }
    { arb_engine_desc d = modulator(); d.caps |= ARB_CAP_AUDIO_OUT; broken(d, full, "modulator with audio"); }
    { arb_engine_desc d = pads(); arb::set_outputs(d, {arb::output("x", "X", true)});
      broken(d, full, "outputs without the modulator cap"); }
    { arb_engine_desc d = pads(); arb::set_assets(d, {"sample"}); broken(d, full, "assets, no load_asset"); }
}

}  // namespace

int main() {
    test_builders_set_the_flags_and_outputs();
    test_round_trip();
    test_drift_in_a_new_field_is_refused();
    test_loader_check();
    test_open_refuses_bad_header();
    test_equal_versions_keep_the_root_order();
    test_card_first_slot_fallback();
    std::printf("%s\n", failures ? "manifest: FAILED" : "manifest: all checks passed");
    return failures ? 1 : 0;
}
