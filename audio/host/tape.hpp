// tape.hpp — 8 stereo float32 tracks as a table of fixed-size ranges over a
// pool of slots (software-stack-v1.md §8; C1 / tape-streaming-design.md).
//
// The audio process is a *renderer* of a clip list, not the editor. The UI owns
// lift / drop / slice / undo and every other editing semantic; it sends the
// resulting clip list down with `set_clips` and this file plays it. That split
// is why there is no clip algebra here and no undo stack.
//
// The medium is **append-only**: a take is written at the track's head, never
// at its timeline position, and so is anything `tape_edit` bounces. A region a
// clip reads is therefore never overwritten, which is what lets the UI move,
// copy and undo clips as pure geometry (`src_offset`, `src_track`) — a retake
// over a moved clip, or a loop recording past its own start, cannot clobber
// audio another clip still points at. The cost is that a retake spends medium;
// `tape.tracks[i].used_frames` says how much. [default chosen here: PROTOCOL.md
// said "from the current transport position" — audio/CLAUDE.md]
//
// **The range table (C1 step 1).** A track's medium is no longer one
// `std::vector<float>`: it is `kRangeFrames`-frame ranges, each held in a pool
// slot, and a per-track table mapping range number → slot or `kAbsent`. The
// audio thread reads the table (acquire) and then the slot, inside one
// callback; it never allocates, never waits, and an absent range is **silence
// plus a count** (`late_mask`), never a null dereference. That is the shape the
// tape I/O thread needs: it can publish and retire table entries while the
// audio thread plays, which is what takes the tape off the heap and onto the
// card (`OS-11` → DDR-072).
//
// A range is **49,152 frames = 1.024 s = exactly 384 KiB = 96 pages**, so every
// range is a whole number of memory pages and of any power-of-two cluster up to
// 128 KiB (tape-streaming-design.md § 3.1, Elvin 2026-09-23: yes).
//
// **Bounded or unbounded (C1 step 2b).** The pool has two modes and the choice
// is one number handed to `init`:
//
//   * `pool_slots == 0` — **unbounded**: `ensure(t)` makes a whole track
//     resident at once, nothing is ever evicted, and the tape is the RAM tape
//     it has always been. This is what a host started without `--tape-dir`
//     gets (only by hand since #297: the unit and the emulator both pass one),
//     because without a medium on storage there is nowhere to fetch an evicted
//     range back *from*, and a bounded pool that cannot refetch does not save
//     memory — it loses audio on its first eviction.
//   * `pool_slots > 0` — **bounded**: every slot the session can ever use is
//     allocated here, at init, and a range becomes resident only by being
//     acquired for a take (the audio thread pops the free list) or fetched back
//     off the card (the I/O thread). `--tape-pool-seconds` sets it and only a
//     `--tape-dir` turns it on, so the two switches that make the tape a file
//     are the same switch.
//
// The free list is one runtime-sized ring: the **I/O thread produces** free
// slots (having zeroed them, so an acquired slot is silence exactly as a freshly
// allocated one was) and both the audio thread (a take) and the I/O thread (a
// fetch) **take** from it. Taking is a CAS on the read index rather than a
// plain store, which is the one place this ring is not the SPSC one in
// `spsc.hpp`: a fetch that could not reach the slots a take is not using would
// strand half the pool, and playback would starve with free slots in hand.
// Nothing in it allocates and nothing in it can wait.
//
// **Absent, wanted, resident.** A table entry is a slot index, `kAbsent`, or
// `kWanted` — "the audio thread has asked for this range and the I/O thread
// has not published it yet". The audio thread moves `kAbsent → kWanted` with a
// CAS and pushes one POD onto the want ring; the I/O thread fetches and
// publishes. A `kWanted` range reads exactly as an absent one does: silence
// plus a `late_mask` bit.
//
// **Read-ahead is the I/O thread's, not this file's (C1 step 5).** Step 2b had
// `render_track` ask for the range *after* the one it was reading — a one-range
// lead, forward only, per clip. Step 5 deletes it: the horizon is now a
// *wanted set* the I/O thread computes from a snapshot of the clip lists and
// the playhead this file publishes (`set_view` / `view`), in seconds of wall
// clock rather than ranges, across all eight tracks, through the loop seam and
// capped at half the pool (`tape_io.hpp`, design § 3.6). What stays here is the
// **demand** ask: a render that finds the range it needs absent sets its
// `late_mask` bit and asks for it, so the next pass over the same tape sounds.
// That is what makes `--tape-readahead-seconds 0` a meaningful switch — with no
// horizon, every range is fetched only once the playhead has already missed it,
// which is T3's negative control (b).
//
// §8.3 of software-stack-v1 specifies mmap'd `track_<n>.f32` files with a
// 64-byte header, and §8.1 mlock. A slot is the same interleaved stereo float32
// layout, in range-sized pieces; the file format is step 2's, and the header it
// grows is the live WAV one (§ 3.5), not that 64-byte one.
//
// A medium is **allocated on first use**, not at init: 8 tracks x 8 minutes of
// stereo float32 is 1.5 GB, which a CM4 does not have and an idle session does
// not need (DDR-060 reveals bank B only once it holds a clip -- the memory
// should follow). `ensure()` is control-thread only and every read path treats
// an absent range as silence, so the audio thread never allocates and never
// touches a null. Bounded, that reasoning is finished: the pool is the whole
// memory the tape can ever cost, whatever the ruler says. [default chosen
// here: the spec sizes a track, not the tape -- audio/CLAUDE.md]
#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <vector>

namespace arb::host {

struct Clip {
    int64_t start_frame = 0;   // where it plays on the timeline
    int64_t len = 0;           // frames
    int64_t src_offset = 0;    // where it reads in the medium
    float   gain = 1.0f;
    int32_t src_track = -1;    // whose medium; -1 = the track the clip is on
};

struct ClipList {
    std::vector<Clip> clips;
};

// The playhead's journey through one block: `pos + k·step`, wrapped into
// [loop_in, loop_out) when the loop is on, else clamped to the tape.
struct Travel {
    double  pos = 0.0;
    double  step = 1.0;
    bool    loop = false;
    int64_t loop_in = 0, loop_out = 0;
    int64_t capacity = 0;

    double at(uint32_t k) const;
};

class Tape {
public:
    static constexpr int kTracks = 8;   // DDR-060, two banks of four
    // 49,152 frames = 1.024 s at 48 kHz = 384 KiB of interleaved stereo float32
    // (tape-streaming-design.md § 3.1).
    static constexpr int64_t kRangeFrames = 49152;
    static constexpr int32_t kAbsent = -1;
    // "asked for, not here yet" -- reads as absent, but only one request per
    // range reaches the want ring however many blocks pass before it lands.
    static constexpr int32_t kWanted = -2;
    // How far either side of a track's live read position a resident range is
    // left alone, in ranges. Step 2b's stand-in for step 5's wanted set, kept
    // beside it: the wanted set needs a clip snapshot and a published playhead,
    // and a host that has sent neither (every C++ unit test that does not push
    // one, and the host between `init` and the UI's first `set_clips`) still
    // gets the evictor kept off what the playhead is about to reach. Where both
    // exist the guard band is the smaller of the two and costs nothing.
    static constexpr int64_t kGuardRanges = 2;
    // Slots stay off the free list for this many rendered blocks after their
    // table entry is cleared (design § 3.6, "safe unpublishing"): a callback
    // that read the entry before the clear has finished within one block.
    static constexpr int64_t kGraceBlocks = 2;

    // The playhead as the I/O thread sees it (C1 step 5). Published by the
    // audio thread once a block -- five relaxed stores -- and read field by
    // field, so the set the reader gets may be torn across a block boundary.
    // That is deliberate and it is why nothing here is a seqlock: the wanted
    // set is a *prediction*, so a torn read makes one turn's horizon a little
    // wrong and the next turn corrects it, while a lock on the audio thread
    // would be a real fault. `pos < 0` means nothing has been published, and
    // then the I/O thread has only the guard band.
    struct View {
        int64_t pos = -1;
        double  step = 1.0;
        int64_t loop_in = 0, loop_out = 0;
        bool    loop = false;
        bool    playing = false;
    };
    void set_view(const View& v);
    View view() const;

    static int64_t range_of(int64_t frame) { return frame / kRangeFrames; }
    static int64_t ranges_for(int64_t frames) {
        return frames <= 0 ? 0 : (frames + kRangeFrames - 1) / kRangeFrames;
    }

    // `pool_slots == 0` is the unbounded pool this file's header describes;
    // anything else is the bounded one, allocated here and never grown.
    void init(int64_t capacity_frames, int64_t pool_slots = 0);
    int64_t capacity() const { return capacity_; }
    int64_t ranges() const { return ranges_; }
    bool bounded() const { return bounded_; }
    // Control thread: give track `t` its medium. False = out of memory; the
    // caller refuses rather than letting the audio thread find a short buffer.
    // Bounded, this only says the track *has* a medium -- the ranges arrive one
    // at a time, from the free list or off the card -- so it cannot fail.
    bool ensure(int t);
    bool allocated(int t) const { return allocated_[t]; }
    // Slots handed out so far — the pool's size while step 1 grows it per
    // track; `audio/README.md`'s RSS row reads it.
    int64_t slots_used() const { return int64_t(slots_.size()); }

    // ---- audio thread ------------------------------------------------------
    // Overwrites l/r with the track's clip list along `tr`. Never allocates.
    void render_track(int t, const Travel& tr, uint32_t n, float* l, float* r) const;
    // Record tap: the active instrument bus into the medium at `at`. Returns
    // the frames written: short of `n` means the pool had no slot for the next
    // range, and the caller ends the take (`tape_full {reason:"stall"}`) rather
    // than dropping audio into a hole. Unbounded it is always `n`.
    uint32_t write(int t, int64_t at, const float* l, const float* r, uint32_t n);
    // Swap in a new clip list; returns the old one for the retire queue.
    ClipList* swap_clips(int t, ClipList* next);

    // Bit `t` is set when a render on track `t` wanted a range that was absent.
    // The control thread reads and clears it per block; step 7 turns it into
    // the `tape_late` push. Absent cannot happen while `ensure()` makes whole
    // tracks resident, which is why step 1 counts it rather than reporting it.
    uint32_t late_mask() const { return late_mask_.load(std::memory_order_relaxed); }
    void clear_late_mask() { late_mask_.store(0, std::memory_order_relaxed); }

    // One rendered block. The grace period counts these, so the host calls it
    // once per block whatever the block size is.
    void tick() { blocks_.fetch_add(1, std::memory_order_release); }
    int64_t blocks() const { return blocks_.load(std::memory_order_acquire); }
    // The range the take is filling on track `t`, or -1. Never evicted.
    void set_write_range(int t, int64_t k);

    // ---- the I/O thread ----------------------------------------------------
    // A range the audio thread asked for. `false` = the ring was empty.
    struct Want { int32_t track = 0; int64_t range = 0; };
    bool take_want(Want& w);
    // The table as the evictor reads it. `slot_at` is the raw entry, so
    // negative means absent or wanted.
    int32_t slot_at(int t, int64_t k) const;
    // The last medium frame a **read** touched on track `t`, or -1 if none has.
    // The evictor's guard band is around this and nothing else: a take's write
    // head is `write_range` and its finished ranges are the first to evict.
    int64_t hot(int t) const { return hot_[t].load(std::memory_order_relaxed); }
    int64_t write_range(int t) const { return write_range_[t].load(std::memory_order_relaxed); }
    // Clear the entry if it still holds `slot` -- a CAS, so a fetch that
    // published in between is never clobbered. The slot is the caller's to
    // reuse only after `kGraceBlocks` more blocks.
    bool retire(int t, int64_t k, int32_t slot);
    // Put a fetched range in the table. Zeroes nothing: the caller filled it.
    void publish(int t, int64_t k, int32_t slot);
    // The audio thread asked and the I/O thread cannot serve it: back to absent
    // so the next block may ask again.
    void unwant(int t, int64_t k);
    // Zero the slot and hand it to the audio thread's free list. Zeroed here,
    // on a thread that may take its time, so an acquired range is silence
    // without the audio thread ever touching 384 KiB.
    void release_free(int32_t slot);
    float* slot_data(int32_t slot);
    int64_t free_count() const;
    int64_t pool_slots() const { return pool_slots_; }
    int64_t resident_count() const;
    // The one range a control-thread reader is holding a pointer into. The
    // evictor skips it; `pin(-1, 0)` releases it. One slot is enough because
    // the control thread reads one run at a time.
    void pin(int t, int64_t k) const { pin_.store(t < 0 ? -1 : t * ranges_ + k, std::memory_order_relaxed); }
    int64_t pinned() const { return pin_.load(std::memory_order_relaxed); }

    // ---- both threads ------------------------------------------------------
    // Frame `f` of track `t`'s medium, interleaved stereo, and in `run` how
    // many frames from `f` are contiguous behind that pointer (never past the
    // range's end or the capacity). NULL when the range is absent or `f` is out
    // of range -- `run` then says how many frames to skip, so a caller walks a
    // medium in runs without knowing where the boundaries are. The pointer is
    // good for this block (audio thread) or until the next `ensure` (control).
    const float* run_at(int t, int64_t f, int64_t& run) const;
    float* run_at_mut(int t, int64_t f, int64_t& run);

    // Control thread: copy `frames` interleaved stereo frames in or out,
    // zero-filling what is absent. Returns the frames touched (0 when the
    // track has no medium).
    int64_t read_frames(int t, int64_t from, int64_t frames, float* dst) const;
    int64_t write_frames(int t, int64_t from, int64_t frames, const float* src);

    // The first free frame of each medium. Moved by the audio thread when a
    // take ends and by the control thread for tape_edit/tape_load; the two
    // never overlap because both refuse while a take is open.
    std::atomic<int64_t> head[kTracks]{};

private:
    // One pool slot: kRangeFrames interleaved stereo frames. `unique_ptr` so
    // the buffer's address never moves when the pool grows.
    using Slot = std::unique_ptr<float[]>;

    // A runtime-sized single-producer / single-consumer ring of indices.
    // `spsc.hpp`'s is a compile-time size and the pool's is a flag's.
    class IndexRing {
    public:
        void reset(std::size_t n);
        bool push(int32_t v);                       // the I/O thread alone
        bool pop(int32_t& v);                       // audio thread or I/O thread
        std::size_t size() const;
    private:
        std::vector<int32_t> buf_;
        std::atomic<std::size_t> w_{0}, r_{0};
    };

    // Audio thread: make range `k` of track `t` resident from the free list.
    // False = the pool is empty, which is `tape_full {reason:"stall"}`.
    bool acquire(int t, int64_t k);
public:
    // The I/O thread's half of the same free list: a slot to fetch into.
    // `kAbsent` = none free. It is here rather than beside `acquire` because
    // the caller is the other thread.
    int32_t take_free();
    // The pool's own clock, for a `FakeStore` whose stalls are counted in
    // rendered blocks (`tape_store.hpp` `set_clock`).
    const std::atomic<int64_t>& block_counter() const { return blocks_; }
private:
    // Audio thread, from a read: ask for `k` unless it is already resident or
    // already asked for. Never blocks; a full want ring is simply dropped, and
    // the next block asks again.
    void want(int t, int64_t k) const;

    std::vector<Slot> slots_;                       // the pool
    // `mutable` for the same reason `late_mask_` is: the audio thread's read
    // path is `const` and it is the thread that asks for a range (kAbsent ->
    // kWanted). The entries are atomics; nothing else here is written by two
    // threads.
    mutable std::vector<std::atomic<int32_t>> table_[kTracks];   // range -> slot
    bool allocated_[kTracks] = {};
    ClipList* live_[kTracks] = {};                  // audio-thread owned
    std::unique_ptr<ClipList> empty_[kTracks];
    mutable std::atomic<uint32_t> late_mask_{0};
    int64_t capacity_ = 0;
    int64_t ranges_ = 0;
    bool    bounded_ = false;
    int64_t pool_slots_ = 0;

    IndexRing free_;                                // I/O -> audio: free slots
    // audio -> I/O: ranges to fetch. Sized with the pool, so a want per slot
    // fits and the ring cannot be the thing that loses a request.
    mutable std::vector<std::atomic<int64_t>> wants_;
    mutable std::atomic<std::size_t> want_w_{0}, want_r_{0};

    std::atomic<int64_t> blocks_{0};
    // `set_view`'s fields, one atomic each (see `View`).
    std::atomic<int64_t> view_pos_{-1}, view_in_{0}, view_out_{0};
    std::atomic<double>  view_step_{1.0};
    std::atomic<uint32_t> view_flags_{0};       // bit 0 loop, bit 1 playing
    std::atomic<int64_t> write_range_[kTracks];
    mutable std::atomic<int64_t> hot_[kTracks];      // last medium frame *read*
    mutable std::atomic<int64_t> pin_{-1};
};

}  // namespace arb::host
