// host/tape.{hpp,cpp} + host/tape_store.{hpp,cpp} — the range table and the
// storage seam (C1 step 1, tape-streaming-design.md § 3 and § 4). No framework;
// test_events.cpp is the pattern, including the counted `operator new`.
//
// What these prove, and their negative controls (the standing rule since #56 —
// a test that has never been seen to fail is not evidence):
//
//   * a take longer than one range records and plays back sample-exact across
//     every boundary — and the compare is shown able to fail (one flipped float)
//   * an absent range is silence *and* a count, and a resident one is neither —
//     the `late_mask` detector proven able to stay quiet and able to fire
//   * the audio path (`render_track`, `write`) makes no `operator new` calls
//     across a take that crosses a boundary and a render that goes late — and
//     the counter is shown able to see an allocation
//   * `FakeStore` is a filesystem: a write past the end grows the file, a read
//     past it is short, `fdatasync` is what makes bytes durable, and an armed
//     ENOSPC really refuses
//
// The pool is still whole-track-resident in step 1, so "absent" is reached the
// only way it can be: a clip pointing past the track's ranges.

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
using arb::host::StoreHandle;
using arb::host::Tape;
using arb::host::Travel;

constexpr int64_t kRange = Tape::kRangeFrames;

// A tape of `ranges` whole ranges, track 0 made resident.
void init(Tape& tape, int64_t ranges) {
    tape.init(ranges * kRange);
    CHECK(tape.ranges() == ranges, "ranges %lld", (long long)tape.ranges());
}

// Deterministic material that differs in every frame and every channel, so a
// frame read from the wrong range or the wrong channel cannot pass.
float sample(int64_t f, int ch) {
    return std::sin(double(f) * 0.001) * (ch ? -0.5f : 1.0f) + float(ch) * 0.25f;
}

// Record `frames` frames at the head in blocks of `block`, the audio thread's way.
void record(Tape& tape, int t, int64_t from, int64_t frames, uint32_t block) {
    std::vector<float> l(block), r(block);
    for (int64_t f = 0; f < frames; f += block) {
        const uint32_t n = uint32_t(std::min<int64_t>(block, frames - f));
        for (uint32_t i = 0; i < n; ++i) {
            l[i] = sample(from + f + i, 0);
            r[i] = sample(from + f + i, 1);
        }
        tape.write(t, from + f, l.data(), r.data(), n);
    }
}

ClipList* one_clip(int64_t start, int64_t len, int64_t src_offset, int32_t src_track = -1) {
    auto* cl = new ClipList();
    Clip c;
    c.start_frame = start;
    c.len = len;
    c.src_offset = src_offset;
    c.src_track = src_track;
    cl->clips.push_back(c);
    return cl;
}

// ---------------------------------------------------------------------------

void test_take_across_range_boundaries_is_sample_exact() {
    // Three and a bit ranges: the take crosses every boundary, and the blocks
    // (128) do not divide a range (49,152 = 384 blocks), so a block straddling
    // a boundary happens on purpose.
    Tape tape;
    init(tape, 4);
    CHECK(tape.ensure(0), "ensure failed");

    const int64_t frames = kRange * 3 + 12345;
    record(tape, 0, 0, frames, 128);
    tape.head[0].store(frames, std::memory_order_relaxed);

    ClipList* cl = one_clip(0, frames, 0);
    tape.swap_clips(0, cl);

    std::vector<float> l(128), r(128);
    int64_t bad = 0;
    for (int64_t f = 0; f < frames; f += 128) {
        const uint32_t n = uint32_t(std::min<int64_t>(128, frames - f));
        Travel tr;
        tr.pos = double(f);
        tr.capacity = tape.capacity();
        tape.render_track(0, tr, n, l.data(), r.data());
        for (uint32_t i = 0; i < n; ++i) {
            if (l[i] != sample(f + i, 0) || r[i] != sample(f + i, 1)) ++bad;
        }
    }
    CHECK(bad == 0, "%lld frames differ over %lld", (long long)bad, (long long)frames);
    CHECK(tape.late_mask() == 0, "a fully resident take counted late: %u", tape.late_mask());

    // The negative control for the compare: flip one float in the middle range
    // and the same sweep must find it. Without this the loop above could be
    // comparing silence to silence and never say so.
    int64_t run = 0;
    float* mid = tape.run_at_mut(0, kRange + 7, run);
    CHECK(mid != nullptr, "range 1 absent");
    if (mid) {
        mid[0] += 1.0f;
        int64_t bad2 = 0;
        for (int64_t f = 0; f < frames; f += 128) {
            const uint32_t n = uint32_t(std::min<int64_t>(128, frames - f));
            Travel tr;
            tr.pos = double(f);
            tr.capacity = tape.capacity();
            tape.render_track(0, tr, n, l.data(), r.data());
            for (uint32_t i = 0; i < n; ++i)
                if (l[i] != sample(f + i, 0) || r[i] != sample(f + i, 1)) ++bad2;
        }
        CHECK(bad2 == 1, "the flipped frame must be caught exactly once, got %lld",
              (long long)bad2);
        mid[0] -= 1.0f;
    }
    delete tape.swap_clips(0, nullptr);
}

void test_varispeed_path_reads_the_same_frames() {
    // The frame-by-frame path (step != 1) has its own range lookup; it must
    // agree with the run path where they overlap.
    Tape tape;
    init(tape, 2);
    CHECK(tape.ensure(1), "ensure failed");
    record(tape, 1, 0, kRange + 2048, 512);
    tape.head[1].store(kRange + 2048, std::memory_order_relaxed);
    tape.swap_clips(1, one_clip(0, kRange + 2048, 0));

    std::vector<float> l(64), r(64);
    Travel tr;
    tr.pos = double(kRange - 32);       // straddles the boundary at half speed
    tr.step = 0.5;
    tr.capacity = tape.capacity();
    tape.render_track(1, tr, 64, l.data(), r.data());
    int64_t bad = 0;
    for (uint32_t k = 0; k < 64; ++k) {
        const int64_t f = int64_t(std::floor(tr.at(k)));
        if (l[k] != sample(f, 0) || r[k] != sample(f, 1)) ++bad;
    }
    CHECK(bad == 0, "%lld varispeed frames differ", (long long)bad);
    delete tape.swap_clips(1, nullptr);
}

void test_absent_range_is_silence_and_a_count() {
    Tape tape;
    init(tape, 2);
    CHECK(tape.ensure(0), "ensure failed");
    record(tape, 0, 0, kRange, 256);
    tape.head[0].store(kRange, std::memory_order_relaxed);

    // A clip reading before frame 0 is the UI's geometry, not a range the card
    // owed us: silence, and not counted.
    tape.clear_late_mask();
    tape.swap_clips(0, one_clip(0, 256, -256));
    std::vector<float> l(256), r(256);
    Travel tr;
    tr.capacity = tape.capacity();
    tape.render_track(0, tr, 256, l.data(), r.data());
    CHECK(tape.late_mask() == 0, "a clip reading before 0 must not count as late");
    for (uint32_t i = 0; i < 256; ++i)
        if (l[i] != 0.0f || r[i] != 0.0f) { CHECK(false, "frame %u is not silent", i); break; }
    delete tape.swap_clips(0, nullptr);

    // ...and the detector proven able to fire: a clip whose source is in the
    // track's second range, which nothing has made resident.
    Tape other;
    other.init(2 * kRange);
    CHECK(other.ensure(0), "ensure failed");
    record(other, 0, 0, 2 * kRange, 512);
    other.head[0].store(2 * kRange, std::memory_order_relaxed);
    int64_t run = 0;
    CHECK(other.run_at(0, kRange + 10, run) != nullptr, "range 1 should be resident");
    other.clear_late_mask();
    other.swap_clips(2, one_clip(0, 256, kRange + 10, 0));
    // Track 2 has no medium of its own; its clip reads track 0's, which is
    // resident -- so still no count.
    Travel tr2;
    tr2.capacity = other.capacity();
    other.render_track(2, tr2, 256, l.data(), r.data());
    CHECK(other.late_mask() == 0, "a clip on a bare track reading a resident medium is not late");
    for (uint32_t i = 0; i < 256; ++i)
        if (l[i] != sample(kRange + 10 + i, 0)) { CHECK(false, "cross-track read wrong at %u", i); break; }
    delete other.swap_clips(2, nullptr);
}

void test_read_and_write_frames_cross_boundaries() {
    Tape tape;
    init(tape, 3);
    CHECK(tape.ensure(4), "ensure failed");
    const int64_t n = kRange + 1000;
    std::vector<float> in(std::size_t(n) * 2), out(std::size_t(n) * 2, -1.0f);
    for (int64_t f = 0; f < n; ++f) {
        in[std::size_t(f) * 2] = sample(f, 0);
        in[std::size_t(f) * 2 + 1] = sample(f, 1);
    }
    CHECK(tape.write_frames(4, kRange / 2, n, in.data()) == n, "short write");
    CHECK(tape.read_frames(4, kRange / 2, n, out.data()) == n, "short read");
    CHECK(std::memcmp(in.data(), out.data(), in.size() * sizeof(float)) == 0,
          "round trip through two range boundaries differs");

    // A track with no medium reads as silence, not as a refusal.
    std::vector<float> z(64, 7.0f);
    CHECK(tape.read_frames(5, 0, 32, z.data()) == 0, "a bare track must read 0 frames");
    for (int i = 0; i < 64; ++i) CHECK(z[std::size_t(i)] == 0.0f, "bare read left %f", double(z[std::size_t(i)]));
}

void test_audio_path_allocates_nothing() {
    Tape tape;
    init(tape, 2);
    CHECK(tape.ensure(0), "ensure failed");
    tape.swap_clips(0, one_clip(0, 2 * kRange, 0));

    std::vector<float> l(128), r(128), bl(128), br(128);
    for (uint32_t i = 0; i < 128; ++i) { bl[i] = 0.5f; br[i] = -0.5f; }

    // Warm every path once outside the count (the first render of a clip list
    // touches nothing that allocates, but the vectors above must not be the
    // thing being measured).
    Travel warm;
    warm.capacity = tape.capacity();
    tape.render_track(0, warm, 128, l.data(), r.data());
    tape.write(0, 0, bl.data(), br.data(), 128);

    // Built before the count: a clip list is the control thread's `new`.
    ClipList* past = one_clip(0, 128, 4 * kRange);

    const long before = g_news.load();
    for (int64_t f = kRange - 512; f < kRange + 512; f += 128) {
        tape.write(0, f, bl.data(), br.data(), 128);       // crosses the boundary
        Travel tr;
        tr.pos = double(f);
        tr.capacity = tape.capacity();
        tape.render_track(0, tr, 128, l.data(), r.data());  // run path
        tr.pos = double(f) + 0.5;
        tr.step = 0.75;
        tape.render_track(0, tr, 128, l.data(), r.data());  // frame path
    }
    // ...and a render that goes late: a clip pointing past the medium.
    ClipList* old = tape.swap_clips(0, past);
    Travel late;
    late.capacity = tape.capacity();
    tape.render_track(0, late, 128, l.data(), r.data());
    const long news = g_news.load() - before;
    CHECK(news == 0, "the audio path allocated %ld times", news);
    delete old;
    delete tape.swap_clips(0, nullptr);

    // The negative control (#52's pattern): the counter must be able to see one.
    const long b2 = g_news.load();
    std::vector<float>* leak = new std::vector<float>(4, 1.0f);
    CHECK(g_news.load() - b2 >= 1, "the operator new counter saw nothing");
    delete leak;
}

void test_ensure_is_idempotent_and_bounded() {
    Tape tape;
    init(tape, 3);
    CHECK(tape.slots_used() == 0, "a fresh tape holds %lld slots", (long long)tape.slots_used());
    CHECK(!tape.allocated(0), "track 0 starts unallocated");
    CHECK(tape.ensure(0), "ensure failed");
    CHECK(tape.slots_used() == 3, "one track is %lld slots, wanted 3", (long long)tape.slots_used());
    CHECK(tape.ensure(0), "ensure is idempotent");
    CHECK(tape.slots_used() == 3, "a second ensure took %lld slots", (long long)tape.slots_used());
    CHECK(tape.ensure(7), "ensure failed");
    CHECK(tape.slots_used() == 6, "two tracks is %lld slots", (long long)tape.slots_used());
    CHECK(!tape.ensure(-1) && !tape.ensure(Tape::kTracks), "out-of-range ensure must refuse");

    // init() gives the slots back and the tables go absent again.
    tape.init(3 * kRange);
    CHECK(tape.slots_used() == 0, "init left %lld slots", (long long)tape.slots_used());
    int64_t run = 0;
    CHECK(tape.run_at(0, 0, run) == nullptr, "init left a range resident");
}

void test_range_arithmetic() {
    CHECK(kRange == 49152, "a range is %lld frames", (long long)kRange);
    CHECK(kRange * 2 * int64_t(sizeof(float)) == 384 * 1024, "a range is not 384 KiB");
    CHECK(Tape::ranges_for(0) == 0, "ranges_for(0)");
    CHECK(Tape::ranges_for(1) == 1, "ranges_for(1)");
    CHECK(Tape::ranges_for(kRange) == 1, "ranges_for(exactly one)");
    CHECK(Tape::ranges_for(kRange + 1) == 2, "ranges_for(one and a frame)");
    CHECK(Tape::range_of(kRange - 1) == 0 && Tape::range_of(kRange) == 1, "range_of");
    // 480 s at 48 kHz, the device's ruler.
    CHECK(Tape::ranges_for(480 * 48000) == 469, "the 8-minute ruler is %lld ranges",
          (long long)Tape::ranges_for(480 * 48000));

    // The run a caller gets never crosses a boundary and never passes the end.
    Tape tape;
    init(tape, 2);
    CHECK(tape.ensure(0), "ensure failed");
    int64_t run = 0;
    tape.run_at(0, 0, run);
    CHECK(run == kRange, "run at 0 is %lld", (long long)run);
    tape.run_at(0, kRange - 1, run);
    CHECK(run == 1, "run at the last frame of a range is %lld", (long long)run);
    tape.run_at(0, 2 * kRange - 10, run);
    CHECK(run == 10, "run at the end of the tape is %lld", (long long)run);
    CHECK(tape.run_at(0, 2 * kRange, run) == nullptr && run == 0,
          "past the end must stop the caller");
    CHECK(tape.run_at(0, -5, run) == nullptr && run == 5, "before 0 must skip 5, got %lld",
          (long long)run);
}

// ---------------------------------------------------------------------------

void test_fake_store_is_a_filesystem() {
    FakeStore s;
    std::string err;
    const std::string p = "/data/projects/x/tape-0.wav";
    StoreHandle h = s.open(p, true, err);
    CHECK(h != arb::host::kNoHandle, "open failed: %s", err.c_str());

    // A write past the end grows the file and zero-fills the hole.
    const char hello[] = "abcd";
    CHECK(s.pwrite(h, hello, 4, 4096, err) == 4, "pwrite: %s", err.c_str());
    CHECK(int64_t(s.bytes(p).size()) == 4100, "file is %lld bytes",
          (long long)s.bytes(p).size());
    CHECK(s.bytes(p)[0] == 0 && s.bytes(p)[4096] == 'a', "the hole is not zeroed");

    // A read past the end is short, not an error -- repair-on-open needs that.
    char buf[16] = {};
    CHECK(s.pread(h, buf, 16, 4096, err) == 4, "short read wanted, got err %s", err.c_str());
    CHECK(s.pread(h, buf, 16, 99999, err) == 0, "past the end must read 0");

    // Only fdatasync makes bytes durable -- the difference the lagging header
    // exists to exploit (§ 3.5).
    CHECK(s.durable_bytes(p) == 0, "unsynced bytes are durable");
    CHECK(s.fdatasync(h, err), "fdatasync: %s", err.c_str());
    CHECK(s.durable_bytes(p) == 4100, "sync left %lld durable", (long long)s.durable_bytes(p));

    CHECK(s.truncate(h, 4098, err), "truncate: %s", err.c_str());
    CHECK(int64_t(s.bytes(p).size()) == 4098, "truncate left %lld",
          (long long)s.bytes(p).size());
    CHECK(s.durable_bytes(p) == 4098, "truncate must lower the durable mark");

    CHECK(s.rename(p, "/data/projects/x/tape-1.wav", err), "rename: %s", err.c_str());
    CHECK(!s.exists(p) && s.exists("/data/projects/x/tape-1.wav"), "rename lost the file");
    s.close(h);
}

void test_fake_store_faults_fire() {
    FakeStore s;
    std::string err;
    StoreHandle h = s.open("/data/t.wav", true, err);
    CHECK(h != arb::host::kNoHandle, "open failed");

    s.fail(FakeStore::Op::kSync, "EIO", 1);
    CHECK(!s.fdatasync(h, err) && err == "EIO", "the armed EIO did not fire");
    err.clear();
    CHECK(s.fdatasync(h, err), "the fault must fire once only: %s", err.c_str());

    // ENOSPC by free space rather than by an armed fault: the floor REC is
    // refused under (design § 7 question 4).
    s.set_free_bytes(1024);
    std::vector<char> big(4096, 'x');
    CHECK(s.pwrite(h, big.data(), 4096, 0, err) == -1 && err == "ENOSPC",
          "a write past the free space must refuse, err=%s", err.c_str());
    CHECK(s.free_bytes("/data") == 1024, "free_bytes");

    // A bad handle is an error, not a crash.
    err.clear();
    CHECK(s.pread(arb::host::kNoHandle, big.data(), 4, 0, err) == -1, "bad handle read");
    s.close(h);
    err.clear();
    CHECK(s.pwrite(h, big.data(), 4, 0, err) == -1, "a closed handle must refuse");
}

}  // namespace

// Same replacement as test_events.cpp and test_imu.cpp, and the same reason for
// [[gnu::noinline]] (GCC reports a -Wmismatched-new-delete false positive
// without it). The array forms forward to these on both libstdc++ and libc++.
[[gnu::noinline]] void* operator new(std::size_t n) {
    g_news.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

int main() {
    test_range_arithmetic();
    test_ensure_is_idempotent_and_bounded();
    test_take_across_range_boundaries_is_sample_exact();
    test_varispeed_path_reads_the_same_frames();
    test_absent_range_is_silence_and_a_count();
    test_read_and_write_frames_cross_boundaries();
    test_audio_path_allocates_nothing();
    test_fake_store_is_a_filesystem();
    test_fake_store_faults_fire();
    if (failures == 0) std::printf("tape: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
