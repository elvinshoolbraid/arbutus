// events.hpp — ordering events on the audio thread without allocating.
//
// `AudioHost::build_events` has to hand every engine its events in frame
// order (`rack.cpp` merge_events assumes it). `std::stable_sort` buys its
// stability with a heap buffer: libstdc++ allocates one for *any* non-empty
// range, libc++ only past ~60 elements — which is why a Mac never saw the
// allocation and a Linux box (the CM4 is one) would pay it on every playing
// block. `--rt-guard` cannot see it either: the buffer is freed before the
// block ends, and the guard is a net-growth alarm by design (#52).
//
// A stable insertion sort allocates nothing (§3.7.1). The host builds its
// clock list in ascending order already, so this is O(n) on what it is
// given and only ever O(n²) on input nobody produces. Found by audit Q1
// (docs/design/audit-2026-09-18.md).
#pragma once

#include <arbutus_engine.h>

namespace arb::host {

inline void sort_events_by_frame(arb_event* first, arb_event* last) {
    if (last - first < 2) return;
    for (arb_event* i = first + 1; i != last; ++i) {
        const arb_event e = *i;
        arb_event* j = i;
        // `<`, not `<=`: an equal frame stays behind what was already there,
        // which is the stability a transport event at frame 0 relies on to
        // reach a sequencer before the clock tick that shares its frame.
        while (j != first && e.frame < (j - 1)->frame) {
            *j = *(j - 1);
            --j;
        }
        *j = e;
    }
}

}  // namespace arb::host
