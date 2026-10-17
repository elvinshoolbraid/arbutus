#include "tape_io.hpp"

#include <algorithm>
#include <cmath>
#include <chrono>
#include <cstring>

#if defined(__linux__)
#include <pthread.h>
#endif

namespace arb::host {

namespace {

void put32(uint8_t* p, uint32_t v) {
    p[0] = uint8_t(v);
    p[1] = uint8_t(v >> 8);
    p[2] = uint8_t(v >> 16);
    p[3] = uint8_t(v >> 24);
}
void put16(uint8_t* p, uint16_t v) {
    p[0] = uint8_t(v);
    p[1] = uint8_t(v >> 8);
}
uint32_t get32(const uint8_t* p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) |
           (uint32_t(p[3]) << 24);
}

constexpr std::chrono::milliseconds kPollInterval{5};

}  // namespace

// The layout, by offset, so the arithmetic below can be checked against § 3.5:
//
//   0    RIFF <size>  WAVE                       12
//   12   "fmt " 16    + 16 bytes                 24
//   36   "fact" 4     + the frame count          12
//   48   "JUNK" 4032  + 4032 bytes of padding    4040
//   4088 "data" <size>                           8   -> payload at 4096
//
// `JUNK` is the RIFF specification's own name for padding and every reader
// skips it, ours by construction (`hostlib/audiofile.cpp` walks the chunks and
// ignores what it does not know). `fact` carries the frame count because the
// specification asks non-PCM formats to; nothing here reads it back, the `data`
// size is what `header_frames` trusts.
void TapeIo::write_header(uint8_t* h, uint32_t sr, int64_t frames) {
    std::memset(h, 0, std::size_t(kHeaderBytes));
    const int64_t data_bytes = std::max<int64_t>(0, frames) * kFrameBytes;
    std::memcpy(h, "RIFF", 4);
    put32(h + 4, uint32_t(kHeaderBytes - 8 + data_bytes));
    std::memcpy(h + 8, "WAVE", 4);

    std::memcpy(h + 12, "fmt ", 4);
    put32(h + 16, 16);
    put16(h + 20, 3);                          // IEEE float
    put16(h + 22, 2);                          // stereo
    put32(h + 24, sr);
    put32(h + 28, sr * uint32_t(kFrameBytes)); // byte rate
    put16(h + 32, uint16_t(kFrameBytes));      // block align
    put16(h + 34, 32);                         // bits

    std::memcpy(h + 36, "fact", 4);
    put32(h + 40, 4);
    put32(h + 44, uint32_t(std::min<int64_t>(frames, 0xFFFFFFFF)));

    std::memcpy(h + 48, "JUNK", 4);
    put32(h + 52, uint32_t(kHeaderBytes - 56 - 8));

    std::memcpy(h + kHeaderBytes - 8, "data", 4);
    put32(h + kHeaderBytes - 4, uint32_t(data_bytes));
}

int64_t TapeIo::header_frames(const uint8_t* h, int64_t bytes) {
    if (!h || bytes < kHeaderBytes) return -1;
    if (std::memcmp(h, "RIFF", 4) || std::memcmp(h + 8, "WAVE", 4)) return -1;
    if (std::memcmp(h + kHeaderBytes - 8, "data", 4)) return -1;
    return int64_t(get32(h + kHeaderBytes - 4)) / kFrameBytes;
}

TapeIo::~TapeIo() { stop(); }

void TapeIo::configure(Tape* tape, TapeStore* store, const std::string& dir, uint32_t sr) {
    tape_ = tape;
    store_ = store;
    dir_ = dir;
    sr_ = sr;
    on_ = tape && store && !dir.empty();
    header_.assign(std::size_t(kHeaderBytes), 0);
    chead_.assign(std::size_t(kHeaderBytes), 0);   // the copy's own (#167)
    whead_.assign(std::size_t(kHeaderBytes), 0);   // the commit's own (#637)
    times_.assign(kTimeSamples, 0);
    syncs_us_.assign(kTimeSamples, 0);
    queue_.clear();
    grace_.clear();
    reclaimed_.clear();
    want_list_.clear();
    for (auto& c : snap_) c.clear();
    if (tape)
        for (int t = 0; t < Tape::kTracks; ++t) {
            dirty_[t].assign(std::size_t(tape->ranges()), 0);
            wanted_[t].assign(std::size_t(tape->ranges()), 0);
        }
}

void TapeIo::set_clips(int t, const std::vector<Clip>& clips) {
    if (t < 0 || t >= Tape::kTracks) return;
    std::lock_guard<std::mutex> g(mu_);
    snap_[t] = clips;
}

void TapeIo::set_horizon(double ahead_s, double behind_s) {
    std::lock_guard<std::mutex> g(mu_);
    ahead_s_ = std::max(0.0, ahead_s);
    behind_s_ = std::max(0.0, behind_s);
}

std::string TapeIo::medium_path(int t) const {
    // 1-based on the wire and on disk (`audio/library/README.md` § Projects:
    // `tape-1.wav` … `tape-8.wav`), 0-based in the code.
    return dir_ + "/tape-" + std::to_string(t + 1) + ".wav";
}

void TapeIo::start() {
    if (!on_ || thread_.joinable()) return;
    stop_.store(false, std::memory_order_relaxed);
    live_.store(true, std::memory_order_relaxed);
    thread_ = std::thread([this] { run(); });
#if defined(__linux__)
    // So `/proc/<pid>/task/*/comm` names it: the design's step 5 asks for this
    // thread's CPU (#160) and a bench probe should not have to guess which of
    // the host's threads it is. Linux caps the name at 16 bytes.
    pthread_setname_np(thread_.native_handle(), "arb-tape-io");
#endif
}

void TapeIo::stop() {
    if (thread_.joinable()) {
        stop_.store(true, std::memory_order_relaxed);
        wake_.notify_all();
        thread_.join();
    }
    live_.store(false, std::memory_order_relaxed);
    // With no `--tape-dir` `configure` never ran: `tape_` and `store_` are
    // null and there is nothing to flush (#612: the destructor read through
    // `tape_` here and every clean exit segfaulted).
    if (!on_) return;
    std::unique_lock<std::mutex> lk(mu_);
    // A compaction in flight at shutdown loses its scratch file, not its
    // medium: the swap is the only thing that would have made it real. The
    // thread is joined above when there was one, so `wait_io` is for the
    // offline case and costs nothing; it is here because every other path that
    // closes the scratch handle has it, and a rule with an exception is not a
    // rule (#167).
    if (comp_.state != CompactState::kIdle) {
        wait_io(lk);
        comp_.state = CompactState::kCancelled;
        compact_drop(true);
        compact_busy_.store(false, std::memory_order_relaxed);
    }
    drain(lk, true);                               // whatever the ring still holds
    for (int t = 0; t < Tape::kTracks; ++t) {
        Medium& m = med_[t];
        // #163: a take that was cut short leaves its last commit without the
        // `flush` that writes the sidecar, so shutdown writes it.
        if (m.h != kNoHandle) {
            save_peaks_locked(t);
            store_->close(m.h);
        }
        m.h = kNoHandle;
        m.opened = false;
    }
}

bool TapeIo::enqueue(const RangeCommit& c) {
    // Audio thread: a ring push and two relaxed adds. No lock, no wake-up —
    // the I/O thread polls on `kPollInterval`, and a range is a second of
    // audio, so five milliseconds of latency on the commit is nothing.
    if (!on_) return true;
    if (!ring_.push(c)) {
        dropped_.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    queued_.fetch_add(1, std::memory_order_relaxed);
    return true;
}

void TapeIo::run() {
    while (!stop_.load(std::memory_order_relaxed)) {
        {
            std::unique_lock<std::mutex> lk(wake_mu_);
            wake_.wait_for(lk, kPollInterval, [this] {
                return stop_.load(std::memory_order_relaxed) ||
                       // A compaction in progress must not sleep out the poll
                       // interval between its slices: 8 ranges every 5 ms
                       // would make a 2 x ruler medium ten minutes.
                       compact_busy_.load(std::memory_order_relaxed) ||
                       queued_.load(std::memory_order_relaxed) >
                           done_.load(std::memory_order_relaxed);
            });
        }
        {
            std::unique_lock<std::mutex> lk(mu_);
            drain(lk);
        }
        done_cv_.notify_all();
    }
    std::unique_lock<std::mutex> lk(mu_);
    drain(lk, true);
}

// ---- one turn of the loop ---------------------------------------------------

void TapeIo::mark(int t, int64_t k, bool dirty) {
    if (t < 0 || t >= Tape::kTracks || k < 0) return;
    std::vector<uint8_t>& d = dirty_[t];
    if (std::size_t(k) >= d.size()) return;
    d[std::size_t(k)] = dirty ? 1 : 0;
}

bool TapeIo::is_dirty(int t, int64_t k) const {
    if (t < 0 || t >= Tape::kTracks || k < 0) return false;
    const std::vector<uint8_t>& d = dirty_[t];
    return std::size_t(k) < d.size() && d[std::size_t(k)] != 0;
}

void TapeIo::advance_grace() {
    const int64_t now = tape_->blocks();
    const bool live = live_.load(std::memory_order_relaxed);
    const bool wait = grace_policy_ == GracePolicy::kAlways ||
                      (grace_policy_ == GracePolicy::kAuto && live);
    while (!grace_.empty() && (!wait || grace_.front().at_block <= now)) {
        reclaimed_.push_back(grace_.front().slot);
        grace_.pop_front();
    }
}

void TapeIo::evict_to(int64_t want, bool relax) {
    if (!tape_->bounded()) return;
    const int64_t ranges = tape_->ranges();
    const int64_t pinned = tape_->pinned();
    int tries = 2 * int(std::min<int64_t>(want, 64)) + 8;
    while (int64_t(reclaimed_.size()) < want) {
        int     best_t = -1;
        int64_t best_k = -1, best_d = -1;
        bool    best_behind = false;
        int32_t best_slot = Tape::kAbsent;
        // The first pass honours the guard band — the ranges a read position is
        // about to reach. `relax` adds a second that drops it, because the guard
        // is a *policy* (step 5's wanted set in miniature) while starving a
        // fetch is a correctness failure: with a pool barely wider than the
        // guard every resident range can be inside it, and "evict nothing" then
        // means playback stops. Only a caller with something to put in the slot
        // relaxes it; the speculative top-up never does, or it would throw away
        // the read-ahead it had just fetched. What neither pass will touch is the
        // take's own range, the write queue and a pinned range — those three are
        // the rules, not the policy.
        for (int pass = 0; pass < (relax ? 2 : 1) && best_t < 0; ++pass) {
            for (int t = 0; t < Tape::kTracks; ++t) {
                const int64_t wr = tape_->write_range(t);
                const int64_t hot = tape_->hot(t);
                // Distance is measured from where the track is being read; a
                // track nothing has read is measured from the take's own head,
                // and then there is no guard band to honour — the ranges behind
                // a write head are finished, and they are exactly the ones to
                // give up first.
                const bool reading = hot >= 0;
                const int64_t hot_k = reading ? Tape::range_of(hot) : std::max<int64_t>(0, wr);
                for (int64_t k = 0; k < ranges; ++k) {
                    const int32_t slot = tape_->slot_at(t, k);
                    if (slot < 0) continue;                            // absent or wanted
                    if (k == wr) continue;                             // the take is in it
                    if (is_dirty(t, k)) continue;                      // the only copy
                    if (pinned == int64_t(t) * ranges + k) continue;    // a reader holds it
                    const bool behind = k <= hot_k;
                    const int64_t d = behind ? hot_k - k : k - hot_k;
                    // Pass 0 honours step 5's wanted set -- the horizon in
                    // seconds over every clip list -- **or**, where there is
                    // none (a host that has published no playhead, or
                    // `--tape-readahead-seconds 0`), step 2b's guard band. Not
                    // both: the band is a blunter protection over the same
                    // ground, and the two together protected seven ranges of an
                    // eight-slot pool, which left no victim and ended a take
                    // the card was serving perfectly.
                    if (pass == 0 && have_wanted_ && is_wanted(t, k)) continue;
                    if (pass == 0 && !have_wanted_ && reading && d <= Tape::kGuardRanges)
                        continue;
                    // Greatest distance from where that track is being read,
                    // ties broken behind before ahead (design § 3.6).
                    if (d > best_d || (d == best_d && behind && !best_behind)) {
                        best_d = d;
                        best_behind = behind;
                        best_t = t;
                        best_k = k;
                        best_slot = slot;
                    }
                }
            }
        }
        if (best_t < 0) return;                                    // nothing evictable
        if (--tries < 0) return;               // a CAS that keeps losing: next turn
        // A retire that loses the CAS means a fetch published there in between;
        // the scan runs again rather than trusting what it saw.
        if (tape_->retire(best_t, best_k, best_slot)) {
            // The slot is not reusable yet: a callback that read the table entry
            // before the retire has a block to finish with it (design § 3.6).
            grace_.push_back(Grace{best_slot, tape_->blocks() + Tape::kGraceBlocks});
            evicted_.fetch_add(1, std::memory_order_relaxed);
        }
        advance_grace();
    }
}

int32_t TapeIo::take_slot(Urgency u) {
    advance_grace();
    if (!reclaimed_.empty()) {
        const int32_t slot = reclaimed_.back();
        reclaimed_.pop_back();
        return slot;
    }
    // Then the audio thread's own free list: a slot nobody is using is a slot,
    // and leaving those to the take alone would starve playback with the pool
    // half empty. But it is also the **take's cover** -- the mark
    // `top_up_free` maintains so an eviction never happens because the take
    // asked -- so the I/O thread's own fetches stop at the mark and evict
    // instead. Without that, eight tracks fetching against a pool of eight
    // ended the take at its first range boundary with the card answering
    // perfectly, which inverts § 3.6's order: playback is what degrades under
    // pressure, never the take.
    if (u == Urgency::kReader || tape_->free_count() > free_mark()) {
        const int32_t free_slot = tape_->take_free();
        if (free_slot != Tape::kAbsent) return free_slot;
    }
    // `relax` only for the reader: read-ahead breaking the guard band or the
    // wanted set would be speculation throwing away what it just fetched.
    evict_to(1, u == Urgency::kReader);
    if (reclaimed_.empty()) return Tape::kAbsent;
    const int32_t slot = reclaimed_.back();
    reclaimed_.pop_back();
    return slot;
}

int64_t TapeIo::free_mark() const {
    // What a take needs to keep going, not a quarter of a small pool: at 118
    // slots it is the four seconds of cover `kFreeLowWater` names, and a test's
    // three-slot pool asks for one.
    if (!tape_ || !tape_->bounded()) return 0;
    return std::min(kFreeLowWater, std::max<int64_t>(1, tape_->pool_slots() / 4));
}

void TapeIo::top_up_free() {
    if (!tape_->bounded()) return;
    advance_grace();
    const int64_t mark = free_mark();
    const int64_t have = tape_->free_count();
    if (have >= mark) return;
    evict_to(mark - have, false);
    while (tape_->free_count() < mark && !reclaimed_.empty()) {
        const int32_t slot = reclaimed_.back();
        reclaimed_.pop_back();
        tape_->release_free(slot);             // zeroed on the way out
    }
}

bool TapeIo::fetch_locked(int t, int64_t k, Urgency u) {
    if (!on_ || t < 0 || t >= Tape::kTracks || k < 0 || k >= tape_->ranges()) return false;
    if (tape_->slot_at(t, k) >= 0) return true;
    // A read never makes a medium (#475): between a LOAD's `tape_dir` and its
    // `set_clips` the clip lists are the old project's, and a medium created
    // here was an empty `tape-N.wav` in the loaded folder -- a lost take, to a
    // player. A track with none is silence, which is what that file played as.
    const bool none = !open_medium(t, false) && !med_[t].opened;
    if (!none && med_[t].h == kNoHandle) {
        errors_.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    const int32_t slot = take_slot(u);
    if (slot == Tape::kAbsent) { starved_.fetch_add(1, std::memory_order_relaxed); return false; }
    float* dst = tape_->slot_data(slot);
    if (!dst) return false;
    if (none) {
        std::memset(dst, 0, std::size_t(kRangeBytes));
        tape_->publish(t, k, slot);
        fetched_.fetch_add(1, std::memory_order_relaxed);
        return true;
    }
    std::string err;
    const int64_t n = store_->pread(med_[t].h, dst, kRangeBytes,
                                    kHeaderBytes + k * kRangeBytes, err);
    if (n < 0) {
        errors_.fetch_add(1, std::memory_order_relaxed);
        last_error_ = err;
        reclaimed_.push_back(slot);
        return false;
    }
    // A short read is a range the medium never reached: the rest is silence,
    // which is what an unwritten range always was. Publishing it anyway is what
    // stops the audio thread asking for it again every block.
    if (n < kRangeBytes)
        std::memset(reinterpret_cast<uint8_t*>(dst) + n, 0, std::size_t(kRangeBytes - n));
    tape_->publish(t, k, slot);
    // #163: a fetched range is the other half of the sidecar's input -- a
    // medium opened without a `.peaks` file fills in as it is played, and the
    // short read above zero-filled its tail, so the bins match what a commit
    // of the same range would have written.
    note_peaks(t, k, dst, Tape::kRangeFrames);
    fetched_.fetch_add(1, std::memory_order_relaxed);
    return true;
}

bool TapeIo::fetch(int t, int64_t k) {
    if (!on_ || !tape_ || !tape_->bounded()) return tape_ && tape_->slot_at(t, k) >= 0;
    // Control thread, so waiting is allowed and is what a reader wants: every
    // slot being inside its grace period is a state that clears in two blocks,
    // and returning silence to a bounce instead would be the wrong answer to a
    // question that was only early. 64 ms of patience, then the truthful no.
    for (int attempt = 0; attempt < 32; ++attempt) {
        {
            std::lock_guard<std::mutex> g(mu_);
            if (fetch_locked(t, k)) return true;
        }
        if (!live_.load(std::memory_order_relaxed)) return false;   // nothing will change
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    return false;
}

void TapeIo::hold_span(int t, int64_t from, int64_t frames) {
    if (!on_ || frames <= 0) return;
    std::lock_guard<std::mutex> g(mu_);
    for (int64_t k = Tape::range_of(std::max<int64_t>(0, from));
         k <= Tape::range_of(from + frames - 1); ++k)
        mark(t, k, true);
}

void TapeIo::release_span(int t, int64_t from, int64_t frames) {
    if (!on_ || frames <= 0) return;
    std::lock_guard<std::mutex> g(mu_);
    for (int64_t k = Tape::range_of(std::max<int64_t>(0, from));
         k <= Tape::range_of(from + frames - 1); ++k)
        mark(t, k, false);
}

// ---- the wanted set (step 5, design § 3.6) ----------------------------------

bool TapeIo::is_wanted(int t, int64_t k) const {
    if (t < 0 || t >= Tape::kTracks || k < 0) return false;
    const std::vector<int32_t>& w = wanted_[t];
    return std::size_t(k) < w.size() && w[std::size_t(k)] != 0;
}

void TapeIo::add_wanted(int mt, int64_t k, int64_t dist, bool rec) {
    if (mt < 0 || mt >= Tape::kTracks || k < 0) return;
    std::vector<int32_t>& w = wanted_[mt];
    if (std::size_t(k) >= w.size()) return;
    // A range two clips read (`src_track`, `src_offset`) is wanted as soon as
    // the **nearest** of them sounds, and by a take if either is one: the
    // first reference met, in track order, could be the far one, and the cap
    // then dropped the range the playhead was about to play (#591).
    if (const int32_t i = w[std::size_t(k)]) {
        WantRange& r = want_list_[std::size_t(i - 1)];
        r.dist = std::min(r.dist, dist);
        r.rec = r.rec || rec;
        return;
    }
    want_list_.push_back(WantRange{int32_t(mt), k, dist, rec});
    w[std::size_t(k)] = int32_t(want_list_.size());
}

void TapeIo::plan_wanted() {
    // Cleared every turn rather than invalidated, because there are four things
    // that move it -- the playhead, the clip lists, the speed and the loop --
    // and a stale wanted set is a range the evictor keeps or a range it takes.
    // The walk is eight short clip lists over a window of a few ranges.
    for (auto& w : wanted_) std::fill(w.begin(), w.end(), 0);
    want_list_.clear();
    wanted_n_.store(0, std::memory_order_relaxed);
    have_wanted_ = false;
    if (!tape_->bounded()) return;
    const Tape::View v = tape_->view();
    // No playhead published (a host between `init` and its first block) or the
    // horizon switched off (`--tape-readahead-seconds 0`, T3's control (b)):
    // the only fetches left are the audio thread's own demand asks.
    if (v.pos < 0 || ahead_s_ <= 0.0) return;
    have_wanted_ = true;

    // Seconds of wall clock, so the window in *frames* carries the speed: four
    // ranges at 1x, sixteen at 4x, one at 0.25x (§ 3.6). Stopped, the window is
    // the 1x one around the parked playhead, so PLAY from a locate is warm.
    const double speed = v.playing ? std::max(0.05, std::fabs(v.step)) : 1.0;
    const int    dir = (v.playing && v.step < 0.0) ? -1 : 1;
    const int64_t ahead_f = int64_t(ahead_s_ * double(sr_) * speed);
    const int64_t behind_f = int64_t(behind_s_ * double(sr_) * speed);
    const int64_t lo = v.pos - (dir > 0 ? behind_f : ahead_f);
    const int64_t hi = v.pos + (dir > 0 ? ahead_f : behind_f);

    // A span of timeline to cover, and how to measure a range inside it from
    // the playhead: `ref` is where the travel enters the span and `base` how
    // far the playhead already is from that entry, so a range reached *through
    // the loop seam* is ranked by how soon it sounds and not by how far away it
    // sits on the ruler. Distance is what the cap below truncates on, and a
    // wrapped range that looked eight minutes away would always be the first
    // thing dropped -- which is exactly the range a looping transport needs.
    struct Span { int64_t a, b, ref, base; };      // [a, b) on the timeline
    Span sp[2];
    int  nsp = 0;
    if (v.playing && v.loop && v.loop_out > v.loop_in) {
        // The loop seam: the window continues from the other edge rather than
        // running off this one, because that is where the playhead goes next.
        // Only one wrap is drawn -- a window longer than the loop itself would
        // want the whole loop, and the `pool / 2` cap below is the honest
        // answer to that rather than a third span.
        const int64_t in = v.loop_in, out = v.loop_out, span = out - in;
        const int64_t a = std::max(lo, in), b = std::min(hi, out);
        if (b > a) sp[nsp++] = Span{a, b, v.pos, 0};
        if (hi > out)
            sp[nsp++] = Span{in, in + std::min(hi - out, span), in, out - v.pos};
        else if (lo < in)
            sp[nsp++] = Span{out - std::min(in - lo, span), out, out, v.pos - in};
    } else {
        const int64_t a = std::max<int64_t>(0, lo);
        const int64_t b = std::min(hi, tape_->capacity());
        if (b > a) sp[nsp++] = Span{a, b, v.pos, 0};
    }

    for (int t = 0; t < Tape::kTracks; ++t) {
        // An overdub take records the track's own playback (`build_events`), so
        // a late range on the recording track puts silence into the take and
        // not only into the speakers: it is fetched first and evicted last.
        const bool rec = tape_->write_range(t) >= 0;
        for (const Clip& c : snap_[t]) {
            if (c.len <= 0) continue;
            const int mt = (c.src_track >= 0 && c.src_track < Tape::kTracks) ? c.src_track : t;
            for (int i = 0; i < nsp; ++i) {
                const int64_t a = std::max(sp[i].a, c.start_frame);
                const int64_t b = std::min(sp[i].b, c.start_frame + c.len);
                if (b <= a) continue;
                // Timeline -> this clip's medium, which may be another track's.
                const int64_t s0 = c.src_offset + (a - c.start_frame);
                const int64_t s1 = c.src_offset + (b - 1 - c.start_frame);
                if (s1 < 0) continue;              // entirely before frame 0
                for (int64_t k = Tape::range_of(std::max<int64_t>(0, s0));
                     k <= Tape::range_of(s1); ++k) {
                    // Distance is measured **per range**, from where that
                    // range sounds on the timeline. Per clip it would be the
                    // same number for every range of one long clip, and the
                    // cap would then keep the lowest-numbered ranges of the
                    // lowest-numbered tracks -- under forward travel, the ones
                    // already played.
                    const int64_t k0 = std::max(
                        a, c.start_frame + (k * Tape::kRangeFrames - c.src_offset));
                    const int64_t k1 = std::min(
                        b, c.start_frame + ((k + 1) * Tape::kRangeFrames - c.src_offset));
                    const int64_t d = k0 > sp[i].ref
                                          ? k0 - sp[i].ref
                                          : (sp[i].ref >= k1 ? sp[i].ref - k1 + 1 : 0);
                    add_wanted(mt, k, sp[i].base + d, rec);
                }
            }
        }
    }

    // § 3.6's cap: eight tracks at 4x want ~136 ranges, more than the pool, and
    // a wanted set larger than the pool is a set that cannot be held -- it
    // would only stop the evictor finding a victim. Half the pool, nearest
    // first, leaves the other half for the take, the write queue and the edits.
    const std::size_t cap = std::size_t(std::max<int64_t>(1, tape_->pool_slots() / 2));
    std::stable_sort(want_list_.begin(), want_list_.end(),
                     [](const WantRange& a, const WantRange& b) {
                         if (a.rec != b.rec) return a.rec;   // the take's track first
                         return a.dist < b.dist;
                     });
    if (want_list_.size() > cap) {
        for (std::size_t i = cap; i < want_list_.size(); ++i)
            wanted_[want_list_[i].track][std::size_t(want_list_[i].range)] = 0;
        want_list_.resize(cap);
        capped_.fetch_add(1, std::memory_order_relaxed);
    }
    wanted_n_.store(int64_t(want_list_.size()), std::memory_order_relaxed);
}

void TapeIo::serve_wanted() {
    int budget = kWantsPerTurn;
    for (const WantRange& w : want_list_) {
        if (budget <= 0) break;
        if (tape_->slot_at(w.track, w.range) >= 0) continue;    // already here
        if (store_->busy()) break;                              // the card is stalled
        --budget;
        // A fetch that fails is a slot the pool could not spare or a card that
        // refused; either way the next turn asks again, and the audio thread's
        // own demand ask is still behind it.
        if (!fetch_locked(w.track, w.range, Urgency::kReadAhead)) break;
    }
}

void TapeIo::drain(std::unique_lock<std::mutex>& lk, bool final) {
    advance_grace();
    // Off the ring first, whatever the card is doing: the audio thread must
    // never find the ring full because the card is slow.
    RangeCommit c;
    while (ring_.pop(c)) {
        queue_.push_back(c);
        mark(c.track, c.range, true);
    }
    queue_depth_.store(int64_t(queue_.size()), std::memory_order_relaxed);
    while (!queue_.empty() && (final || !store_->busy())) {
        const RangeCommit q = queue_.front();
        queue_.pop_front();
        commit(q, lk);
        mark(q.track, q.range, false);
        queue_depth_.store(int64_t(queue_.size()), std::memory_order_relaxed);
        done_.fetch_add(1, std::memory_order_release);
    }
    if (tape_->bounded()) {
        // The order matters. The wanted set is planned **first**, so every
        // eviction this turn causes -- including the ones a demand fetch
        // forces -- reads a horizon computed from this turn's playhead.
        plan_wanted();
        Tape::Want w;
        while (tape_->take_want(w)) {
            if (final || store_->busy() ||
                !fetch_locked(w.track, w.range, Urgency::kReadAhead))
                tape_->unwant(w.track, w.range);   // back to absent: ask again
        }
        // Then the horizon, which is speculative and comes after the audio
        // thread's own asks; then the free list, which must not spend the
        // read-ahead it has just fetched (`evict_to(.., relax=false)`).
        if (!final) serve_wanted();
        top_up_free();
    }
    // Last in the turn: compaction is background work by definition (§ 3.8,
    // "in the background of a stopped instrument"), so the take's commits, the
    // audio thread's demand asks and the free list all come first. `final`
    // is a shutdown -- the scratch file is nobody's loss.
    if (!final) compact_step(lk);
}

void TapeIo::service() {
    // Offline the host has no threads: the I/O thread's loop body is this
    // call, made once per rendered block, so the offline path is the real
    // path (`audio/CLAUDE.md` § The host).
    if (!on_ || thread_.joinable()) return;
    std::unique_lock<std::mutex> lk(mu_);
    drain(lk);
}

void TapeIo::flush() {
    if (!on_) return;
    if (!thread_.joinable()) {
        service();
        return;
    }
    // The ring has one consumer and it is the thread; asking for it back here
    // would make two. So wait on the counter instead: every popped item bumps
    // `done_`, whatever the commit's outcome.
    const int64_t target = queued_.load(std::memory_order_relaxed);
    wake_.notify_all();
    std::unique_lock<std::mutex> lk(wake_mu_);
    done_cv_.wait(lk, [this, target] {
        return done_.load(std::memory_order_acquire) >= target;
    });
}

void TapeIo::persist_span(int t, int64_t from, int64_t frames) {
    if (!on_ || frames <= 0 || t < 0 || t >= Tape::kTracks) return;
    flush();                                   // the take's ranges first, in order
    const int64_t first = Tape::range_of(from);
    const int64_t last = Tape::range_of(from + frames - 1);
    std::unique_lock<std::mutex> lk(mu_);
    for (int64_t k = first; k <= last; ++k) {
        RangeCommit c;
        c.track = t;
        c.range = k;
        c.frames = std::min<int64_t>(Tape::kRangeFrames, from + frames - k * Tape::kRangeFrames);
        c.flush = k == last;                   // the span's tail is exact on the card
        commit(c, lk);
        mark(t, k, false);                     // durable: the evictor may have it
    }
}

bool TapeIo::open_medium(int t, bool create) {
    Medium& m = med_[t];
    if (m.opened) return m.h != kNoHandle;
    if (!create && !store_->exists(medium_path(t))) return false;
    m.opened = true;
    std::string err;
    m.h = store_->open(medium_path(t), true, err);
    if (m.h == kNoHandle) {
        last_error_ = err;
        return false;
    }
    if (!repair(t)) {
        store_->close(m.h);
        m.h = kNoHandle;
        return false;
    }
    // The lead starts where the file already ends: repair has just made the
    // length exact, so those blocks exist and asking for them again would be a
    // syscall that allocates nothing (#158).
    std::string szerr;
    const int64_t sz = store_->size(m.h, szerr);
    m.alloc_end = sz > 0 ? sz : 0;
    load_peaks(t);                     // #163: after the repair, so the check is against the exact claim
    return true;
}

// Repair on open (C1 step 3, design § 3.5). The head comes from the header's
// frame count, never from the file's length, and the two are made to agree
// before anything appends -- because a take appends at the head, and a head
// taken from a file longer than its header claims would write *over* the tail
// a cut left behind, interleaving two takes inside one range.
//
// Four cases, and the order they are tested in is the part that matters:
//
//   * **empty** -- the file `open(create)` has just made. Claim 0, nothing to
//     do; the first commit writes the header.
//   * **no `RIFF` at all** -- ours, cut before the header page ever landed
//     (the `pwrite` of a range at offset 4096 zero-fills the header page into
//     existence). Everything in it is audio no commit ever claimed, which by
//     the lag rule is audio that was never durable: claim 0, truncate to the
//     header, write it. Losing it is the promise the lag already makes.
//   * **`RIFF`, but not one of ours** -- somebody else's WAV under our name,
//     the 44-byte header `tape_save` writes being the one that will really
//     happen. **Refused, and left untouched**: its payload is not at byte 4096
//     so it cannot be used in place, and overwriting its header to save a
//     reopen would destroy a take. The caller sees the medium fail to open.
//     Converting one is `tape_load`'s job (step 3b), where a player asked.
//   * **ours** -- clamp the claim to the whole frames that are really there,
//     truncate to exactly the claim, rewrite the header, sync. A file *longer*
//     than its claim is a cut mid-take and loses its tail (<= 2 ranges,
//     § 3.3); a file *shorter* is an `fsck` truncation and loses down to what
//     survived.
//
// Truncate before the header, so a cut inside the repair leaves a file shorter
// than the old claim -- which the next open repairs the same way. The other
// order converges too; this one is § 3.5's own sentence.
bool TapeIo::repair(int t) {
    Medium& m = med_[t];
    std::string err;

    const int64_t bytes = store_->size(m.h, err);
    if (bytes < 0) {
        last_error_ = medium_path(t) + ": " + err;
        return false;
    }
    if (bytes == 0) {
        m.durable_end = m.claimed = 0;
        durable_pub_[t].store(0, std::memory_order_relaxed);
        return true;
    }

    std::vector<uint8_t> h(std::size_t(kHeaderBytes), 0);
    const int64_t n = store_->pread(m.h, h.data(), std::min(bytes, kHeaderBytes), 0, err);
    if (n < 0) {
        last_error_ = medium_path(t) + ": " + err;
        return false;
    }
    const bool riff = n >= 4 && std::memcmp(h.data(), "RIFF", 4) == 0;
    const int64_t claimed = n == kHeaderBytes ? header_frames(h.data(), n) : -1;
    if (riff && claimed < 0) {
        refused_.fetch_add(1, std::memory_order_relaxed);
        last_error_ = medium_path(t) +
                      ": a WAV that is not one of our media (no 4 KiB header) -- left alone";
        return false;
    }

    // Whole frames of payload that are really on the card. A partial frame at
    // the end is not a frame: half of a stereo float pair is a click.
    const int64_t have = std::max<int64_t>(0, (bytes - kHeaderBytes) / kFrameBytes);
    const int64_t keep = claimed < 0 ? 0 : std::min(claimed, have);
    const int64_t want_bytes = kHeaderBytes + keep * kFrameBytes;
    if (bytes == want_bytes && claimed == keep) {
        m.durable_end = m.claimed = keep;
        durable_pub_[t].store(keep, std::memory_order_relaxed);
        return true;
    }

    write_header(header_.data(), sr_, keep);
    if (!store_->truncate(m.h, want_bytes, err) ||
        store_->pwrite(m.h, header_.data(), kHeaderBytes, 0, err) != kHeaderBytes ||
        !store_->fdatasync(m.h, err)) {
        last_error_ = medium_path(t) + ": repair failed: " + err;
        return false;
    }
    syncs_.fetch_add(1, std::memory_order_relaxed);
    repaired_.fetch_add(1, std::memory_order_relaxed);
    // What the repair cost the player: the frames the provisional clip
    // (DDR-070) has to be shortened by. It is the larger of the two disagreeing
    // numbers minus what survived, because the two cases lose from opposite
    // ends -- a file *longer* than its claim loses the tail the header never
    // reached (`have - keep`), one *shorter* loses the frames the header
    // promised and the card has not got (`claimed - keep`). A file with no
    // header of ours claimed nothing, so its frames were never durable and are
    // not "lost" in the sense a clip is shortened by -- but they are gone, and
    // counting them is cheaper than explaining a shorter take later.
    lost_frames_.fetch_add(std::max(have, std::max<int64_t>(0, claimed)) - keep,
                           std::memory_order_relaxed);
    m.durable_end = m.claimed = keep;
    // The repaired claim **is** the durable prefix, and #163 gates the peaks
    // sidecar on it, so the lock-free mirror has to say so before anything
    // draws. Until then only a commit ever published it, which left a medium
    // that had been opened and not written claiming 0 frames durable.
    durable_pub_[t].store(keep, std::memory_order_relaxed);
    return true;
}

int64_t TapeIo::medium_frames(int t) const {
    if (!on_ || t < 0 || t >= Tape::kTracks) return 0;
    std::lock_guard<std::mutex> g(mu_);
    return med_[t].claimed;
}

bool TapeIo::open_all() {
    if (!on_) return true;
    std::lock_guard<std::mutex> g(mu_);
    bool ok = true;
    for (int t = 0; t < Tape::kTracks; ++t) ok = open_medium(t) && ok;
    return ok;
}

// After a rename (#591): make the folder entries of `a`'s folder and `b`'s
// durable (a move's two folders; a swap passes one path twice), so a power cut cannot undo a move or a swap the host has already
// reported. Best effort, as `hostlib/audiofile.cpp`'s `fsync_dir` is: the
// rename itself has happened and is visible, so a filesystem that refuses the
// sync is counted as an error rather than turned into a failed SAVE.
void TapeIo::sync_dirs(const std::string& a, const std::string& b) {
    std::string err;
    if (!store_->sync_dir_of(a, err)) note_error(err);
    if (b != a && !store_->sync_dir_of(b, err)) note_error(err);
}

bool TapeIo::rename_dir(const std::string& to, std::string& code, std::string& err) {
    if (!on_ || dir_.empty()) {
        code = "bad_request";
        err = "NO TAPE DIR";
        return false;
    }
    flush();                                   // every range is on the card first
    std::unique_lock<std::mutex> lk(mu_);
    wait_io(lk);
    if (comp_.state == CompactState::kRunning || comp_.state == CompactState::kDone) {
        code = "busy";
        err = "COMPACTING";
        return false;
    }
    std::string e;
    if (!store_->rename_new(dir_, to, e)) {
        code = e == "EEXIST" ? "bad_request" : "write_failed";
        err = e == "EEXIST" ? "EXISTS" : e;
        if (e != "EEXIST") note_error(e);
        return false;
    }
    sync_dirs(to, to);
    dir_ = to;
    return true;
}

bool TapeIo::set_dir(const std::string& dir, bool move, int64_t frames[Tape::kTracks],
                     std::string& code, std::string& err) {
    for (int t = 0; t < Tape::kTracks; ++t) frames[t] = 0;
    if (!on_) {
        code = "bad_request";
        err = "NO TAPE DIR";
        return false;
    }
    if (dir.empty()) {
        code = "bad_request";
        err = "tape_dir needs \"dir\"";
        return false;
    }
    flush();                                   // every range the take filled is on the card
    std::unique_lock<std::mutex> lk(mu_);
    wait_io(lk);
    // The caller cancels a compaction first (`AudioHost::cancel_compaction`):
    // a finished one swapped in after this would rename its scratch file onto
    // a medium path in the *new* folder.
    if (comp_.state == CompactState::kRunning || comp_.state == CompactState::kDone) {
        code = "busy";
        err = "COMPACTING";
        return false;
    }

    if (move) {
        // #484: the folder the tape is in under another spelling (`/p/`, a
        // symlink to it) is that folder, and moving there moves nothing.
        if (!store_->same_dir(dir, dir_)) {
            // Check every name the tape could ever write before renaming
            // anything, so a refusal moves nothing -- **every** track's, not
            // only the ones with a file here: a track with no medium yet makes
            // one under its name on its next take, from frame zero, over
            // whatever the folder held (#484 finding 1).
            std::vector<std::pair<std::string, std::string>> todo;
            for (int t = 0; t < Tape::kTracks; ++t) {
                const std::string name = "/tape-" + std::to_string(t + 1);
                for (const char* ext : {".wav", ".peaks"}) {
                    const std::string to = dir + name + ext;
                    if (store_->exists(to)) {
                        code = "exists";
                        err = to + " is already there";
                        return false;
                    }
                    const std::string from = dir_ + name + ext;
                    if (store_->exists(from)) todo.emplace_back(from, to);
                }
            }
            for (std::size_t i = 0; i < todo.size(); ++i) {
                std::string e;
                if (store_->rename_new(todo[i].first, todo[i].second, e)) continue;
                // Put back what moved, newest first. A rename that worked one
                // way usually works the other; when it does not (a card
                // failing mid-SAVE), the tape is in two folders and the host
                // says so, naming where each moved file is, rather than
                // report a plain failure that implies it is all still here
                // (#484 finding 3).
                std::string stuck;
                for (std::size_t j = i; j-- > 0;) {
                    std::string back;
                    if (store_->rename_new(todo[j].second, todo[j].first, back)) continue;
                    note_error(back);
                    stuck += (stuck.empty() ? "" : ", ") + todo[j].second;
                }
                if (i > 0) sync_dirs(todo[0].first, todo[0].second);
                note_error(e);
                if (stuck.empty()) {
                    code = "move_failed";
                    err = e;
                } else {
                    code = "move_split";
                    err = e + "; not put back: " + stuck + "; the rest in " + dir_;
                }
                return false;
            }
            if (!todo.empty()) sync_dirs(todo[0].first, todo[0].second);
            dir_ = dir;
        }
        for (int t = 0; t < Tape::kTracks; ++t) frames[t] = med_[t].claimed;
        return true;
    }

    for (int t = 0; t < Tape::kTracks; ++t) {
        Medium& m = med_[t];
        if (m.h != kNoHandle) store_->close(m.h);
        m = Medium{};
        durable_pub_[t].store(0, std::memory_order_relaxed);
        // The table is keyed by frame offset and every offset is about to mean
        // a different medium: retire the lot the way `compact_finish` does, so
        // a callback that read an entry before this still has its block.
        if (tape_->bounded()) {
            const int64_t ranges = tape_->ranges();
            for (int64_t k = 0; k < ranges; ++k) {
                const int32_t slot = tape_->slot_at(t, k);
                if (slot < 0) continue;
                if (tape_->retire(t, k, slot))
                    grace_.push_back(Grace{slot, tape_->blocks() + Tape::kGraceBlocks});
            }
        }
        std::fill(dirty_[t].begin(), dirty_[t].end(), uint8_t(0));
        drop_peaks(t, false);                  // the old folder keeps its sidecar
    }
    dir_ = dir;
    for (int t = 0; t < Tape::kTracks; ++t) {
        if (!store_->exists(medium_path(t))) continue;   // opened, and created, by its first commit
        frames[t] = open_medium(t) ? med_[t].claimed : -1;
    }
    return true;
}

void TapeIo::record_time(int64_t us, int64_t sync_us) {
    std::lock_guard<std::mutex> g(times_mu_);
    times_[times_at_] = int32_t(std::min<int64_t>(us, 0x7FFFFFFF));
    syncs_us_[times_at_] = int32_t(std::min<int64_t>(sync_us, 0x7FFFFFFF));
    times_at_ = (times_at_ + 1) % times_.size();
    ++times_n_;
    times_max_ = std::max(times_max_, int32_t(std::min<int64_t>(us, 0x7FFFFFFF)));
    sync_max_ = std::max(sync_max_, int32_t(std::min<int64_t>(sync_us, 0x7FFFFFFF)));
}

void TapeIo::commit_times(std::vector<int32_t>& out, int64_t& count, int32_t& worst) const {
    std::lock_guard<std::mutex> g(times_mu_);
    count = times_n_;
    worst = times_max_;
    const std::size_t have = std::size_t(std::min<int64_t>(times_n_, int64_t(times_.size())));
    out.assign(times_.begin(), times_.begin() + std::ptrdiff_t(have));
}

void TapeIo::sync_times(std::vector<int32_t>& out, int64_t& count, int32_t& worst) const {
    std::lock_guard<std::mutex> g(times_mu_);
    count = times_n_;
    worst = sync_max_;
    const std::size_t have =
        std::size_t(std::min<int64_t>(times_n_, int64_t(syncs_us_.size())));
    out.assign(syncs_us_.begin(), syncs_us_.begin() + std::ptrdiff_t(have));
}

// § 3.4's lead (#158). `FALLOC_FL_KEEP_SIZE`, so the blocks exist and the
// file's length does not move: repair on open (§ 3.5) reads that length and
// treats anything past the header's claim as audio a cut left behind, so a
// lead that grew the length would look to it like exactly that.
void TapeIo::extend_alloc(Medium& m, int64_t need_end) {
    if (!prealloc_ || m.alloc_off || need_end <= m.alloc_end) return;
    const int64_t from = m.alloc_end;
    std::string err;
    if (!store_->allocate(m.h, from, need_end - from, err)) {
        // Not an error the commit fails on: a filesystem that cannot
        // preallocate simply grows the way it did before this existed. Counted
        // once per medium rather than once per range, because asking a card
        // that has already said no is a syscall a second for nothing -- and
        // because the number worth reading is "this card cannot", not a rate.
        m.alloc_off = true;
        alloc_refused_.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    m.alloc_end = need_end;
    allocs_.fetch_add(1, std::memory_order_relaxed);
    alloc_bytes_.fetch_add(need_end - from, std::memory_order_relaxed);
}

// One place for "a store call refused", because exactly one refusal has its
// own consequence: out of space ends the take with `tape_full {reason:"disk"}`
// (§ 3.7 reason 2), while every other one is counted and the tape carries on.
// `no_space()` is asked here and nowhere else, straight after the call that
// failed, which is the only moment it means anything.
void TapeIo::note_error(const std::string& err) {
    note_error(err, store_ && store_->no_space());
}

// The commit's form (#637): its store calls run with `mu_` dropped, so by the
// time it is back under the lock another thread's call may have reset the
// store's flag. It asks inside the window and hands the answer here.
void TapeIo::note_error(const std::string& err, bool no_space) {
    errors_.fetch_add(1, std::memory_order_relaxed);
    last_error_ = err;
    if (no_space) disk_full_.fetch_add(1, std::memory_order_relaxed);
}

bool TapeIo::space_for_take() const {
    if (!on_ || !store_ || !tape_) return true;
    int64_t free = store_->free_bytes(dir_);
    if (free_cap_ >= 0) free = free < 0 ? free_cap_ : std::min(free, free_cap_);
    if (free < 0) return true;                 // the filesystem will not say
    return free >= 2 * tape_->pool_slots() * kRangeBytes;
}

void TapeIo::commit(const RangeCommit& c, std::unique_lock<std::mutex>& lk) {
    const auto t_start = std::chrono::steady_clock::now();
    const int t = c.track;
    if (t < 0 || t >= Tape::kTracks || c.range < 0) return;
    // #637: one commit on the card at a time. The I/O thread's drain and a
    // control thread's `persist_span` can both get here, and each drops `mu_`
    // below; two in flight on one medium could land their headers out of order
    // and leave the card claiming less than it holds.
    io_cv_.wait(lk, [this] { return !committing_; });
    if (!open_medium(t)) {
        note_error(last_error_);
        return;
    }
    Medium& m = med_[t];
    const StoreHandle h = m.h;                 // `wait_io` keeps it open across the window
    const int64_t first = c.range * Tape::kRangeFrames;
    int64_t frames = std::clamp<int64_t>(c.frames, 0, Tape::kRangeFrames);
    const float* src = nullptr;
    if (frames > 0) {
        int64_t run = 0;
        src = tape_->run_at(t, first, run);
        if (!src) {
            // Step 2a never evicts, so this is a bug rather than a path — it
            // is counted and not written, because writing whatever the slot
            // now holds would put another take's audio in this one's range.
            absent_.fetch_add(1, std::memory_order_relaxed);
            frames = 0;
        } else {
            frames = std::min(frames, run);
        }
    }

    const int64_t end = first + frames;
    // `durable_end` is the length of the **contiguous** prefix the card holds,
    // so a commit only extends it when it writes audio and starts at or before
    // where the prefix ended. Two commits fail that test: the empty flush
    // commit at the end of a take that lands on a range boundary (`frames ==
    // 0`, nothing new on the card), and any commit past a range the card
    // refused -- the ENOSPC case, which would claim the whole take as durable.
    const int64_t prefix =
        (frames > 0 && first <= m.durable_end) ? std::max(m.durable_end, end) : m.durable_end;
    // The lag (§ 3.5): claim what the *previous* commit made durable, unless
    // this is the end of a take (or a test asked for no lag) -- and then this
    // commit's prefix, never `end` itself: past a refused range the header
    // would claim the hole as part of the take (#591).
    const int64_t claim = (no_lag_ || c.flush) ? prefix : m.durable_end;
    // `whead_`, not `header_`: `repair` writes that one under `mu_`, and the
    // write below no longer holds it.
    write_header(whead_.data(), sr_, claim);
    const std::string ppath = c.flush ? peaks_path(t) : std::string();   // `dir_` is `mu_`'s

    // #637: the card's part of the commit -- the lead, both `pwrite`s and the
    // `fdatasync` -- runs with `mu_` **dropped**, the way #167 dropped it
    // around the compaction's copy. Held, a slow sync (10-140+ ms on a microSD)
    // stalled every control-thread call that takes `mu_` -- `set_clips`,
    // `medium_frames`, `compact_state` -- and with them every client's notes.
    // What the window touches is the commit's own: the medium's handle (no
    // closer runs while `io_busy_` is up), its `alloc_*` fields (only a commit
    // writes them, one at a time), `whead_`, and the range's slot, which the
    // evictor will not take while it is dirty (`drain`'s queue and
    // `hold_span` both mark it). The bookkeeping that others read is updated
    // after the lock is back.
    std::string err;
    bool nospace = false;
    int64_t sync_us = 0, total_us = 0;
    const auto card = [&]() -> bool {
        if (frames > 0) {
            // The lead, ahead of both `pwrite`s and **inside the timed window**
            // on purpose: it is a real cost on the commit's own thread inside
            // the same 1.024 s budget, and a `commit_us` that hid it would not
            // be comparable with the 65-92 ms p99 #158 was opened on. Only on a
            // commit that carries audio -- a take's final flush commit is often
            // empty, and leading past the end for it would allocate a range
            // nothing will write and leave it behind as dead space.
            extend_alloc(m, kHeaderBytes + (c.range + 1 + kAllocLeadRanges) * kRangeBytes);
            const int64_t want = frames * kFrameBytes;
            if (store_->pwrite(h, src, want, kHeaderBytes + c.range * kRangeBytes, err) != want) {
                nospace = store_->no_space();
                return false;                  // no header: it must never claim this
            }
            // The peaks sidecar (#163): the whole range is in hand here, which
            // is the cheapest moment in the instrument to work out what TRACK
            // draws from it. Every commit of a range recomputes it, so a take
            // appending into a partly-written range leaves no stale bins.
            note_peaks(t, c.range, src, frames);
        }
        if (store_->pwrite(h, whead_.data(), kHeaderBytes, 0, err) != kHeaderBytes) {
            nospace = store_->no_space();
            return false;
        }
        const auto t_sync = std::chrono::steady_clock::now();
        if (!store_->fdatasync(h, err)) {
            nospace = store_->no_space();
            return false;
        }
        const auto t_end = std::chrono::steady_clock::now();
        sync_us = std::chrono::duration_cast<std::chrono::microseconds>(t_end - t_sync).count();
        // Timed before the `fadvise` hint, which is not part of what the
        // design compares against C6's number.
        total_us = std::chrono::duration_cast<std::chrono::microseconds>(t_end - t_start).count();
        if (frames > 0)
            store_->advise_dontneed(h, kHeaderBytes + c.range * kRangeBytes, frames * kFrameBytes);
        // `tape-N.peaks` on the commit that ends a take, which is the one
        // moment the medium's header is exact (§ 3.5's no-lag flush), written
        // here in the window: under `mu_` it was a card open, truncate and
        // write right after STOP that every control call waited on (#826).
        // `claim` is what `m.claimed` becomes below; `io_busy_` keeps the
        // closers that drop or move the sidecar (`compact_finish`, `set_dir`)
        // waiting until it is done. Unsynced and unchecked, as before: a cache.
        // [deviation from § 3.6, which says "at SAVE": `tape_save` writes a
        // **copy** somewhere else until step 3b makes the medium the
        // project's own file, and which file that is is #157's to answer.
        // The take's own end is the same instant for the medium and needs no
        // decision.]
        if (c.flush) {
            std::vector<uint8_t> img;
            if (peaks_image(t, claim, img)) write_peaks(ppath, img);
        }
        return true;
    };
    bool ok;
    if (commit_holds_lock_) {
        ok = card();                           // #637's *before* switch
    } else {
        committing_ = true;
        ++io_busy_;
        lk.unlock();
        ok = card();
        lk.lock();
        --io_busy_;
        committing_ = false;
        io_cv_.notify_all();
    }
    if (!ok) {
        note_error(err, nospace);
        return;
    }
    syncs_.fetch_add(1, std::memory_order_relaxed);
    m.claimed = claim;
    m.durable_end = prefix;
    // The control thread's copy, for the clip a disk-full take is shortened to
    // (§ 3.7 reason 2: "end the take at the last committed range").
    durable_pub_[t].store(m.durable_end, std::memory_order_relaxed);
    record_time(total_us, sync_us);
    committed_.fetch_add(1, std::memory_order_relaxed);
}

// ---- the peaks sidecar (#163, design § 3.6) ---------------------------------
//
// `clip_peaks` is how TRACK draws a clip, and #162 measured what it costs once
// the tape is on the card: **4.0 s for one bank of four 180 s lanes** against
// the 16.7 ms a 60 Hz frame allows -- 830 ms a clip reading 64 MB off the card,
// 110 ms scanning it, 60 ms sending 67,500 numbers back. This is the first two
// of those three. Every range this thread commits or fetches already has the
// whole range in its hands, so it works out `kPeakBins` min/max pairs for it
// there and then; a draw at or below that resolution is answered out of RAM.
//
// Three things make it safe to be wrong about:
//
//   * it is a **cache**. `peaks_for` refuses anything it cannot answer exactly
//     -- too fine, past the durable prefix, a range it has no bins for -- and
//     the caller then reads the audio the way it always did;
//   * the file is **not synced** and is checked on open against the frame count
//     the medium's own header claims, so a torn or stale one is discarded
//     rather than drawn;
//   * a range's bins are recomputed on **every** commit of it, which is what
//     keeps a take that appends into a partly-written range honest.

std::string TapeIo::peaks_path(int t) const {
    return dir_ + "/tape-" + std::to_string(t + 1) + ".peaks";
}

void TapeIo::note_peaks(int t, int64_t k, const float* src, int64_t n) {
    if (!peaks_on_ || !src || t < 0 || t >= Tape::kTracks || k < 0) return;
    if (k >= Tape::ranges_for(tape_ ? tape_->capacity() : 0)) return;
    const std::size_t bins = std::size_t(k + 1) * kPeakBins;
    std::lock_guard<std::mutex> g(peaks_mu_);
    std::vector<float>& p = peaks_[t];
    std::vector<uint8_t>& ok = peaks_ok_[t];
    if (p.size() < bins * 2) p.resize(bins * 2, 0.0f);
    if (ok.size() < std::size_t(k + 1)) ok.resize(std::size_t(k + 1), 0);
    for (int b = 0; b < kPeakBins; ++b) {
        const int64_t a = int64_t(b) * kPeakBinFrames;
        const int64_t e = a + kPeakBinFrames;
        // Past `n` the range is silence -- that is what an unwritten tail reads
        // as on the card and what `run_at` hands a reader -- so a bin that
        // straddles the end folds a zero in. Without that a commit's bins and a
        // fetch's bins for the same range would disagree at the tail, and the
        // whole point of the file is that the two agree.
        const int64_t upto = std::clamp(n, a, e);
        float lo = 0.0f, hi = 0.0f;
        if (upto > a) {
            lo = hi = src[std::size_t(a) * 2];
            for (int64_t i = a; i < upto; ++i) {
                const float l = src[std::size_t(i) * 2], r = src[std::size_t(i) * 2 + 1];
                lo = std::min(lo, std::min(l, r));
                hi = std::max(hi, std::max(l, r));
            }
        }
        if (upto < e) {
            lo = std::min(lo, 0.0f);
            hi = std::max(hi, 0.0f);
        }
        const std::size_t i = (std::size_t(k) * kPeakBins + std::size_t(b)) * 2;
        p[i] = lo;
        p[i + 1] = hi;
    }
    ok[std::size_t(k)] = 1;
}

bool TapeIo::peaks_for(int t, int64_t from, int64_t len, int bins,
                       std::vector<float>& mn, std::vector<float>& mx) const {
    mn.clear();
    mx.clear();
    const auto miss = [this] {
        peaks_miss_.fetch_add(1, std::memory_order_relaxed);
        return false;
    };
    if (!on_ || !peaks_on_) return false;        // not a miss: there is no sidecar to miss
    if (t < 0 || t >= Tape::kTracks || from < 0 || len <= 0 || bins <= 0) return false;
    // Finer than the sidecar holds: an output bin has to cover a whole bin of
    // it, or the answer would be coarser than the zoom asked for. This is the
    // half of #163 that the UI's bins cap turns from always-true into
    // never-true for a clip drawn zoomed out.
    if (len / bins < kPeakBinFrames) return miss();
    // And only over the medium's **durable prefix**: a range the pool holds and
    // the card has not taken either has no bins or has last take's.
    if (from + len > durable_frames(t)) return miss();
    std::lock_guard<std::mutex> g(peaks_mu_);
    const std::vector<float>& p = peaks_[t];
    const std::vector<uint8_t>& ok = peaks_ok_[t];
    mn.reserve(std::size_t(bins));
    mx.reserve(std::size_t(bins));
    for (int b = 0; b < bins; ++b) {
        const int64_t a = from + int64_t(uint64_t(b) * uint64_t(len) / uint64_t(bins));
        int64_t e = from + int64_t(uint64_t(b + 1) * uint64_t(len) / uint64_t(bins));
        e = std::max(e, a + 1);
        float lo = 0.0f, hi = 0.0f;
        for (int64_t j = a / kPeakBinFrames, j0 = j, j1 = (e - 1) / kPeakBinFrames; j <= j1; ++j) {
            const int64_t k = j / kPeakBins;
            const std::size_t i = std::size_t(j) * 2;
            if (std::size_t(k) >= ok.size() || !ok[std::size_t(k)] || i + 1 >= p.size()) {
                mn.clear();
                mx.clear();
                return miss();
            }
            if (j == j0) {
                lo = p[i];
                hi = p[i + 1];
            } else {
                lo = std::min(lo, p[i]);
                hi = std::max(hi, p[i + 1]);
            }
        }
        mn.push_back(lo);
        mx.push_back(hi);
    }
    peaks_hit_.fetch_add(1, std::memory_order_relaxed);
    return true;
}

int64_t TapeIo::peaks_ranges(int t) const {
    if (t < 0 || t >= Tape::kTracks) return 0;
    std::lock_guard<std::mutex> g(peaks_mu_);
    int64_t n = 0;
    for (uint8_t v : peaks_ok_[t]) n += v ? 1 : 0;
    return n;
}

void TapeIo::corrupt_peaks(int t, float scale) {
    if (t < 0 || t >= Tape::kTracks) return;
    std::lock_guard<std::mutex> g(peaks_mu_);
    for (float& v : peaks_[t]) v *= scale;
}

void TapeIo::drop_peaks(int t, bool unlink) {      // `mu_` held
    {
        std::lock_guard<std::mutex> g(peaks_mu_);
        peaks_[t].clear();
        peaks_ok_[t].clear();
    }
    if (unlink && !dir_.empty()) {
        std::string err;
        store_->remove(peaks_path(t), err);        // may not exist; not an error
    }
}

bool TapeIo::save_peaks(int t) {
    std::lock_guard<std::mutex> g(mu_);
    return save_peaks_locked(t);
}

// The file: 32 bytes of header, then the bins of every range the medium's
// header claims, then one validity byte a range -- so a medium whose ranges
// were only partly touched still writes a usable file rather than none.
bool TapeIo::save_peaks_locked(int t) {            // `mu_` held
    if (t < 0 || t >= Tape::kTracks) return false;
    std::vector<uint8_t> buf;
    return peaks_image(t, med_[t].claimed, buf) && write_peaks(peaks_path(t), buf);
}

// The file's bytes for a medium claiming `frames`. Reads only the bins (under
// `peaks_mu_`) and settings fixed at `configure`, so a commit can build it
// with `mu_` dropped.
bool TapeIo::peaks_image(int t, int64_t frames, std::vector<uint8_t>& buf) const {
    if (!on_ || !peaks_on_ || t < 0 || t >= Tape::kTracks) return false;
    const int64_t ranges = Tape::ranges_for(frames);
    if (ranges <= 0) return false;
    const std::size_t floats = std::size_t(ranges) * kPeakBins * 2;
    buf.assign(std::size_t(kPeaksHeaderBytes) + floats * sizeof(float) + std::size_t(ranges), 0);
    std::memcpy(buf.data(), "ARBPEAK1", 8);
    const auto put32 = [&buf](std::size_t at, uint32_t v) {
        for (int i = 0; i < 4; ++i) buf[at + std::size_t(i)] = uint8_t(v >> (8 * i));
    };
    const auto put64 = [&buf](std::size_t at, uint64_t v) {
        for (int i = 0; i < 8; ++i) buf[at + std::size_t(i)] = uint8_t(v >> (8 * i));
    };
    put32(8, uint32_t(kPeakBins));
    put32(12, uint32_t(Tape::kRangeFrames));
    put32(16, sr_);
    put32(20, 0);
    put64(24, uint64_t(frames));
    {
        std::lock_guard<std::mutex> g(peaks_mu_);
        const std::vector<float>& p = peaks_[t];
        const std::vector<uint8_t>& ok = peaks_ok_[t];
        const std::size_t have = std::min(floats, p.size());
        if (have) std::memcpy(buf.data() + kPeaksHeaderBytes, p.data(), have * sizeof(float));
        for (int64_t k = 0; k < ranges; ++k)
            buf[std::size_t(kPeaksHeaderBytes) + floats * sizeof(float) + std::size_t(k)] =
                std::size_t(k) < ok.size() ? ok[std::size_t(k)] : uint8_t(0);
    }
    return true;
}

bool TapeIo::write_peaks(const std::string& path, const std::vector<uint8_t>& buf) {
    std::string err;
    const StoreHandle h = store_->open(path, true, err);
    if (h == kNoHandle) return false;
    // Truncate first: a shorter medium must not leave a longer sidecar's tail
    // behind for the next open to read as bins.
    const bool ok = store_->truncate(h, int64_t(buf.size()), err) &&
                    store_->pwrite(h, buf.data(), int64_t(buf.size()), 0, err) ==
                        int64_t(buf.size());
    store_->close(h);
    // Deliberately no `fdatasync`: it is a cache, the medium's own commit is
    // what may not be lost, and a sidecar that did not reach the card is
    // discarded on open by the frame-count check below.
    return ok;
}

void TapeIo::load_peaks(int t) {                   // `mu_` held, after `repair`
    if (!on_ || !peaks_on_) return;
    const int64_t frames = med_[t].claimed;
    const int64_t ranges = Tape::ranges_for(frames);
    if (ranges <= 0) return;
    std::string err;
    const StoreHandle h = store_->open(peaks_path(t), false, err);
    if (h == kNoHandle) return;                    // no sidecar: the ranges fill in as they are read
    const std::size_t floats = std::size_t(ranges) * kPeakBins * 2;
    const int64_t want = kPeaksHeaderBytes + int64_t(floats * sizeof(float)) + ranges;
    std::vector<uint8_t> buf(std::size_t(want), 0);
    const int64_t n = store_->pread(h, buf.data(), want, 0, err);
    store_->close(h);
    if (n != want) return;
    const auto get32 = [&buf](std::size_t at) {
        uint32_t v = 0;
        for (int i = 0; i < 4; ++i) v |= uint32_t(buf[at + std::size_t(i)]) << (8 * i);
        return v;
    };
    const auto get64 = [&buf](std::size_t at) {
        uint64_t v = 0;
        for (int i = 0; i < 8; ++i) v |= uint64_t(buf[at + std::size_t(i)]) << (8 * i);
        return v;
    };
    // The whole staleness check: a medium and its sidecar are only ever written
    // together, so a frame count that does not match the header the repair just
    // made exact is a file from before something else happened to the medium.
    if (std::memcmp(buf.data(), "ARBPEAK1", 8) != 0 || get32(8) != uint32_t(kPeakBins) ||
        get32(12) != uint32_t(Tape::kRangeFrames) || get32(16) != sr_ ||
        int64_t(get64(24)) != frames)
        return;
    std::lock_guard<std::mutex> g(peaks_mu_);
    peaks_[t].assign(floats, 0.0f);
    std::memcpy(peaks_[t].data(), buf.data() + kPeaksHeaderBytes, floats * sizeof(float));
    peaks_ok_[t].assign(std::size_t(ranges), 0);
    for (int64_t k = 0; k < ranges; ++k)
        peaks_ok_[t][std::size_t(k)] =
            buf[std::size_t(kPeaksHeaderBytes) + floats * sizeof(float) + std::size_t(k)];
}

// ---- compaction (C1 step 8, design § 3.8) ----------------------------------
//
// The medium is append-only, so a retake, a LIFT without a DROP and every
// `tape_edit` leave audio nothing references. Compaction is how the disk comes
// back, and § 3.8's whole argument is about *who decides what to keep*: the
// clip list is the UI's, and the UI holds two more references the host cannot
// see -- the lift buffer and the one-level undo record (DDR-054/055). So the
// UI sends the keep set, this copies exactly those spans, and the map goes
// back for the UI to remap its own geometry through.
//
// Three rules the job keeps, each of them a way the copy could otherwise read
// something that was about to change under it:
//
//   * **never while a take is open** -- the caller refuses with `RECORDING`,
//     like `tape_edit` and `tape_save`;
//   * **only while the transport is stopped**, so the copy is not competing
//     with read-ahead for the card;
//   * **everything durable first** -- `compact_start` flushes, because the
//     copy reads the card rather than the pool. That is also what makes the
//     result correct for a range the pool still holds: the card's copy is the
//     same bytes.
//
// Cancelled by PLAY or REC arriving: the scratch file is deleted and the old
// medium has not been touched, so the worst a cancel costs is the copying
// already done. The retry is the next trigger's.

// #167: the copy drops `mu_` around every read/write pair, so a caller holding
// `mu_` can find the scratch handle and the medium handle in use by a thread
// that is not holding it. Anything about to close either waits here first. The
// wait is one range of card time -- ~27 ms on the bench eMMC, against the whole
// job's seven seconds, which is what `compact_cancel` used to wait.
void TapeIo::wait_io(std::unique_lock<std::mutex>& lk) {
    io_cv_.wait(lk, [this] { return io_busy_ == 0; });
}

void TapeIo::compact_drop(bool unlink) {            // `mu_` held
    if (comp_.h != kNoHandle) {
        store_->close(comp_.h);
        comp_.h = kNoHandle;
    }
    if (unlink && !comp_.path.empty()) {
        std::string e;
        store_->remove(comp_.path, e);
    }
}

TapeIo::CompactState TapeIo::compact_state() const {
    std::lock_guard<std::mutex> g(mu_);
    return comp_.state;
}

int TapeIo::compact_track() const {
    std::lock_guard<std::mutex> g(mu_);
    return comp_.track;
}

std::string TapeIo::compact_error() const {
    std::lock_guard<std::mutex> g(mu_);
    return comp_.err;
}

int64_t TapeIo::compact_freed() const {
    std::lock_guard<std::mutex> g(mu_);
    return comp_.freed;
}

void TapeIo::compact_clear() {
    std::unique_lock<std::mutex> lk(mu_);
    if (comp_.state == CompactState::kRunning) return;   // `compact_cancel` first
    // `io_busy_` cannot be set here -- a slice only runs while the job does,
    // and `compact_cancel` has already waited one out. It is the standing rule
    // rather than a live case: nothing closes either handle without it (#167).
    wait_io(lk);
    compact_drop(true);
    comp_ = Compact{};
    compact_busy_.store(false, std::memory_order_relaxed);
}

void TapeIo::compact_cancel() {
    if (!on_) return;
    std::unique_lock<std::mutex> lk(mu_);
    if (comp_.state == CompactState::kIdle) return;
    // A `kDone` job is cancelled too: nothing has been swapped in, and the
    // caller that cancels is starting a take on the medium the job meant to
    // replace.
    //
    // The state goes down **before** the wait, so a slice that is in the card
    // right now comes back, sees `kRunning` gone and copies no further: the
    // cancel lands inside the slice rather than behind it. Then `wait_io`
    // makes this a wait for that one slice -- one range, not the eight
    // `kCompactRanges` a turn copies and not the whole job (#167) -- which is
    // what the contract above promises, since the caller is about to record
    // onto the medium this job was reading.
    comp_.state = CompactState::kCancelled;
    comp_.err = "cancelled";
    compact_busy_.store(false, std::memory_order_relaxed);
    wait_io(lk);
    compact_drop(true);
}

bool TapeIo::compact_start(int t, std::vector<KeepSpan> keep, int64_t* freed) {
    if (freed) *freed = 0;
    if (!on_ || t < 0 || t >= Tape::kTracks) return false;
    {
        std::lock_guard<std::mutex> g(mu_);
        if (comp_.state != CompactState::kIdle) return false;
    }
    // Before `mu_`: `flush()` waits on the I/O thread, which wants it.
    flush();

    std::unique_lock<std::mutex> lk(mu_);
    if (comp_.state != CompactState::kIdle) return false;
    if (!open_medium(t)) return false;

    comp_ = Compact{};
    comp_.track = t;
    comp_.path = medium_path(t) + ".compact";

    // Sorted, clamped to what the medium really claims, and coalesced: two
    // spans that touch or overlap become one, so the map has one row per run
    // of kept audio and the UI's remap is a lookup rather than a merge.
    const int64_t claimed = med_[t].claimed;
    std::sort(keep.begin(), keep.end(),
              [](const KeepSpan& a, const KeepSpan& b) { return a.from < b.from; });
    int64_t covered = 0;
    for (const KeepSpan& s : keep) {
        const int64_t from = std::max<int64_t>(0, s.from);
        const int64_t end = std::min(claimed, s.from + std::max<int64_t>(0, s.len));
        if (end <= from) continue;
        if (!comp_.keep.empty()) {
            KeepSpan& last = comp_.keep.back();
            if (from <= last.from + last.len) {
                last.len = std::max(last.len, end - last.from);
                continue;
            }
        }
        comp_.keep.push_back(KeepSpan{from, end - from});
    }
    for (const KeepSpan& s : comp_.keep) covered += s.len;
    comp_.freed = std::max<int64_t>(0, claimed - covered);
    // Handed back here rather than left for a second `compact_freed()` call:
    // that call is the reply's `dead_frames`, and a reply is the one thing
    // this design promised would not wait for the copy (#167).
    if (freed) *freed = comp_.freed;

    std::string err;
    store_->remove(comp_.path, err);              // a cancelled job's leftovers
    comp_.h = store_->open(comp_.path, true, err);
    if (comp_.h == kNoHandle) {
        comp_.state = CompactState::kFailed;
        comp_.err = comp_.path + ": " + err;
        note_error(comp_.err);
        return false;
    }
    // A claim of zero from the first byte, so a cut mid-copy leaves a scratch
    // file repair-on-open would read as empty -- and `compact_finish` is the
    // only thing that ever gives it the medium's name.
    write_header(header_.data(), sr_, 0);
    if (!store_->truncate(comp_.h, kHeaderBytes, err) ||
        store_->pwrite(comp_.h, header_.data(), kHeaderBytes, 0, err) != kHeaderBytes) {
        comp_.state = CompactState::kFailed;
        comp_.err = comp_.path + ": " + err;
        note_error(comp_.err);
        compact_drop(true);
        return false;
    }
    comp_.state = CompactState::kRunning;
    compact_busy_.store(true, std::memory_order_relaxed);

    if (!thread_.joinable()) {
        // Offline there is no I/O thread and no callback can be in flight, so
        // the job runs here -- the same arrangement `flush()` makes, and what
        // keeps one wire shape for both (`poll()` pushes the result either
        // way). Nothing can cancel it, because nothing else is running.
        while (comp_.state == CompactState::kRunning) compact_step(lk);
    } else {
        wake_.notify_all();
    }
    return true;
}

void TapeIo::compact_step(std::unique_lock<std::mutex>& lk) {
    Compact& c = comp_;
    if (c.state != CompactState::kRunning) return;
    const auto fail = [&](const std::string& what) {
        c.state = CompactState::kFailed;
        c.err = c.path + ": " + what;
        note_error(c.err);
        compact_drop(true);
        compact_busy_.store(false, std::memory_order_relaxed);
    };
    if (med_[c.track].h == kNoHandle || c.h == kNoHandle) { fail("medium closed"); return; }
    if (copy_.size() < std::size_t(kRangeBytes)) copy_.assign(std::size_t(kRangeBytes), 0);

    // #167: `mu_` goes down around every call that touches the card, and while
    // it is down this job's own state is the only thing that may be read or
    // written without it. Both handles are taken by value first, because `med_`
    // and `comp_` are `mu_`'s -- and because `wait_io` guarantees neither
    // handle is closed inside the window, a copy of one stays valid for it.
    // Everything after the relock re-checks `c.state`: a cancel that landed
    // while the card had us has already given the scratch file back, and
    // advancing the job over the top of it would write into a closed handle
    // next turn.
    const bool hold = compact_holds_lock_;        // the *before* switch
    const auto io = [&](auto&& call) {
        if (hold) return call();
        ++io_busy_;
        lk.unlock();
        auto r = call();
        lk.lock();
        --io_busy_;
        io_cv_.notify_all();
        return r;
    };

    std::string err;
    for (int64_t n = 0; n < kCompactRanges; ++n) {
        if (c.state != CompactState::kRunning) return;      // cancelled under the card
        const StoreHandle src = med_[c.track].h;
        const StoreHandle dst = c.h;
        if (c.at >= c.keep.size()) {
            // The header last, and only now: until it claims them the frames
            // in the file are not audio the medium owns -- the same lag rule
            // a take's commit keeps (§ 3.5). `chead_` and not `header_`,
            // because that buffer belongs to `commit` and `repair` and this
            // write no longer holds the lock that kept them apart.
            write_header(chead_.data(), sr_, c.written);
            const bool ok = io([&] {
                return store_->pwrite(dst, chead_.data(), kHeaderBytes, 0, err) == kHeaderBytes &&
                       store_->fdatasync(dst, err);
            });
            if (c.state != CompactState::kRunning) return;
            if (!ok) { fail(err); return; }
            syncs_.fetch_add(1, std::memory_order_relaxed);
            store_->close(c.h);
            c.h = kNoHandle;
            c.state = CompactState::kDone;
            compact_busy_.store(false, std::memory_order_relaxed);
            return;
        }
        const KeepSpan s = c.keep[c.at];                    // by value: `lk` goes down below
        if (c.done == 0) {
            // § 3.8's "each starting on a range boundary": a range of the new
            // medium then never straddles two source spans, which is what lets
            // the pool go on treating a range as one fetch and one commit.
            c.written = Tape::ranges_for(c.written) * Tape::kRangeFrames;
            c.map.push_back(CompactMove{s.from, c.written, s.len});
        }
        const int64_t take = std::min<int64_t>(Tape::kRangeFrames, s.len - c.done);
        const int64_t bytes = take * kFrameBytes;
        const int64_t from = kHeaderBytes + (s.from + c.done) * kFrameBytes;
        const int64_t to = kHeaderBytes + c.written * kFrameBytes;
        const int64_t rd = io([&] { return store_->pread(src, copy_.data(), bytes, from, err); });
        if (c.state != CompactState::kRunning) return;
        if (rd < 0) { fail(err); return; }
        // A short read is a span the card does not really have behind it
        // (a repair truncated it): silence, not a refusal -- the keep set is
        // the UI's geometry and the medium's length is the host's.
        if (rd < bytes) std::memset(copy_.data() + rd, 0, std::size_t(bytes - rd));
        const int64_t wr = io([&] { return store_->pwrite(dst, copy_.data(), bytes, to, err); });
        if (c.state != CompactState::kRunning) return;
        if (wr != bytes) { fail(err); return; }
        c.written += take;
        c.done += take;
        if (c.done >= s.len) {
            ++c.at;
            c.done = 0;
        }
    }
}

bool TapeIo::compact_finish(std::vector<CompactMove>& map, int64_t& frames, std::string& err) {
    std::unique_lock<std::mutex> lk(mu_);
    if (comp_.state != CompactState::kDone) {
        err = "no finished compaction";
        return false;
    }
    wait_io(lk);                               // #167: it closes both handles
    const int t = comp_.track;
    map = comp_.map;
    frames = comp_.written;

    compact_drop(false);                       // close the scratch; keep the file
    Medium& m = med_[t];
    if (m.h != kNoHandle) store_->close(m.h);
    m.h = kNoHandle;
    m.opened = false;
    const std::string scratch = comp_.path;
    comp_ = Compact{};
    compact_busy_.store(false, std::memory_order_relaxed);

    if (!store_->rename(scratch, medium_path(t), err)) {
        note_error(err);
        open_medium(t);                        // the old medium is still its own name
        return false;
    }
    sync_dirs(medium_path(t), medium_path(t));
    // Every resident range of this track holds audio from a frame offset that
    // has just moved, and the table is keyed by offset: drop the lot through
    // the same retire-and-grace path the evictor uses, so a callback that read
    // an entry before the drop still has its block to finish with it. They are
    // fetched back off the new medium on demand. Safe because the transport is
    // stopped and no take is open -- the two rules above.
    if (tape_->bounded()) {
        const int64_t ranges = tape_->ranges();
        for (int64_t k = 0; k < ranges; ++k) {
            const int32_t slot = tape_->slot_at(t, k);
            if (slot < 0) continue;
            if (tape_->retire(t, k, slot))
                grace_.push_back(Grace{slot, tape_->blocks() + Tape::kGraceBlocks});
        }
    }
    std::fill(dirty_[t].begin(), dirty_[t].end(), uint8_t(0));
    // #163: every frame offset in this medium has just moved, so its bins are
    // about a medium that no longer exists -- dropped, and the file with them,
    // rather than remapped. It costs the first draw of each clip after a
    // compaction the scan it cost before the sidecar existed, and then heals
    // itself: that scan fetches, and a fetch computes the bins again.
    drop_peaks(t, true);
    if (!open_medium(t)) {
        err = last_error_;
        return false;
    }
    return true;
}

TapeIo::Stats TapeIo::stats() const {
    Stats s;
    s.queued = queued_.load(std::memory_order_relaxed);
    s.committed = committed_.load(std::memory_order_relaxed);
    s.allocs = allocs_.load(std::memory_order_relaxed);
    s.alloc_bytes = alloc_bytes_.load(std::memory_order_relaxed);
    s.alloc_refused = alloc_refused_.load(std::memory_order_relaxed);
    s.dropped = dropped_.load(std::memory_order_relaxed);
    s.absent = absent_.load(std::memory_order_relaxed);
    s.errors = errors_.load(std::memory_order_relaxed);
    s.syncs = syncs_.load(std::memory_order_relaxed);
    s.fetched = fetched_.load(std::memory_order_relaxed);
    s.evicted = evicted_.load(std::memory_order_relaxed);
    s.starved = starved_.load(std::memory_order_relaxed);
    s.repaired = repaired_.load(std::memory_order_relaxed);
    s.lost_frames = lost_frames_.load(std::memory_order_relaxed);
    s.refused = refused_.load(std::memory_order_relaxed);
    s.wanted = wanted_n_.load(std::memory_order_relaxed);
    s.capped = capped_.load(std::memory_order_relaxed);
    s.disk_full = disk_full_.load(std::memory_order_relaxed);
    s.peaks_hit = peaks_hit_.load(std::memory_order_relaxed);
    s.peaks_miss = peaks_miss_.load(std::memory_order_relaxed);
    for (int t = 0; t < Tape::kTracks; ++t) s.peaks_ranges += peaks_ranges(t);
    // Read without `mu_` on purpose: that mutex is held across a commit, and a
    // state document must never wait behind the card (#93's freeze, again).
    s.queue = queue_depth_.load(std::memory_order_relaxed);
    return s;
}

}  // namespace arb::host
