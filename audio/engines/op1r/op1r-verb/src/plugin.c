/* op1r's reverb, the file itself, unmodified (audio/third_party/op1r-src). Its
 * last line leaves op1_fx_t's `press` to zero-initialise, which -Wextra names;
 * the warning is silenced for this one include, not for the box. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
#include "plugins/reverb/reverb.c"
#pragma GCC diagnostic pop
