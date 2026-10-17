/* The header has to be C, not "C-ish": this file is compiled as C99 and its
 * struct layouts are compared against the C++ compiler's in test_abi.cpp. A
 * host and a box may be built by different languages but never disagree about
 * the bytes. */
#include "abi_layout.h"

#define ARB_AS_VALUE(label, expr, lp64) (size_t)(expr),

static const size_t kLayout[] = { ARB_LAYOUT(ARB_AS_VALUE) };

size_t arb_layout_count_from_c(void) { return sizeof kLayout / sizeof kLayout[0]; }
size_t arb_layout_from_c(size_t i) { return kLayout[i]; }
