// tape_io.hpp — the tape I/O thread: write-behind from the pool onto storage
// (C1 step 2a, tape-streaming-design.md § 3.3, § 3.5, § 6.2).
//
// The take is written into pool slots by the audio thread (`Tape::write`).
// Each time a range fills, the audio thread pushes one POD — "range k of track
// t is finished" — onto an SPSC ring and carries on; this class is the other
// end of that ring. It is the **only** thing in the host that touches a medium
// on storage, through `TapeStore`, and it is an ordinary `SCHED_OTHER` thread:
// it blocks on the card by design, and a realtime thread that blocks is a lie
// the scheduler believes (§ 3.9).
//
// **The commit.** One `fallocate` of the lead, one `pwrite` of the range at its
// byte offset, one `pwrite` of the header, one `fdatasync` — C6's measured
// pattern (0.54 ms + 10 ms on the eMMC), and the one row of this design with a
// bench number behind it. `POSIX_FADV_DONTNEED` afterwards, so the page cache
// does not hold a second copy of the pool.
//
// **The lead** (§ 3.4's fallback, #158). § 3.4 recommended *against*
// preallocation, reasoning that a card's own allocation is cheap; step 2's
// measurement on the bench eMMC is the evidence against that reasoning —
// 12.0 ms p50, exactly C6's number, but **65–92 ms p99** against § 6.2's ~20 ms
// line, and pacing the commits to a take's own cadence moved the tail only from
// ~102 to ~78 ms, so the cost is in *growing* the file rather than in the rate.
// So every commit first asks the filesystem for blocks `kAllocLeadRanges` past
// the range it is about to write, with `FALLOC_FL_KEEP_SIZE` — the blocks
// exist, the file's length does not move (which repair on open depends on,
// § 3.5), and the `pwrite` lands in space that is already allocated. One range
// per commit rather than a chunk every N: a uniform small cost is what a p99
// target wants, where one commit in sixteen paying for sixteen ranges is the
// tail all over again. A filesystem that cannot preallocate (exFAT may be one —
// step 4 finds out) is counted once per medium and the medium grows as before;
// `--no-tape-prealloc` is the *before* switch, so one boot can measure both.
//
// **Why the header lags.** `fdatasync` says everything before it is on the
// device when it returns; it says nothing about the order the pages got there.
// So the header written in commit *k* claims only the frames that commit
// *k−1* already made durable: whatever subset of this commit survives a power
// cut, the header never claims audio that is not there. The price is one range
// in the loss bound (≤ ~2 s with the range being filled, § 3.3); the
// alternative — two syncs a commit — doubles the exposure to a card stall,
// which is the expensive mistake. At the end of a take the header is written
// with **no lag** and synced, so a file the instrument has stopped writing is
// exact and a Mac never sees a live header.
//
// **The grace period is a live-only wait.** Design § 3.6 gives a retired slot
// two rendered blocks before it may be reused, so a callback that read the table
// entry just before it was cleared has finished with the slot. That is a rule
// about two threads: offline there is only one — `service()` *is* a step of the
// render loop — so no callback can be in flight and a retired slot is the I/O
// thread's at once. Live it is the two blocks the design asks for, and a
// control-thread `fetch` that finds every slot still inside its grace waits for
// it (≤ 2.67 ms a block) rather than handing a reader silence.
//
// **The pool, evicted and refilled (step 2b).** With a bounded pool this class
// is also the only thing that frees a slot and the only thing that fetches a
// range back, and one turn of it does four things in this order:
//
//   1. **advance the grace period** — slots retired two or more blocks ago
//      become the I/O thread's to reuse (design § 3.6 "safe unpublishing");
//   2. **pop the commit ring into a queue and commit what it can**. A range in
//      that queue is *dirty* and may not be evicted: it is the only copy;
//   3. **serve the audio thread's wants** — one `pread` per range, published
//      into a slot;
//   4. **evict** enough resident ranges to keep the audio thread's free list
//      above a low-water mark, so a take almost never finds it empty.
//
// The victim is the **clean, unpinned resident range furthest from where its
// own track is being read** (`Tape::hot`), never the range the take is filling,
// never one within `Tape::kGuardRanges` of a read position. That guard band is
// step 2b's stand-in for step 5's wanted set — it has no horizon in seconds and
// knows nothing about clip lists.
//
// **A stalled card converts the pool into write queue, and then ends the take.**
// A store that is not answering (`TapeStore::busy`, which offline is how a
// blocking card is modelled) leaves the queue growing; every queued range holds
// its slot; when the free list is empty the audio thread's next range cannot be
// made resident and `Tape::write` returns short, which the host turns into
// `tape_full {reason:"stall"}` — the take becomes a clip of what was captured
// and the transport keeps rolling. That is the order Elvin's "a frozen screen is
// not acceptable, more latency is" asks for.
//
// **The wanted set (step 5).** Step 2b's read-ahead was one range, asked for by
// the audio thread while it read the one before it. Step 5 moves read-ahead
// here entirely, as design § 3.6 specifies it: a **window on the timeline**,
// `[pos − behind, pos + ahead]` in the direction of travel and in seconds of
// *wall clock* (so it is four ranges at 1x and sixteen at 4x), through the loop
// seam, mapped through every track's clip list onto the ranges of the media
// those clips read (`src_track`, which may not be the clip's own track), capped
// at half the pool and ordered nearest-first with the **recording track first**
// — an overdub records the track's own playback, so a late range there is
// silence in the take, not just in the speakers (§ 3.6).
//
// Its two inputs are the two threads that own them: the **clip lists** come
// from the control thread through `set_clips`, copied under `mu_` (both are
// non-real-time threads; the audio thread never sees that mutex), and the
// **playhead** comes from the audio thread through `Tape::set_view`, five
// relaxed stores a block. The set is recomputed every turn — it is cheap and
// there is no invalidation to get wrong.
//
// A wanted range is also **not evicted** while it is wanted, which is what the
// `kGuardRanges` band stood in for in step 2b. The band stays beside it for a
// host that has published neither input yet.
//
// `--tape-readahead-seconds 0` turns the whole horizon off — the only fetches
// left are the demand ones the audio thread asks for when a render finds a
// range absent — which is how T3 proves its own detector.
//
// **What step 5 is still not.** Repair on open is step 3a (done); `tape_load`
// as open-and-set-head is step 3b; `tape_late`'s own push, the flag and
// `tape.pool` in `state` are step 7; the peaks sidecar and the streaming
// control-thread readers are step 6. And `--tape-dir` is still what turns any
// of it on: with no directory this class is inert, the pool is unbounded and
// the host behaves exactly as it did.
#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "spsc.hpp"
#include "tape.hpp"
#include "tape_store.hpp"

namespace arb::host {

// audio -> I/O: "range `range` of track `track` holds `frames` valid frames
// from its start". `flush` marks the end of a take: write the partial range,
// then the header with no lag. A POD, so the push allocates nothing.
struct RangeCommit {
    int32_t track = 0;
    int32_t flush = 0;
    int64_t range = 0;
    int64_t frames = 0;
};

// Compaction (C1 step 8, design § 3.8). A span of a medium the UI still
// references -- a clip, its lift buffer, its one undo record -- in frames.
struct KeepSpan {
    int64_t from = 0;
    int64_t len = 0;
};

// Where a kept span landed in the compacted medium. The UI remaps every
// `src_offset` it holds through this: a reference at `from + d` becomes
// `to + d`.
struct CompactMove {
    int64_t from = 0;
    int64_t to = 0;
    int64_t len = 0;
};

class TapeIo {
public:
    // The `data` payload starts here, so every range is page- and
    // cluster-aligned (§ 3.1, § 3.5).
    static constexpr int64_t kHeaderBytes = 4096;
    static constexpr int64_t kFrameBytes = 8;      // interleaved stereo float32
    static constexpr int64_t kRangeBytes = Tape::kRangeFrames * kFrameBytes;   // 384 KiB
    // A take at 1x fills one range a second; 256 is four minutes of backlog,
    // which is far more than the pool can hold unsynced. A full ring is
    // counted, never waited on.
    static constexpr std::size_t kRingSlots = 256;

    // The last N commits' wall time, for the bench: step 2's measurement is
    // "the commit on the eMMC -- `pwrite` + header + `fdatasync` per range,
    // alone -- against C6's 0.54 + 10 ms" (§ 6.2), and the honest place to
    // take it is the production path rather than a re-implementation of it.
    // 4096 samples is 70 minutes of a take at one range a second.
    static constexpr std::size_t kTimeSamples = 4096;

    // How far past the range being committed the filesystem is asked to have
    // blocks (§ 3.4's lead, #158). Four ranges is 4.1 s of cover — four
    // commits ahead of a take at 1x — and 1.5 MiB of allocated-but-unwritten
    // blocks past the head of a recorded track, which the next take on that
    // track reuses. It is deliberately small: with `FALLOC_FL_KEEP_SIZE` those
    // blocks stay allocated past the file's end until something truncates, so
    // the lead is also the dead space a project that never records again
    // carries, per recorded track.
    static constexpr int64_t kAllocLeadRanges = 4;

    // Keep the audio thread's free list at least this deep. A take needs one
    // slot a second; four is four seconds of cover for a card that is answering
    // slowly, and it is what stops an eviction ever happening *because* the
    // take asked.
    static constexpr int64_t kFreeLowWater = 4;

    // § 3.6's horizon, in **seconds of wall clock** — so the window in frames
    // is these times the transport's own speed, four ranges at 1x and sixteen
    // at 4x. `ahead` 4 s because the eMMC serves a range in ~3 ms and a card's
    // worst read stall is C6b's unmeasured number; `behind` 1 s because a REV
    // hold reverses instantly and one range behind means the first block
    // backwards is warm. Both are flags (`--tape-readahead-seconds`,
    // `--tape-behind-seconds`), because play-testing is what settles them.
    static constexpr double kAheadSeconds = 4.0;
    static constexpr double kBehindSeconds = 1.0;
    // Fetches *started* per turn of the loop. A turn is 5 ms live and one
    // rendered block offline, and a range buys 1.024 s, so eight a turn fills
    // the deepest horizon in well under the time the playhead takes to reach
    // it — while keeping one turn's worth of card latency bounded, so a
    // commit behind it is not held up by a burst of read-ahead.
    static constexpr int kWantsPerTurn = 8;

    // **The peaks sidecar** (#163, design § 3.6). `clip_peaks` is TRACK's
    // waveform, and with the tape on the card drawing one bank of four lanes
    // cost **4.0 s** on the bench CM4 (#162) -- 830 ms a clip reading the card
    // and 110 ms scanning it. So this thread works out `kPeakBins` min/max
    // pairs for every range it commits or fetches -- 128 B a range, ~60 KB for
    // an eight-minute track -- keeps them in RAM and writes them beside the
    // medium as `tape-N.peaks`. A draw at or below that resolution is then
    // answered out of RAM and neither reads nor scans a sample.
    //
    // **It is a cache, and nothing depends on it.** The file is not synced, a
    // missing or stale one is discarded on open, and every path that cannot be
    // answered from it falls through to the audio -- which is the code that was
    // there before, fetching as it goes, so a fall-through is slow and never
    // wrong. `--no-tape-peaks` is the *before* switch (#162's rig measures both
    // sides on one boot), and `peaks_hit` / `peaks_miss` are the counter a
    // cache-warm bench cell needs, because wall time cannot say whether the
    // card was asked.
    static constexpr int     kPeakBins = 16;
    static constexpr int64_t kPeakBinFrames = Tape::kRangeFrames / kPeakBins;   // 3072
    static_assert(Tape::kRangeFrames % kPeakBins == 0, "a peak bin is whole frames");
    // `tape-N.peaks`: "ARBPEAK1", the bin geometry, the sample rate, and the
    // frame count the medium's header claimed when it was written -- which is
    // the whole staleness check, since a medium and its sidecar are only ever
    // written together. Then the bins, then one validity byte a range.
    static constexpr int64_t kPeaksHeaderBytes = 32;

    struct Stats {
        int64_t queued = 0;      // pushed by the audio thread
        int64_t committed = 0;   // ranges on the card
        int64_t dropped = 0;     // ring full: the audio thread could not push
        int64_t absent = 0;      // the slot was gone before the I/O thread read it
        int64_t errors = 0;      // a store call refused
        int64_t syncs = 0;
        int64_t queue = 0;       // ranges popped and not yet committed
        int64_t fetched = 0;     // ranges read back off the card
        int64_t evicted = 0;     // resident ranges given up to free a slot
        int64_t starved = 0;     // a want with no slot to put it in, deferred
        // Repair on open (step 3, § 3.5): media whose length and header
        // disagreed, and the frames that disagreement cost. `lost` is what a
        // cut mid-take took with it -- up to two ranges, § 3.3's bound -- and
        // it is the number the provisional clip (DDR-070) has to be shortened
        // by, so it is reported rather than only fixed.
        int64_t repaired = 0;
        int64_t lost_frames = 0;
        int64_t refused = 0;     // a file in the directory that is not ours
        // The lead (§ 3.4, #158). `alloc_refused` is a filesystem that cannot
        // preallocate: counted once per medium, after which that medium grows
        // the way it did before -- so it is a fact about the card, not an
        // error rate.
        int64_t allocs = 0;
        int64_t alloc_bytes = 0;
        int64_t alloc_refused = 0;
        // Storage refused for want of space (§ 3.7 reason 2, step 7). Counted
        // rather than flagged so a second full card during one session is a
        // second push; `AudioHost::poll` ends the take on the rise.
        int64_t disk_full = 0;
        // The wanted set (step 5): how many ranges the last turn asked to keep
        // resident, and how many turns the `pool / 2` cap truncated. A `capped`
        // that climbs is eight tracks at 4x — the rare case § 3.6 names — and
        // it is the number that says the horizon flags want lowering.
        int64_t wanted = 0;
        int64_t capped = 0;
        // The sidecar (#163): ranges it holds bins for across all eight
        // tracks, and how many `clip_peaks` answers came out of it against how
        // many had to read the audio. A `peaks_miss` that climbs while drawing
        // a settled clip is the bins count being asked too fine, not the
        // sidecar being cold.
        int64_t peaks_ranges = 0;
        int64_t peaks_hit = 0;
        int64_t peaks_miss = 0;
    };

    ~TapeIo();

    // ---- control thread, before the audio thread runs ----------------------
    // `dir` empty = no medium on storage: every call below is a no-op.
    void configure(Tape* tape, TapeStore* store, const std::string& dir, uint32_t sr);
    bool on() const { return on_; }
    std::string medium_path(int t) const;
    const std::string& last_error() const { return last_error_; }
    // How many times storage has refused a commit for want of space (ENOSPC),
    // and how many frames of track `t` a commit has actually made durable.
    // Both are read by the control thread on every `poll`, so neither may
    // touch `mu_` — that mutex is held across a commit and a state document
    // must never wait behind the card (#93's freeze).
    int64_t disk_full() const { return disk_full_.load(std::memory_order_relaxed); }
    int64_t durable_frames(int t) const {
        return t >= 0 && t < Tape::kTracks ? durable_pub_[t].load(std::memory_order_relaxed) : 0;
    }
    // § 3.7 reason 2's prevention, and Elvin's answer to § 7 question 4: REC is
    // refused while the medium's filesystem has less than **twice the pool**
    // free (~92 MB at the default 120 track-seconds), so "the card is full"
    // happens before a take rather than in the middle of one. True means
    // "there is room" — and so does an unanswerable question: with no medium
    // on storage, or a filesystem that will not say, a refusal would be an
    // invention. [deviation from § 3.7: the design has the I/O thread check
    // this on `arm` and cache it; it is asked here, on the control thread, at
    // the `record` that starts the take. `statvfs` is microseconds and REC is
    // a key press, not a hot path -- and a cached answer taken at `arm` is a
    // reading of the card from before whatever filled it.]
    bool space_for_take() const;
    // Frames the medium on the card holds for track `t`, as its header claims
    // after repair -- 0 for a medium that has never been opened. `tape_load`
    // (step 3b) is what turns this into the track's head and the provisional
    // clip's length; step 3a only makes it true.
    int64_t medium_frames(int t) const;
    // Opens all eight media, repairing each (§ 3.5), and **creates the ones
    // that are not there** -- `open_medium` passes `create`, so a directory
    // this has run over holds eight files, some of them empty. `fetch` and the
    // first commit open lazily, which is fine while a take is the only reader;
    // a caller that wants the repaired frame counts before touching the tape
    // calls this. Returns false if any file was refused (not one of ours).
    // Whether a project folder should carry eight files or only the tracks
    // that have audio is step 3b's, with the rest of #157.
    bool open_all();

    // ---- where the media live (C1 step 3b, #157) ---------------------------
    // The folder the eight media are in. Control thread only: it is the one
    // writer (`set_dir`, under `mu_`), so reading it there needs no lock, and a
    // `state` document never waits behind the card for it.
    const std::string& dir() const { return dir_; }
    // Point the tape at `dir`. Control thread, no take open; it flushes first.
    //
    //   * `move` -- SAVE (Elvin's answer on #157, (a)): every medium of the
    //     current folder, and its sidecar, is **renamed** into `dir` and the
    //     take carries on there. A rename within one disk is instant however
    //     big the file is, so SAVE never copies audio (§ 3.3). The open handles
    //     follow the rename and the pool is untouched: it is the same audio at
    //     the same offsets. Nothing moves at all if any of the sixteen names
    //     (every track's medium and sidecar, with a file here or not) is taken
    //     in `dir` (`exists`), and a rename that fails part way -- another
    //     disk -- is rolled back (`move_failed`). A rollback that itself fails
    //     is `move_split`, the one failure that does change something: `err`
    //     names each file left in `dir`, and `dir()` stays the old folder
    //     (#484). `dir` naming the current folder (another spelling, a
    //     symlink) moves nothing and succeeds.
    //   * not `move` -- LOAD: forget the current media (every resident range
    //     retired through the evictor's grace path, the sidecars dropped
    //     without unlinking) and open the ones in `dir` **in place**, each
    //     repaired (§ 3.5) -- the open-and-set-head § 6 step 3 asks of
    //     `tape_load`. A track with no file in `dir` gets none until its first
    //     commit creates it, so a project folder holds only the tracks that
    //     have audio.
    //
    // `frames[t]` comes back as the medium's repaired claim -- the head the
    // caller sets -- 0 for no file, and -1 for a file that is not one of our
    // media (refused and left alone, as `repair` does). False + `code`/`err`
    // changes nothing, except `move_split` (above).
    bool set_dir(const std::string& dir, bool move, int64_t frames[Tape::kTracks],
                 std::string& code, std::string& err);
    // #821 (D99): the phone renames the project the tape is open in. The
    // folder itself is renamed to `to` (no-replace) and `dir()` follows: the
    // open handles follow a rename, so nothing is closed, reopened or copied,
    // and every path made after this (a first commit, a sidecar) is in `to`.
    // Drained first and refused `COMPACTING`, `set_dir`'s rules; the caller
    // refuses a take. `EXISTS` for a taken name. Never on a `FakeStore`,
    // whose media are not in a folder.
    bool rename_dir(const std::string& to, std::string& code, std::string& err);

    void start();                     // live: spawn the thread
    void stop();                      // drain, join, close every medium

    // ---- the wanted set's inputs (step 5) ----------------------------------
    // Control thread: the clip list the I/O thread plans read-ahead from. A
    // copy, taken under `mu_`, because the list the audio thread plays is the
    // audio thread's and must not be walked by anyone else. Call it wherever
    // `Ck::SetClips` is pushed, with the same list.
    void set_clips(int t, const std::vector<Clip>& clips);
    // § 3.6's horizon in seconds of wall clock. `ahead <= 0` turns read-ahead
    // off altogether, which is T3's negative control (b). Control thread,
    // before or after `configure`.
    void set_horizon(double ahead_s, double behind_s);
    double ahead_seconds() const { return ahead_s_; }
    double behind_seconds() const { return behind_s_; }
    // The grace period § 3.6 puts between retiring a table entry and reusing
    // its slot. `kAuto` is production: **live only**, because offline
    // `service()` is a step of the render loop and no callback can be in
    // flight. `kAlways` makes the wait happen offline too, which is the only
    // way a *deterministic* test can watch the mechanism work; `kNever` is
    // T5's negative control — the configuration § 3.6 says is unsafe — and it
    // is a **live** switch, since the fault it admits is a race and a race
    // cannot be made to fire on one thread. Tests only.
    enum class GracePolicy { kAuto, kAlways, kNever };
    void set_grace(GracePolicy g) { grace_policy_ = g; }

    // ---- audio thread ------------------------------------------------------
    // Never blocks, never allocates. False = the ring was full (counted).
    bool enqueue(const RangeCommit& c);

    // ---- control thread ----------------------------------------------------
    void service();                   // offline: one turn of the loop, on this thread
    void flush();                     // drain and return when the card has it
    // Control thread: make range `k` of track `t` resident, blocking on the
    // card if it has to (`bake`, `clip_peaks`, `tape_save` and the rest read
    // fetch-then-read). False = no slot, or the store refused.
    bool fetch(int t, int64_t k);
    // Mark [from, from+frames) dirty before the control thread writes it, so
    // the evictor cannot take a range between the write and its commit.
    // `persist_span` clears each range as it commits it.
    void hold_span(int t, int64_t from, int64_t frames);
    // The mirror of `hold_span`, for frames the control thread marked and then
    // did **not** write (the pool had no slot for them: C1 step 6's short
    // `tape_edit`). A range left marked is a range the evictor may never take,
    // so the mark has to come back off even though nothing was committed.
    void release_span(int t, int64_t from, int64_t frames);
    // It wrote frames [from, from+frames) of track `t` itself (`tape_edit`,
    // `tape_load`): commit every range that write completed, plus the partial
    // tail, and return when they are durable. Allowed to block — this is not
    // the audio thread — and only ever called with no take open, which is what
    // keeps it from interleaving with the ring's own claims.
    void persist_span(int t, int64_t from, int64_t frames);

    // ---- the peaks sidecar (#163, design § 3.6) ----------------------------
    // Control thread: the min/max envelope of `[from, from + len)` of track
    // `t`'s medium in `bins` bins, **from RAM alone**. False = the sidecar
    // cannot answer this one and the caller must read the audio; `mn`/`mx` are
    // left empty. It is refused for three reasons, all of them honest:
    //
    //   * the question is **finer than the sidecar** -- an output bin has to
    //     cover a whole `kPeakBinFrames` bin, or the answer would be coarser
    //     than the zoom asked for;
    //   * the span leaves the medium's **durable prefix**. A range the pool has
    //     written and the card has not taken has no entry, or has a stale one
    //     from a previous take that appended into it, and `durable_end` is the
    //     one number that excludes both;
    //   * a range in the span has no bins yet (a medium opened without a
    //     sidecar, whose ranges get theirs as they are fetched).
    //
    // Its own small mutex, taken for the length of a `memcpy`-shaped loop, so
    // a redraw never waits behind the card the way `mu_` would make it (#167).
    //
    // **The envelope is inclusive, not exact**: an output bin takes whole
    // sidecar bins, so one straddling its edge -- or straddling the medium's
    // own end, where the bin folded in the silence past it -- widens it by up
    // to `kPeakBinFrames` of neighbouring audio. For a waveform at one bin a
    // pixel that is invisible; for a test it is why agreement is asserted on
    // spans that line up with the bins.
    bool peaks_for(int t, int64_t from, int64_t len, int bins,
                   std::vector<float>& mn, std::vector<float>& mx) const;
    // Write `tape-N.peaks` for track `t`. Called on the commit that ends a
    // take and at `stop()`; a cache, so it is not synced and a failure is not
    // an error. False = nothing written (off, no medium, no bins).
    bool save_peaks(int t);
    // Ranges track `t` holds bins for. Tests and the bench.
    int64_t peaks_ranges(int t) const;
    // Tests only, and the #56 rule's negative control: multiply every bin
    // track `t` holds, so a **stale** sidecar can be shown to fail the same
    // agreement test a fresh one passes.
    void corrupt_peaks(int t, float scale);
    // #167's *before* switch, tests only: hold `mu_` across the copy's reads
    // and writes the way it was held until #167, so the test that measures the
    // freeze can be shown to go red. Without it that test is a green harness
    // and the #56 rule says a green harness is not evidence -- which is the
    // whole reason this fault reached a board: `test_compact_cancelled_live`
    // passed throughout, against a store whose slices finish in microseconds.
    void set_compact_holds_lock(bool v) { compact_holds_lock_ = v; }
    // #637's, the same idea for a take's commit: hold `mu_` across its
    // `pwrite`s and `fdatasync`, as it was held until #637. Tests only.
    void set_commit_holds_lock(bool v) { commit_holds_lock_ = v; }

    // `--no-tape-peaks`: the *before* switch, so one boot of the #162 rig
    // measures a draw with the sidecar and without it. Set before `configure`.
    void set_peaks(bool v) { peaks_on_ = v; }
    bool peaks_on() const { return peaks_on_; }

    // ---- compaction (C1 step 8, design § 3.8) ------------------------------
    // A job's life. `kDone` waits for `compact_finish`; `kCancelled` and
    // `kFailed` wait for `compact_clear`. One job at a time, any track.
    enum class CompactState { kIdle, kRunning, kDone, kCancelled, kFailed };

    // Control thread: copy the kept spans of track `t` into
    // `<medium>.compact`, on the I/O thread. The spans are sorted and
    // coalesced here, and each one lands on a **range boundary** in the new
    // medium, so a range of the result never straddles two source spans.
    //
    // The copy reads the **card**, not the pool, which is why the caller must
    // have flushed: everything the medium is going to hold has to be durable
    // before the first byte is read. That is the same rule as "never while a
    // take is open", arrived at from the other side.
    //
    // False = a job is already running, compaction is off (no `--tape-dir`),
    // or the medium would not open. Live, the job runs on the I/O thread and
    // is cancellable; offline there is no thread, so it runs here and returns
    // `kDone` -- which is `flush()`'s own arrangement, and it keeps one wire
    // shape for both (`poll()` pushes the result either way).
    // `freed`, when given, comes back with `compact_freed()`'s answer as of
    // this start -- so the reply's `dead_frames` costs the caller no second
    // trip through `mu_` while the copy is running (#167).
    bool compact_start(int t, std::vector<KeepSpan> keep, int64_t* freed = nullptr);
    CompactState compact_state() const;
    int compact_track() const;
    // Ask a running job to stop: § 3.8's "cancelled if PLAY or REC arrives".
    // The scratch file is deleted and the medium is left untouched. Returns
    // once the job has stopped, so the caller may start a take immediately.
    void compact_cancel();
    // Control thread, after `kDone`: swap the new medium in -- rename over the
    // old, reopen it, and drop every resident range of the track, whose slots
    // now hold audio from frame offsets that have moved. The map and the new
    // frame count come back with it. False + `err` leaves the old medium in
    // place and the job cleared, so nothing is half-swapped.
    bool compact_finish(std::vector<CompactMove>& map, int64_t& frames, std::string& err);
    // After `kCancelled` or `kFailed`: the job's own error, then clear it.
    std::string compact_error() const;
    void compact_clear();
    // Frames of the medium no kept span covers, as of the last `compact_start`
    // -- what a compaction would win back. The UI is the only thing that knows
    // the keep set, so `state` reports the medium's length and the UI does the
    // subtraction; this is here for the tests and the bench.
    int64_t compact_freed() const;

    Stats stats() const;
    // Commit wall time in microseconds: how many were timed, the worst, and a
    // copy of the ring for percentiles. Its own small mutex, held for a few
    // nanoseconds either side, so reading it never waits behind the card.
    void commit_times(std::vector<int32_t>& out, int64_t& count, int32_t& worst) const;
    // The `fdatasync` alone, the same ring, the same percentiles. Split out
    // when #158's lead did **not** bring the commit's p99 inside § 6.2's
    // ~20 ms on the eMMC: "the commit is 63 ms" does not say whether the cost
    // is our two `pwrite`s or the card, and the two have different answers.
    void sync_times(std::vector<int32_t>& out, int64_t& count, int32_t& worst) const;
    // T8's negative control: claim the range just written instead of the one
    // before it. The lag is what T8 protects, so a test has to be able to
    // switch it off and see the protection go.
    void set_no_lag(bool v) { no_lag_ = v; }
    // § 3.4's lead. On by default; `--no-tape-prealloc` is the *before* switch,
    // which is what lets `arbutus_os/bench/c1-pi.sh` measure both sides of
    // #158 on one boot -- and what the lead's own test uses as its negative
    // control. Set before the thread starts.
    void set_prealloc(bool v) { prealloc_ = v; }
    bool prealloc() const { return prealloc_; }
    // `--tape-free-bytes`: an upper bound on what the filesystem is believed
    // to have free, so `space_for_take` can be tested without filling a disk.
    // Negative = ask the filesystem and believe it. Tests and the bench only.
    void set_free_cap(int64_t b) { free_cap_ = b; }

    // ---- the live WAV header (§ 3.5) ---------------------------------------
    // `RIFF`/`WAVE`, `fmt ` (IEEE float, 2 ch), `fact` (the frame count), a
    // `JUNK` pad, then `data` whose payload begins at byte 4096. Writes
    // exactly `kHeaderBytes`.
    static void write_header(uint8_t* h, uint32_t sr, int64_t frames);
    // The frame count a header claims, or -1 when `h` is not one of ours.
    static int64_t header_frames(const uint8_t* h, int64_t bytes);

private:
    struct Medium {
        StoreHandle h = kNoHandle;
        bool opened = false;          // tried; `h` says whether it worked
        int64_t durable_end = 0;      // frames the last commit made durable
        int64_t claimed = 0;          // frames the header on the card claims
        int64_t alloc_end = 0;        // bytes the lead has had allocated (#158)
        bool    alloc_off = false;    // this filesystem cannot preallocate
    };

    void run();                       // the thread body
    // One turn: grace, queue, commits, wants, eviction. `final`
    // ignores a stalling store, which only a shutdown does -- otherwise a fake
    // card that stopped answering would swallow the take at teardown.
    //
    // It takes the caller's own lock on `mu_` rather than assuming one, because
    // `compact_step` **drops** it around the copy's reads and writes (#167) and
    // a `lock_guard` cannot be dropped. Every other thing a turn does still
    // runs with it held from end to end.
    void drain(std::unique_lock<std::mutex>& lk, bool final = false);
    // One commit. Entered and left with `lk` held, but it **drops** it around
    // the card's part -- the lead, both `pwrite`s and the `fdatasync` (#637) --
    // with `io_busy_` up, so no closer runs, and `committing_` up, so a second
    // commit waits for it.
    void commit(const RangeCommit& c, std::unique_lock<std::mutex>& lk);
    // A store call refused: count it, keep the message, and tell a full card
    // from every other refusal (step 7). `mu_` held, like its callers. The
    // second form takes the store's `no_space()` as read straight after the
    // refused call, which a commit does before it has the lock back.
    void note_error(const std::string& err);
    void note_error(const std::string& err, bool no_space);
    // § 3.4's lead: have blocks for everything up to `need_end` bytes. Called
    // only from inside `commit`'s window (`mu_` dropped, #637): it touches the
    // medium's `alloc_*` fields, which only a commit writes, one at a time.
    void extend_alloc(Medium& m, int64_t need_end);
    void record_time(int64_t us, int64_t sync_us);
    // `mu_` held. `create` is false for a read (#475): a fetch of a track whose
    // folder holds no medium returns false and leaves it untried, so the
    // track's first commit still makes one.
    bool open_medium(int t, bool create = true);
    // § 3.5's repair, on a medium that has just been opened. `mu_` held, and
    // it blocks on the card -- both true of every caller.
    bool repair(int t);
    // The sidecar's own (#163). `note_peaks` is called from a fetch with `mu_`
    // held and from a commit's card window without it; it takes `peaks_mu_`,
    // and nothing takes `mu_` while holding that. `peaks_image` and
    // `write_peaks` are `save_peaks_locked`'s two halves: a commit runs them
    // in its window, so STOP's sidecar write does not hold `mu_` (#826).
    void note_peaks(int t, int64_t k, const float* src, int64_t n);
    bool save_peaks_locked(int t);               // `mu_` held
    bool peaks_image(int t, int64_t frames, std::vector<uint8_t>& buf) const;  // no lock needed
    bool write_peaks(const std::string& path, const std::vector<uint8_t>& buf);
    void load_peaks(int t);                      // `mu_` held, after `repair`
    void drop_peaks(int t, bool unlink);         // `mu_` held
    std::string peaks_path(int t) const;
    void mark(int t, int64_t k, bool dirty);     // `mu_` held
    bool is_dirty(int t, int64_t k) const;       // `mu_` held
    void advance_grace();             // `mu_` held
    // Whether the caller may spend the audio thread's free list down to
    // nothing. The I/O thread's own fetches -- read-ahead and the audio
    // thread's demand asks alike -- may **not**: that list is the take's cover,
    // and § 3.6's order is that a pool under pressure degrades playback to
    // `tape_late` *before* it ends a take. A control-thread reader may, because
    // it has to give a real answer and it is not competing every block.
    enum class Urgency { kReadAhead, kReader };
    int32_t take_slot(Urgency u);     // a slot to fill, evicting if need be
    // Top `reclaimed_` up to `want` slots. `relax` allows the guard band to be
    // broken, which only a caller that has something to put in the slot may do.
    void evict_to(int64_t want, bool relax);
    void top_up_free();               // hand the audio thread its low water
    int64_t free_mark() const;        // the free list's low-water mark
    bool fetch_locked(int t, int64_t k, Urgency u = Urgency::kReader);   // `mu_` held
    // Step 5: recompute the wanted set from the clip snapshot and the
    // published playhead, then fetch what of it is absent. Both `mu_` held.
    void plan_wanted();
    void serve_wanted();
    // One turn's worth of the copy: at most `kCompactRanges` ranges, so the
    // commits and fetches of a turn still get served.
    //
    // It is entered with `lk` held and leaves it held, but **drops it around
    // every `pread`/`pwrite` pair** -- the fix for #167. Holding `mu_` across
    // the card is what froze the whole host for a compaction's seven seconds:
    // the copy's thread is only 9 % busy in that time and the rest is a thread
    // asleep in the kernel holding the one lock `poll()`, a fresh `hello`, the
    // `started` reply and the cancel itself all need. While the lock is down,
    // `io_busy_` is up and marks the window: anything that would close the
    // scratch handle or the medium under the copy waits for it through
    // `wait_io`, which is at most one range of card time.
    void compact_step(std::unique_lock<std::mutex>& lk);
    // Wait out an in-flight `compact_step` slice or commit (#637), `lk` held
    // on the way in and on the way out. Call before closing a handle either
    // may be using.
    void wait_io(std::unique_lock<std::mutex>& lk);
    void compact_drop(bool unlink);              // `mu_` held: close + forget the scratch
    void add_wanted(int mt, int64_t k, int64_t dist, bool rec);   // `mu_` held
    void sync_dirs(const std::string& a, const std::string& b);   // #591, after a rename
    bool is_wanted(int t, int64_t k) const;      // `mu_` held

    Tape*       tape_ = nullptr;
    TapeStore*  store_ = nullptr;
    std::string dir_;
    uint32_t    sr_ = 48000;
    bool        on_ = false;
    bool        no_lag_ = false;
    bool        prealloc_ = true;
    int64_t     free_cap_ = -1;

    Spsc<RangeCommit, kRingSlots> ring_;
    Medium med_[Tape::kTracks];
    std::vector<uint8_t> header_;     // one scratch page, never on the audio thread
    // Lock-free mirrors of the two `Medium` fields the control thread reads
    // every poll. `mu_` was held across a commit until #637, so reading `med_`
    // there was reading behind the card; the mirror stays, it costs nothing.
    std::atomic<int64_t> disk_full_{0};
    std::atomic<int64_t> durable_pub_[Tape::kTracks]{};

    // ---- the bounded pool's bookkeeping, all of it `mu_`-held ---------------
    // Popped off the ring and not yet on the card. Their slots are the only
    // copy of that audio, so `dirty_` keeps the evictor off them.
    std::deque<RangeCommit>  queue_;
    std::vector<uint8_t>     dirty_[Tape::kTracks];
    // A retired slot and the block at which it may be reused.
    struct Grace { int32_t slot = 0; int64_t at_block = 0; };
    std::deque<Grace>        grace_;
    std::vector<int32_t>     reclaimed_;         // past grace, ours to fill
    GracePolicy              grace_policy_ = GracePolicy::kAuto;

    // The wanted set (step 5), all `mu_`-held. `snap_` is the control thread's
    // copy of each track's clips; `wanted_` is one entry per range per track,
    // the set as the evictor reads it (nonzero = wanted; while the set is
    // being built, 1 + the range's index in `want_list_`, #591);
    // `want_list_` is the same set ordered nearest-first, which is the order it is fetched in. All three keep their
    // capacity between turns, so recomputing every turn allocates nothing after
    // the first — on a thread where allocating would be legal anyway.
    std::vector<Clip>        snap_[Tape::kTracks];
    std::vector<int32_t>     wanted_[Tape::kTracks];   // 0, or 1 + its `want_list_` index
    struct WantRange { int32_t track; int64_t range; int64_t dist; bool rec; };
    std::vector<WantRange>   want_list_;

    // ---- the compaction job (§ 3.8), all of it `mu_`-held ------------------
    // 8 ranges is 3 MB a turn: enough that a 2 x ruler medium is seconds
    // rather than minutes at the 5 ms poll, small enough that a cancel lands
    // inside one of them.
    static constexpr int64_t kCompactRanges = 8;
    struct Compact {
        int         track = -1;
        CompactState state = CompactState::kIdle;
        std::vector<KeepSpan>   keep;            // sorted and coalesced
        std::vector<CompactMove> map;
        std::size_t at = 0;                      // which span is being copied
        int64_t     done = 0;                    // frames of it copied
        int64_t     written = 0;                 // frames in the new medium
        int64_t     freed = 0;                   // frames the old one loses
        StoreHandle h = kNoHandle;
        std::string path;
        std::string err;
    };
    Compact comp_;
    // `run()`'s wait predicate reads this without `mu_`: a turn that left a
    // job running must not then sleep out the poll interval.
    std::atomic<bool> compact_busy_{false};
    // How many card calls are in flight with `mu_` dropped: a `compact_step`
    // slice (#167) and a commit (#637), which can overlap -- so a count, not a
    // flag, or the first to finish would clear the other's. `mu_`-held;
    // `io_cv_` announces the end of each, and `wait_io` waits for zero.
    int  io_busy_ = 0;
    bool committing_ = false;                    // a commit is in its window (#637)
    bool compact_holds_lock_ = false;            // #167's *before* switch
    bool commit_holds_lock_ = false;             // #637's *before* switch
    std::vector<uint8_t> copy_;                  // one range of scratch
    // The copy's own header page. `header_` is shared with `commit` and
    // `repair`, and once the copy writes its header without `mu_` a control
    // thread's `persist_span` could be writing that buffer at the same moment.
    std::vector<uint8_t> chead_;
    // The commit's own, for the same reason (#637): `repair` still writes
    // `header_` under `mu_` while a commit's header write is outside it.
    std::vector<uint8_t> whead_;
    // Whether the last `plan_wanted` had both its inputs -- a published
    // playhead and a horizon above zero. It is what tells `evict_to` whether
    // step 2b's guard band is still needed: with a wanted set the band is a
    // *second*, blunter protection over the same ground, and keeping both
    // protected up to seven ranges of an eight-slot pool, which left the
    // evictor no victim and ended a take the card was serving perfectly.
    bool                     have_wanted_ = false;
    double                   ahead_s_ = kAheadSeconds;
    double                   behind_s_ = kBehindSeconds;

    // The store is serialised by this: the ring's consumer holds it, and so
    // does the control thread when it commits a span of its own.
    mutable std::mutex      mu_;
    // `wake_` wakes the thread, `done_cv_` tells a waiting control thread that
    // the ring has been drained past the point it asked about. `done_` counts
    // items *popped*, whatever the commit's outcome, so a refused write can
    // never leave `flush()` waiting for ever.
    std::mutex              wake_mu_;
    std::condition_variable wake_, done_cv_;
    // On `mu_`: the end of a `compact_step` slice, for `wait_io` (#167).
    std::condition_variable io_cv_;
    std::thread             thread_;
    // Whether a real I/O thread exists, which is the same question as "can a
    // callback be running *right now*". Offline it cannot — `service()` is the
    // render loop's own call on the render loop's own thread — and that is what
    // makes the grace period a live-only wait rather than an offline deadlock.
    std::atomic<bool>       live_{false};
    std::atomic<bool>       stop_{false};
    std::atomic<int64_t>    done_{0};
    std::string             last_error_;

    std::atomic<int64_t> queued_{0}, committed_{0}, dropped_{0}, absent_{0}, errors_{0},
        syncs_{0}, fetched_{0}, evicted_{0}, starved_{0}, queue_depth_{0},
        repaired_{0}, lost_frames_{0}, refused_{0}, allocs_{0}, alloc_bytes_{0},
        alloc_refused_{0}, wanted_n_{0}, capped_{0};

    mutable std::mutex   times_mu_;
    std::vector<int32_t> times_;      // ring of kTimeSamples, us per commit
    std::vector<int32_t> syncs_us_;   // the same ring, the `fdatasync` alone
    std::size_t          times_at_ = 0;
    int64_t              times_n_ = 0;
    int32_t              times_max_ = 0;
    int32_t              sync_max_ = 0;

    // The sidecar (#163). `peaks_[t]` is `kPeakBins` min/max pairs a range,
    // interleaved (`2 * (k * kPeakBins + b)` is the min, `+ 1` the max);
    // `peaks_ok_[t]` is one byte a range, so a medium opened without a file
    // fills in as its ranges are fetched rather than answering with zeros.
    bool                 peaks_on_ = true;
    mutable std::mutex   peaks_mu_;
    std::vector<float>   peaks_[Tape::kTracks];
    std::vector<uint8_t> peaks_ok_[Tape::kTracks];
    mutable std::atomic<int64_t> peaks_hit_{0}, peaks_miss_{0};
};

}  // namespace arb::host
