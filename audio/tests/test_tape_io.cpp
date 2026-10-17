// host/tape_io.{hpp,cpp} — the write-behind (C1 step 2a,
// tape-streaming-design.md § 3.3, § 3.5, § 6.2). No framework; test_tape.cpp is
// the pattern, `FakeStore` is the card.
//
// What these prove, and their negative controls (the standing rule since #56 —
// a test that has never been seen to fail is not evidence):
//
//   * the header is one our own reader parses, at the 4 KiB offset the
//     alignment needs — and `header_frames` is shown able to refuse one that
//     is not ours
//   * a take of several ranges reaches the card byte for byte, and the file it
//     leaves parses as a WAV of exactly the take — the compare shown able to
//     fail on one flipped float
//   * **the lag is what survives a cut** (T8): with the header one range behind
//     the data, a file made of *this* commit's header page and the *previous*
//     commit's data never claims audio that is not there — and with the lag
//     switched off (`set_no_lag`) that same check fails, which is the whole
//     reason the lag exists
//   * the audio thread's half — `Tape::write` plus the ring push — makes no
//     `operator new` calls across a take that crosses range boundaries, and
//     the counter is shown able to see one
//   * a full ring is counted and returns false rather than blocking
//   * **the lead** (§ 3.4, #158): every commit has blocks allocated
//     `kAllocLeadRanges` ranges past the range it writes, and the lead never
//     moves the file's **length**, which is the number repair on open reads --
//     with `--no-tape-prealloc` as the negative control (no `allocate` call at
//     all, and the same bytes on the card), and a filesystem that refuses to
//     preallocate shown to cost one count and no commit
//   * `PosixStore` is a filesystem: the same take through it lands in a real
//     file that `arb::io::read` opens and finds sample-exact
//   * **repair on open** (T6, step 3): a medium whose length and header
//     disagree is made to agree before anything appends -- the head comes off
//     the header, a file longer than its claim loses its tail, one shorter is
//     clamped to whole frames of what is there, one with no header of ours is
//     truncated to none, and a WAV that is somebody else's is **refused and
//     left byte for byte alone**. Each with its negative control, and the
//     whole thing once through `PosixStore` on a real file that was cut
//     mid-take, reopened, and played back sample-exact up to the claim
//   * **a compaction does not hold the tape's lock across the card** (#167):
//     with the fake card's latch holding the copy inside its third range's
//     write (#542; it was a per-call delay and a wall-clock limit), a cancel
//     issued there stops the copy there and `compact_state()` -- the call
//     `poll()` makes every turn -- answers while the card still has it. Its
//     negative control is `set_compact_holds_lock`, the lock discipline as it
//     stood until #167: the same two assertions are required to fail, and they
//     do (the copy runs the whole slice and `compact_state()` answers only
//     after it). That switch is why this one is evidence and
//     `test_compact_cancelled_live` was not: a fake card answering in
//     microseconds cannot show a lock held across a slow one
//   * **a take's commit does not hold it across its `fdatasync`** (#637): the
//     same latch, on the sync (`park_sync`), and the control thread's
//     `compact_state`, `medium_frames` and `set_clips` answer while the commit
//     is parked; `set_commit_holds_lock` is the negative control and is
//     required to fail
//   * **nor across the take's peaks sidecar** (#826): the latch on the flush
//     commit's sidecar write, the same probe, the same negative control
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <chrono>
#include <new>
#include <string>
#include <thread>
#include <vector>

#include <sys/stat.h>
#include <unistd.h>   // getpid; glibc's <thread> brings it in, libc++'s does not

#include <audiofile.hpp>

#include <tape.hpp>
#include <tape_io.hpp>
#include <tape_store.hpp>

// `fsync`, interposed (#1131): this executable's own definition wins the link
// over libc's, for `tape_store.cpp` compiled into it as for this file. A
// folder's `fsync` is counted and remembered by device and inode, so a
// `sync_dir_of` that never reaches the card is seen. Nothing here needs the
// data on the platter, so no call goes through; a bad descriptor still fails.
namespace {
std::atomic<long> g_dir_fsyncs{0};
dev_t g_dir_fsync_dev = 0;
ino_t g_dir_fsync_ino = 0;
}

extern "C" int fsync(int fd) {
    struct stat st{};
    if (::fstat(fd, &st) != 0) return -1;   // errno is fstat's EBADF
    if (S_ISDIR(st.st_mode)) {
        g_dir_fsync_dev = st.st_dev;
        g_dir_fsync_ino = st.st_ino;
        ++g_dir_fsyncs;
    }
    return 0;
}

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

using arb::host::FakeStore;
using arb::host::kNoHandle;
using arb::host::StoreHandle;
using arb::host::PosixStore;
using arb::host::RangeCommit;
using arb::host::Tape;
using arb::host::TapeIo;

constexpr int64_t kRange = Tape::kRangeFrames;
constexpr int64_t kHeader = TapeIo::kHeaderBytes;

float sample(int64_t f, int ch) {
    return std::sin(double(f) * 0.001) * (ch ? -0.5f : 1.0f) + float(ch) * 0.25f;
}

// The audio thread's half of a take: write a block into the medium, then hand
// over every range that filled. Exactly what `AudioHost::queue_ranges` does.
struct Recorder {
    Tape& tape;
    TapeIo& io;
    int track;
    int64_t at = 0;
    int64_t next_range = 0;

    void block(uint32_t n) {
        std::vector<float> l(n), r(n);
        for (uint32_t i = 0; i < n; ++i) {
            l[i] = sample(at + i, 0);
            r[i] = sample(at + i, 1);
        }
        tape.write(track, at, l.data(), r.data(), n);
        at += n;
        queue();
    }
    void queue() {
        const int64_t complete = Tape::range_of(at);
        while (next_range < complete) {
            RangeCommit c;
            c.track = track;
            c.range = next_range;
            c.frames = kRange;
            if (!io.enqueue(c)) break;
            ++next_range;
        }
    }
    void end() {
        queue();
        RangeCommit c;
        c.track = track;
        c.flush = 1;
        c.range = Tape::range_of(at);
        c.frames = at - c.range * kRange;
        io.enqueue(c);
    }
};

// ---- the header -------------------------------------------------------------

void test_header() {
    std::vector<uint8_t> h(std::size_t(kHeader), 0xAB);
    TapeIo::write_header(h.data(), 48000, 12345);
    CHECK(TapeIo::header_frames(h.data(), kHeader) == 12345, "claim %lld",
          (long long)TapeIo::header_frames(h.data(), kHeader));
    CHECK(!std::memcmp(h.data(), "RIFF", 4), "no RIFF");
    CHECK(!std::memcmp(h.data() + 8, "WAVE", 4), "no WAVE");
    // The `data` payload must begin at exactly 4096 or a range is no longer a
    // whole number of pages (§ 3.1) -- so the chunk header sits at 4088.
    CHECK(!std::memcmp(h.data() + kHeader - 8, "data", 4), "data is not at 4088");

    // Our own reader finds it, skipping `fact` and `JUNK` as every RIFF reader
    // does, and reads the frames the header claims.
    std::vector<uint8_t> file(h);
    const int64_t frames = 3;
    for (int64_t f = 0; f < frames; ++f)
        for (int ch = 0; ch < 2; ++ch) {
            const float v = sample(f, ch);
            uint8_t b[4];
            std::memcpy(b, &v, 4);
            file.insert(file.end(), b, b + 4);
        }
    TapeIo::write_header(file.data(), 48000, frames);
    arb::io::Audio a;
    const std::string e = arb::io::read_wav(file, a);
    CHECK(e.empty(), "read_wav: %s", e.c_str());
    CHECK(a.sample_rate == 48000, "sr %u", a.sample_rate);
    CHECK(a.ch.size() == 2 && a.frames() == std::size_t(frames), "frames %zu", std::size_t(a.frames()));
    if (a.ch.size() == 2 && a.frames() == std::size_t(frames))
        for (int64_t f = 0; f < frames; ++f)
            for (int ch = 0; ch < 2; ++ch)
                CHECK(a.ch[std::size_t(ch)][std::size_t(f)] == sample(f, ch), "frame %lld",
                      (long long)f);

    // Negative control: the detector proven able to refuse. A header that is
    // not ours must not be read as a frame count of zero, which would look
    // like an empty medium rather than a file to leave alone.
    std::vector<uint8_t> junk(std::size_t(kHeader), 0);
    CHECK(TapeIo::header_frames(junk.data(), kHeader) == -1, "junk header accepted");
    std::vector<uint8_t> shortish(16, 0);
    CHECK(TapeIo::header_frames(shortish.data(), 16) == -1, "short header accepted");
}

// ---- a take reaches the card ------------------------------------------------

void test_take_lands() {
    Tape tape;
    tape.init(8 * kRange);
    CHECK(tape.ensure(0), "ensure");
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    CHECK(io.on(), "configure");

    Recorder rec{tape, io, 0};
    // Two and a half ranges, in the audio thread's own blocks.
    const int64_t total = kRange * 2 + 1000;
    while (rec.at < total) rec.block(uint32_t(std::min<int64_t>(128, total - rec.at)));
    rec.end();
    io.flush();                                   // offline: drains on this thread

    const TapeIo::Stats st = io.stats();
    CHECK(st.queued == 3, "queued %lld", (long long)st.queued);   // 2 full + the tail
    CHECK(st.committed == 3, "committed %lld", (long long)st.committed);
    CHECK(st.errors == 0 && st.dropped == 0 && st.absent == 0, "errors %lld dropped %lld absent %lld",
          (long long)st.errors, (long long)st.dropped, (long long)st.absent);

    const std::vector<uint8_t>& bytes = store.bytes("/p/tape-1.wav");
    CHECK(int64_t(bytes.size()) == kHeader + total * TapeIo::kFrameBytes, "size %zu", bytes.size());
    CHECK(TapeIo::header_frames(bytes.data(), int64_t(bytes.size())) == total, "claim");

    arb::io::Audio a;
    const std::string e = arb::io::read_wav(bytes, a);
    CHECK(e.empty(), "read_wav: %s", e.c_str());
    CHECK(a.frames() == std::size_t(total), "frames %zu", std::size_t(a.frames()));
    int wrong = 0;
    if (a.ch.size() == 2 && a.frames() == std::size_t(total))
        for (int64_t f = 0; f < total; ++f)
            for (int ch = 0; ch < 2; ++ch)
                if (a.ch[std::size_t(ch)][std::size_t(f)] != sample(f, ch)) ++wrong;
    CHECK(wrong == 0, "%d frames differ", wrong);

    // Negative control: the compare shown able to fail. One float flipped in
    // the middle range -- the one nothing but the boundary arithmetic puts
    // there -- must be caught.
    std::vector<uint8_t> tampered(bytes);
    const std::size_t at = std::size_t(kHeader + (kRange + 7) * TapeIo::kFrameBytes);
    const float bad = 0.5f;
    std::memcpy(tampered.data() + at, &bad, 4);
    arb::io::Audio b;
    CHECK(arb::io::read_wav(tampered, b).empty(), "read_wav (tampered)");
    int caught = 0;
    if (b.ch.size() == 2 && b.frames() == std::size_t(total))
        for (int64_t f = 0; f < total; ++f)
            for (int ch = 0; ch < 2; ++ch)
                if (b.ch[std::size_t(ch)][std::size_t(f)] != sample(f, ch)) ++caught;
    CHECK(caught == 1, "the flipped float was caught %d times", caught);
}

// ---- T8: the lag is what survives a cut -------------------------------------

// Records `ranges` whole ranges one commit at a time and, after each, builds
// the file a power cut could leave: **this** commit's header page over the
// **previous** commit's data, which is the one reordering `fdatasync` permits
// (§ 3.5). Returns how many of those survivors claimed more audio than they
// carried.
int cut_survivors_claiming_too_much(bool no_lag) {
    Tape tape;
    tape.init(8 * kRange);
    tape.ensure(0);
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    io.set_no_lag(no_lag);

    Recorder rec{tape, io, 0};
    std::vector<uint8_t> previous;                 // the file as of the last commit
    int bad = 0;
    for (int64_t k = 0; k < 4; ++k) {
        while (rec.at < (k + 1) * kRange) rec.block(1024);
        io.flush();                                // exactly one commit happened
        const std::vector<uint8_t>& now = store.bytes("/p/tape-1.wav");
        if (!previous.empty()) {
            std::vector<uint8_t> survivor(previous);
            std::memcpy(survivor.data(), now.data(), std::size_t(kHeader));
            const int64_t claim = TapeIo::header_frames(survivor.data(), int64_t(survivor.size()));
            const int64_t carried =
                (int64_t(survivor.size()) - kHeader) / TapeIo::kFrameBytes;
            if (claim > carried) ++bad;
        }
        previous = now;
    }
    return bad;
}

void test_header_lag() {
    CHECK(cut_survivors_claiming_too_much(false) == 0,
          "a cut left a header claiming audio that is not on the card");
    // The negative control the design names: with the header written in the
    // same commit as the data it claims, a cut between the two pages leaves a
    // file that claims a range it never got. That failure is what the lag buys.
    const int bad = cut_survivors_claiming_too_much(true);
    CHECK(bad == 3, "no-lag left %d over-claiming survivors, expected 3", bad);
}

// ---- T7: the audio thread's half allocates nothing ---------------------------

void test_no_allocation() {
    Tape tape;
    tape.init(8 * kRange);
    tape.ensure(0);
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);

    // Everything that could allocate happens before the count starts.
    std::vector<float> l(128), r(128);
    Recorder rec{tape, io, 0};
    rec.block(128);
    io.flush();

    const long before = g_news.load();
    for (int64_t f = rec.at; f < kRange * 2 + 500; f += 128) {
        const uint32_t n = 128;
        for (uint32_t i = 0; i < n; ++i) {
            l[i] = sample(f + i, 0);
            r[i] = sample(f + i, 1);
        }
        tape.write(0, f, l.data(), r.data(), n);
        // The ring push, which is all the audio thread does about storage.
        const int64_t complete = Tape::range_of(f + n);
        while (rec.next_range < complete) {
            RangeCommit c;
            c.track = 0;
            c.range = rec.next_range;
            c.frames = kRange;
            if (!io.enqueue(c)) break;
            ++rec.next_range;
        }
    }
    const long after = g_news.load();
    CHECK(after == before, "%ld allocations on the audio path", after - before);

    // Negative control: the counter shown able to see one.
    const long b2 = g_news.load();
    // `::operator new` called by name: clang may elide a new-expression
    // paired with its delete, and then the control sees nothing.
    { void* p = ::operator new(sizeof(float)); ::operator delete(p); }
    CHECK(g_news.load() > b2, "the operator new counter is not counting");
}

// ---- a full ring is counted, not waited on ----------------------------------

void test_ring_full() {
    Tape tape;
    tape.init(4 * kRange);
    tape.ensure(0);
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);

    RangeCommit c;
    c.track = 0;
    c.frames = 0;                                  // header-only commits: cheap
    int pushed = 0;
    for (std::size_t i = 0; i < TapeIo::kRingSlots + 8; ++i) {
        c.range = int64_t(i % 4);
        if (io.enqueue(c)) ++pushed;
    }
    CHECK(pushed == int(TapeIo::kRingSlots) - 1, "pushed %d of %zu", pushed, TapeIo::kRingSlots);
    CHECK(io.stats().dropped == int64_t(TapeIo::kRingSlots + 8) - pushed, "dropped %lld",
          (long long)io.stats().dropped);
    io.flush();
    CHECK(io.stats().committed == pushed, "committed %lld", (long long)io.stats().committed);
}

// ---- the lead ahead of the write head (§ 3.4, #158) -------------------------

// Records `ranges` whole ranges and returns what the store saw.
struct LeadRun {
    int64_t allocs = 0;
    int64_t allocated_to = 0;
    int64_t length = 0;
    int64_t committed = 0;
    int64_t errors = 0;
    int64_t stat_allocs = 0;
    int64_t stat_refused = 0;
    std::vector<uint8_t> bytes;
};

LeadRun lead_run(bool prealloc, bool refuse_alloc, int64_t ranges) {
    Tape tape;
    tape.init((ranges + 2) * kRange);
    tape.ensure(0);
    FakeStore store;
    if (refuse_alloc) store.fail(FakeStore::Op::kAllocate, "EOPNOTSUPP", 8);
    TapeIo io;
    io.set_prealloc(prealloc);
    io.configure(&tape, &store, "/p", 48000);

    Recorder rec{tape, io, 0};
    const int64_t total = kRange * ranges;
    while (rec.at < total) rec.block(uint32_t(std::min<int64_t>(512, total - rec.at)));
    rec.end();
    io.flush();

    const TapeIo::Stats st = io.stats();
    LeadRun r;
    r.allocs = store.allocs("/p/tape-1.wav");
    r.allocated_to = store.allocated_to("/p/tape-1.wav");
    r.length = int64_t(store.bytes("/p/tape-1.wav").size());
    r.committed = st.committed;
    r.errors = st.errors;
    r.stat_allocs = st.allocs;
    r.stat_refused = st.alloc_refused;
    r.bytes = store.bytes("/p/tape-1.wav");
    return r;
}

void test_prealloc_lead() {
    const int64_t ranges = 6;
    const LeadRun on = lead_run(true, false, ranges);

    // Every commit that moved the head asked for blocks, and the last ask
    // reached `kAllocLeadRanges` past the last range written -- which is the
    // whole claim: the `pwrite` lands in space that already exists.
    CHECK(on.allocs == ranges, "allocate calls %lld for %lld ranges",
          (long long)on.allocs, (long long)ranges);
    CHECK(on.stat_allocs == on.allocs, "stats.allocs %lld vs store %lld",
          (long long)on.stat_allocs, (long long)on.allocs);
    CHECK(on.allocated_to ==
              kHeader + (ranges + TapeIo::kAllocLeadRanges) * TapeIo::kRangeBytes,
          "allocated to %lld", (long long)on.allocated_to);
    CHECK(on.errors == 0 && on.committed == ranges + 1, "errors %lld committed %lld",
          (long long)on.errors, (long long)on.committed);

    // **The length did not move.** `FALLOC_FL_KEEP_SIZE` is what keeps repair
    // on open (§ 3.5) from reading the lead as audio a cut left behind, so the
    // file is exactly the take -- byte for byte what the same take writes with
    // the lead switched off.
    const LeadRun off = lead_run(false, false, ranges);
    CHECK(on.length == kHeader + ranges * kRange * TapeIo::kFrameBytes,
          "length %lld", (long long)on.length);
    CHECK(on.length == off.length, "lead moved the length: %lld vs %lld",
          (long long)on.length, (long long)off.length);
    CHECK(on.bytes == off.bytes, "the lead changed the medium's bytes");

    // Negative control: with `--no-tape-prealloc` the store is never asked,
    // which is what says the assertions above are about the lead and not about
    // something the commit did anyway.
    CHECK(off.allocs == 0, "prealloc off still allocated %lld times",
          (long long)off.allocs);
    CHECK(off.stat_allocs == 0 && off.stat_refused == 0, "prealloc off counted %lld/%lld",
          (long long)off.stat_allocs, (long long)off.stat_refused);

    // A filesystem that cannot preallocate (exFAT may be one -- step 4 finds
    // out): the commits all land, and it is counted **once** rather than once
    // a range, because asking a card that has said no is a syscall a second
    // for nothing.
    const LeadRun refused = lead_run(true, true, ranges);
    CHECK(refused.stat_refused == 1, "refusals counted %lld", (long long)refused.stat_refused);
    CHECK(refused.stat_allocs == 0, "a refused allocate was counted as one %lld",
          (long long)refused.stat_allocs);
    CHECK(refused.errors == 0 && refused.committed == ranges + 1,
          "a refused lead broke the commit: errors %lld committed %lld",
          (long long)refused.errors, (long long)refused.committed);
    CHECK(refused.bytes == off.bytes, "a refused lead changed the medium");
}

// ---- the same take through PosixStore ---------------------------------------

void test_posix_store() {
    namespace fs = std::filesystem;
    // #105: a gate run leaves /tmp as it found it.
    const fs::path dir = fs::temp_directory_path() /
                         ("arbutus-tapeio-" + std::to_string(::getpid()));
    std::error_code ec;
    fs::remove_all(dir, ec);
    fs::create_directories(dir, ec);
    CHECK(!ec, "mkdir: %s", ec.message().c_str());

    {
        Tape tape;
        tape.init(4 * kRange);
        tape.ensure(2);
        PosixStore store;
        TapeIo io;
        io.configure(&tape, &store, dir.string(), 48000);
        CHECK(store.free_bytes(dir.string()) > 0, "free_bytes");

        Recorder rec{tape, io, 2};                 // track 2 -> tape-3.wav
        const int64_t total = kRange + 777;
        while (rec.at < total) rec.block(uint32_t(std::min<int64_t>(256, total - rec.at)));
        rec.end();
        io.flush();
        CHECK(io.stats().errors == 0 && io.stats().absent == 0, "errors %lld absent %lld (%s)",
              (long long)io.stats().errors, (long long)io.stats().absent,
              io.last_error().c_str());

        arb::io::Audio a;
        const std::string e = arb::io::read((dir / "tape-3.wav").string(), a);
        CHECK(e.empty(), "read: %s", e.c_str());
        CHECK(a.frames() == std::size_t(total), "frames %zu", std::size_t(a.frames()));
        int wrong = 0;
        if (a.ch.size() == 2 && a.frames() == std::size_t(total))
            for (int64_t f = 0; f < total; ++f)
                for (int ch = 0; ch < 2; ++ch)
                    if (a.ch[std::size_t(ch)][std::size_t(f)] != sample(f, ch)) ++wrong;
        CHECK(wrong == 0, "%d frames differ through PosixStore", wrong);

        // The lead through a real filesystem (#158): either `fallocate` worked
        // -- and the file is still exactly the take, which the `frames` check
        // above has just said -- or the platform said it cannot, once. There
        // is no third answer, and `FakeStore` cannot tell us which.
        const TapeIo::Stats st = io.stats();
        CHECK((st.allocs > 0) != (st.alloc_refused > 0),
              "prealloc through PosixStore: %lld calls, %lld refused (%s)",
              (long long)st.allocs, (long long)st.alloc_refused, io.last_error().c_str());
        CHECK(st.alloc_refused <= 1, "a refusal per range: %lld",
              (long long)st.alloc_refused);
    }

    fs::remove_all(dir, ec);
    CHECK(!fs::exists(dir), "the test left %s behind", dir.string().c_str());
}

// ---- the live thread ---------------------------------------------------------

void test_live_thread() {
    Tape tape;
    tape.init(8 * kRange);
    tape.ensure(1);
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    io.start();                                    // the real thread, not service()

    Recorder rec{tape, io, 1};
    while (rec.at < kRange * 2 + 64) rec.block(128);
    const int64_t total = rec.at;                  // whole blocks, so a little past
    rec.end();
    io.flush();                                    // waits on the thread's counter

    const std::vector<uint8_t>& bytes = store.bytes("/p/tape-2.wav");
    CHECK(TapeIo::header_frames(bytes.data(), int64_t(bytes.size())) == total,
          "claim %lld of %lld", (long long)TapeIo::header_frames(bytes.data(), int64_t(bytes.size())),
          (long long)total);
    CHECK(store.durable_bytes("/p/tape-2.wav") == int64_t(bytes.size()), "not all durable");
    io.stop();
    CHECK(io.stats().errors == 0, "errors %lld", (long long)io.stats().errors);
}

// ---- a commit does not hold the tape's lock across its sync (#637) ----------
//
// The I/O thread held `mu_` from the drain through the commit's `fdatasync`,
// and the control thread takes the same lock in `set_clips`, `medium_frames`
// and `compact_state`: a microSD sync of 10-140+ ms then held up every
// client's notes. #167's test is the pattern: the fake card parks the take's
// first commit inside its `fdatasync` (`FakeStore::park_sync`), and while it is
// parked a thread of the test's own asks the two questions the control thread
// asks. The fixed code answers with the commit still in the card; the
// negative control (`set_commit_holds_lock(true)`, the lock as it was held
// until #637) cannot answer before the release at all, whatever the
// scheduler does, so the wait below is a bound and not the measure.
struct SyncCell {
    bool parked = false;       // the commit reached the latch at all
    bool answered = false;     // the control-thread calls came back while parked
    int64_t claim = -1;        // the header's frames after the take
    int64_t total = 0;
};

SyncCell sync_cell(bool holds_lock) {
    Tape tape;
    tape.init(8 * kRange);
    tape.ensure(1);
    FakeStore store;
    TapeIo io;
    io.set_commit_holds_lock(holds_lock);
    io.configure(&tape, &store, "/p", 48000);
    io.start();
    store.park_sync(1);            // an empty medium's repair syncs nothing: this is the commit's

    SyncCell c;
    Recorder rec{tape, io, 1};
    while (rec.at < kRange + 64) rec.block(128);  // one range finished, its commit queued
    c.parked = store.wait_parked(std::chrono::seconds(10));

    std::atomic<bool> done{false};
    std::thread probe([&] {
        (void)io.compact_state();
        (void)io.medium_frames(1);
        io.set_clips(1, std::vector<arb::host::Clip>{});
        done.store(true);
    });
    for (int i = 0; i < 300 && !done.load(); ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    c.answered = done.load();
    store.release_write();
    probe.join();

    rec.end();
    io.flush();
    c.total = rec.at;
    io.stop();
    const std::vector<uint8_t>& bytes = store.bytes("/p/tape-2.wav");
    c.claim = TapeIo::header_frames(bytes.data(), int64_t(bytes.size()));
    return c;
}

void test_commit_sync_does_not_hold_the_lock() {
    const SyncCell fixed = sync_cell(false);
    CHECK(fixed.parked, "the commit never reached its fdatasync");
    CHECK(fixed.answered, "the control thread waited for the commit's fdatasync");
    CHECK(fixed.claim == fixed.total, "fixed arm: the take claims %lld of %lld",
          (long long)fixed.claim, (long long)fixed.total);

    const SyncCell held = sync_cell(true);
    CHECK(held.parked, "the before-arm never reached its fdatasync");
    CHECK(!held.answered,
          "#637's before switch did not reproduce the wait: the control thread answered "
          "with mu_ held across the sync");
    CHECK(held.claim == held.total, "before arm: the take claims %lld of %lld",
          (long long)held.claim, (long long)held.total);
}

// ---- nor across the take's peaks sidecar, right after STOP (#826) -----------
//
// The commit that ends a take wrote `tape-N.peaks` (an open, a truncate and a
// write) after taking `mu_` back, so the control thread waited on the card
// right after STOP. Same latch, on the flush commit's third write -- its audio,
// its header, then the sidecar -- and the same probe. The header written before
// the park already claims the whole take, which is what shows the parked write
// is the sidecar's. `set_commit_holds_lock(true)` holds the whole commit, the
// sidecar included, as the code before #826 held the sidecar: required to fail.
struct PeaksCell {
    bool parked = false;
    int64_t parked_at = -1;    // writes the flush commit made before the park
    int64_t claim_at_park = -1;
    bool answered = false;
    bool sidecar = false;      // the file exists once released
    int64_t total = 0;
};

PeaksCell peaks_cell(bool holds_lock) {
    Tape tape;
    tape.init(8 * kRange);
    tape.ensure(1);
    FakeStore store;
    TapeIo io;
    io.set_commit_holds_lock(holds_lock);
    io.configure(&tape, &store, "/p", 48000);
    io.start();

    PeaksCell c;
    Recorder rec{tape, io, 1};
    while (rec.at < kRange + 64) rec.block(128);
    io.flush();                    // range 0 on the card; only the take's end is left
    const int64_t w0 = store.writes();
    store.park_write(3);           // the flush commit's audio, header, then the sidecar
    rec.end();
    c.total = rec.at;
    c.parked = store.wait_parked(std::chrono::seconds(10));
    c.parked_at = store.writes() - w0;
    {
        const std::vector<uint8_t> bytes = store.bytes("/p/tape-2.wav");
        c.claim_at_park = TapeIo::header_frames(bytes.data(), int64_t(bytes.size()));
    }

    std::atomic<bool> done{false};
    std::thread probe([&] {
        (void)io.compact_state();
        (void)io.medium_frames(1);
        io.set_clips(1, std::vector<arb::host::Clip>{});
        done.store(true);
    });
    for (int i = 0; i < 300 && !done.load(); ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    c.answered = done.load();
    store.release_write();
    probe.join();
    io.flush();
    io.stop();
    c.sidecar = store.exists("/p/tape-2.peaks");
    return c;
}

void test_peaks_sidecar_does_not_hold_the_lock() {
    const PeaksCell fixed = peaks_cell(false);
    CHECK(fixed.parked && fixed.parked_at == 2,
          "the flush commit never parked in its third write: %lld", (long long)fixed.parked_at);
    CHECK(fixed.claim_at_park == fixed.total, "parked before the header: it claims %lld of %lld",
          (long long)fixed.claim_at_park, (long long)fixed.total);
    CHECK(fixed.answered, "the control thread waited for the take's peaks sidecar");
    CHECK(fixed.sidecar, "no tape-2.peaks after the take");

    const PeaksCell held = peaks_cell(true);
    CHECK(held.parked && held.parked_at == 2, "the before-arm never parked: %lld",
          (long long)held.parked_at);
    CHECK(!held.answered,
          "the before switch did not reproduce the wait: the control thread answered "
          "with mu_ held across the sidecar");
    CHECK(held.sidecar, "before arm: no tape-2.peaks after the take");
}

// ---- a refused write never leaves a header claiming it -----------------------

void test_write_refused() {
    Tape tape;
    tape.init(8 * kRange);
    tape.ensure(0);
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);

    Recorder rec{tape, io, 0};
    while (rec.at < kRange) rec.block(1024);       // range 0 commits cleanly
    io.flush();
    const int64_t claim0 = TapeIo::header_frames(store.bytes("/p/tape-1.wav").data(),
                                                 int64_t(store.bytes("/p/tape-1.wav").size()));

    store.fail(FakeStore::Op::kWrite, "ENOSPC");   // range 1's data
    while (rec.at < kRange * 2) rec.block(1024);
    io.flush();
    const std::vector<uint8_t>& bytes = store.bytes("/p/tape-1.wav");
    CHECK(io.stats().errors == 1, "errors %lld", (long long)io.stats().errors);
    // The header must not have moved: the commit returned before writing it.
    CHECK(TapeIo::header_frames(bytes.data(), int64_t(bytes.size())) == claim0,
          "the header moved on a refused write");
    CHECK(!io.last_error().empty(), "no error recorded");
}

// #591 finding 1: the take's end (`flush`) claims with no lag, and it claimed
// `max(end, durable_end)` -- so a take whose range 1 the card refused, and
// whose last commit then landed, ended with a header claiming range 1 too: a
// range of whatever the file held there, played back as the take. The end of
// a take claims the contiguous durable prefix and nothing past it. Two
// shapes: a take ending mid-range (the flush carries audio past the hole) and
// one ending exactly on a range boundary (the flush is empty).
int64_t claim_after_a_hole(int64_t tail) {
    Tape tape;
    tape.init(8 * kRange);
    tape.ensure(0);
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    Recorder rec{tape, io, 0};
    while (rec.at < kRange) rec.block(1024);       // range 0 commits cleanly
    io.flush();
    store.fail(FakeStore::Op::kWrite, "ENOSPC");   // range 1's data
    while (rec.at < 2 * kRange + tail) rec.block(uint32_t(std::min<int64_t>(1024, 2 * kRange + tail - rec.at)));
    rec.end();
    io.flush();
    CHECK(io.stats().errors == 1, "errors %lld", (long long)io.stats().errors);
    const std::vector<uint8_t>& bytes = store.bytes("/p/tape-1.wav");
    return TapeIo::header_frames(bytes.data(), int64_t(bytes.size()));
}

void test_take_end_claims_only_the_durable_prefix() {
    const int64_t partial = claim_after_a_hole(5000);
    CHECK(partial == kRange, "partial end: the header claims %lld past the refused range "
          "(durable prefix %lld)", (long long)partial, (long long)kRange);
    const int64_t exact = claim_after_a_hole(0);
    CHECK(exact == kRange, "boundary end: the header claims %lld past the refused range "
          "(durable prefix %lld)", (long long)exact, (long long)kRange);
}

// ---- repair on open (T6, design § 3.5) --------------------------------------

// Open a medium on `store` at `/p` and report what the repair made of it.
struct Reopened {
    bool opened = false;
    int64_t claim = 0;
    int64_t repaired = 0;
    int64_t lost = 0;
    int64_t refused = 0;
    std::string err;
};

Reopened reopen(FakeStore& store, int track) {
    Tape tape;
    tape.init(8 * kRange);
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    Reopened r;
    r.opened = io.open_all();
    r.claim = io.medium_frames(track);
    const TapeIo::Stats st = io.stats();
    r.repaired = st.repaired;
    r.lost = st.lost_frames;
    r.refused = st.refused;
    r.err = io.last_error();
    return r;
}

// The bytes a completed take of `frames` leaves on the card, through the real
// write path -- so the repair is tested against a file the host really writes
// rather than one this test assembled.
std::vector<uint8_t> take_bytes(int64_t frames) {
    Tape tape;
    tape.init(8 * kRange);
    tape.ensure(0);
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    Recorder rec{tape, io, 0};
    while (rec.at < frames) rec.block(uint32_t(std::min<int64_t>(1024, frames - rec.at)));
    rec.end();
    io.flush();
    return store.bytes("/p/tape-1.wav");
}

// Put `data` on a fresh store as track 0's medium and reopen it.
Reopened repair_of(const std::vector<uint8_t>& data) {
    FakeStore store;
    std::string err;
    const arb::host::StoreHandle h = store.open("/p/tape-1.wav", true, err);
    store.pwrite(h, data.data(), int64_t(data.size()), 0, err);
    store.close(h);
    return reopen(store, 0);
}

void test_repair_on_open() {
    const int64_t total = kRange * 2 + 5000;
    const std::vector<uint8_t> whole = take_bytes(total);
    CHECK(int64_t(whole.size()) == kHeader + total * 8, "take is %lld bytes",
          (long long)whole.size());

    // (a) An exact file is left exactly alone, and the head comes off the
    //     header. This is also the control for every case below: if the repair
    //     touched a file that did not need it, this is what would go red.
    {
        const Reopened r = repair_of(whole);
        CHECK(r.opened && r.claim == total, "exact: opened %d claim %lld (%s)", int(r.opened),
              (long long)r.claim, r.err.c_str());
        CHECK(r.repaired == 0 && r.lost == 0, "exact file was repaired (%lld, %lld lost)",
              (long long)r.repaired, (long long)r.lost);
    }

    // (b) Longer than its claim -- a cut mid-take, where the data page of the
    //     last commit landed and its header page did not. The tail is gone and
    //     the file is exact again, which is what stops the next take appending
    //     at a head that has somebody else's audio under it.
    {
        std::vector<uint8_t> cut = whole;
        TapeIo::write_header(cut.data(), 48000, total - kRange);   // the lagging header
        const Reopened r = repair_of(cut);
        CHECK(r.opened && r.claim == total - kRange, "long: claim %lld", (long long)r.claim);
        CHECK(r.repaired == 1 && r.lost == kRange, "long: repaired %lld lost %lld",
              (long long)r.repaired, (long long)r.lost);
        // And the file really was truncated, not merely re-claimed.
        FakeStore store;
        std::string err;
        const arb::host::StoreHandle h = store.open("/p/tape-1.wav", true, err);
        store.pwrite(h, cut.data(), int64_t(cut.size()), 0, err);
        store.close(h);
        reopen(store, 0);
        const std::vector<uint8_t>& on_card = store.bytes("/p/tape-1.wav");
        CHECK(int64_t(on_card.size()) == kHeader + (total - kRange) * 8,
              "long: file is %lld bytes after repair", (long long)on_card.size());
        // The header on the card says the same thing the head does. Checked on
        // the bytes rather than through `medium_frames`, which would agree with
        // a repair that never wrote anything down.
        CHECK(TapeIo::header_frames(on_card.data(), int64_t(on_card.size())) == total - kRange,
              "long: the header on the card claims %lld",
              (long long)TapeIo::header_frames(on_card.data(), int64_t(on_card.size())));
        // And the repair converges: opening the repaired file repairs nothing.
        // A repair that fixed the length but not the header would pass every
        // check above and come back here for ever.
        CHECK(reopen(store, 0).repaired == 0, "long: the repair did not converge");
        // The repair is **durable** before the first take appends. Without the
        // sync the file is right in the page cache and a second cut puts it
        // back where it started -- the one part of this that only
        // `FakeStore`'s durable mark can see (T8's instrument).
        CHECK(store.durable_bytes("/p/tape-1.wav") == kHeader + (total - kRange) * 8,
              "long: %lld bytes durable after the repair",
              (long long)store.durable_bytes("/p/tape-1.wav"));
    }

    // (c) Shorter than its claim -- an `fsck.exfat` truncation, and a partial
    //     frame at the end for good measure. Clamped to whole frames of what
    //     is there; half a stereo pair is a click, not a frame.
    {
        std::vector<uint8_t> lost = whole;
        lost.resize(std::size_t(kHeader + (total - 1000) * 8 + 3));
        FakeStore store;
        std::string err;
        const arb::host::StoreHandle h = store.open("/p/tape-1.wav", true, err);
        store.pwrite(h, lost.data(), int64_t(lost.size()), 0, err);
        store.close(h);
        const Reopened r = reopen(store, 0);
        CHECK(r.opened && r.claim == total - 1000, "short: claim %lld", (long long)r.claim);
        CHECK(r.repaired == 1 && r.lost == 1000, "short: repaired %lld lost %lld",
              (long long)r.repaired, (long long)r.lost);
        const std::vector<uint8_t>& on_card = store.bytes("/p/tape-1.wav");
        CHECK(TapeIo::header_frames(on_card.data(), int64_t(on_card.size())) == total - 1000,
              "short: the header on the card claims %lld",
              (long long)TapeIo::header_frames(on_card.data(), int64_t(on_card.size())));
        CHECK(reopen(store, 0).repaired == 0, "short: the repair did not converge");
    }

    // (d) No header at all -- a cut before any commit's header page landed.
    //     The `pwrite` of a range at offset 4096 zero-fills the header page
    //     into existence, so this is what such a file looks like.
    {
        std::vector<uint8_t> headerless = whole;
        std::fill(headerless.begin(), headerless.begin() + std::ptrdiff_t(kHeader), uint8_t(0));
        const Reopened r = repair_of(headerless);
        CHECK(r.opened && r.claim == 0, "headerless: claim %lld", (long long)r.claim);
        CHECK(r.repaired == 1 && r.refused == 0, "headerless: repaired %lld refused %lld",
              (long long)r.repaired, (long long)r.refused);
    }

    // (e) **Somebody else's WAV.** The one file that must survive untouched:
    //     `tape_save` writes a 44-byte header, and a directory that holds one
    //     under our name is a take we did not write. Refused, and the bytes
    //     are compared afterwards -- "we did not open it" is not the claim,
    //     "we did not change it" is.
    {
        arb::io::Audio a;
        a.sample_rate = 48000;
        a.ch.assign(2, std::vector<float>(2000, 0.0f));
        for (std::size_t f = 0; f < 2000; ++f)
            for (int ch = 0; ch < 2; ++ch) a.ch[std::size_t(ch)][f] = sample(int64_t(f), ch);
        namespace fs = std::filesystem;
        const fs::path tmp = fs::temp_directory_path() /
                             ("arbutus-tapeio-foreign-" + std::to_string(::getpid()) + ".wav");
        const std::string we = arb::io::write(tmp.string(), a, 32);
        CHECK(we.empty(), "write: %s", we.c_str());
        std::vector<uint8_t> foreign;
        {
            std::FILE* f = std::fopen(tmp.string().c_str(), "rb");
            CHECK(f != nullptr, "reopen the foreign wav");
            if (f) {
                uint8_t buf[4096];
                std::size_t n = 0;
                while ((n = std::fread(buf, 1, sizeof buf, f)) > 0)
                    foreign.insert(foreign.end(), buf, buf + n);
                std::fclose(f);
            }
        }
        std::error_code ec;
        fs::remove(tmp, ec);
        CHECK(foreign.size() > 44, "foreign wav is %zu bytes", foreign.size());

        FakeStore store;
        std::string err;
        const arb::host::StoreHandle h = store.open("/p/tape-1.wav", true, err);
        store.pwrite(h, foreign.data(), int64_t(foreign.size()), 0, err);
        store.close(h);
        const Reopened r = reopen(store, 0);
        CHECK(!r.opened && r.refused == 1, "foreign: opened %d refused %lld", int(r.opened),
              (long long)r.refused);
        CHECK(store.bytes("/p/tape-1.wav") == foreign, "a foreign WAV was modified");

        // Negative control for (e): the same file with our own 4 KiB header in
        // front of it *is* ours, and is repaired rather than refused -- so the
        // refusal is reading the header, not refusing everything it is handed.
        CHECK(repair_of(whole).refused == 0, "our own medium was refused");
    }
}

// T6 on a real filesystem: a take is cut mid-commit with `PosixStore`, a new
// host opens the project, and what it plays back is exactly the claim.
void test_repair_posix() {
    namespace fs = std::filesystem;
    const fs::path dir = fs::temp_directory_path() /
                         ("arbutus-tapeio-repair-" + std::to_string(::getpid()));
    std::error_code ec;
    fs::remove_all(dir, ec);
    fs::create_directories(dir, ec);
    CHECK(!ec, "mkdir: %s", ec.message().c_str());

    const int64_t total = kRange * 2 + 3000;
    const int64_t claim = kRange;            // what the lagging header claimed
    {
        // Write a whole take, then put the file in the state a cut mid-take
        // leaves: all the data, a header one range behind it.
        Tape tape;
        tape.init(8 * kRange);
        tape.ensure(0);
        PosixStore store;
        TapeIo io;
        io.configure(&tape, &store, dir.string(), 48000);
        Recorder rec{tape, io, 0};
        while (rec.at < total) rec.block(uint32_t(std::min<int64_t>(1024, total - rec.at)));
        rec.end();
        io.flush();
        CHECK(io.stats().errors == 0, "write: %s", io.last_error().c_str());

        std::string err;
        const arb::host::StoreHandle h = store.open((dir / "tape-1.wav").string(), false, err);
        CHECK(h != arb::host::kNoHandle, "reopen to forge the cut: %s", err.c_str());
        std::vector<uint8_t> hdr(std::size_t(kHeader), 0);
        TapeIo::write_header(hdr.data(), 48000, claim);
        store.pwrite(h, hdr.data(), kHeader, 0, err);
        store.fdatasync(h, err);
        store.close(h);
    }

    {
        // A new host opens the project. The claim is the head, the file is
        // exact again, and every frame up to the claim is the one recorded.
        Tape tape;
        tape.init(8 * kRange);
        PosixStore store;
        TapeIo io;
        io.configure(&tape, &store, dir.string(), 48000);
        CHECK(io.open_all(), "reopen: %s", io.last_error().c_str());
        CHECK(io.medium_frames(0) == claim, "claim %lld", (long long)io.medium_frames(0));
        CHECK(io.stats().repaired == 1 && io.stats().lost_frames == total - claim,
              "repaired %lld lost %lld", (long long)io.stats().repaired,
              (long long)io.stats().lost_frames);
    }

    arb::io::Audio a;
    const std::string e = arb::io::read((dir / "tape-1.wav").string(), a);
    CHECK(e.empty(), "read back: %s", e.c_str());
    CHECK(a.frames() == std::size_t(claim), "frames %zu, claim %lld", std::size_t(a.frames()),
          (long long)claim);
    int wrong = 0;
    if (a.ch.size() == 2 && a.frames() == std::size_t(claim))
        for (int64_t f = 0; f < claim; ++f)
            for (int ch = 0; ch < 2; ++ch)
                if (a.ch[std::size_t(ch)][std::size_t(f)] != sample(f, ch)) ++wrong;
    CHECK(wrong == 0, "%d frames differ after repair", wrong);

    // Negative control, T6's own: corrupt the header's frame count *upward* by
    // hand and the open must clamp it back and rewrite it, rather than handing
    // a reader a length the file has not got.
    {
        PosixStore store;
        std::string err;
        const arb::host::StoreHandle h = store.open((dir / "tape-1.wav").string(), false, err);
        std::vector<uint8_t> hdr(std::size_t(kHeader), 0);
        TapeIo::write_header(hdr.data(), 48000, claim * 4);
        store.pwrite(h, hdr.data(), kHeader, 0, err);
        store.fdatasync(h, err);
        store.close(h);

        Tape tape;
        tape.init(8 * kRange);
        PosixStore s2;
        TapeIo io;
        io.configure(&tape, &s2, dir.string(), 48000);
        CHECK(io.open_all(), "reopen after the lie: %s", io.last_error().c_str());
        CHECK(io.medium_frames(0) == claim, "over-claim not clamped: %lld",
              (long long)io.medium_frames(0));
        arb::io::Audio b;
        CHECK(arb::io::read((dir / "tape-1.wav").string(), b).empty(), "read after clamp");
        CHECK(b.frames() == std::size_t(claim), "clamped file is %zu frames",
              std::size_t(b.frames()));
    }

    fs::remove_all(dir, ec);
    CHECK(!fs::exists(dir), "the test left %s behind", dir.string().c_str());
}

}  // namespace

[[gnu::noinline]] void* operator new(std::size_t n) {
    g_news.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void* p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void* p, std::size_t) noexcept { std::free(p); }

// ---- compaction (C1 step 8, § 3.8) -----------------------------------------

using arb::host::CompactMove;
using arb::host::KeepSpan;

// Lay a take of `frames` down on track 0 and make it durable, so the card holds
// a medium the compactor can read. Returns the frames written.
int64_t lay_take(Tape& tape, TapeIo& io, int64_t frames) {
    Recorder rec{tape, io, 0};
    while (rec.at < frames) rec.block(uint32_t(std::min<int64_t>(256, frames - rec.at)));
    rec.end();
    io.flush();
    return rec.at;
}

// The compacted medium, as our own WAV reader sees it.
bool read_medium(FakeStore& store, arb::io::Audio& a) {
    const std::vector<uint8_t>& bytes = store.bytes("/p/tape-1.wav");
    return arb::io::read_wav(bytes, a).empty();
}

void test_compact_keeps_what_it_is_told() {
    Tape tape;
    tape.init(16 * kRange);
    CHECK(tape.ensure(0), "ensure");
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    const int64_t total = lay_take(tape, io, 5 * kRange + 777);

    // Two spans out of five ranges, neither of them range-aligned: the medium
    // is append-only, so this is what a retake and a LIFT leave behind.
    // The first span is deliberately **not** a whole range: with a round
    // number the range-alignment below is a no-op and the test proves nothing
    // (it survived deleting the alignment outright).
    std::vector<KeepSpan> keep{{kRange + 100, 1000}, {4 * kRange + 7, 2000}};
    CHECK(io.compact_start(0, keep), "compact_start: %s", io.compact_error().c_str());
    CHECK(io.compact_state() == TapeIo::CompactState::kDone, "offline runs to completion");
    // Freed is measured against the medium's own claim, not the keep set's.
    CHECK(io.compact_freed() == total - 3000, "freed %lld", (long long)io.compact_freed());

    std::vector<CompactMove> map;
    int64_t frames = 0;
    std::string err;
    CHECK(io.compact_finish(map, frames, err), "compact_finish: %s", err.c_str());
    CHECK(map.size() == 2, "map rows %zu", map.size());
    if (map.size() == 2) {
        // § 3.8: every kept span starts on a range boundary of the new medium.
        CHECK(map[0].from == kRange + 100 && map[0].to == 0 && map[0].len == 1000,
              "row 0 %lld->%lld+%lld", (long long)map[0].from, (long long)map[0].to,
              (long long)map[0].len);
        CHECK(map[1].from == 4 * kRange + 7 && map[1].to == kRange && map[1].len == 2000,
              "row 1 %lld->%lld+%lld", (long long)map[1].from, (long long)map[1].to,
              (long long)map[1].len);
        // `%` cannot appear inside a CHECK's condition -- the macro prints it
        // as a format string -- so the modulo goes in a name.
        const int64_t row1_phase = map[1].to - Tape::range_of(map[1].to) * kRange;
        CHECK(row1_phase == 0, "row 1 is %lld into a range", (long long)row1_phase);
    }
    CHECK(frames == kRange + 2000, "frames %lld", (long long)frames);   // 1000 + a pad

    arb::io::Audio a;
    CHECK(read_medium(store, a), "the compacted medium is not a WAV of ours");
    CHECK(a.frames() == std::size_t(frames), "compacted frames %zu", std::size_t(a.frames()));
    // Every kept frame is the frame it was, at its new offset. That is the
    // whole contract: the UI's remap is `to + d` and nothing else moved.
    int wrong = 0;
    if (a.ch.size() == 2 && a.frames() == std::size_t(frames))
        for (const CompactMove& m : map)
            for (int64_t d = 0; d < m.len; ++d)
                for (int ch = 0; ch < 2; ++ch)
                    if (a.ch[std::size_t(ch)][std::size_t(m.to + d)] != sample(m.from + d, ch))
                        ++wrong;
    CHECK(wrong == 0, "%d kept frames moved wrong", wrong);

    // Negative control: the same compare against a map row shifted by one
    // frame must fail everywhere, so "it matched" is not "it matched anything".
    int caught = 0;
    if (a.ch.size() == 2 && a.frames() == std::size_t(frames) && !map.empty())
        for (int64_t d = 0; d < 64; ++d)
            for (int ch = 0; ch < 2; ++ch)
                if (a.ch[std::size_t(ch)][std::size_t(map[0].to + d)] !=
                    sample(map[0].from + d + 1, ch))
                    ++caught;
    CHECK(caught == 128, "the shifted compare was caught %d times of 128", caught);

    // The scratch file is gone and the medium is the one under its own name.
    CHECK(store.bytes("/p/tape-1.wav.compact").empty(), "the scratch file survived");
    CHECK(io.medium_frames(0) == frames, "reopened claim %lld", (long long)io.medium_frames(0));
}

void test_compact_coalesces_and_clamps() {
    Tape tape;
    tape.init(16 * kRange);
    CHECK(tape.ensure(0), "ensure");
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    const int64_t total = lay_take(tape, io, 3 * kRange);

    // Out of order, overlapping, touching, and one span running past the end
    // of the medium -- every shape a UI keep set can really take, because the
    // spans come from clips the player moved and sliced.
    std::vector<KeepSpan> keep{{2000, 1000},                   // inside the next
                               {1000, 3000},                   // overlaps it
                               {4000, 500},                    // touches at 4000
                               {total - 100, 4 * kRange}};     // past the end
    CHECK(io.compact_start(0, keep), "compact_start: %s", io.compact_error().c_str());
    std::vector<CompactMove> map;
    int64_t frames = 0;
    std::string err;
    CHECK(io.compact_finish(map, frames, err), "compact_finish: %s", err.c_str());
    CHECK(map.size() == 2, "coalesced to %zu rows", map.size());
    if (map.size() == 2) {
        CHECK(map[0].from == 1000 && map[0].len == 3500, "row 0 %lld+%lld",
              (long long)map[0].from, (long long)map[0].len);
        CHECK(map[1].from == total - 100 && map[1].len == 100, "row 1 clamped to %lld",
              (long long)map[1].len);
    }
}

void test_compact_empty_keep_truncates() {
    Tape tape;
    tape.init(16 * kRange);
    CHECK(tape.ensure(0), "ensure");
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    lay_take(tape, io, 2 * kRange);

    // § 3.8: "a track whose `keep` is empty is truncated to its header" -- the
    // player who deleted every clip on a track and saved.
    CHECK(io.compact_start(0, {}), "compact_start: %s", io.compact_error().c_str());
    std::vector<CompactMove> map;
    int64_t frames = 0;
    std::string err;
    CHECK(io.compact_finish(map, frames, err), "compact_finish: %s", err.c_str());
    CHECK(map.empty() && frames == 0, "rows %zu frames %lld", map.size(), (long long)frames);
    CHECK(int64_t(store.bytes("/p/tape-1.wav").size()) == kHeader, "size %zu",
          store.bytes("/p/tape-1.wav").size());
    CHECK(io.medium_frames(0) == 0, "claim %lld", (long long)io.medium_frames(0));
}

// The cancel path, on the live thread -- the only place it can be exercised,
// because offline `compact_start` runs the job to completion before it returns
// and there is nothing to cancel. The medium must be untouched afterwards.
void test_compact_cancelled_live() {
    Tape tape;
    tape.init(64 * kRange);
    CHECK(tape.ensure(0), "ensure");
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    const int64_t total = lay_take(tape, io, 40 * kRange);
    const std::vector<uint8_t> before = store.bytes("/p/tape-1.wav");

    io.start();
    std::vector<KeepSpan> keep{{0, total}};
    CHECK(io.compact_start(0, keep), "compact_start: %s", io.compact_error().c_str());
    io.compact_cancel();                          // "PLAY or REC arrived"
    CHECK(io.compact_state() == TapeIo::CompactState::kCancelled, "state after cancel");
    CHECK(store.bytes("/p/tape-1.wav.compact").empty(), "the scratch file survived a cancel");
    io.compact_clear();
    CHECK(io.compact_state() == TapeIo::CompactState::kIdle, "cleared");
    io.stop();
    CHECK(store.bytes("/p/tape-1.wav") == before, "the medium was touched by a cancelled job");

    // Negative control for the cancel itself: the same job, left alone, really
    // does finish and really does replace the medium -- so "unchanged" above
    // is the cancel's doing and not a job that never started.
    TapeIo io2;
    io2.configure(&tape, &store, "/p", 48000);
    io2.start();
    CHECK(io2.compact_start(0, std::vector<KeepSpan>{{kRange, kRange}}), "compact_start 2");
    for (int i = 0; i < 2000 && io2.compact_state() == TapeIo::CompactState::kRunning; ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    CHECK(io2.compact_state() == TapeIo::CompactState::kDone, "the uncancelled job finished");
    std::vector<CompactMove> map;
    int64_t frames = 0;
    std::string err;
    CHECK(io2.compact_finish(map, frames, err), "compact_finish: %s", err.c_str());
    io2.stop();
    CHECK(frames == kRange, "frames %lld", (long long)frames);
    CHECK(store.bytes("/p/tape-1.wav") != before, "the medium was not replaced");
}


// #167, and the reason it reached a board. `test_compact_cancelled_live` above
// runs a real job on a real thread and passes -- against a `FakeStore` whose
// slices finish in microseconds, so `mu_` is free between them constantly. On
// the bench CM4, where a slice is ~220 ms of card, the same job froze the whole
// host for its seven seconds: the `started` reply came at 7.09 s of 7.14, a
// fresh connection's `hello` took 4.7-5.3 s, and PLAY two seconds in did not
// cancel anything, because by the time `compact_cancel` won the lock the copy
// had finished and replaced the medium.
//
// So the card is given a delay (`FakeStore::set_op_delay`) and the two things
// the freeze is measured by are asserted:
//
//   * **the cancel lands inside the job**, which the store's own write counter
//     says without a clock: the copy stops within a range or two of where PLAY
//     arrived instead of running every range to the end;
//   * **a control-thread call does not wait for the card** -- `compact_state()`
//     is what `poll()` asks every turn, and it is why an unrelated `hello` took
//     five seconds.
//
// The negative control is `set_compact_holds_lock(true)`, which puts `mu_` back
// across the copy's I/O: the *same* assertions must then fail, and the test
// asserts that they do. Without it this is a green harness, which #56 says is
// not evidence.
//
// #206: the negative control's job is **one slice** long. With `mu_` held
// across the copy's I/O the cancel cannot land inside a slice -- that is the
// whole of what the before-code guarantees -- but between two slices the I/O
// thread lets go of `mu_` and a waiting cancel can win it. A 24-range job is
// three slices, so "the cancel could not stop the copy" held only while the
// I/O thread won both of those races, which an unfair mutex makes likely and
// a loaded container does not (1 run in 30). One slice leaves no gap before
// the copy's last range: the cancel lands after it whichever thread wins, so
// the assertion holds under every interleaving and is not loosened.
// #542: both questions are asked of a **latch**, not a clock. The fake card
// parks the copy inside its third range's `pwrite` (`FakeStore::park_write`),
// and while it is parked the test asks `compact_state()` on a thread of its
// own and notes the store's write count at the moment the answer came back.
// The fixed code answers with the copy still in the card -- the count is the
// parked one -- and the before-code cannot answer until the slice is over,
// whatever the scheduler does, because `mu_` is held across the parked write.
// It replaced a `worst_poll_us < 2000` limit and an unlocked read of the
// store's write counter, which failed 37 runs in 300 under load and was a
// data race (#242's rule: a timing test never counts a sleep it did not mean
// to measure).
struct CancelCell {
    int64_t copied = 0;        // writes the copy made before the cancel returned
    int64_t needed = 0;        // ranges the whole job would have written
    int64_t parked_at = 0;     // writes made when the copy stopped in the card
    int64_t answered_at = -1;  // writes made when `compact_state()` came back
    bool parked = false;       // the copy reached the latch at all
    bool untouched = false;    // the medium's bytes after == before the job
};

// `TapeIo::kCompactRanges` (private): the ranges one I/O-thread turn copies.
constexpr int64_t kCompactSlice = 8;

CancelCell compact_cancel_cell(bool holds_lock, int64_t ranges) {
    Tape tape;
    tape.init(64 * kRange);
    CHECK(tape.ensure(0), "ensure");
    FakeStore store;
    TapeIo io;
    io.set_compact_holds_lock(holds_lock);
    io.configure(&tape, &store, "/p", 48000);
    const int64_t total = lay_take(tape, io, ranges * kRange);
    const std::vector<uint8_t> before = store.bytes_copy("/p/tape-1.wav");

    CancelCell c;
    c.needed = ranges;
    io.start();
    const int64_t writes0 = store.writes();
    // The scratch file's header is the first write, the copy's ranges the
    // rest: the fourth is the third range, so a cancel that stops the copy
    // there has most of the job left to stop.
    store.park_write(4);
    CHECK(io.compact_start(0, std::vector<KeepSpan>{{0, total}}), "compact_start: %s",
          io.compact_error().c_str());
    c.parked = store.wait_parked(std::chrono::seconds(10));
    c.parked_at = store.writes() - writes0;

    // `poll()`'s question, asked while the card has the copy. The wait is a
    // bound, not the measure: the fixed code answers at once, and the
    // before-code cannot answer before `release_write` at all, so all a longer
    // wait buys is a slower before-arm.
    std::atomic<int64_t> answered{-1};
    std::thread probe([&] {
        (void)io.compact_state();
        answered.store(store.writes() - writes0);
    });
    for (int i = 0; i < 1000 && answered.load() < 0; ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));

    // "PLAY arrived", while the copy is still in the card. Where the lock is
    // free the cancel is seen to have put the state down before the write
    // comes back, so the copy has to see it on its way out of the card.
    std::thread cancel([&] { io.compact_cancel(); });
    if (answered.load() >= 0) {
        for (int i = 0; i < 10000 && io.compact_state() != TapeIo::CompactState::kCancelled; ++i)
            std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
    store.release_write();
    cancel.join();
    probe.join();
    c.answered_at = answered.load();
    c.copied = store.writes() - writes0;
    io.compact_clear();
    io.stop();
    c.untouched = store.bytes("/p/tape-1.wav") == before;
    return c;
}

void test_compact_cancel_does_not_wait_for_the_card() {
    // 24 ranges is three slices, so the fixed arm's cancel has whole slices
    // left to stop.
    const CancelCell fixed = compact_cancel_cell(false, 3 * kCompactSlice);
    CHECK(fixed.parked && fixed.parked_at == 3, "the copy never parked in its third range: %lld",
          (long long)fixed.parked_at);

    // The cancel landed inside the job: the parked write finishes and the copy
    // goes no further. A third of the job is the bound #167 set; the before-arm
    // below is what says the bound is not simply always true.
    CHECK(fixed.copied < fixed.needed / 3, "the copy ran on after the cancel: %lld writes of %lld",
          (long long)fixed.copied, (long long)fixed.needed);
    // And `poll()`'s own question was answered while the card had the copy.
    CHECK(fixed.answered_at == fixed.parked_at,
          "compact_state() waited for the card: answered at write %lld, parked at %lld",
          (long long)fixed.answered_at, (long long)fixed.parked_at);

    // The negative control: `mu_` back across the copy's I/O, which is the code
    // as it stood until #167. Both assertions above must go red. One slice
    // (#206): the copy parks with the lock held, so neither the question nor
    // the cancel can land until the slice's last range is down.
    const CancelCell held = compact_cancel_cell(true, kCompactSlice);
    CHECK(held.parked && held.parked_at == 3, "the before-arm never parked: %lld",
          (long long)held.parked_at);
    CHECK(held.copied >= held.needed,
          "#167's before switch did not reproduce the freeze: %lld writes of %lld",
          (long long)held.copied, (long long)held.needed);
    CHECK(held.answered_at >= held.needed,
          "compact_state() was not seen to wait with the lock held: answered at write %lld of %lld",
          (long long)held.answered_at, (long long)held.needed);

    // Both arms leave the medium exactly as it was -- the cancel's own promise,
    // and in the before-arm's case the one thing that still held on the bench.
    CHECK(fixed.untouched, "the fixed arm's cancel changed the medium");
    CHECK(held.untouched, "the before-arm's cancel changed the medium");
}

// A refused write on the scratch file must leave the medium alone: the swap is
// the only thing that makes a compaction real, and it never happens.
void test_compact_write_refused() {
    Tape tape;
    tape.init(16 * kRange);
    CHECK(tape.ensure(0), "ensure");
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/p", 48000);
    const int64_t total = lay_take(tape, io, 3 * kRange);
    const std::vector<uint8_t> before = store.bytes("/p/tape-1.wav");

    store.fail(FakeStore::Op::kWrite, "ENOSPC");
    CHECK(!io.compact_start(0, std::vector<KeepSpan>{{0, total}}), "a refused write started");
    CHECK(io.compact_state() == TapeIo::CompactState::kFailed ||
              io.compact_state() == TapeIo::CompactState::kIdle,
          "state after a refused write");
    io.compact_clear();
    CHECK(store.bytes("/p/tape-1.wav") == before, "the medium was touched by a failed job");
    CHECK(store.bytes("/p/tape-1.wav.compact").empty(), "the scratch file survived a failure");

    // And the medium still plays: the same job without the fault succeeds.
    CHECK(io.compact_start(0, std::vector<KeepSpan>{{0, total}}), "compact_start after failure: %s",
          io.compact_error().c_str());
    std::vector<CompactMove> map;
    int64_t frames = 0;
    std::string err;
    CHECK(io.compact_finish(map, frames, err), "compact_finish: %s", err.c_str());
    CHECK(frames == total, "frames %lld", (long long)frames);
}

// ---- C1 step 3b (#157): moving the media is all or nothing -------------------
// `tape_dir {move}` renames each medium and its sidecar into the project folder.
// A rename that fails part way -- another disk, on a real card -- must put back
// the ones already moved, or the instrument is left recording into a folder
// that holds half its tape. host_tapedir.py covers the paths a real filesystem
// reaches; this is the one only an armed fault can.
void test_set_dir_move_rolls_back() {
    Tape tape;
    tape.init(8 * kRange);
    CHECK(tape.ensure(0) && tape.ensure(1), "ensure");
    FakeStore store;
    TapeIo io;
    io.configure(&tape, &store, "/w", 48000);
    io.set_peaks(false);                       // two files to move, not four
    int64_t took = 0;
    for (int t = 0; t < 2; ++t) {
        Recorder rec{tape, io, t};
        while (rec.at < kRange + 100) rec.block(128);
        rec.end();
        took = rec.at;
    }
    io.flush();
    CHECK(store.exists("/w/tape-1.wav") && store.exists("/w/tape-2.wav"), "two media");

    int64_t frames[Tape::kTracks];
    std::string code, err;
    store.fail(FakeStore::Op::kRename, "", 1);          // tape-1.wav moves...
    store.fail(FakeStore::Op::kRename, "EXDEV", 1);     // ...tape-2.wav does not
    CHECK(!io.set_dir("/p", true, frames, code, err), "a failed rename reported success");
    CHECK(code == "move_failed" && err == "EXDEV", "code %s err %s", code.c_str(), err.c_str());
    CHECK(store.exists("/w/tape-1.wav") && store.exists("/w/tape-2.wav"),
          "the moved medium was not put back");
    CHECK(!store.exists("/p/tape-1.wav") && !store.exists("/p/tape-2.wav"), "half a move left in /p");
    CHECK(io.dir() == "/w", "dir moved to %s", io.dir().c_str());

    // Negative control: the same move with nothing armed goes through, so the
    // assertions above are about the fault and not about a move that never ran.
    CHECK(io.set_dir("/p", true, frames, code, err), "move: %s", err.c_str());
    CHECK(store.exists("/p/tape-1.wav") && store.exists("/p/tape-2.wav"), "media not in /p");
    CHECK(!store.exists("/w/tape-1.wav") && !store.exists("/w/tape-2.wav"), "media left in /w");
    CHECK(frames[0] == took && frames[1] == took, "frames %lld %lld",
          (long long)frames[0], (long long)frames[1]);
}

// ---- #484: what the move must never write over ------------------------------
// The review of t157 found four ways SAVE's move could damage or misplace
// audio. Each case is one of them; each was seen to fail on the code before.

// A track's media recorded in /w, track 1 only.
struct MoveRig {
    Tape tape;
    FakeStore store;
    TapeIo io;
    int64_t took = 0;
    explicit MoveRig(int tracks = 1) {
        tape.init(8 * kRange);
        for (int t = 0; t < tracks; ++t) tape.ensure(t);
        io.configure(&tape, &store, "/w", 48000);
        io.set_peaks(false);
        for (int t = 0; t < tracks; ++t) {
            Recorder rec{tape, io, t};
            while (rec.at < kRange + 100) rec.block(128);
            rec.end();
            took = rec.at;
        }
        io.flush();
    }
};

// Finding 1: the destination holds `tape-2.wav`, the source has no track 2.
// Before, the move said `ok` and track 2's next take wrote over that file
// from frame zero; now the whole move is refused and nothing moves.
void test_set_dir_move_refuses_a_name_the_source_lacks() {
    MoveRig r;
    std::string err;
    const StoreHandle h = r.store.open("/p/tape-2.wav", true, err);
    CHECK(h != kNoHandle, "seed: %s", err.c_str());
    const std::vector<uint8_t> theirs(4096 + 800, 0x5A);
    CHECK(r.store.pwrite(h, theirs.data(), int64_t(theirs.size()), 0, err) == int64_t(theirs.size()),
          "seed write: %s", err.c_str());
    r.store.close(h);
    const std::vector<uint8_t> before = r.store.bytes("/w/tape-1.wav");

    int64_t frames[Tape::kTracks];
    std::string code;
    CHECK(!r.io.set_dir("/p", true, frames, code, err), "moved over another track's file");
    CHECK(code == "exists" && err.find("/p/tape-2.wav") != std::string::npos,
          "code %s err %s", code.c_str(), err.c_str());
    CHECK(r.store.bytes("/w/tape-1.wav") == before && !r.store.exists("/p/tape-1.wav"),
          "the medium moved on a refusal");
    CHECK(r.store.bytes("/p/tape-2.wav") == theirs, "the folder's own file changed");
    CHECK(r.io.dir() == "/w", "dir moved to %s", r.io.dir().c_str());
}

// Finding 4: `/w/` is `/w`. Before, the raw-string compare made it a move,
// which on a real disk was refused `exists` against the tape's own files.
void test_set_dir_move_to_itself_by_another_spelling() {
    MoveRig r;
    const std::vector<uint8_t> before = r.store.bytes("/w/tape-1.wav");
    int64_t frames[Tape::kTracks];
    std::string code, err;
    for (const char* again : {"/w/", "/w//", "/w/."}) {
        CHECK(r.io.set_dir(again, true, frames, code, err), "%s: %s %s", again, code.c_str(), err.c_str());
        CHECK(r.io.dir() == "/w", "%s: dir became %s", again, r.io.dir().c_str());
        CHECK(r.store.bytes("/w/tape-1.wav") == before, "%s: the medium moved or changed", again);
        CHECK(frames[0] == r.took, "%s: frames %lld", again, (long long)frames[0]);
    }
}

// Finding 3: the forward rename of track 2 fails and so does putting track 1
// back. Before, that was a plain `move_failed` with `dir()` still /w while
// track 1 was only in /p, so the next open of /w lost it. Now the reply says
// the tape is split and where the stranded file is.
void test_set_dir_move_failed_rollback() {
    MoveRig r(2);
    int64_t frames[Tape::kTracks];
    std::string code, err;
    r.store.fail(FakeStore::Op::kRename, "", 1);       // tape-1.wav moves...
    r.store.fail(FakeStore::Op::kRename, "EIO", 2);    // ...tape-2.wav does not, nor does tape-1 back
    CHECK(!r.io.set_dir("/p", true, frames, code, err), "a failed move reported success");
    CHECK(code == "move_split", "code %s err %s", code.c_str(), err.c_str());
    CHECK(err.find("/p/tape-1.wav") != std::string::npos && err.find("EIO") != std::string::npos,
          "err does not say where the stranded file is: %s", err.c_str());
    CHECK(r.store.exists("/p/tape-1.wav") && r.store.exists("/w/tape-2.wav"),
          "the fault did not split the tape the way the test means");
    CHECK(r.io.dir() == "/w", "dir moved to %s", r.io.dir().c_str());

    // Negative control: one failure and a working rollback is still the old,
    // plain `move_failed`.
    MoveRig ok(2);
    ok.store.fail(FakeStore::Op::kRename, "", 1);
    ok.store.fail(FakeStore::Op::kRename, "EXDEV", 1);
    CHECK(!ok.io.set_dir("/p", true, frames, code, err), "a failed move reported success");
    CHECK(code == "move_failed" && err == "EXDEV", "code %s err %s", code.c_str(), err.c_str());
}

// Finding 2, on a real disk: a dangling symlink is a taken name. `stat`
// follows the link, found nothing, and `rename` replaced the link. And
// finding 4's symlink spelling: a link to the tape's own folder is that folder.
void test_set_dir_move_posix_names() {
    namespace fs = std::filesystem;
    const fs::path root = fs::temp_directory_path() /
                          ("arbutus-tapeio-move-" + std::to_string(::getpid()));
    std::error_code ec;
    fs::remove_all(root, ec);
    const fs::path w = root / "w", p = root / "p";
    fs::create_directories(w, ec);
    fs::create_directories(p, ec);
    CHECK(!ec, "mkdir: %s", ec.message().c_str());
    {
        Tape tape;
        tape.init(8 * kRange);
        tape.ensure(0);
        PosixStore store;
        TapeIo io;
        io.configure(&tape, &store, w.string(), 48000);
        io.set_peaks(false);
        Recorder rec{tape, io, 0};
        while (rec.at < kRange + 100) rec.block(128);
        rec.end();
        io.flush();

        fs::create_symlink(root / "owner-missing.wav", p / "tape-1.wav", ec);
        CHECK(!ec, "symlink: %s", ec.message().c_str());
        int64_t frames[Tape::kTracks];
        std::string code, err;
        CHECK(!io.set_dir(p.string(), true, frames, code, err), "moved over a dangling symlink");
        CHECK(code == "exists", "code %s err %s", code.c_str(), err.c_str());
        CHECK(fs::is_symlink(fs::symlink_status(p / "tape-1.wav")), "the link was replaced");
        CHECK(fs::exists(w / "tape-1.wav"), "the medium left /w");

        // The link to /w is /w: nothing moves, nothing is refused.
        fs::create_directory_symlink(w, root / "here", ec);
        CHECK(!ec, "dir symlink: %s", ec.message().c_str());
        CHECK(io.set_dir((root / "here").string(), true, frames, code, err),
              "a symlink to the tape's folder: %s %s", code.c_str(), err.c_str());
        CHECK(fs::exists(w / "tape-1.wav") && io.dir() == w.string(), "the medium moved");

        // Negative control: with the link gone the same move goes through.
        fs::remove(p / "tape-1.wav", ec);
        CHECK(io.set_dir(p.string(), true, frames, code, err), "move: %s %s", code.c_str(), err.c_str());
        CHECK(fs::exists(p / "tape-1.wav") && !fs::exists(w / "tape-1.wav"), "the medium did not move");
    }
    fs::remove_all(root, ec);
    CHECK(!fs::exists(root), "the test left %s behind", root.string().c_str());
}

// #591 finding 3: SAVE's move and compaction's swap are renames, and a
// rename is a change to the folder that `fdatasync` on the file never
// reaches; on a non-journaled exFAT card a power cut could undo it after the
// host had answered ok. `FakeStore` counts a folder whose rename no
// `sync_dir_of` has followed: after each of the three, it must be none.
std::string unsynced(const FakeStore& store) {
    std::string s;
    for (const std::string& d : store.unsynced_dirs()) s += d + " ";
    return s;
}

void test_renames_are_synced() {
    {   // `PosixStore`'s half: the folder itself reaches `fsync` (rows above
        // only see `FakeStore` count the call).
        namespace fs = std::filesystem;
        const fs::path dir = fs::temp_directory_path() /
                             ("arbutus-tapeio-syncdir-" + std::to_string(::getpid()));
        std::error_code ec;
        fs::create_directories(dir, ec);
        struct stat st{};
        CHECK(::stat(dir.c_str(), &st) == 0, "stat %s", dir.c_str());
        PosixStore store;
        std::string err;
        const long before = g_dir_fsyncs.load();
        CHECK(store.sync_dir_of((dir / "tape-1.wav").string(), err), "sync_dir_of: %s", err.c_str());
        CHECK(g_dir_fsyncs.load() == before + 1, "%ld folder fsyncs, want 1",
              g_dir_fsyncs.load() - before);
        CHECK(g_dir_fsync_dev == st.st_dev && g_dir_fsync_ino == st.st_ino,
              "the fsync was not on %s", dir.c_str());
        fs::remove_all(dir, ec);
    }
    {   // SAVE's move, both folders.
        MoveRig r;
        int64_t frames[Tape::kTracks];
        std::string code, err;
        CHECK(r.io.set_dir("/p", true, frames, code, err), "move: %s", err.c_str());
        CHECK(r.store.unsynced_dirs().empty(), "move: unsynced folders %s", unsynced(r.store).c_str());
    }
    {   // A move put back after a failed rename: the put-back is renames too.
        MoveRig r(2);
        int64_t frames[Tape::kTracks];
        std::string code, err;
        r.store.fail(FakeStore::Op::kRename, "", 1);
        r.store.fail(FakeStore::Op::kRename, "EXDEV", 1);
        CHECK(!r.io.set_dir("/p", true, frames, code, err), "a failed rename reported success");
        CHECK(r.store.unsynced_dirs().empty(), "rollback: unsynced folders %s",
              unsynced(r.store).c_str());
    }
    {   // Compaction's swap of its scratch file onto the medium.
        Tape tape;
        tape.init(16 * kRange);
        CHECK(tape.ensure(0), "ensure");
        FakeStore store;
        TapeIo io;
        io.configure(&tape, &store, "/p", 48000);
        lay_take(tape, io, 3 * kRange + 10);
        std::vector<KeepSpan> keep{{kRange, 1000}};
        CHECK(io.compact_start(0, keep), "compact_start: %s", io.compact_error().c_str());
        std::vector<CompactMove> map;
        int64_t frames = 0;
        std::string err;
        CHECK(io.compact_finish(map, frames, err), "compact_finish: %s", err.c_str());
        CHECK(store.unsynced_dirs().empty(), "compaction: unsynced folders %s",
              unsynced(store).c_str());
    }
}

int main() {
    test_header();
    test_take_lands();
    test_header_lag();
    test_no_allocation();
    test_ring_full();
    test_prealloc_lead();
    test_posix_store();
    test_live_thread();
    test_commit_sync_does_not_hold_the_lock();
    test_peaks_sidecar_does_not_hold_the_lock();
    test_write_refused();
    test_take_end_claims_only_the_durable_prefix();
    test_repair_on_open();
    test_repair_posix();
    test_compact_keeps_what_it_is_told();
    test_compact_coalesces_and_clamps();
    test_compact_empty_keep_truncates();
    test_compact_cancelled_live();
    test_compact_cancel_does_not_wait_for_the_card();
    test_compact_write_refused();
    test_set_dir_move_rolls_back();
    test_set_dir_move_refuses_a_name_the_source_lacks();
    test_set_dir_move_to_itself_by_another_spelling();
    test_set_dir_move_failed_rollback();
    test_set_dir_move_posix_names();
    test_renames_are_synced();
    if (failures) {
        std::printf("tape_io: %d failure(s)\n", failures);
        return 1;
    }
    std::printf("tape_io: ok\n");
    return 0;
}
