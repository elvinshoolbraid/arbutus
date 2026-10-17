// host/tape_io.cpp — the **wanted set**: read-ahead as a window on the timeline
// rather than a one-range lead, the loop seam, the `pool / 2` cap, and the
// eviction rule that grows out of it (C1 step 5, tape-streaming-design.md
// § 3.6, § 4's T3 and T5). No framework; `test_tape_pool.cpp` is the pattern
// and `FakeStore` is the card.
//
// What these prove, and their negative controls (the standing rule since #56 —
// a test that has never been seen to fail is not evidence):
//
//   * **T3** a read stall longer than the horizon costs `tape_late`: the late
//     blocks are exactly **zero**, not garbage; the count stops rising when the
//     stall ends; the audio after the gap is sample-exact again. Controls:
//     (a) the same stall *inside* the horizon costs nothing at all, and
//     (b) `--tape-readahead-seconds 0` goes late with no stall whatsoever —
//     the detector itself proven, and the whole mechanism's *before* switch
//   * **the horizon is what keeps a range**: with a wanted set the evictor
//     leaves the ranges the playhead is about to reach and takes the ones
//     behind it; with the horizon off the same playback, the same pool and the
//     same pressure go late
//   * **the loop seam**: within `ahead` of `loop_out` the window continues from
//     `loop_in`, so a loop that fits in the pool never goes late after the
//     first lap. Control: the identical transport with the loop *off* does not
//     fetch the ranges at `loop_in`
//   * **the cap**: eight tracks wanting more than the pool is truncated at
//     `pool / 2` and counted, so a wanted set can never be the reason the
//     evictor finds no victim — and a take rolling under exactly that pressure
//     still reaches the card sample-exact, with nothing in the write queue
//     evicted (**T5**)
//   * **the grace period**, `kAlways`, holds a retired slot for
//     `Tape::kGraceBlocks` and `kNever` does not. That is the mechanism, not
//     the fault: the fault it prevents is a callback reading a slot that is
//     being refilled, which is a *race*, and a race cannot be made to fire on
//     one thread. Offline the policy is `kAuto` = no wait, which is correct
//     because `service()` is a step of the render loop; `kNever` is the switch
//     a **live** run would flip, and it is documented as such rather than
//     asserted here.
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include <tape.hpp>
#include <tape_io.hpp>
#include <tape_store.hpp>

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

using arb::host::Clip;
using arb::host::ClipList;
using arb::host::FakeStore;
using arb::host::RangeCommit;
using arb::host::Tape;
using arb::host::TapeIo;
using arb::host::Travel;

constexpr int64_t kRange = Tape::kRangeFrames;
constexpr uint32_t kBlock = 128;
constexpr uint32_t kSr = 48000;
constexpr int64_t kBlocksPerSecond = int64_t(kSr) / kBlock;  // 375

// Distinct per track, so a fetch that lands on the wrong medium is a mismatch
// rather than a coincidence.
float sample(int t, int64_t f, int ch) {
    return std::sin(double(f) * 0.001 + double(t)) * (ch ? -0.5f : 1.0f) + float(ch) * 0.25f;
}

struct Deck {
    Tape      tape;
    FakeStore store;
    TapeIo    io;

    void init(int64_t ranges, int64_t pool, double ahead = TapeIo::kAheadSeconds,
              double behind = TapeIo::kBehindSeconds) {
        tape.init(ranges * kRange, pool);
        store.set_clock(&tape.block_counter());
        io.configure(&tape, &store, "/p", kSr);
        io.set_horizon(ahead, behind);
        CHECK(io.on(), "configure");
    }
    // Exactly what `render_offline` does after a block: tick the pool's clock,
    // then one turn of the I/O thread's loop on this thread.
    void turn() {
        tape.tick();
        io.service();
    }
    std::string path(int t) const { return io.medium_path(t); }
};

// A take of `frames` from frame 0 of track `t`, pushed range by range exactly
// as `AudioHost::queue_ranges` does. Returns the frames actually captured.
int64_t record(Deck& d, int t, int64_t frames) {
    d.tape.ensure(t);
    std::vector<float> l(kBlock), r(kBlock);
    int64_t len = 0, next = 0;
    while (len < frames) {
        const uint32_t n = uint32_t(std::min<int64_t>(kBlock, frames - len));
        for (uint32_t i = 0; i < n; ++i) {
            l[i] = sample(t, len + int64_t(i), 0);
            r[i] = sample(t, len + int64_t(i), 1);
        }
        const uint32_t got = d.tape.write(t, len, l.data(), r.data(), n);
        len += int64_t(got);
        const int64_t complete = Tape::range_of(len);
        while (next < complete) {
            RangeCommit c;
            c.track = t;
            c.range = next;
            c.frames = kRange;
            if (!d.io.enqueue(c)) break;
            ++next;
        }
        if (got < n) break;
        d.turn();
    }
    RangeCommit c;
    c.track = t;
    c.flush = 1;
    c.range = Tape::range_of(len);
    c.frames = len - c.range * kRange;
    d.io.enqueue(c);
    d.tape.set_write_range(t, -1);
    for (int i = 0; i < 16; ++i) d.turn();
    return len;
}

// A playback run over `tracks` tracks, each holding one clip over [0, frames).
// It plays the two halves `AudioHost` plays: the clip lists go to the I/O
// thread (`set_clips`) and the playhead is published once a block
// (`Tape::set_view`), which is what `render_block` does at its end.
struct Play {
    int64_t late_blocks = 0;      // blocks where any track wanted an absent range
    int64_t first_late = -1;      // the block it first happened on
    int64_t last_late = -1;
    int64_t bad = 0;              // frames that are neither the input nor silence
    int64_t silent = 0;           // frames a late block wrote as exactly zero
    int64_t nonzero_late = 0;     // ... and frames a late block wrote as anything else
};

struct Rig {
    explicit Rig(Deck& deck) : d(deck) {}
    Deck&                 d;
    int                   tracks = 1;
    std::vector<ClipList> lists;
    bool                  loop = false;
    int64_t               loop_in = 0, loop_out = 0;
    // Called before each block with the block index, so a test can stall the
    // card at a chosen point in the playback.
    void (*at_block)(Deck&, int64_t) = nullptr;

    void set_clips(int64_t frames, int64_t src_offset = 0) {
        lists.assign(std::size_t(tracks), ClipList{});
        for (int t = 0; t < tracks; ++t) {
            lists[std::size_t(t)].clips.push_back(Clip{0, frames, src_offset, 1.0f, -1});
            d.tape.swap_clips(t, &lists[std::size_t(t)]);
            d.io.set_clips(t, lists[std::size_t(t)].clips);
        }
    }
    void unset() {
        for (int t = 0; t < tracks; ++t) {
            d.tape.swap_clips(t, &lists[std::size_t(t)]);
            d.io.set_clips(t, std::vector<Clip>{});
        }
        d.tape.set_view(Tape::View{});
    }

    Play run(int64_t from, int64_t frames) {
        Play p;
        std::vector<float> l(kBlock), r(kBlock);
        int64_t block = 0;
        for (int64_t f = from; f < from + frames; f += kBlock, ++block) {
            if (at_block) at_block(d, block);
            const uint32_t n = uint32_t(std::min<int64_t>(kBlock, from + frames - f));
            Travel tr;
            tr.pos = double(f);
            tr.step = 1.0;
            tr.loop = loop;
            tr.loop_in = loop_in;
            tr.loop_out = loop_out;
            tr.capacity = d.tape.capacity();
            d.tape.clear_late_mask();
            bool late = false;
            for (int t = 0; t < tracks; ++t) {
                d.tape.render_track(t, tr, n, l.data(), r.data());
                const bool t_late = d.tape.late_mask() != 0;
                for (uint32_t i = 0; i < n; ++i) {
                    const int64_t src = int64_t(tr.at(i));
                    const float want_l = sample(t, src, 0), want_r = sample(t, src, 1);
                    if (l[i] == want_l && r[i] == want_r) continue;
                    if (t_late && l[i] == 0.0f && r[i] == 0.0f) { ++p.silent; continue; }
                    if (t_late) { ++p.nonzero_late; continue; }
                    ++p.bad;
                }
                late = late || t_late;
                // `late_mask` is per block across every track, so it is read and
                // cleared per track here to tell them apart.
                d.tape.clear_late_mask();
            }
            if (late) {
                ++p.late_blocks;
                if (p.first_late < 0) p.first_late = block;
                p.last_late = block;
            }
            d.tape.set_view(Tape::View{int64_t(tr.at(n - 1)), 1.0, loop_in, loop_out, loop, true});
            d.turn();
        }
        return p;
    }
};

// ---- T3: a read stall longer than the horizon -------------------------------

// `stall_seconds` of card against an `ahead`-second horizon. The playback runs
// on a **cold** deck over the card the take left behind -- T1's negative
// control's trick -- because a deck that has just recorded still holds the
// take's last ranges in its pool, and a stall that only has to outlast RAM is
// not a test of the horizon. The stall is armed 1.5 s in, so what it spends is
// the window the read-ahead built.
Play stalled_playback(double ahead, int64_t stall_seconds, int64_t* late_first = nullptr) {
    static int64_t s_stall_blocks = 0;
    s_stall_blocks = stall_seconds * kBlocksPerSecond;
    const int64_t frames = 16 * kRange;
    Deck d;
    d.init(20, 12, ahead, TapeIo::kBehindSeconds);
    CHECK(record(d, 0, frames) == frames, "the take was cut short");

    Deck e;
    e.init(20, 12, ahead, TapeIo::kBehindSeconds);
    e.store = d.store;
    e.store.set_clock(&e.tape.block_counter());
    e.tape.ensure(0);
    Rig rig(e);
    rig.set_clips(frames);
    // The locate: the range under the playhead, which is the control thread's
    // (`AudioHost::tape_run` through `fetch`), not the horizon's.
    e.io.fetch(0, 0);
    rig.at_block = [](Deck& dk, int64_t b) {
        if (b == 3 * kBlocksPerSecond / 2 && s_stall_blocks > 0)
            dk.store.stall_blocks(s_stall_blocks);
    };
    const Play p = rig.run(0, frames);
    if (late_first) *late_first = p.first_late;
    rig.unset();
    return p;
}

void test_stall_past_the_horizon() {
    int64_t first = -1;
    const Play p = stalled_playback(TapeIo::kAheadSeconds, 6, &first);
    CHECK(p.late_blocks > 0, "a 6 s read stall against a 4 s horizon cost nothing");
    CHECK(p.bad == 0, "%lld frames were neither the take nor silence", (long long)p.bad);
    CHECK(p.nonzero_late == 0, "%lld frames of a late block were not exactly zero",
          (long long)p.nonzero_late);
    // The stall is armed at 1.5 s and the horizon is 4 s deep, so nothing may
    // go late before ~5.5 s: the window is what the horizon bought.
    CHECK(first >= 4 * kBlocksPerSecond, "went late at block %lld, before the horizon was spent",
          (long long)first);
    // ... and the count stops rising once the card answers again. The stall
    // ends at 7.5 s; the last late block must be within a range of it, not at
    // the end of a 16-second playback.
    CHECK(p.last_late < 10 * kBlocksPerSecond,
          "still going late at block %lld, long after the stall ended",
          (long long)p.last_late);
    // Sample-exact after the gap: every frame is either the take or a silent
    // frame of a late block, which is what `bad == 0` above says, and there is
    // audio after the last late block.
    CHECK(p.silent > 0, "no silence was written for the blocks that went late");
}

void test_stall_inside_the_horizon() {
    // Control (a): the same schedule, 2 s — inside the cover the horizon buys.
    const Play p = stalled_playback(TapeIo::kAheadSeconds, 2, nullptr);
    CHECK(p.late_blocks == 0, "%lld blocks went late inside the horizon",
          (long long)p.late_blocks);
    CHECK(p.bad == 0, "%lld frames differ with no stall to blame", (long long)p.bad);
}

void test_no_horizon_is_late_without_any_stall() {
    // Control (b): `--tape-readahead-seconds 0`. No stall at all, and the same
    // playback over the same pool goes late — a range is fetched only once the
    // playhead has already reached it. That is the detector proven able to
    // fire, and it is the *before* switch for the whole of step 5.
    const Play off = stalled_playback(0.0, 0, nullptr);
    CHECK(off.late_blocks > 0, "with no horizon and a 12-slot pool nothing went late");
    CHECK(off.bad == 0, "%lld frames were neither the take nor silence", (long long)off.bad);
    // And with the horizon on, the identical run is clean.
    const Play on = stalled_playback(TapeIo::kAheadSeconds, 0, nullptr);
    CHECK(on.late_blocks == 0, "%lld blocks went late with a 4 s horizon and no stall",
          (long long)on.late_blocks);
}

// ---- the horizon is what the evictor leaves alone ---------------------------

void test_playback_under_turnover() {
    // A pool of five slots against sixteen ranges: every range the playhead
    // passes has to be evicted to make room for the next, so the only thing
    // that can keep the *next* one resident is the wanted set.
    Deck d;
    d.init(16, 5);
    const int64_t frames = 12 * kRange;
    CHECK(record(d, 0, frames) == frames, "the take was cut short");
    Rig rig(d);
    rig.set_clips(frames);
    d.io.fetch(0, 0);
    const Play p = rig.run(0, frames);
    rig.unset();
    const TapeIo::Stats st = d.io.stats();
    CHECK(p.late_blocks == 0, "%lld blocks late with a horizon deeper than the pool's turnover",
          (long long)p.late_blocks);
    CHECK(p.bad == 0, "%lld frames differ", (long long)p.bad);
    CHECK(st.evicted > 0, "nothing was evicted: the pool was not under pressure");
    CHECK(st.fetched > 0, "nothing was fetched: the playback was all RAM");
    // The cap is `pool / 2` = 2 here, so the wanted set is smaller than the
    // pool by construction and the evictor always has a victim.
    CHECK(st.starved == 0, "%lld fetches found no slot", (long long)st.starved);
}

// The rule on its own: **a range in the wanted set is not evicted**, even when
// that means the evictor finds nothing at all.
//
// Playing a tape back does not test it, which is worth saying: read-ahead
// refills whatever it loses, so a run whose only symptom would be lateness
// passes with the rule deleted (the mutation was tried). So the pool is
// squeezed until the wanted ranges are the *only* evictable thing left --
// every other resident range marked dirty, which is what a range in the write
// queue is -- and then a demand fetch is forced. With the rule the fetch
// starves and the window survives; without it, the window is what pays.
void test_wanted_is_never_the_victim() {
    const int64_t frames = 16 * kRange;
    Deck rec;
    rec.init(20, 12);
    CHECK(record(rec, 0, frames) == frames, "the take was cut short");

    Deck d;
    d.init(20, 6);
    d.store = rec.store;
    d.store.set_clock(&d.tape.block_counter());
    d.tape.ensure(0);
    ClipList cl;
    cl.clips.push_back(Clip{0, frames, 0, 1.0f, -1});
    d.tape.swap_clips(0, &cl);
    d.io.set_clips(0, cl.clips);
    // Settle the window on range 8, then hold it there for the whole test.
    const int64_t pos = 8 * kRange;
    d.tape.set_view(Tape::View{pos, 1.0, 0, 0, false, true});
    for (int i = 0; i < 40; ++i) d.turn();
    CHECK(d.tape.slot_at(0, 8) >= 0, "the playhead's own range was never fetched");
    CHECK(d.tape.slot_at(0, 9) >= 0, "the range after the playhead was never fetched");

    const int64_t starved_before = d.io.stats().starved;
    // Squeeze: every resident range other than the two the window wants
    // becomes the write queue (`hold_span` is what `tape_edit` uses for the
    // same reason), and a render far outside the window asks for a range that
    // is not there -- the audio thread's own demand path, the one fetch that
    // is not read-ahead. Repeated, because the first few fetches are served
    // out of the free list; once that is gone the only slots left in the pool
    // are dirty ones and the two wanted ones.
    std::vector<float> l(kBlock), r(kBlock);
    for (int i = 0; i < 24; ++i) {
        for (int64_t k = 0; k < 20; ++k)
            if (k != 8 && k != 9 && d.tape.slot_at(0, k) >= 0)
                d.io.hold_span(0, k * kRange, kRange);
        Travel tr;
        tr.pos = double((12 + i % 8) * kRange);
        tr.step = 1.0;
        tr.capacity = d.tape.capacity();
        d.tape.render_track(0, tr, kBlock, l.data(), r.data());
        d.tape.set_view(Tape::View{pos, 1.0, 0, 0, false, true});
        d.turn();
    }
    const TapeIo::Stats st = d.io.stats();
    CHECK(st.starved > starved_before,
          "the fetch found a slot, so nothing was protected and the test proves nothing");
    CHECK(d.tape.slot_at(0, 8) >= 0, "the playhead's own range was evicted for a far fetch");
    CHECK(d.tape.slot_at(0, 9) >= 0, "the range after the playhead was evicted for a far fetch");
    d.tape.swap_clips(0, &cl);
    d.io.set_clips(0, std::vector<Clip>{});
}

// ---- the loop seam ----------------------------------------------------------

// Record a take, then play the second before `loop_out` on a **cold** deck
// over the same card -- T1's negative control's trick, and the only way to ask
// "was range 0 fetched?" rather than "was it never evicted?". Returns whether
// the loop's first range became resident without the playhead reaching it.
bool loop_in_fetched(bool loop_on) {
    const int64_t frames = 12 * kRange;
    Deck d;
    d.init(16, 8);
    CHECK(record(d, 0, frames) == frames, "the take was cut short");

    Deck e;                                   // nothing resident
    e.init(16, 8);
    e.store = d.store;
    e.store.set_clock(&e.tape.block_counter());
    e.tape.ensure(0);
    Rig rig(e);
    rig.loop = loop_on;
    rig.loop_in = 0;                          // the loop is the whole take
    rig.loop_out = frames;
    rig.set_clips(frames);
    // Two seconds before `loop_out`, which is inside the 4 s horizon: a wanted
    // set that understands the seam asks for range 0 from here. One second of
    // playback, so the playhead itself never wraps.
    const int64_t from = rig.loop_out - 2 * int64_t(kSr);
    e.io.fetch(0, Tape::range_of(from));
    rig.run(from, int64_t(kSr));
    const bool resident = e.tape.slot_at(0, 0) >= 0;
    rig.unset();
    return resident;
}

void test_loop_seam() {
    CHECK(loop_in_fetched(true), "the loop's first range was never fetched across the seam");
    // Negative control: the identical transport with the loop off. The window
    // runs off the end instead of wrapping, so range 0 -- ten ranges behind the
    // playhead and far outside `behind` -- is never asked for.
    CHECK(!loop_in_fetched(false),
          "the loop's first range was fetched with the loop off, so the seam proves nothing");
}

// ---- T5: eviction under pressure --------------------------------------------

void test_eight_tracks_and_a_take() {
    // § 4's T5 configuration: a pool of eight ranges, eight tracks playing and
    // a take rolling on top of them. The pool cannot hold that, which is the
    // point — what must survive is not every range but the three classes § 3.6
    // says are never evicted: the range the take is filling, the write queue,
    // and a pinned range.
    Deck d;
    d.init(24, 8);
    const int64_t played = 6 * kRange;
    for (int t = 1; t < Tape::kTracks; ++t)
        CHECK(record(d, t, played) == played, "track %d was cut short", t);

    Rig rig(d);
    rig.tracks = Tape::kTracks;
    rig.lists.assign(Tape::kTracks, ClipList{});
    for (int t = 1; t < Tape::kTracks; ++t) {
        rig.lists[std::size_t(t)].clips.push_back(Clip{0, played, 0, 1.0f, -1});
        d.tape.swap_clips(t, &rig.lists[std::size_t(t)]);
        d.io.set_clips(t, rig.lists[std::size_t(t)].clips);
    }
    // Track 0 records while the other seven play.
    d.tape.ensure(0);
    std::vector<float> wl(kBlock), wr(kBlock), l(kBlock), r(kBlock);
    int64_t len = 0, next = 0;
    bool stalled = false;
    for (int64_t f = 0; f < played && !stalled; f += kBlock) {
        for (uint32_t i = 0; i < kBlock; ++i) {
            wl[i] = sample(0, len + int64_t(i), 0);
            wr[i] = sample(0, len + int64_t(i), 1);
        }
        const uint32_t got = d.tape.write(0, len, wl.data(), wr.data(), kBlock);
        len += int64_t(got);
        stalled = got < kBlock;
        const int64_t complete = Tape::range_of(len);
        while (next < complete) {
            RangeCommit c;
            c.track = 0;
            c.range = next;
            c.frames = kRange;
            if (!d.io.enqueue(c)) break;
            ++next;
        }
        Travel tr;
        tr.pos = double(f);
        tr.step = 1.0;
        tr.capacity = d.tape.capacity();
        for (int t = 1; t < Tape::kTracks; ++t) d.tape.render_track(t, tr, kBlock, l.data(), r.data());
        d.tape.clear_late_mask();
        d.tape.set_view(Tape::View{f, 1.0, 0, 0, false, true});
        d.turn();
    }
    CHECK(!stalled, "the take ended: eight tracks of read-ahead ate the write queue");
    CHECK(len == played, "recorded %lld of %lld", (long long)len, (long long)played);
    RangeCommit c;
    c.track = 0;
    c.flush = 1;
    c.range = Tape::range_of(len);
    c.frames = len - c.range * kRange;
    d.io.enqueue(c);
    d.tape.set_write_range(0, -1);
    for (int i = 0; i < 32; ++i) d.turn();

    const TapeIo::Stats st = d.io.stats();
    CHECK(st.absent == 0, "%lld queued ranges were evicted under the I/O thread",
          (long long)st.absent);
    CHECK(st.evicted > 0, "nothing was evicted with eight tracks against eight slots");
    CHECK(st.capped > 0, "the pool/2 cap never bit with eight tracks wanting 5 ranges each");
    CHECK(st.wanted <= 4, "the wanted set is %lld, over the pool/2 cap of 4",
          (long long)st.wanted);

    // The take is on the card, sample-exact: no range of it was evicted between
    // being written and being committed. This is T5's checksum, taken where it
    // means something — the bytes `FakeStore` holds, not the pool.
    const std::vector<uint8_t>& bytes = d.store.bytes(d.path(0));
    CHECK(int64_t(bytes.size()) >= TapeIo::kHeaderBytes + played * TapeIo::kFrameBytes,
          "the medium is %lld bytes, short of the take", (long long)bytes.size());
    int64_t bad = 0;
    for (int64_t f = 0; f < played; ++f) {
        float v[2];
        std::memcpy(v, bytes.data() + std::size_t(TapeIo::kHeaderBytes + f * TapeIo::kFrameBytes),
                    sizeof v);
        if (v[0] != sample(0, f, 0) || v[1] != sample(0, f, 1)) ++bad;
    }
    CHECK(bad == 0, "%lld frames of the take differ on the card", (long long)bad);
    for (int t = 0; t < Tape::kTracks; ++t) {
        d.tape.swap_clips(t, &rig.lists[std::size_t(t)]);
        d.io.set_clips(t, std::vector<Clip>{});
    }
}

// ---- the window follows the direction of travel, and the clip's own medium --

// A cold deck over a recorded card, a published playhead and no rendering at
// all: what becomes resident is the wanted set and nothing else, which is the
// cleanest way to ask what the window covers. `src` is the clip's `src_track`
// (-1 = its own).
struct Cold {
    Deck      d;
    ClipList  cl;
    void open(const FakeStore& card, int clip_track, int src, int64_t frames,
              int64_t pool = 12) {
        d.init(20, pool);
        d.store = card;
        d.store.set_clock(&d.tape.block_counter());
        for (int t = 0; t < Tape::kTracks; ++t) d.tape.ensure(t);
        cl.clips.push_back(Clip{0, frames, 0, 1.0f, int32_t(src)});
        d.tape.swap_clips(clip_track, &cl);
        d.io.set_clips(clip_track, cl.clips);
    }
    // Publish `pos` travelling at `step` and let the I/O thread work.
    void settle(int64_t pos, double step, int turns = 40) {
        d.tape.set_view(Tape::View{pos, step, 0, 0, false, true});
        for (int i = 0; i < turns; ++i) d.turn();
    }
    ~Cold() { d.tape.swap_clips(0, &cl); }
};

void test_direction_follows_travel() {
    const int64_t frames = 16 * kRange;
    Deck rec;
    rec.init(20, 12);
    CHECK(record(rec, 0, frames) == frames, "the take was cut short");

    {   // Forward: ahead is 4 s in front, behind is 1 s behind.
        Cold c;
        c.open(rec.store, 0, -1, frames);
        c.settle(8 * kRange, 1.0);
        CHECK(c.d.tape.slot_at(0, 11) >= 0, "forward: range 11 (3 s ahead) was not fetched");
        CHECK(c.d.tape.slot_at(0, 4) < 0, "forward: range 4 (4 s behind) was fetched anyway");
    }
    {   // REV: the window turns round with the travel, which is what a REV hold
        // needs -- the first block backwards must already be warm.
        Cold c;
        c.open(rec.store, 0, -1, frames);
        c.settle(8 * kRange, -1.0);
        CHECK(c.d.tape.slot_at(0, 5) >= 0, "REV: range 5 (3 s back along the travel) was not fetched");
        CHECK(c.d.tape.slot_at(0, 12) < 0, "REV: range 12 (4 s the wrong way) was fetched anyway");
    }
}

void test_horizon_scales_with_speed() {
    // § 3.6's horizon is **4 s of wall clock**, so it is four ranges at 1x and
    // sixteen at 4x -- the window has to be measured in time, or a SPEED hold
    // outruns it and every range past the fourth is late.
    const int64_t frames = 16 * kRange;
    Deck rec;
    rec.init(20, 12);
    CHECK(record(rec, 0, frames) == frames, "the take was cut short");
    {
        Cold c;
        c.open(rec.store, 0, -1, frames, 24);
        c.settle(8 * kRange, 1.0);
        CHECK(c.d.tape.slot_at(0, 14) < 0, "1x: range 14 (6 s ahead) was fetched anyway");
    }
    {
        Cold c;
        // A pool of 24, so the `pool / 2` cap is 12 and does not do the
        // truncating the horizon is supposed to be doing here. At 4x the
        // window is twelve ranges wide -- eight ahead and four behind, because
        // `behind` is a second of wall clock too.
        c.open(rec.store, 0, -1, frames, 24);
        c.settle(8 * kRange, 4.0);
        CHECK(c.d.tape.slot_at(0, 14) >= 0,
              "4x: range 14 is 1.5 s of wall clock ahead and was not fetched");
    }
}

void test_src_track_is_followed() {
    const int64_t frames = 16 * kRange;
    Deck rec;
    rec.init(20, 12);
    CHECK(record(rec, 0, frames) == frames, "the take was cut short");
    {   // A clip on track 3 reading track 0's medium (LIFT on one track, DROP
        // on another -- `Clip::src_track`). The ranges fetched must be track
        // **0**'s, which is the only track with anything on the card.
        Cold c;
        c.open(rec.store, 3, 0, frames);
        c.settle(8 * kRange, 1.0);
        CHECK(c.d.tape.slot_at(0, 9) >= 0, "the clip's source track was not read ahead");
        CHECK(c.d.tape.slot_at(3, 9) < 0, "the clip's own track was read ahead instead");
    }
    {   // Negative control: the same clip on its own (empty) medium. Nothing of
        // track 0 is wanted, so nothing of it is fetched -- which is what says
        // the assertion above is reading `src_track` and not the tape at large.
        Cold c;
        c.open(rec.store, 3, -1, frames);
        c.settle(8 * kRange, 1.0);
        CHECK(c.d.tape.slot_at(0, 9) < 0, "track 0 was fetched for a clip that does not read it");
    }
}

// ---- #591 finding 2: a shared range is ranked by its nearest reference ------
//
// Two clips can read one range of one medium (`src_track`, `src_offset`: a
// LIFT dropped twice, a copy on another track). `add_wanted` kept the **first**
// reference it met, track order, so a range that sounds at the playhead
// through track 2 and four seconds away through track 1 was ranked four
// seconds away -- and the `pool / 2` cap dropped the one range the playhead
// was about to play. Every case below wants three of track 0's ranges against
// a cap of two (pool 4): range 12 through a far reference on track 1 and a
// near (or recording) one on track 2, and ranges 5 and 6 between them on
// track 3. Range 12 must be resident; with the near reference removed it must
// not be, which is what says the cap is biting at all.
struct Shared {
    Deck     d;
    ClipList cl[Tape::kTracks];
    bool     set[Tape::kTracks] = {};
    Shared(const FakeStore& card) {
        d.init(20, 4);
        d.store = card;
        d.store.set_clock(&d.tape.block_counter());
        for (int t = 0; t < Tape::kTracks; ++t) d.tape.ensure(t);
    }
    void clip(int t, int64_t start, int64_t src) {
        cl[t].clips.push_back(Clip{start, kRange, src, 1.0f, 0});
    }
    bool kept(int64_t pos, double step) {
        for (int t = 0; t < Tape::kTracks; ++t) {
            if (cl[t].clips.empty()) continue;
            d.tape.swap_clips(t, &cl[t]);
            d.io.set_clips(t, cl[t].clips);
            set[t] = true;
        }
        d.tape.set_view(Tape::View{pos, step, 0, 0, false, true});
        for (int i = 0; i < 40; ++i) d.turn();
        return d.tape.slot_at(0, 12) >= 0;
    }
    ~Shared() {
        for (int t = 0; t < Tape::kTracks; ++t)
            if (set[t]) d.tape.swap_clips(t, &cl[t]);
    }
};

void test_shared_range_takes_the_nearest_reference() {
    const int64_t frames = 16 * kRange;
    Deck rec;
    rec.init(20, 12);
    CHECK(record(rec, 0, frames) == frames, "the take was cut short");
    const int64_t K = kRange;
    // Each case twice, the far and the near reference on tracks 1 and 2 and
    // then exchanged, so neither "keep the first met" nor "keep the last met"
    // passes for the nearest (#1131).
    for (const bool swap : {false, true})
    for (const bool near : {true, false}) {
        const int far_t = swap ? 2 : 1, near_t = swap ? 1 : 2;
        {   // Forward from 8 s: range 12 at 3 s through one track, at 0 through the other.
            Shared s(rec.store);
            s.clip(far_t, 11 * K, 12 * K);
            if (near) s.clip(near_t, 8 * K, 12 * K);
            s.clip(3, 9 * K, 5 * K);
            s.clip(3, 10 * K, 6 * K);
            CHECK(s.kept(8 * K, 1.0) == near, "forward, near reference %d swap %d: range 12 %s",
                  int(near), int(swap), near ? "was dropped" : "was kept with no near reference");
        }
        {   // REV from 8 s: the same, mirrored behind the playhead.
            Shared s(rec.store);
            s.clip(far_t, 4 * K, 12 * K);
            if (near) s.clip(near_t, 7 * K, 12 * K);
            s.clip(3, 6 * K, 5 * K);
            s.clip(3, 5 * K, 6 * K);
            CHECK(s.kept(8 * K, -1.0) == near, "REV, near reference %d swap %d: range 12 %s",
                  int(near), int(swap), near ? "was dropped" : "was kept with no near reference");
        }
        {   // The recording track: both references are 3 s out, and one is a
            // take's, which § 3.6 fetches first whatever the distance.
            Shared s(rec.store);
            s.clip(1, 11 * K, 12 * K);
            s.clip(2, 11 * K, 12 * K);
            s.clip(3, 9 * K, 5 * K);
            s.clip(3, 10 * K, 6 * K);
            if (near) s.d.tape.set_write_range(near_t, 0);
            CHECK(s.kept(8 * K, 1.0) == near, "recording %d swap %d: range 12 %s", int(near),
                  int(swap), near ? "lost the take's priority" : "was kept with no take");
        }
    }
}

// ---- the grace period's mechanism -------------------------------------------

// Eight distinct ranges fetched into a three-slot pool **without rendering a
// single block in between**. Every fetch past the third has to evict, and an
// evicted slot is only reusable once its grace period is over -- which, with no
// block rendered, never happens. So the count is the mechanism: with the wait
// in force it stops at what the pool already had, and without it every fetch
// succeeds because the slot comes straight back.
int fetches_without_a_block(TapeIo::GracePolicy policy) {
    Deck d;
    d.init(16, 3);
    d.io.set_grace(policy);
    const int64_t frames = 8 * kRange;
    CHECK(record(d, 0, frames) == frames, "the take was cut short");
    int n = 0;
    for (int64_t k = 0; k < 8; ++k) {
        if (!d.io.fetch(0, k)) break;
        ++n;
    }
    return n;
}

void test_grace_period() {
    // `kAuto` offline is *no* wait, and that is correct rather than a
    // shortcut: `service()` is a step of the render loop, so no callback can be
    // in flight (`tape_io.hpp`). `kNever` is the same offline by construction.
    CHECK(fetches_without_a_block(TapeIo::GracePolicy::kAuto) == 8,
          "offline the grace period must be nothing: service() is a step of the render loop");
    CHECK(fetches_without_a_block(TapeIo::GracePolicy::kNever) == 8,
          "kNever must never make a slot wait");
    // `kAlways` is the live rule made visible: a retired slot waits
    // `Tape::kGraceBlocks` rendered blocks, and with none rendered the pool
    // runs dry at what it already had.
    const int held = fetches_without_a_block(TapeIo::GracePolicy::kAlways);
    CHECK(held < 8, "kAlways handed retired slots back with no block rendered (%d of 8)", held);
}

// ---- #475: a LOAD's reads make no medium in the project folder ---------------
// LOAD is `tape_dir {dir}` and then the UI's `set_clips`, one per track. In
// between, the I/O thread's clip lists and the audio thread's are still the old
// project's, so the read-ahead and the demand asks go on fetching tracks the new
// folder may hold no medium for. A fetch that opened its medium with `create`
// left an empty `tape-N.wav` there, and an empty medium where a take should be
// reads as a lost take. Only a record makes a medium; a read of a track with
// none is silence, which is what the empty file played as.
void test_load_makes_no_medium() {
    const int64_t frames = 4 * kRange;
    Deck d;
    d.init(20, 12);
    CHECK(record(d, 0, frames) == frames, "take 1 was cut short");
    CHECK(record(d, 1, frames) == frames, "take 2 was cut short");
    // The project being loaded holds track 1's medium and not track 2's.
    {
        std::string err;
        const std::vector<uint8_t> b = d.store.bytes_copy("/p/tape-1.wav");
        const auto h = d.store.open("/q/tape-1.wav", true, err);
        CHECK(d.store.pwrite(h, b.data(), int64_t(b.size()), 0, err) == int64_t(b.size()),
              "copy: %s", err.c_str());
        d.store.close(h);
    }
    Rig rig(d);
    rig.tracks = 2;
    rig.set_clips(frames);                     // the old project's lists, on both threads

    int64_t got[Tape::kTracks];
    std::string code, err;
    CHECK(d.io.set_dir("/q", false, got, code, err), "tape_dir: %s", err.c_str());
    CHECK(got[0] == frames && got[1] == 0, "frames %lld %lld", (long long)got[0],
          (long long)got[1]);
    const int64_t fetched = d.io.stats().fetched;
    rig.run(0, 2 * kRange);                    // before the UI's set_clips arrives
    rig.unset();

    CHECK(!d.store.exists("/q/tape-2.wav"), "a read made an empty medium in the project folder");
    CHECK(!d.store.exists("/q/tape-2.peaks"), "a read made a sidecar in the project folder");
    // Control: the reads did run, and off the medium that is there.
    CHECK(d.io.stats().fetched > fetched, "the read-ahead never fetched");
    CHECK(d.store.exists("/q/tape-1.wav"), "track 1's medium went");
    // And a record still makes one: the first take on track 2 in the new folder.
    CHECK(record(d, 1, kRange + 100) == kRange + 100, "the take in /q was cut short");
    d.io.flush();
    CHECK(d.store.exists("/q/tape-2.wav"), "a record in the loaded project made no medium");
}

}  // namespace

int main() {
    test_stall_past_the_horizon();
    test_stall_inside_the_horizon();
    test_no_horizon_is_late_without_any_stall();
    test_playback_under_turnover();
    test_wanted_is_never_the_victim();
    test_shared_range_takes_the_nearest_reference();
    test_loop_seam();
    test_direction_follows_travel();
    test_horizon_scales_with_speed();
    test_src_track_is_followed();
    test_eight_tracks_and_a_take();
    test_grace_period();
    test_load_makes_no_medium();
    if (failures) {
        std::printf("tape_readahead: %d failure(s)\n", failures);
        return 1;
    }
    std::printf("tape_readahead: ok\n");
    return 0;
}
