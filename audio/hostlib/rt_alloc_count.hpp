// rt_alloc_count — the one implementation of `--rt-guard`'s heap counter (#52).
//
// `live_blocks()` is the smoke alarm behind `--rt-guard`: sample it either side
// of one `process()` / `render_block()` call and a rise means the box allocated
// on the audio thread, which engine-spec §3.7.1 forbids. It used to live twice,
// once in `arbutus-run/main.cpp` and once in `host/audio_host.cpp`, and counted
// only on macOS; the CM4 is Linux, so P1 would have benched every box with the
// guard switched off (#52).
//
// Semantics are macOS's, everywhere: **live blocks, process-wide, sampled
// before and after the block**. A malloc/free pair inside the block nets to
// zero and is not caught — the guard is a net-growth alarm, not an allocation
// tracer. The count is only meaningful when nothing else can allocate between
// the two samples, which is why `--rt-guard` is an offline flag.
//
// | platform      | how                                                     |
// |---------------|---------------------------------------------------------|
// | macOS         | `malloc_zone_statistics(malloc_default_zone()).blocks_in_use` |
// | Linux / glibc | this build's own counting allocator (`rt_alloc_hooks.c`)  |
// | anything else | `-1` — "the platform cannot count"                       |
//
// The glibc counter has to be armed first: `enable_alloc_counting()` is called
// once, from `main()`, only when `--rt-guard` was passed. Until it is,
// `live_blocks()` returns `-1` on Linux and the callers print
// "rt-guard: unavailable on this platform", exactly as before.

#pragma once

namespace arb::rt {

// Arm the glibc counting allocator. Call once from main(), before any render
// thread exists, and only under `--rt-guard`. No-op on macOS (the zone is
// always counting) and on platforms that cannot count.
void enable_alloc_counting();

// Live heap blocks, or -1 when this platform/build cannot count them. The
// absolute value carries an arbitrary offset on Linux (see rt_alloc_hooks.c) —
// only differences between two samples mean anything.
long live_blocks();

}  // namespace arb::rt
