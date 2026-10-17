// STEPS' readout through the box as the host loads it (§15.1). STEPS 0 is AUTO
// and only the engine knows what AUTO resolves to, so the string carries the
// length: a fresh engine says "AUTO 0" and one with a four-step pattern loaded
// says "AUTO 4". Every other slot uses the host's default readout.

#include <cstdio>
#include <cstring>

#include <box.hpp>
#include <params.hpp>

namespace {

int check(const char* what, const char* got, const char* want) {
    const bool pass = std::strcmp(got, want) == 0;
    std::printf("%s  %-18s -> %-10s (want %s)\n", pass ? "ok  " : "FAIL", what, got, want);
    return pass ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "usage: test_pattern_format <box dir>\n"); return 2; }
    arb::box::Box box;
    if (arb::box::Refusal r = arb::box::open(argv[1], box); !r.ok()) {
        std::fprintf(stderr, "%s: %s\n", r.code.c_str(), r.detail.c_str());
        return 1;
    }
    const arb_param_desc& steps = box.desc->params[1];
    arb_host host{};
    host.sample_rate = 48000;
    host.max_block = 512;
    host.tempo_bpm = 120.0;
    void* h = box.vt->create(&host, argv[1]);
    if (!h) { std::fprintf(stderr, "create failed\n"); return 1; }

    int bad = 0;
    char buf[32] = {};
    box.vt->format_param(h, 1, arb::norm(steps, 0.0f), buf, sizeof buf);
    bad += check("STEPS 0, empty", buf, "AUTO 0");
    box.vt->format_param(h, 1, arb::norm(steps, 16.0f), buf, sizeof buf);
    bad += check("STEPS 16", buf, "16");
    box.vt->format_param(h, 1, arb::norm(steps, 32.0f), buf, sizeof buf);
    bad += check("STEPS 32", buf, "32");

    // The four-step asset: AUTO now means four.
    std::string asset = std::string(argv[1]) + "/../../../../engines/arbutus/pattern/tests/pattern.json";
    if (argc >= 3) asset = argv[2];
    if (box.vt->load_asset(h, "pattern", asset.c_str()) != 0) {
        std::printf("FAIL  load_asset refused %s\n", asset.c_str());
        ++bad;
    } else {
        box.vt->format_param(h, 1, arb::norm(steps, 0.0f), buf, sizeof buf);
        bad += check("STEPS 0, 4 loaded", buf, "AUTO 4");
    }

    // Every other slot: the host's default readout.
    if (box.vt->format_param(h, 0, 0.5f, buf, sizeof buf) != 0) {
        std::printf("FAIL  DIV should use the host default\n");
        ++bad;
    }
    box.vt->destroy(h);
    arb::box::close(box);
    std::printf("%s\n", bad ? "FAIL" : "PASS");
    return bad ? 1 : 0;
}
