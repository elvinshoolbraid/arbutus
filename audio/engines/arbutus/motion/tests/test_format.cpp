// SHK-SENS and SPN-SENS read as the full scale they set (README.md).

#include <cstdio>
#include <cstring>

#include <box.hpp>

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "usage: test_motion_format <box dir>\n"); return 2; }
    arb::box::Box box;
    if (arb::box::Refusal r = arb::box::open(argv[1], box); !r.ok()) {
        std::fprintf(stderr, "%s: %s\n", r.code.c_str(), r.detail.c_str());
        return 1;
    }
    const struct { unsigned idx; float n; const char* want; } table[] = {
        {1, 0.0f, "4.0 g"}, {1, 0.5f, "0.50 g"}, {1, 1.0f, "0.06 g"},
        {2, 0.0f, "2000 dps"}, {2, 0.5f, "250 dps"}, {2, 1.0f, "31 dps"},
    };
    int bad = 0;
    for (const auto& row : table) {
        char buf[32] = {};
        const int ok = box.vt->format_param(nullptr, row.idx, row.n, buf, sizeof buf);
        const bool pass = ok && std::strcmp(buf, row.want) == 0;
        std::printf("%s  %u %.2f -> %-9s (want %s)\n", pass ? "ok  " : "FAIL", row.idx, double(row.n), buf, row.want);
        if (!pass) ++bad;
    }
    char buf[32] = {};
    if (box.vt->format_param(nullptr, 0, 0.5f, buf, sizeof buf) != 0) {
        std::printf("FAIL  SMOOTH should use the host default\n");
        ++bad;
    }
    arb::box::close(box);
    std::printf("%s\n", bad ? "FAIL" : "PASS");
    return bad ? 1 : 0;
}
