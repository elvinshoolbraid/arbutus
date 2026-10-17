#include "tape.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <new>

namespace arb::host {

double Travel::at(uint32_t k) const {
    const double p = pos + double(k) * step;
    if (loop && loop_out > loop_in) {
        const double span = double(loop_out - loop_in);
        double m = std::fmod(p - double(loop_in), span);
        if (m < 0.0) m += span;
        return double(loop_in) + m;
    }
    return std::clamp(p, 0.0, double(std::max<int64_t>(0, capacity - 1)));
}

// ---- the free list ----------------------------------------------------------

void Tape::IndexRing::reset(std::size_t n) {
    buf_.assign(n + 1, kAbsent);               // one spare: full is w+1 == r
    w_.store(0, std::memory_order_relaxed);
    r_.store(0, std::memory_order_relaxed);
}

bool Tape::IndexRing::push(int32_t v) {
    if (buf_.empty()) return false;
    const std::size_t w = w_.load(std::memory_order_relaxed);
    const std::size_t n = (w + 1) % buf_.size();
    if (n == r_.load(std::memory_order_acquire)) return false;
    buf_[w] = v;
    w_.store(n, std::memory_order_release);
    return true;
}

bool Tape::IndexRing::pop(int32_t& v) {
    if (buf_.empty()) return false;
    // Read the value, then claim the index. A producer may only write at `w_`
    // and stops one short of `r_`, so an unclaimed entry cannot be overwritten
    // under us: if the CAS succeeds nobody had claimed it, and if it fails the
    // read is discarded and tried again.
    for (;;) {
        const std::size_t r = r_.load(std::memory_order_acquire);
        if (r == w_.load(std::memory_order_acquire)) return false;
        const int32_t candidate = buf_[r];
        std::size_t expect = r;
        if (r_.compare_exchange_weak(expect, (r + 1) % buf_.size(),
                                     std::memory_order_acq_rel, std::memory_order_acquire)) {
            v = candidate;
            return true;
        }
    }
}

std::size_t Tape::IndexRing::size() const {
    if (buf_.empty()) return 0;
    const std::size_t w = w_.load(std::memory_order_acquire);
    const std::size_t r = r_.load(std::memory_order_acquire);
    return (w + buf_.size() - r) % buf_.size();
}

void Tape::init(int64_t capacity_frames, int64_t pool_slots) {
    capacity_ = capacity_frames;
    ranges_ = ranges_for(capacity_);
    bounded_ = pool_slots > 0;
    pool_slots_ = bounded_ ? pool_slots : 0;
    slots_.clear();
    slots_.shrink_to_fit();
    // The pool never reallocates its table while the audio thread is reading a
    // slot out of it: every slot a session can ever need has its place here
    // from init (8 x 469 pointers for the device's 8-minute ruler). Bounded,
    // that is the flag's number and the slots themselves are allocated now.
    slots_.reserve(bounded_ ? std::size_t(pool_slots_) : std::size_t(ranges_) * kTracks);
    free_.reset(bounded_ ? std::size_t(pool_slots_) : 0);
    wants_ = std::vector<std::atomic<int64_t>>(
        bounded_ ? std::size_t(pool_slots_) + 1 : std::size_t(1));
    want_w_.store(0, std::memory_order_relaxed);
    want_r_.store(0, std::memory_order_relaxed);
    for (int t = 0; t < kTracks; ++t) {
        table_[t] = std::vector<std::atomic<int32_t>>(std::size_t(ranges_));
        for (auto& e : table_[t]) e.store(kAbsent, std::memory_order_relaxed);
        allocated_[t] = false;                 // resident on first use (tape.hpp)
        empty_[t] = std::make_unique<ClipList>();
        live_[t] = empty_[t].get();
        head[t].store(0, std::memory_order_relaxed);
        write_range_[t].store(-1, std::memory_order_relaxed);
        hot_[t].store(-1, std::memory_order_relaxed);   // no read position yet
    }
    late_mask_.store(0, std::memory_order_relaxed);
    blocks_.store(0, std::memory_order_relaxed);
    pin_.store(-1, std::memory_order_relaxed);
    view_pos_.store(-1, std::memory_order_relaxed);        // nothing published yet
    view_step_.store(1.0, std::memory_order_relaxed);
    view_in_.store(0, std::memory_order_relaxed);
    view_out_.store(0, std::memory_order_relaxed);
    view_flags_.store(0, std::memory_order_relaxed);
    if (bounded_)
        for (int64_t i = 0; i < pool_slots_; ++i) {
            slots_.push_back(std::make_unique<float[]>(std::size_t(kRangeFrames) * 2));
            free_.push(int32_t(i));            // value-initialised, so silence
        }
}

bool Tape::ensure(int t) {
    if (t < 0 || t >= kTracks) return false;
    if (allocated_[t]) return true;
    // Bounded, a medium is a name and a table, not memory: the ranges arrive
    // from the free list as the take fills them and off the card as it is read.
    if (bounded_) { allocated_[t] = true; return true; }
    const std::size_t first = slots_.size();
    try {
        for (int64_t k = 0; k < ranges_; ++k) {
            // value-initialised: a fresh range is silence, as a zeroed medium was
            slots_.push_back(std::make_unique<float[]>(std::size_t(kRangeFrames) * 2));
            table_[t][std::size_t(k)].store(int32_t(first + std::size_t(k)),
                                            std::memory_order_release);
        }
    } catch (const std::bad_alloc&) {
        for (int64_t k = 0; k < ranges_; ++k)
            table_[t][std::size_t(k)].store(kAbsent, std::memory_order_release);
        slots_.resize(first);
        return false;
    }
    allocated_[t] = true;
    return true;
}

const float* Tape::run_at(int t, int64_t f, int64_t& run) const {
    run = 0;
    if (t < 0 || t >= kTracks || f >= capacity_) return nullptr;   // nothing further
    if (f < 0) { run = -f; return nullptr; }                       // skip up to frame 0
    const int64_t k = range_of(f);
    const int64_t in = f - k * kRangeFrames;
    run = std::min(kRangeFrames - in, capacity_ - f);
    const int32_t s = table_[t][std::size_t(k)].load(std::memory_order_acquire);
    if (s < 0) return nullptr;                 // kAbsent or kWanted: silence
    return slots_[std::size_t(s)].get() + std::size_t(in) * 2;
}

float* Tape::run_at_mut(int t, int64_t f, int64_t& run) {
    return const_cast<float*>(const_cast<const Tape*>(this)->run_at(t, f, run));
}

// ---- the bounded pool -------------------------------------------------------

void Tape::set_view(const View& v) {
    view_pos_.store(v.pos, std::memory_order_relaxed);
    view_step_.store(v.step, std::memory_order_relaxed);
    view_in_.store(v.loop_in, std::memory_order_relaxed);
    view_out_.store(v.loop_out, std::memory_order_relaxed);
    view_flags_.store((v.loop ? 1u : 0u) | (v.playing ? 2u : 0u), std::memory_order_relaxed);
}

Tape::View Tape::view() const {
    View v;
    v.pos = view_pos_.load(std::memory_order_relaxed);
    v.step = view_step_.load(std::memory_order_relaxed);
    v.loop_in = view_in_.load(std::memory_order_relaxed);
    v.loop_out = view_out_.load(std::memory_order_relaxed);
    const uint32_t f = view_flags_.load(std::memory_order_relaxed);
    v.loop = (f & 1u) != 0;
    v.playing = (f & 2u) != 0;
    return v;
}

void Tape::set_write_range(int t, int64_t k) {
    if (t < 0 || t >= kTracks) return;
    write_range_[t].store(k, std::memory_order_relaxed);
}

int32_t Tape::slot_at(int t, int64_t k) const {
    if (t < 0 || t >= kTracks || k < 0 || k >= ranges_) return kAbsent;
    return table_[t][std::size_t(k)].load(std::memory_order_acquire);
}

bool Tape::acquire(int t, int64_t k) {
    if (k < 0 || k >= ranges_) return false;
    std::atomic<int32_t>& e = table_[t][std::size_t(k)];
    const int32_t cur = e.load(std::memory_order_acquire);
    if (cur >= 0) return true;                 // already resident
    int32_t slot = kAbsent;
    if (!free_.pop(slot)) return false;        // the pool is empty: TAPE FULL
    // The slot came off the free list zeroed (`release_free`), so a range the
    // take has not written yet is silence, exactly as a fresh medium was.
    e.store(slot, std::memory_order_release);
    return true;
}

void Tape::want(int t, int64_t k) const {
    if (!bounded_ || k < 0 || k >= ranges_) return;
    std::atomic<int32_t>& e = table_[t][std::size_t(k)];
    int32_t expect = kAbsent;
    // Only the transition wins, so however many blocks pass before the fetch
    // lands, the I/O thread is asked exactly once.
    if (!e.compare_exchange_strong(expect, kWanted, std::memory_order_acq_rel,
                                   std::memory_order_acquire))
        return;
    const std::size_t w = want_w_.load(std::memory_order_relaxed);
    const std::size_t n = (w + 1) % wants_.size();
    if (n == want_r_.load(std::memory_order_acquire)) {
        e.store(kAbsent, std::memory_order_release);   // ring full: ask again later
        return;
    }
    wants_[w].store(int64_t(t) * ranges_ + k, std::memory_order_relaxed);
    want_w_.store(n, std::memory_order_release);
}

bool Tape::take_want(Want& w) {
    const std::size_t r = want_r_.load(std::memory_order_relaxed);
    if (r == want_w_.load(std::memory_order_acquire)) return false;
    const int64_t v = wants_[r].load(std::memory_order_relaxed);
    want_r_.store((r + 1) % wants_.size(), std::memory_order_release);
    w.track = int32_t(v / ranges_);
    w.range = v % ranges_;
    return true;
}

bool Tape::retire(int t, int64_t k, int32_t slot) {
    if (t < 0 || t >= kTracks || k < 0 || k >= ranges_ || slot < 0) return false;
    int32_t expect = slot;
    return table_[t][std::size_t(k)].compare_exchange_strong(
        expect, kAbsent, std::memory_order_acq_rel, std::memory_order_acquire);
}

void Tape::publish(int t, int64_t k, int32_t slot) {
    if (t < 0 || t >= kTracks || k < 0 || k >= ranges_ || slot < 0) return;
    table_[t][std::size_t(k)].store(slot, std::memory_order_release);
}

void Tape::unwant(int t, int64_t k) {
    if (t < 0 || t >= kTracks || k < 0 || k >= ranges_) return;
    int32_t expect = kWanted;
    table_[t][std::size_t(k)].compare_exchange_strong(expect, kAbsent,
                                                      std::memory_order_acq_rel,
                                                      std::memory_order_acquire);
}

void Tape::release_free(int32_t slot) {
    if (slot < 0 || std::size_t(slot) >= slots_.size()) return;
    std::memset(slots_[std::size_t(slot)].get(), 0,
                std::size_t(kRangeFrames) * 2 * sizeof(float));
    free_.push(slot);
}

float* Tape::slot_data(int32_t slot) {
    if (slot < 0 || std::size_t(slot) >= slots_.size()) return nullptr;
    return slots_[std::size_t(slot)].get();
}

int32_t Tape::take_free() {
    int32_t slot = kAbsent;
    return free_.pop(slot) ? slot : kAbsent;
}

int64_t Tape::free_count() const { return int64_t(free_.size()); }

int64_t Tape::resident_count() const {
    int64_t n = 0;
    for (int t = 0; t < kTracks; ++t)
        for (const auto& e : table_[t])
            if (e.load(std::memory_order_relaxed) >= 0) ++n;
    return n;
}

int64_t Tape::read_frames(int t, int64_t from, int64_t frames, float* dst) const {
    if (frames <= 0) return 0;
    std::memset(dst, 0, std::size_t(frames) * 2 * sizeof(float));
    if (t < 0 || t >= kTracks || !allocated_[t]) return 0;
    int64_t done = 0;
    while (done < frames) {
        int64_t run = 0;
        const float* src = run_at(t, from + done, run);
        if (run <= 0) break;
        const int64_t n = std::min(run, frames - done);
        if (src) std::memcpy(dst + std::size_t(done) * 2, src, std::size_t(n) * 2 * sizeof(float));
        done += n;
    }
    return done;
}

int64_t Tape::write_frames(int t, int64_t from, int64_t frames, const float* src) {
    if (frames <= 0 || t < 0 || t >= kTracks || !allocated_[t]) return 0;
    int64_t done = 0;
    while (done < frames) {
        // `tape_edit` / `tape_load`, the only callers here, are refused while a
        // take is open, so nothing else is writing this medium -- the same
        // invariant `head` already rests on. Taking a slot itself is safe from
        // any thread (IndexRing::pop).
        if (bounded_ && !acquire(t, range_of(from + done))) break;
        int64_t run = 0;
        float* dst = run_at_mut(t, from + done, run);
        if (run <= 0) break;
        const int64_t n = std::min(run, frames - done);
        if (dst) std::memcpy(dst, src + std::size_t(done) * 2, std::size_t(n) * 2 * sizeof(float));
        done += n;
    }
    return done;
}

void Tape::render_track(int t, const Travel& tr, uint32_t n, float* l, float* r) const {
    std::fill_n(l, n, 0.0f);
    std::fill_n(r, n, 0.0f);
    const ClipList* cl = live_[t];
    if (!cl || cl->clips.empty()) return;
    const uint32_t bit = 1u << t;

    // 1x forward inside the tape with no wrap in this block: whole runs per
    // clip. Anything else (varispeed, REV, a loop seam) goes frame by frame,
    // reading the frame the playhead is on -- the emulator's nearest-lower
    // index, no interpolation.
    const double last = tr.pos + double(n - 1) * tr.step;
    const bool plain = tr.step == 1.0 && tr.pos == std::floor(tr.pos) && tr.pos >= 0.0 &&
                       (!tr.loop || tr.loop_out <= tr.loop_in ||
                        (tr.pos >= double(tr.loop_in) && last < double(tr.loop_out))) &&
                       last < double(capacity_);
    if (plain) {
        const int64_t pos = int64_t(tr.pos);
        for (const Clip& c : cl->clips) {
            const int mt = c.src_track >= 0 && c.src_track < kTracks ? c.src_track : t;
            if (!allocated_[mt]) continue;              // never written: silence, uncounted
            const int64_t a = std::max(pos, c.start_frame);
            const int64_t b = std::min(pos + int64_t(n), c.start_frame + c.len);
            int64_t f = a;
            while (f < b) {
                const int64_t s = c.src_offset + (f - c.start_frame);
                int64_t run = 0;
                const float* src = run_at(mt, s, run);
                if (run <= 0) break;                    // past the end of the medium
                const int64_t take = std::min(run, b - f);
                if (!src) {
                    // A clip reading before frame 0 is the UI's geometry, not a
                    // range the card owed us: silence, uncounted.
                    if (s >= 0) {
                        late_mask_.fetch_or(bit, std::memory_order_relaxed);
                        want(mt, range_of(s));
                    }
                } else {
                    float* dl = l + std::size_t(f - pos);
                    float* dr = r + std::size_t(f - pos);
                    for (int64_t i = 0; i < take; ++i) {
                        dl[i] += src[std::size_t(i) * 2] * c.gain;
                        dr[i] += src[std::size_t(i) * 2 + 1] * c.gain;
                    }
                }
                f += take;
            }
            // Where this track is being read, which is the evictor's distance
            // key. Step 2b also asked here for the range *after* it -- a
            // one-range lead. Step 5 deletes that: read-ahead is the I/O
            // thread's wanted set now (tape.hpp), computed in seconds of wall
            // clock over every clip list, and a lead left here would make
            // `--tape-readahead-seconds 0` mean nothing.
            if (b > a)
                hot_[mt].store(c.src_offset + (b - 1 - c.start_frame),
                               std::memory_order_relaxed);
        }
        return;
    }
    for (uint32_t k = 0; k < n; ++k) {
        const int64_t f = int64_t(std::floor(tr.at(k)));
        for (const Clip& c : cl->clips) {
            if (f < c.start_frame || f >= c.start_frame + c.len) continue;
            const int mt = c.src_track >= 0 && c.src_track < kTracks ? c.src_track : t;
            if (!allocated_[mt]) continue;
            const int64_t s = c.src_offset + (f - c.start_frame);
            int64_t run = 0;
            const float* src = run_at(mt, s, run);
            if (run <= 0) continue;
            if (!src) {
                if (s >= 0) {
                    late_mask_.fetch_or(bit, std::memory_order_relaxed);
                    want(mt, range_of(s));
                }
                continue;
            }
            // Varispeed, REV and the scrub grain: the read position, for the
            // evictor. The horizon that follows the direction of travel is the
            // I/O thread's (it has `Travel`'s own `step` through `set_view`).
            if (k == 0 && s >= 0) hot_[mt].store(s, std::memory_order_relaxed);
            l[k] += src[0] * c.gain;
            r[k] += src[1] * c.gain;
        }
    }
}

uint32_t Tape::write(int t, int64_t at, const float* l, const float* r, uint32_t n) {
    // The control thread calls ensure() before it arms a take, so an absent
    // range here is a bug, not a path (the audio thread never allocates).
    if (t < 0 || t >= kTracks || !allocated_[t]) return 0;
    // `hot_` is deliberately *not* touched here: it is the **read** position the
    // evictor keeps its guard band around, and a take's write head is the
    // opposite — the ranges behind it are finished and are the first that should
    // go. The range being written is protected by `write_range_` instead.
    int64_t done = 0;
    while (done < int64_t(n)) {
        const int64_t k = range_of(at + done);
        set_write_range(t, k);                 // never evicted while it is this
        // Bounded, the range the take is about to fill has to be made resident
        // first, from the free list -- the one place the audio thread takes a
        // slot. Empty means the pool has become all write queue (a stalled
        // card) and the caller ends the take: `tape_full {reason:"stall"}`.
        if (bounded_ && !acquire(t, k)) break;
        int64_t run = 0;
        float* dst = run_at_mut(t, at + done, run);
        if (run <= 0) break;
        const int64_t take = std::min(run, int64_t(n) - done);
        if (dst) {
            for (int64_t i = 0; i < take; ++i) {
                dst[std::size_t(i) * 2] = l[std::size_t(done + i)];
                dst[std::size_t(i) * 2 + 1] = r[std::size_t(done + i)];
            }
        }
        done += take;
    }
    return uint32_t(done);
}

ClipList* Tape::swap_clips(int t, ClipList* next) {
    ClipList* old = live_[t];
    live_[t] = next;
    return (old == empty_[t].get()) ? nullptr : old;
}

}  // namespace arb::host
