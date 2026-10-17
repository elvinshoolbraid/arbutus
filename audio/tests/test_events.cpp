// host/events.hpp — the audio thread's event sort: stable, in order, and it
// allocates nothing. No framework (test_params.cpp is the pattern).
//
// The allocation check replaces the global operator new in this binary and
// counts calls: `sort_events_by_frame` over a full clock list must make none.
// That is what `--rt-guard` cannot prove -- the guard is a net-growth alarm
// (#52) and a sort's merge buffer is freed before the block ends -- and why
// audit Q1 found `std::stable_sort` on the audio thread (audit-2026-09-18.md).

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <vector>

#include <arbutus_engine.h>
#include <events.hpp>

namespace {

std::atomic<long> g_news{0};

int failures = 0;

#define CHECK(cond, ...)                                                        \
    do {                                                                        \
        if (!(cond)) {                                                          \
            std::printf("FAIL %s:%d  " #cond "\n      ", __FILE__, __LINE__);   \
            std::printf(__VA_ARGS__);                                           \
            std::printf("\n");                                                  \
            ++failures;                                                         \
        }                                                                       \
    } while (0)

arb_event ev(uint32_t frame, uint16_t type, uint16_t a) {
    arb_event e{};
    e.frame = frame;
    e.type = type;
    e.a = a;
    return e;
}

bool sorted_by_frame(const std::vector<arb_event>& v) {
    for (std::size_t i = 1; i < v.size(); ++i)
        if (v[i].frame < v[i - 1].frame) return false;
    return true;
}

void test_orders_and_keeps_ties_in_arrival_order() {
    // A transport event at frame 0 arrives before the clock tick that shares
    // its frame; both must stay that way round (stability), and everything
    // else must end up by frame.
    std::vector<arb_event> v = {
        ev(0, ARB_EV_TRANSPORT, 1), ev(96, ARB_EV_CLOCK, 3), ev(0, ARB_EV_CLOCK, 0),
        ev(32, ARB_EV_CLOCK, 1),    ev(64, ARB_EV_CLOCK, 2), ev(0, ARB_EV_NOTE_ON, 60),
    };
    arb::host::sort_events_by_frame(v.data(), v.data() + v.size());
    CHECK(sorted_by_frame(v), "not in frame order");
    CHECK(v[0].type == ARB_EV_TRANSPORT, "frame-0 transport must stay first, got type %u", v[0].type);
    CHECK(v[1].type == ARB_EV_CLOCK && v[1].a == 0, "frame-0 clock must stay second");
    CHECK(v[2].type == ARB_EV_NOTE_ON, "frame-0 note must stay third");
    CHECK(v[3].a == 1 && v[4].a == 2 && v[5].a == 3, "ticks out of order: %u %u %u", v[3].a, v[4].a, v[5].a);
}

void test_agrees_with_stable_sort_on_random_input() {
    std::vector<arb_event> a;
    uint32_t x = 12345u;
    for (uint16_t i = 0; i < 400; ++i) {
        x = x * 1664525u + 1013904223u;
        a.push_back(ev((x >> 8) % 128u, ARB_EV_CLOCK, i));   // `a` = arrival order
    }
    std::vector<arb_event> b = a;
    arb::host::sort_events_by_frame(a.data(), a.data() + a.size());
    std::stable_sort(b.begin(), b.end(),
                     [](const arb_event& p, const arb_event& q) { return p.frame < q.frame; });
    bool same = a.size() == b.size();
    for (std::size_t i = 0; same && i < a.size(); ++i)
        same = a[i].frame == b[i].frame && a[i].a == b[i].a;
    CHECK(same, "insertion sort and std::stable_sort disagree");
}

void test_empty_and_single_are_no_ops() {
    std::vector<arb_event> v;
    v.reserve(4);
    arb::host::sort_events_by_frame(v.data(), v.data());          // empty
    v.push_back(ev(7, ARB_EV_CLOCK, 0));
    arb::host::sort_events_by_frame(v.data(), v.data() + 1);      // one
    CHECK(v[0].frame == 7, "single element must be untouched");
}

void test_sorting_a_full_clock_list_allocates_nothing() {
    // 1024 is the host's reserve for ev_clock_ (audio_host.cpp init): more
    // ticks than any block can carry, in the order the host generates them
    // plus a handful out of place.
    std::vector<arb_event> v;
    for (uint16_t i = 0; i < 1024; ++i) v.push_back(ev(uint32_t(i) * 2u, ARB_EV_CLOCK, i));
    std::swap(v[10], v[900]);
    std::swap(v[3], v[4]);
    const long before = g_news.load();
    arb::host::sort_events_by_frame(v.data(), v.data() + v.size());
    const long after = g_news.load();
    CHECK(after == before, "sort_events_by_frame called operator new %ld time(s)", after - before);
    CHECK(sorted_by_frame(v), "not in frame order after the allocation check");
}

}  // namespace

// Count every heap block this binary asks for. Replacing the throwing form is
// enough on both libstdc++ and libc++: their nothrow and array forms forward
// to it, and so does std::get_temporary_buffer, which is how std::stable_sort
// gets its merge buffer -- the allocation this test exists to keep off the
// audio thread. noinline: GCC 14 inlines the replacement delete into
// std::allocator and then reports its free() as mismatched with the (opaque)
// operator new -- a -Wmismatched-new-delete false positive, found on the CM4.
[[gnu::noinline]] void* operator new(std::size_t n) {
    g_news.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

int main() {
    test_orders_and_keeps_ties_in_arrival_order();
    test_agrees_with_stable_sort_on_random_input();
    test_empty_and_single_are_no_ops();
    test_sorting_a_full_clock_list_allocates_nothing();
    if (failures) {
        std::printf("test_events: %d failure(s)\n", failures);
        return 1;
    }
    std::printf("test_events: ok\n");
    return 0;
}
