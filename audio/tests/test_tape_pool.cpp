// host/tape.cpp + host/tape_io.cpp — the **bounded** pool: eviction, fetching a
// range back off the card, and the take that ends when a stalled card has
// turned the whole pool into write queue (C1 step 2b,
// tape-streaming-design.md § 3.6, § 3.7, § 4's T1/T2/T4/T5/T7, § 6.2). No
// framework; test_tape.cpp is the pattern and `FakeStore` is the card.
//
// What these prove, and their negative controls (the standing rule since #56 —
// a test that has never been seen to fail is not evidence):
//
//   * **T1** a take three times longer than the pool records and plays back
//     sample-exact, across every range boundary *and* every eviction — with one
//     float flipped in the card's bytes, the same comparison fails exactly once,
//     which is what says playback is really coming off the card
//   * the evictor is shown to have run (`evicted > 0`) and the fetch to have run
//     (`fetched > 0`): without both, T1 would be the unbounded pool passing
//     under a new name. The unbounded pool is checked beside it — same take,
//     `pool_slots = 0`, nothing evicted, nothing fetched
//   * **no queued range is ever evicted**: `absent` — the I/O thread finding a
//     slot gone before it could write it — stays 0 in every case here, and a
//     range whose commit is deferred by a stall is still on the card at the end
//   * **T2** a 2 s `fdatasync` stall inside the pool's cover costs nothing: no
//     `TAPE FULL`, the queue drains afterwards, every range reaches the card —
//     and with the pool cut below the stall's length the same schedule *does*
//     end the take, which is T4
//   * **T4** the take that ends is a take, not a hole: every frame it captured
//     is on the card once the stall clears, and a stall one range shorter ends
//     no take at all
//   * a range pinned by a control-thread reader is not evicted under it
//   * **T7** zero `operator new` on the audio path across a take that crosses a
//     boundary, an eviction, a fetch and a render that goes late — and the
//     counter shown able to see one
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <string>
#include <vector>

#include <tape.hpp>
#include <tape_io.hpp>
#include <tape_store.hpp>

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

using arb::host::Clip;
using arb::host::ClipList;
using arb::host::FakeStore;
using arb::host::RangeCommit;
using arb::host::Tape;
using arb::host::TapeIo;
using arb::host::Travel;

constexpr int64_t kRange = Tape::kRangeFrames;
constexpr int64_t kHeader = TapeIo::kHeaderBytes;
constexpr uint32_t kBlock = 128;                 // blocks per range: 384

float sample(int64_t f, int ch) {
    return std::sin(double(f) * 0.001) * (ch ? -0.5f : 1.0f) + float(ch) * 0.25f;
}

// The host, reduced to the three things this step is about: a tape with a pool,
// a card, and the I/O thread's turn. `turn()` is exactly what `render_offline`
// does after a block — tick the block counter, then one `service()`.
struct Deck {
    Tape       tape;
    FakeStore  store;
    TapeIo     io;

    void init(int64_t ranges, int64_t pool) {
        tape.init(ranges * kRange, pool);
        store.set_clock(&tape.block_counter());   // stalls are counted in blocks
        io.configure(&tape, &store, "/p", 48000);
        CHECK(io.on(), "configure");
    }
    void turn() {
        tape.tick();
        io.service();
    }
    std::string path(int t) const { return io.medium_path(t); }
};

// The audio thread's half of a take plus the two control-thread calls around it
// (`AudioHost`'s `record` handler and `end_take`).
struct Take {
    Deck&   d;
    int     track = 0;
    int64_t medium = 0, len = 0, next_range = 0;
    bool    stalled = false;

    void arm(int64_t head, bool prepare = true) {
        d.tape.ensure(track);
        medium = head;
        len = 0;
        next_range = Tape::range_of(head);
        // What `AudioHost::tape_prepare_head` does: the head's own range comes
        // back off the card before the take writes into it. `prepare = false` is
        // the negative control for exactly that.
        if (prepare && head % kRange) d.io.fetch(track, Tape::range_of(head));
    }
    void block(uint32_t n) {
        std::vector<float> l(n), r(n);
        for (uint32_t i = 0; i < n; ++i) {
            l[i] = sample(medium + len + int64_t(i), 0);
            r[i] = sample(medium + len + int64_t(i), 1);
        }
        const uint32_t got = d.tape.write(track, medium + len, l.data(), r.data(), n);
        len += int64_t(got);
        queue();
        if (got < n && !stalled) {              // the pool had no slot: TAPE FULL
            stalled = true;
            end();
        }
        d.turn();
    }
    void queue() {
        const int64_t complete = Tape::range_of(medium + len);
        while (next_range < complete) {
            RangeCommit c;
            c.track = track;
            c.range = next_range;
            c.frames = kRange;
            if (!d.io.enqueue(c)) break;
            ++next_range;
        }
    }
    void end() {
        queue();
        RangeCommit c;
        c.track = track;
        c.flush = 1;
        c.range = Tape::range_of(medium + len);
        c.frames = medium + len - c.range * kRange;
        d.io.enqueue(c);
        d.tape.set_write_range(track, -1);
    }
    void record(int64_t frames) {
        for (int64_t f = 0; f < frames && !stalled; f += kBlock) block(kBlock);
    }
};

// Record `frames` from frame 0 of track 0, ending the take. Returns the take.
Take take_of(Deck& d, int64_t frames) {
    Take tk{d};
    tk.arm(0);
    tk.record(frames);
    if (!tk.stalled) tk.end();
    for (int i = 0; i < 8; ++i) d.turn();       // let the tail reach the card
    return tk;
}

// Play [0, frames) of track 0 back through one clip and count the frames that
// are not what was recorded. Since step 5 the read-ahead is the I/O thread's
// wanted set, so this plays the two parts `AudioHost` plays for it: the clip
// list goes to `TapeIo::set_clips` beside `swap_clips`, and the playhead is
// published with `Tape::set_view` once a block, which is what `render_block`
// does at its end.
int64_t play_and_compare(Deck& d, int64_t frames, int64_t* late_blocks = nullptr) {
    ClipList cl;
    cl.clips.push_back(Clip{0, frames, 0, 1.0f, -1});
    ClipList* old = d.tape.swap_clips(0, &cl);
    (void)old;
    d.io.set_clips(0, cl.clips);
    d.io.fetch(0, 0);                            // the locate
    std::vector<float> l(kBlock), r(kBlock);
    int64_t bad = 0, late = 0;
    for (int64_t f = 0; f < frames; f += kBlock) {
        const uint32_t n = uint32_t(std::min<int64_t>(kBlock, frames - f));
        Travel tr;
        tr.pos = double(f);
        tr.step = 1.0;
        tr.capacity = d.tape.capacity();
        d.tape.set_view(Tape::View{int64_t(f), 1.0, 0, 0, false, true});
        d.tape.clear_late_mask();
        d.tape.render_track(0, tr, n, l.data(), r.data());
        if (d.tape.late_mask()) ++late;
        for (uint32_t i = 0; i < n; ++i) {
            if (l[i] != sample(f + int64_t(i), 0)) ++bad;
            if (r[i] != sample(f + int64_t(i), 1)) ++bad;
        }
        d.turn();
    }
    d.tape.swap_clips(0, &cl);                   // leave nothing dangling
    d.io.set_clips(0, std::vector<Clip>{});
    d.tape.set_view(Tape::View{});               // and no playhead
    if (late_blocks) *late_blocks = late;
    return bad;
}

// ---- T1: three times the pool, recorded and played back ---------------------

void test_take_longer_than_pool() {
    Deck d;
    d.init(16, 4);                               // 4 slots = 4.096 s resident
    const int64_t frames = 12 * kRange;          // 3x the pool
    Take tk = take_of(d, frames);
    CHECK(!tk.stalled, "the take stalled with a card that never stalls");
    CHECK(tk.len == frames, "recorded %lld of %lld", (long long)tk.len, (long long)frames);

    TapeIo::Stats st = d.io.stats();
    CHECK(st.committed >= 12, "committed %lld", (long long)st.committed);
    CHECK(st.absent == 0, "a queued range was evicted under the I/O thread (%lld)",
          (long long)st.absent);
    CHECK(st.evicted > 0, "nothing was evicted: the pool was not bounded");
    CHECK(d.tape.resident_count() <= 4, "resident %lld over a pool of 4",
          (long long)d.tape.resident_count());

    int64_t late = 0;
    const int64_t bad = play_and_compare(d, frames, &late);
    CHECK(bad == 0, "%lld frames differ", (long long)bad);
    CHECK(late == 0, "%lld blocks played a range the lead should have fetched",
          (long long)late);
    st = d.io.stats();
    CHECK(st.fetched > 0, "nothing was fetched back: playback was all RAM");

    // Negative control: the comparison proven able to fail, and proven to be
    // reading the *card* -- one float changed in a range that cannot still be
    // resident after a take three times the pool's length.
    std::vector<uint8_t>& bytes =
        const_cast<std::vector<uint8_t>&>(d.store.bytes(d.path(0)));
    const std::size_t at = std::size_t(kHeader + 2 * kRange * TapeIo::kFrameBytes + 64);
    CHECK(bytes.size() > at + 4, "the medium is too short to flip a float in");
    if (bytes.size() > at + 4) {
        float v = 0.0f;
        std::memcpy(&v, bytes.data() + at, 4);
        v += 1.0f;
        std::memcpy(bytes.data() + at, &v, 4);
        // A fresh deck over the same card, so nothing of range 2 is in RAM.
        Deck e;
        e.init(16, 4);
        e.store = d.store;
        e.store.set_clock(&e.tape.block_counter());
        e.tape.ensure(0);
        const int64_t bad2 = play_and_compare(e, frames, nullptr);
        CHECK(bad2 == 1, "the flipped float was caught %lld times, want 1",
              (long long)bad2);
    }
}

// The same take with no pool bound at all: the emulator's and the device's tape
// until step 3, and the proof that step 2b changed nothing there.
void test_unbounded_is_unchanged() {
    Deck d;
    d.init(16, 0);
    CHECK(!d.tape.bounded(), "pool_slots 0 must be the unbounded pool");
    const int64_t frames = 12 * kRange;
    Take tk = take_of(d, frames);
    CHECK(!tk.stalled, "the unbounded pool stalled");
    const TapeIo::Stats st = d.io.stats();
    CHECK(st.evicted == 0 && st.fetched == 0, "unbounded evicted %lld / fetched %lld",
          (long long)st.evicted, (long long)st.fetched);
    int64_t late = 0;
    CHECK(play_and_compare(d, frames, &late) == 0, "unbounded playback differs");
    CHECK(late == 0, "unbounded playback went late");
}

// ---- T2 / T4: a card that stops answering -----------------------------------

// Record `ranges` ranges with the card stalled for `stall` blocks from the
// start. Returns the take.
Take stalled_take(Deck& d, int64_t pool, int64_t ranges, int64_t stall) {
    d.init(int64_t(ranges) + 8, pool);
    d.store.stall_blocks(stall);
    Take tk{d};
    tk.arm(0);
    tk.record(ranges * kRange);
    if (!tk.stalled) tk.end();
    for (int i = 0; i < 64; ++i) d.turn();       // the queue drains after the stall
    return tk;
}

// Every frame the take captured, as the card has it. -1 = the file is short.
int64_t frames_on_card(Deck& d, int64_t want) {
    const std::vector<uint8_t>& b = d.store.bytes(d.path(0));
    const int64_t have = (int64_t(b.size()) - kHeader) / TapeIo::kFrameBytes;
    if (have < want) return -1;
    int64_t bad = 0;
    for (int64_t f = 0; f < want; ++f)
        for (int ch = 0; ch < 2; ++ch) {
            float v = 0.0f;
            std::memcpy(&v, b.data() + std::size_t(kHeader + (f * 2 + ch) * 4), 4);
            if (v != sample(f, ch)) ++bad;
        }
    return bad;
}

void test_stall_inside_the_cover() {
    // T2: a 2 s stall (750 blocks of 128 frames) with 8 slots -- 8.192 s of
    // cover -- costs nothing at all.
    Deck d;
    Take tk = stalled_take(d, 8, 6, 750);
    CHECK(!tk.stalled, "a 2 s stall under 8 s of cover ended the take");
    CHECK(tk.len == 6 * kRange, "recorded %lld", (long long)tk.len);
    const TapeIo::Stats st = d.io.stats();
    CHECK(st.queue == 0, "the queue did not drain: %lld left", (long long)st.queue);
    CHECK(st.absent == 0, "a queued range was evicted (%lld)", (long long)st.absent);
    CHECK(d.store.stalls() > 0, "the store never actually stalled");
    CHECK(frames_on_card(d, 6 * kRange) == 0, "the card does not hold the take");
}

void test_stall_past_the_pool() {
    // T4: the same card, a pool of 4 slots and a stall longer than the 4 ranges
    // the take can hold -- so the pool becomes all write queue and the take
    // ends. 4 ranges is 4 x 384 = 1536 blocks.
    Deck d;
    Take tk = stalled_take(d, 4, 8, 1536 + 64);
    CHECK(tk.stalled, "a stall past the pool did not end the take");
    CHECK(tk.len > 0 && tk.len < 8 * kRange, "the take is %lld frames of 8 ranges",
          (long long)tk.len);
    const TapeIo::Stats st = d.io.stats();
    CHECK(st.absent == 0, "a queued range was evicted (%lld)", (long long)st.absent);
    CHECK(st.queue == 0, "the queue did not drain after the stall (%lld)",
          (long long)st.queue);
    // What it captured is a take, not a hole: every frame of it is on the card.
    CHECK(frames_on_card(d, tk.len) == 0, "the captured %lld frames are not on the card",
          (long long)tk.len);

    // Negative control: one range less of stall and the same take runs whole.
    Deck e;
    Take ok = stalled_take(e, 4, 8, 3 * 384);
    CHECK(!ok.stalled, "a stall one range shorter ended the take anyway");
    CHECK(ok.len == 8 * kRange, "the unstalled take is %lld frames", (long long)ok.len);
    CHECK(frames_on_card(e, ok.len) == 0, "the unstalled take is not on the card");
}

// ---- appending at a head that is inside an already-written range ------------

// The one place a bounded pool can *lose* audio that a whole-track-resident one
// could not: a take appends at `head`, which is nearly always inside a range
// that already holds the end of the last take. If that range is not resident,
// `acquire` hands out a zeroed slot and the commit writes those zeroes over the
// audio in front of the head — the earlier take, silently truncated to a range
// boundary. So the head's range is fetched on the control thread before the
// take arms, and this is the test that says so.
int64_t second_take_on_card(bool prepare) {
    Deck d;
    // Six slots rather than four so there is a free one when the second take
    // arms: the point here is what gets *written*, and a pool so tight that the
    // take cannot start at all would prove something else.
    d.init(16, 6);
    // Take one: two and a half ranges, so the head lands well inside range 2.
    Take one = take_of(d, 2 * kRange + kRange / 2);
    const int64_t head = one.medium + one.len;
    const bool aligned = head - Tape::range_of(head) * kRange == 0;
    CHECK(!aligned, "the first take left an aligned head");
    // Push range 2 out of the pool the way ordinary work would: play somewhere
    // else entirely, so the guard band around the read position moves off it,
    // and let the fetches that follow take the slots.
    ClipList cl;
    cl.clips.push_back(Clip{0, 16 * kRange, 0, 1.0f, -1});
    d.tape.swap_clips(0, &cl);
    std::vector<float> pl(kBlock), pr(kBlock);
    for (int64_t k = 8; k < 14; ++k) {
        Travel tr;
        tr.pos = double(k * kRange);
        tr.step = 1.0;
        tr.capacity = d.tape.capacity();
        d.tape.render_track(0, tr, kBlock, pl.data(), pr.data());
        d.turn();
        d.io.fetch(0, k);
        d.turn();
    }
    d.tape.swap_clips(0, &cl);
    CHECK(d.tape.slot_at(0, 2) < 0, "range 2 is still resident: nothing was evicted");

    Take two{d};
    two.track = 0;
    two.arm(head, prepare);
    two.record(kRange);
    if (!two.stalled) two.end();
    for (int i = 0; i < 16; ++i) d.turn();
    // Everything both takes wrote, as the card has it. The interesting frames
    // are the ones between the range-2 boundary and `head`: the first take's.
    Deck probe;
    probe.init(16, 4);
    probe.store = d.store;
    probe.store.set_clock(&probe.tape.block_counter());
    return frames_on_card(probe, head + kRange);
}

void test_append_inside_a_written_range() {
    CHECK(second_take_on_card(true) == 0,
          "the first take's tail was overwritten by the second take's first range");
    // Negative control: without the fetch, the zeroes of a fresh slot are
    // committed over the audio in front of the head — the whole reason the
    // prepare exists, shown failing. (-1 is "the card is short of the take",
    // which is the same verdict by another route.)
    const int64_t bad = second_take_on_card(false);
    CHECK(bad != 0, "skipping the head fetch cost nothing, so the fetch proves nothing");
}

// ---- a reader's pin ---------------------------------------------------------

void test_pin_survives_eviction() {
    Deck d;
    d.init(16, 4);
    take_of(d, 12 * kRange);
    // Fetch range 1 as `bake` would and hold the pin. Then put the read position
    // at the far end of the tape, which makes range 1 the **first** thing the
    // evictor would take — so if the pin is ignored, it goes.
    CHECK(d.io.fetch(0, 1), "fetch");
    d.tape.pin(0, 1);
    const int32_t slot = d.tape.slot_at(0, 1);
    CHECK(slot >= 0, "range 1 is not resident after a fetch");
    ClipList cl;
    cl.clips.push_back(Clip{0, 16 * kRange, 0, 1.0f, -1});
    d.tape.swap_clips(0, &cl);
    std::vector<float> l(kBlock), r(kBlock);
    Travel tr;
    tr.pos = double(13 * kRange);
    tr.step = 1.0;
    tr.capacity = d.tape.capacity();
    d.tape.render_track(0, tr, kBlock, l.data(), r.data());
    d.turn();
    for (int64_t k = 6; k < 12; ++k) {
        d.io.fetch(0, k);
        d.turn();
    }
    CHECK(d.tape.slot_at(0, 1) == slot, "the pinned range was evicted under the reader");

    // And once the pin goes it is an ordinary candidate, taken on the next
    // demand precisely because it is the furthest from the read position.
    d.tape.pin(-1, 0);
    bool gone = false;
    for (int i = 0; i < 16 && !gone; ++i) {
        d.io.fetch(0, 6 + (i % 6));
        d.turn();
        gone = d.tape.slot_at(0, 1) < 0;
    }
    CHECK(gone, "an unpinned range was never evictable");
    d.tape.swap_clips(0, &cl);
}

// ---- T7: the audio path allocates nothing -----------------------------------

void test_no_allocation() {
    Deck d;
    d.init(16, 4);
    Take tk{d};
    tk.arm(0);
    // Warm everything the first block would otherwise allocate (the test's own
    // vectors, the store's file, the I/O thread's queue) outside the window.
    tk.record(2 * kRange);
    ClipList cl;
    cl.clips.push_back(Clip{0, 12 * kRange, 0, 1.0f, -1});
    d.tape.swap_clips(0, &cl);
    std::vector<float> wl(kBlock), wr(kBlock), l(kBlock), r(kBlock);
    for (uint32_t i = 0; i < kBlock; ++i) { wl[i] = 0.25f; wr[i] = -0.25f; }

    const long before = g_news.load();
    // A take crossing a range boundary (so `write` acquires a slot from the free
    // list), a render of a range that is absent (so `render_track` counts it
    // late and asks for it), and a render of one that is resident.
    for (int64_t k = 0; k < 4 * 384; ++k) {
        d.tape.write(0, tk.medium + tk.len, wl.data(), wr.data(), kBlock);
        tk.len += kBlock;
        Travel tr;
        tr.pos = double(10 * kRange + k * kBlock);      // far ahead: absent
        tr.step = 1.0;
        tr.capacity = d.tape.capacity();
        d.tape.render_track(0, tr, kBlock, l.data(), r.data());
        d.tape.tick();
    }
    const long after = g_news.load();
    CHECK(after == before, "%ld allocations on the audio path", after - before);
    d.tape.swap_clips(0, &cl);

    // Negative control: the counter proven able to see one.
    const long b2 = g_news.load();
    std::vector<float>* leak = new std::vector<float>(4, 1.0f);
    CHECK(g_news.load() - b2 >= 1, "the operator new counter saw nothing");
    delete leak;
}

}  // namespace

// test_events.cpp's pattern, and test_tape.cpp's: the scalar forms only, so the
// array forms keep going to the library's own and `-Wmismatched-new-delete` has
// nothing to complain about.
[[gnu::noinline]] void* operator new(std::size_t n) {
    g_news.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

int main() {
    test_take_longer_than_pool();
    test_unbounded_is_unchanged();
    test_stall_inside_the_cover();
    test_stall_past_the_pool();
    test_append_inside_a_written_range();
    test_pin_survives_eviction();
    test_no_allocation();
    if (failures) {
        std::printf("tape_pool: %d failure(s)\n", failures);
        return 1;
    }
    std::printf("tape_pool: ok\n");
    return 0;
}
