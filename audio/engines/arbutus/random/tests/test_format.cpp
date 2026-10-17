// RATE and SEED readouts through the box as the host loads it. RATE is the
// same table as arbutus/lfo's (the helper is a copy and must stay one).

#include <cstdio>
#include <cstring>

#include <box.hpp>
#include <params.hpp>

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "usage: test_random_format <box dir>\n"); return 2; }
    arb::box::Box box;
    if (arb::box::Refusal r = arb::box::open(argv[1], box); !r.ok()) {
        std::fprintf(stderr, "%s: %s\n", r.code.c_str(), r.detail.c_str());
        return 1;
    }
    const struct { unsigned idx; float x; const char* want; } table[] = {
        {0, 0.0f, "STOP"},    {0, 0.015f, "STOP"},   {0, 0.02f, "8 BAR"},  {0, 0.47f, "1/4"},
        {0, 0.62f, "1/8"},    {0, 0.7738f, "1/16"},  {0, 1.0f, "1/32T"},   {0, -0.02f, "0.05 Hz"},
        {0, -0.6667f, "4.8 Hz"}, {0, -1.0f, "50 Hz"},
        {3, 0.0f, "HOST"},    {3, 1.0f, "1"},        {3, 42.0f, "42"},     {3, 999.0f, "999"},
    };
    int bad = 0;
    for (const auto& row : table) {
        char buf[32] = {};
        const float n = arb::norm(box.desc->params[row.idx], row.x);
        const int ok = box.vt->format_param(nullptr, row.idx, n, buf, sizeof buf);
        const bool pass = ok && std::strcmp(buf, row.want) == 0;
        std::printf("%s  %u %+.4f -> %-8s (want %s)\n", pass ? "ok  " : "FAIL", row.idx, double(row.x), buf, row.want);
        if (!pass) ++bad;
    }
    char buf[32] = {};
    if (box.vt->format_param(nullptr, 1, 0.5f, buf, sizeof buf) != 0) {
        std::printf("FAIL  SLEW should use the host default\n");
        ++bad;
    }
    arb::box::close(box);
    std::printf("%s\n", bad ? "FAIL" : "PASS");
    return bad ? 1 : 0;
}
