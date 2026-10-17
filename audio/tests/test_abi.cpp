// The ABI 2 layout, pinned: C and C++ must agree on every size and offset in
// abi_layout.h, and on LP64 both must match the numbers written there.
#include <cstddef>
#include <cstdio>
#include <string_view>

#include "abi_layout.h"

extern "C" std::size_t arb_layout_count_from_c(void);
extern "C" std::size_t arb_layout_from_c(std::size_t i);

namespace {

struct Row { const char* label; std::size_t cpp; std::size_t lp64; };

#define ARB_AS_ROW(label, expr, lp64) Row{label, std::size_t(expr), std::size_t(lp64)},
const Row kRows[] = { ARB_LAYOUT(ARB_AS_ROW) };

}  // namespace

int main() {
    const std::size_t n = sizeof kRows / sizeof kRows[0];
    int bad = 0;
    if (arb_layout_count_from_c() != n) {
        std::printf("FAIL C sees %zu rows, C++ %zu\n", arb_layout_count_from_c(), n);
        return 1;
    }
    const bool lp64 = sizeof(void*) == 8 && sizeof(long) == 8;
    for (std::size_t i = 0; i < n; ++i) {
        const Row& r = kRows[i];
        const std::size_t c = arb_layout_from_c(i);
        if (c != r.cpp) {
            std::printf("FAIL %s: C %zu, C++ %zu\n", r.label, c, r.cpp);
            ++bad;
        }
        // The pinned numbers are the LP64 layout; a 32-bit build still has to
        // agree with itself across languages, which the check above covers.
        if (lp64 && r.cpp != r.lp64) {
            std::printf("FAIL %s: %zu, the header promises %zu\n", r.label, r.cpp, r.lp64);
            ++bad;
        }
    }
    if (ARB_ABI != 2) { std::printf("FAIL ARB_ABI is %d\n", ARB_ABI); ++bad; }
    const char* caps[ARB_CAP_COUNT] = ARB_CAP_NAMES;
    if (ARB_CAP_MODULATOR != (1u << (ARB_CAP_COUNT - 1)) ||
        std::string_view(caps[ARB_CAP_COUNT - 1]) != "modulator") {
        std::printf("FAIL ARB_CAP_NAMES does not end in \"modulator\" at bit 6\n");
        ++bad;
    }
    // ARB_EV_BEND (7) was removed before release: bend is arb_host.pitch_st.
    if (ARB_EV_SOFTKEY != 5 || ARB_EV_CC != 6) {
        std::printf("FAIL event values moved: an append must not renumber\n");
        ++bad;
    }
    std::printf("%s (%zu layout rows)\n",
                bad ? "abi: FAILED" : "abi: C, C++ and the pinned layout agree", n);
    return bad ? 1 : 0;
}
