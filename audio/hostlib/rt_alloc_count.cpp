// The platform switch behind `--rt-guard`'s heap counter. See the header.

#include "rt_alloc_count.hpp"

#if defined(__APPLE__)
#include <malloc/malloc.h>
#endif

extern "C" {
// rt_alloc_hooks.c — always present, but only counts on Linux/glibc.
void arb_rt_alloc_enable(void);
long arb_rt_alloc_live(void);
}

namespace arb::rt {

void enable_alloc_counting() {
#if !defined(__APPLE__)
    arb_rt_alloc_enable();
#endif
}

long live_blocks() {
#if defined(__APPLE__)
    // The zone counts whether or not we asked it to, so there is nothing to arm
    // and the Mac's numbers are exactly what they were before #52.
    malloc_statistics_t st{};
    malloc_zone_statistics(malloc_default_zone(), &st);
    return long(st.blocks_in_use);
#else
    return arb_rt_alloc_live();
#endif
}

}  // namespace arb::rt
