// arb::Smoothed (params.hpp) — realtime rule 7, §15.7. No framework.

#include <cmath>
#include <cstdio>
#include <type_traits>

#include <arbutus_engine.h>
#include <desc.hpp>
#include <params.hpp>

namespace {

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

arb_host host(uint32_t sr, uint32_t max_block) {
    arb_host h{};
    h.sample_rate = sr;
    h.max_block = max_block;
    return h;
}

void test_ramp_is_one_block_unless_asked_for_more() {
    arb::Smoothed<float> s;
    s.prepare(host(48000, 128));
    CHECK(s.ramp_frames() == 128, "default is one block, got %u", s.ramp_frames());
    s.prepare(host(48000, 128), 10.0f);
    CHECK(s.ramp_frames() == 480, "10 ms at 128-frame blocks, got %u", s.ramp_frames());
    s.prepare(host(48000, 2048), 10.0f);
    CHECK(s.ramp_frames() == 2048, "never shorter than a block, got %u", s.ramp_frames());
    arb::Smoothed<float> unprepared;
    CHECK(unprepared.ramp_frames() >= ARB_MAX_BLOCK, "unprepared must still be >= any block");
}

void test_sets_before_the_first_frame_land_immediately() {
    // A patch load sets every knob before the first block; those values are
    // where the sound starts, so nothing may glide in from the default.
    arb::Smoothed<float> s(0.7f);
    s.prepare(host(48000, 128));
    s.set(0.2f);
    CHECK(s.value() == 0.2f && s.settled(), "value %f", double(s.value()));
    CHECK(s.next() == 0.2f, "first frame is the set value");
}

void test_a_change_glides_linearly_and_arrives_exactly() {
    arb::Smoothed<float> s(0.0f);
    s.set_ramp_frames(100);
    s.next();                                  // running: sets now glide
    s.set(1.0f);
    float prev = s.value();
    bool monotonic = true;
    uint32_t frames = 0;
    while (!s.settled()) {
        const float v = s.next();
        if (v < prev) monotonic = false;
        prev = v;
        ++frames;
    }
    CHECK(frames == 100, "arrives in exactly the ramp, took %u frames", frames);
    CHECK(monotonic, "a rising glide never dips");
    CHECK(s.value() == 1.0f, "arrives exactly, is %.9f", double(s.value()));
    CHECK(s.next() == 1.0f && s.next() == 1.0f, "rests exactly on the target");

    arb::Smoothed<float> h(0.0f);
    h.set_ramp_frames(100);
    h.next();
    h.set(1.0f);
    float mid = 0.0f;
    for (int i = 0; i < 50; ++i) mid = h.next();
    CHECK(std::abs(mid - 0.5f) < 1e-5f, "linear: half way at half the ramp, is %f", double(mid));
}

void test_retarget_mid_glide_continues_from_where_it_is() {
    arb::Smoothed<float> s(0.0f);
    s.set_ramp_frames(100);
    s.next();
    s.set(1.0f);
    for (int i = 0; i < 40; ++i) s.next();
    const float at = s.value();
    s.set(1.0f);                               // a macro re-sending the same value
    CHECK(s.next() > at, "re-sending the target must not restart the glide");
    const float before = s.value();
    s.set(0.0f);
    const float after = s.next();
    CHECK(std::abs(after - before) < 0.02f, "no jump on retarget: %f -> %f",
          double(before), double(after));
    uint32_t frames = 1;
    while (!s.settled()) { s.next(); ++frames; }
    CHECK(frames == 100 && s.value() == 0.0f, "a new target gets a full ramp, %u", frames);
}

void test_advance_matches_next() {
    arb::Smoothed<float> a(0.0f), b(0.0f);
    a.set_ramp_frames(300);
    b.set_ramp_frames(300);
    a.next();
    b.next();
    a.set(2.0f);
    b.set(2.0f);
    for (int i = 0; i < 128; ++i) a.next();
    b.advance(128);
    CHECK(std::abs(a.value() - b.value()) < 1e-5f, "advance(128) %f vs 128 x next %f",
          double(b.value()), double(a.value()));
    b.advance(1000);
    CHECK(b.settled() && b.value() == 2.0f, "advance past the end lands exactly");
}

void test_reset_ends_the_glide_and_rearms_snapping() {
    arb::Smoothed<float> s(0.0f);
    s.set_ramp_frames(100);
    s.next();
    s.set(1.0f);
    s.next();
    s.reset();
    CHECK(s.settled() && s.value() == 1.0f, "reset lands on the target (§3.7.5)");
    s.set(0.25f);
    CHECK(s.value() == 0.25f, "after reset, a set before the next frame snaps");
}

void test_block_rate_targets_become_straight_lines() {
    // What a route does (modulation-spec §7): a new target every block. With a
    // one-block ramp each block ends exactly on its target, so the staircase
    // is joined into lines and nothing is attenuated.
    arb::Smoothed<float> s(0.0f);
    s.prepare(host(48000, 128));
    s.next();
    const float targets[] = {0.25f, 0.75f, 0.5f, 1.0f};
    for (float t : targets) {
        s.set(t);
        for (int i = 0; i < 127; ++i) s.next();
        CHECK(s.next() == t, "block ends on its target %f, is %f", double(t), double(s.value()));
    }
}

void test_no_allocation_by_construction() {
    // §3.7.1: a Smoothed lives in the engine struct and is copied by value.
    CHECK(std::is_trivially_copyable_v<arb::Smoothed<float>>, "must be plain data");
    CHECK(sizeof(arb::Smoothed<float>) <= 24, "is %zu bytes", sizeof(arb::Smoothed<float>));
}

void test_integer_and_enum_round_to_the_nearest_index() {
    // #675 (review m-audio-sdk row 9): every ENUM and INTEGER knob relies on
    // denorm() rounding, and denorm_int() truncates what it is given, so
    // without the round a position a hair under an index plays the one below.
    // Each index is checked at its own normalised value and at a third of a
    // step either side of it.
    static const char* const kLabels[] = {"A", "B", "C", "D", "E", "F", "G"};
    const arb_param_desc e = arb::param("e", "E", ARB_PARAM_ENUM, ARB_CURVE_LIN, 0.0f, 6.0f, 0.0f,
                                        1.0f, "", kLabels);
    const arb_param_desc i = arb::param("i", "I", ARB_PARAM_INTEGER, ARB_CURVE_LIN, -12.0f, 12.0f,
                                        0.0f, 1.0f, "st");
    for (const arb_param_desc* p : {&e, &i}) {
        const int lo = int(p->min), hi = int(p->max);
        const float step = 1.0f / float(hi - lo);
        for (int k = lo; k <= hi; ++k) {
            const float n = arb::norm(*p, float(k));
            CHECK(arb::denorm(*p, n) == float(k), "%s %d: denorm(norm) is %f", p->id, k,
                  double(arb::denorm(*p, n)));
            CHECK(arb::denorm_int(*p, n) == k, "%s %d: denorm_int(norm) is %d", p->id, k,
                  arb::denorm_int(*p, n));
            if (k > lo) CHECK(arb::denorm_int(*p, n - step / 3.0f) == k, "%s %d: a third of a step under is %d",
                              p->id, k, arb::denorm_int(*p, n - step / 3.0f));
            if (k < hi) CHECK(arb::denorm_int(*p, n + step / 3.0f) == k, "%s %d: a third of a step over is %d",
                              p->id, k, arb::denorm_int(*p, n + step / 3.0f));
        }
    }
}

}  // namespace

int main() {
    test_ramp_is_one_block_unless_asked_for_more();
    test_sets_before_the_first_frame_land_immediately();
    test_a_change_glides_linearly_and_arrives_exactly();
    test_retarget_mid_glide_continues_from_where_it_is();
    test_advance_matches_next();
    test_reset_ends_the_glide_and_rearms_snapping();
    test_block_rate_targets_become_straight_lines();
    test_no_allocation_by_construction();
    test_integer_and_enum_round_to_the_nearest_index();
    std::printf("%s\n", failures ? "params: FAILED" : "params: all checks passed");
    return failures ? 1 : 0;
}
