// RATE's readout (modulation-spec §3.3) through the box as the host loads it:
// the table a player reads off the knob, pinned. Values are RATE in units
// (-1..1); the host passes them normalised, as it does from `state`.

#include <cstdio>
#include <cstring>

#include <box.hpp>
#include <params.hpp>

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "usage: test_lfo_format <box dir>\n"); return 2; }
    arb::box::Box box;
    if (arb::box::Refusal r = arb::box::open(argv[1], box); !r.ok()) {
        std::fprintf(stderr, "%s: %s\n", r.code.c_str(), r.detail.c_str());
        return 1;
    }
    const struct { float x; const char* want; } table[] = {
        {0.0f, "STOP"},     {0.015f, "STOP"},   {-0.015f, "STOP"},
        // right: 14 divisions, slowest beside STOP
        {0.02f, "8 BAR"},   {0.10f, "4 BAR"},   {0.16f, "2 BAR"},  {0.25f, "1 BAR"},
        {0.32f, "1/2"},     {0.40f, "1/2T"},    {0.47f, "1/4"},    {0.62f, "1/8"},
        {0.85f, "1/16T"},   {0.93f, "1/32"},    {1.0f, "1/32T"},
        // left: 0.05 .. 50 Hz, log
        {-0.02f, "0.05 Hz"}, {-0.5f, "1.5 Hz"}, {-0.6667f, "4.8 Hz"}, {-0.85f, "17 Hz"},
        {-1.0f, "50 Hz"},
    };
    const arb_param_desc& rate = box.desc->params[0];
    int bad = 0;
    for (const auto& row : table) {
        char buf[32] = {};
        const float n = arb::norm(rate, row.x);
        const int ok = box.vt->format_param(nullptr, 0, n, buf, sizeof buf);
        const bool pass = ok && std::strcmp(buf, row.want) == 0;
        std::printf("%s  RATE %+.4f -> %-8s (want %s)\n", pass ? "ok  " : "FAIL", double(row.x), buf, row.want);
        if (!pass) ++bad;
    }
    // Only RATE has a readout; the rest are the host's default.
    char buf[32] = {};
    if (box.vt->format_param(nullptr, 1, 0.5f, buf, sizeof buf) != 0) {
        std::printf("FAIL  MORPH should use the host default\n");
        ++bad;
    }
    arb::box::close(box);
    std::printf("%s\n", bad ? "FAIL" : "PASS");
    return bad ? 1 : 0;
}
