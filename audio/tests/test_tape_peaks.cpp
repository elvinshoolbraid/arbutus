// host/tape_io.cpp — **the peaks sidecar** (#163, the host half of C1 step 6's
// follow-up; tape-streaming-design.md § 3.6). No framework;
// `test_tape_readahead.cpp` is the pattern and `FakeStore` is the card.
//
// The claim is one sentence: *the bins the I/O thread keeps say the same thing
// about a settled clip as reading its audio does.* Everything below is that
// claim, the three refusals that keep it true, and the controls the #56 rule
// asks for (a test that has never been seen to fail is not evidence):
//
//   * **agreement**, at the sidecar's own resolution and at one bin a range:
//     `peaks_for` against a scan of the samples the take was made of — not
//     against another reading of the same bins. Control: `corrupt_peaks`
//     makes a **stale** sidecar and the same comparison has to fail
//   * **too fine is refused.** A question below `kPeakBinFrames` is answered
//     `false` and counted as a miss, because the sidecar would be coarser than
//     the zoom asked for. This is the half of #163 the UI's bins cap turns from
//     always-true into never-true for a clip drawn zoomed out
//   * **past the durable prefix is refused**, which is what keeps a range the
//     pool has written and the card has not taken from being drawn from the
//     bins of whatever was there before. Control: the same span *inside* the
//     prefix is answered
//   * **`--no-tape-peaks` refuses everything and counts nothing** — the
//     *before* switch, so one boot of the #162 rig measures both sides
//   * **the file**: `tape-N.peaks` is written by the commit that ends a take,
//     it is the size its shape says, and a second host reading the same card
//     answers from it **without committing or fetching anything**. Controls: a
//     sidecar whose frame count disagrees with the medium's header is
//     discarded, and a medium with no sidecar at all answers nothing until a
//     range is fetched — at which point that range alone answers
//
// **Eight mutations of the implementation, each caught by the assertion meant
// for it** (no bins on commit, no bins on fetch, no fold of the silence past a
// partial range's end, no resolution refusal, no durable-prefix gate, no
// staleness check, nothing written at the take's end, no published prefix after
// a repair). The one line here that is deliberately **not** asserted is
// `save_peaks_locked`'s `truncate`: nothing reachable writes a *shorter*
// sidecar over a longer one — a medium only grows — and the two checks that
// would matter if one ever did (the exact read length and the frame count) are
// asserted. It stays because a file whose size is not a statement about its
// shape is a file the next person has to read twice.
#include <algorithm>
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

using arb::host::FakeStore;
using arb::host::RangeCommit;
using arb::host::StoreHandle;
using arb::host::Tape;
using arb::host::TapeIo;

constexpr int64_t kRange = Tape::kRangeFrames;
constexpr uint32_t kBlock = 128;
constexpr uint32_t kSr = 48000;
constexpr int64_t kBin = TapeIo::kPeakBinFrames;            // 3072

// The take's audio, as a function of the frame — so a scan of it is an
// independent copy of what the medium should hold. Deliberately **strictly
// positive**: silence is then a value the audio never takes, so a bin that
// folded one in — or failed to — shows up as a min of exactly zero.
float sample(int64_t f, int ch) {
    const double x = double(f) * 0.0004;
    return float(0.55 + 0.4 * std::sin(x)) * (ch ? 0.75f : 1.0f);
}

struct Deck {
    Tape      tape;
    TapeIo    io;

    void init(FakeStore& store, int64_t ranges, int64_t pool) {
        tape.init(ranges * kRange, pool);
        store.set_clock(&tape.block_counter());
        io.configure(&tape, &store, "/p", kSr);
        CHECK(io.on(), "configure");
    }
    void turn() {
        tape.tick();
        io.service();
    }
};

// A take of `frames` from frame 0 of track `t`, pushed range by range exactly as
// `AudioHost::queue_ranges` does.
int64_t record(Deck& d, int t, int64_t frames) {
    d.tape.ensure(t);
    std::vector<float> l(kBlock), r(kBlock);
    int64_t len = 0, next = 0;
    while (len < frames) {
        const uint32_t n = uint32_t(std::min<int64_t>(kBlock, frames - len));
        for (uint32_t i = 0; i < n; ++i) {
            l[i] = sample(len + int64_t(i), 0);
            r[i] = sample(len + int64_t(i), 1);
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

// The envelope of `[from, from + len)` of the take's own audio in `bins` bins —
// the same arithmetic `AudioHost::clip_peaks` does over the samples, on the
// function the take was made of.
void scan(int64_t from, int64_t len, int bins, std::vector<float>& mn,
          std::vector<float>& mx) {
    mn.assign(std::size_t(bins), 0.0f);
    mx.assign(std::size_t(bins), 0.0f);
    for (int b = 0; b < bins; ++b) {
        const int64_t a = from + int64_t(uint64_t(b) * uint64_t(len) / uint64_t(bins));
        const int64_t e = std::max(from + int64_t(uint64_t(b + 1) * uint64_t(len) /
                                                 uint64_t(bins)),
                                   a + 1);
        float lo = sample(a, 0), hi = lo;
        for (int64_t f = a; f < e; ++f)
            for (int ch = 0; ch < 2; ++ch) {
                const float v = sample(f, ch);
                lo = std::min(lo, v);
                hi = std::max(hi, v);
            }
        mn[std::size_t(b)] = lo;
        mx[std::size_t(b)] = hi;
    }
}

// Bins where the sidecar and the audio disagree. Zero is the claim; a number is
// what a control has to produce.
int disagreements(TapeIo& io, int t, int64_t from, int64_t len, int bins) {
    std::vector<float> smn, smx, amn, amx;
    if (!io.peaks_for(t, from, len, bins, smn, smx)) return -1;
    scan(from, len, bins, amn, amx);
    int bad = 0;
    for (int b = 0; b < bins; ++b)
        if (smn[std::size_t(b)] != amn[std::size_t(b)] ||
            smx[std::size_t(b)] != amx[std::size_t(b)])
            ++bad;
    return bad;
}

void test_agreement_and_refusals() {
    FakeStore store;
    Deck d;
    d.init(store, 16, 32);                      // bounded, so `fetch` is reachable
    const int64_t ranges = 3;
    const int64_t took = record(d, 0, ranges * kRange);
    CHECK(took == ranges * kRange, "took %lld", (long long)took);
    CHECK(d.io.peaks_ranges(0) == ranges, "ranges %lld", (long long)d.io.peaks_ranges(0));

    // At the sidecar's own resolution: every output bin is exactly one of its
    // bins, so this is the sharpest form of the claim.
    const int fine = int(ranges * TapeIo::kPeakBins);
    CHECK(disagreements(d.io, 0, 0, ranges * kRange, fine) == 0,
          "at %d bins: %d differ", fine, disagreements(d.io, 0, 0, ranges * kRange, fine));
    // And one bin a range, which is the aggregation of sixteen of them.
    CHECK(disagreements(d.io, 0, 0, ranges * kRange, int(ranges)) == 0, "one bin a range");
    // A span that starts and ends on bin boundaries inside the medium.
    CHECK(disagreements(d.io, 0, kBin * 5, kBin * 20, 20) == 0, "an aligned span");

    const int64_t miss0 = d.io.stats().peaks_miss;
    // Finer than the sidecar: refused and counted.
    std::vector<float> mn, mx;
    CHECK(!d.io.peaks_for(0, 0, ranges * kRange, fine * 2, mn, mx), "too fine answered");
    CHECK(mn.empty() && mx.empty(), "a refusal left bins behind");
    CHECK(d.io.stats().peaks_miss == miss0 + 1, "too fine was not counted");
    // Past the durable prefix: refused. The control is the same length one
    // range earlier, which is answered.
    CHECK(!d.io.peaks_for(0, ranges * kRange - kBin, kBin * 2, 1, mn, mx),
          "past the prefix answered");
    CHECK(d.io.peaks_for(0, ranges * kRange - kBin * 2, kBin * 2, 1, mn, mx),
          "inside the prefix refused");

    // The negative control (#56): a **stale** sidecar has to fail the same
    // comparison a fresh one passes.
    d.io.corrupt_peaks(0, 0.5f);
    const int bad = disagreements(d.io, 0, 0, ranges * kRange, fine);
    CHECK(bad > 0, "a stale sidecar agreed with the audio in all %d bins", fine);
    std::printf("ok - peaks: %d of %d bins agree with a scan of the samples; a stale "
                "sidecar disagrees in %d\n", fine, fine, bad);
}

void test_before_switch() {
    FakeStore store;
    Deck d;
    d.tape.init(16 * kRange, 32);
    store.set_clock(&d.tape.block_counter());
    d.io.set_peaks(false);
    d.io.configure(&d.tape, &store, "/p", kSr);
    record(d, 0, 2 * kRange);
    CHECK(d.io.peaks_ranges(0) == 0, "bins kept with the switch off");
    CHECK(!store.exists("/p/tape-1.peaks"), "a sidecar file with the switch off");
    std::vector<float> mn, mx;
    CHECK(!d.io.peaks_for(0, 0, 2 * kRange, 2, mn, mx), "answered with the switch off");
    // Not a miss: there is no sidecar to miss, and a bench cell reading the
    // counter must see the *before* side as zero of both.
    CHECK(d.io.stats().peaks_miss == 0 && d.io.stats().peaks_hit == 0, "counted a miss");
    std::printf("ok - peaks: --no-tape-peaks keeps no bins, writes no file and counts nothing\n");
}

void test_the_file() {
    FakeStore store;
    const int64_t ranges = 3;
    {
        Deck d;
        d.init(store, 16, 32);
        record(d, 0, ranges * kRange);
        // Written by the commit that ended the take, before anything shuts
        // down: a project that is never closed still has its sidecar.
        CHECK(store.exists("/p/tape-1.peaks"), "no sidecar at the end of the take");
        d.io.stop();                            // offline there is no thread; closes the media
    }
    CHECK(store.exists("/p/tape-1.peaks"), "no sidecar file");
    const int64_t want = TapeIo::kPeaksHeaderBytes +
                         ranges * TapeIo::kPeakBins * 2 * int64_t(sizeof(float)) + ranges;
    CHECK(int64_t(store.bytes("/p/tape-1.peaks").size()) == want, "file is %lld, want %lld",
          (long long)store.bytes("/p/tape-1.peaks").size(), (long long)want);

    // A second host over the same card: the bins come back off the file, and
    // nothing has been committed or fetched to make them.
    {
        Deck e;
        e.init(store, 16, 32);
        CHECK(e.io.open_all(), "open_all");
        CHECK(e.io.peaks_ranges(0) == ranges, "reloaded %lld ranges",
              (long long)e.io.peaks_ranges(0));
        CHECK(e.io.stats().fetched == 0 && e.io.stats().committed == 0,
              "the reload touched the audio");
        const int fine = int(ranges * TapeIo::kPeakBins);
        CHECK(disagreements(e.io, 0, 0, ranges * kRange, fine) == 0, "reloaded bins differ");
        std::printf("ok - peaks: a second host answers %d bins off tape-1.peaks with 0 "
                    "fetches and 0 commits\n", fine);
    }

    // Control (a): a sidecar whose frame count disagrees with the medium's own
    // header is a file from before something happened to the medium, and is
    // discarded rather than drawn.
    {
        std::string err;
        const StoreHandle h = store.open("/p/tape-1.peaks", true, err);
        CHECK(h != arb::host::kNoHandle, "reopen the sidecar: %s", err.c_str());
        const uint8_t wrong[8] = {1, 0, 0, 0, 0, 0, 0, 0};   // frames = 1
        CHECK(store.pwrite(h, wrong, 8, 24, err) == 8, "poke: %s", err.c_str());
        store.close(h);
        Deck e;
        e.init(store, 16, 32);
        CHECK(e.io.open_all(), "open_all");
        CHECK(e.io.peaks_ranges(0) == 0, "a stale file was loaded anyway");
        std::vector<float> mn, mx;
        CHECK(!e.io.peaks_for(0, 0, ranges * kRange, int(ranges), mn, mx),
              "answered from a discarded file");
    }

    // Control (b): no sidecar at all answers nothing — until a range is
    // fetched, which is the other input the bins have.
    {
        std::string err;
        store.remove("/p/tape-1.peaks", err);
        Deck e;
        e.init(store, 16, 32);
        CHECK(e.io.open_all(), "open_all");
        CHECK(e.io.peaks_ranges(0) == 0, "bins out of nowhere");
        CHECK(e.io.fetch(0, 1), "fetch range 1");
        CHECK(e.io.peaks_ranges(0) == 1, "a fetch left no bins");
        CHECK(disagreements(e.io, 0, kRange, kRange, TapeIo::kPeakBins) == 0,
              "a fetched range's bins differ from its audio");
        // And the range beside it still cannot be answered.
        std::vector<float> mn, mx;
        CHECK(!e.io.peaks_for(0, 0, kRange, 1, mn, mx), "answered for a range never read");
        std::printf("ok - peaks: a stale file is discarded, and a fetched range's bins "
                    "agree with its audio\n");
    }
}

// The tail of a take does not fill its range, and the two ways a range's bins
// come to exist have to agree about the silence past it: a commit sees `frames`
// frames and stops, a fetch reads the whole range off a file that ends early and
// zero-fills the rest. If they disagreed, a medium would draw differently
// before and after it had been evicted once — which is a bug nothing else here
// would catch, because every other case is a whole range.
void test_a_partial_range_commits_and_fetches_the_same() {
    FakeStore store;
    const int64_t tail = 12000;
    const int64_t frames = 2 * kRange + tail;
    std::vector<float> cmn, cmx;
    {
        Deck d;
        d.init(store, 16, 32);
        CHECK(record(d, 0, frames) == frames, "record");
        CHECK(d.io.peaks_ranges(0) == 3, "ranges %lld", (long long)d.io.peaks_ranges(0));
        // Three bins over the tail: the last of them straddles the medium's end,
        // which is the bin the fold is about.
        CHECK(d.io.peaks_for(0, 2 * kRange, tail, 3, cmn, cmx), "the tail was refused");
        CHECK(cmn.size() == 3, "%zu bins came back", cmn.size());
        if (cmn.size() == 3)
            CHECK(cmn[2] == 0.0f, "the bin over the end did not fold the silence in");
        d.io.stop();
    }
    std::string err;
    store.remove("/p/tape-1.peaks", err);        // so the bins can only come from a fetch
    Deck e;
    e.init(store, 16, 32);
    CHECK(e.io.open_all(), "open_all");
    CHECK(e.io.peaks_ranges(0) == 0, "bins survived the file being removed");
    CHECK(e.io.fetch(0, 2), "fetch the partial range");
    std::vector<float> fmn, fmx;
    CHECK(e.io.peaks_for(0, 2 * kRange, tail, 3, fmn, fmx), "the fetched tail was refused");
    int bad = 0;
    for (std::size_t b = 0; b < cmn.size() && b < fmn.size(); ++b)
        if (cmn[b] != fmn[b] || cmx[b] != fmx[b]) ++bad;
    CHECK(bad == 0 && cmn.size() == fmn.size(),
          "%d of 3 bins over the tail differ between a commit and a fetch", bad);

    // And the case the durable-prefix gate is really for. Range 2 is in the
    // sidecar, so "has this range got bins" says yes; but the medium claims
    // only `2 * kRange + tail` frames, and a second take now **appends into the
    // same range**. Until that commit lands, the bins know nothing about the
    // new frames, and the only number that says so is `durable_end`. The
    // control is the identical query one block shorter, which is inside the
    // claim and is answered.
    std::vector<float> mn, mx;
    CHECK(e.io.peaks_for(0, 2 * kRange, tail, 3, mn, mx), "inside the claim was refused");
    CHECK(!e.io.peaks_for(0, 2 * kRange, kRange, TapeIo::kPeakBins, mn, mx),
          "answered for the whole of a range the medium only partly claims");
    std::vector<float> l(kBlock, 0.25f), r(kBlock, 0.25f);
    e.tape.ensure(0);                            // what the control thread does before it arms
    CHECK(e.tape.write(0, frames, l.data(), r.data(), kBlock) == kBlock, "append");
    CHECK(!e.io.peaks_for(0, 2 * kRange, tail + int64_t(kBlock), 3, mn, mx),
          "answered over frames a take had written and the card had not taken");
    std::printf("ok - peaks: a partial range's bins are the same whether they came from the "
                "commit or from reading it back, and stop at what the card claims\n");
}

}  // namespace

int main() {
    test_agreement_and_refusals();
    test_before_switch();
    test_the_file();
    test_a_partial_range_commits_and_fetches_the_same();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("tape peaks: all checks passed\n");
    return 0;
}
