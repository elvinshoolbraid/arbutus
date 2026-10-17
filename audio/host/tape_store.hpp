// tape_store.hpp — the one seam between the tape and storage (C1 step 1,
// tape-streaming-design.md § 4).
//
// Every storage call the tape makes goes through this interface: a POSIX
// implementation on the device and a `FakeStore` in the tests, so a card that
// stalls for two seconds is a test that runs in milliseconds. Nothing else in
// the host opens, reads or writes a medium.
//
// The interface is deliberately POSIX-shaped (`pread`/`pwrite` at a byte
// offset, `fdatasync`, `truncate`, `rename`, `free_bytes`) because the medium
// is written **in place** — a range at a time at its own offset, never
// temp-then-rename (§ 3.5; DDR-068's rename rule is for documents, and a
// rename-based medium would be the 737 MB copy on SAVE that the design
// rejects).
//
// C1 step 2a puts the write-behind thread (`tape_io.hpp`) behind it and
// `PosixStore` in front of a real file; step 2b bounds the pool, so the same
// thread also reads ranges back through `pread` and `busy()` is how an offline
// test says the card has stopped answering.
#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <vector>

namespace arb::host {

// An opaque, non-negative file id. `kNoHandle` is "not open".
using StoreHandle = int;
inline constexpr StoreHandle kNoHandle = -1;

class TapeStore {
public:
    virtual ~TapeStore() = default;

    // Opens `path`, creating it when `create`. kNoHandle + `err` on failure.
    virtual StoreHandle open(const std::string& path, bool create, std::string& err) = 0;
    virtual void close(StoreHandle h) = 0;

    // POSIX semantics: the byte count transferred, or -1 with `err` set. A read
    // that runs past the end of the file is short, not an error -- it returns
    // what is there, which is what repair-on-open needs (§ 3.5).
    virtual int64_t pread(StoreHandle h, void* dst, int64_t bytes, int64_t off, std::string& err) = 0;
    virtual int64_t pwrite(StoreHandle h, const void* src, int64_t bytes, int64_t off,
                           std::string& err) = 0;

    // Everything written before this call is on the device when it returns --
    // but not necessarily in the order it was written, which is why the live
    // header lags the data by one range (§ 3.5).
    virtual bool fdatasync(StoreHandle h, std::string& err) = 0;
    virtual bool truncate(StoreHandle h, int64_t bytes, std::string& err) = 0;
    virtual bool rename(const std::string& from, const std::string& to, std::string& err) = 0;
    // `rename` that **refuses** to replace whatever is at `to` -- a file, a
    // folder, a symlink, dangling or not -- with `err` "EEXIST" (#484). SAVE's
    // move uses it, so a name taken between the check and the rename is still
    // never written over; compaction's swap onto the medium is the one rename
    // that means to replace, and keeps `rename`.
    virtual bool rename_new(const std::string& from, const std::string& to, std::string& err) = 0;
    // Make the directory entries in `path`'s folder durable (#591): a rename
    // is a change to the folder, not to the file, and `fdatasync` on the file
    // does not reach it. On a non-journaled exFAT `/data` a power cut after a
    // SAVE's move or a compaction's swap could otherwise undo the rename.
    // `hostlib/audiofile.cpp`'s `fsync_dir` is the same call for documents.
    // The default is "nothing to sync": a store without folders.
    virtual bool sync_dir_of(const std::string& path, std::string& err) {
        (void)path;
        (void)err;
        return true;
    }
    // Unlink `path`. A path that is not there is **not** an error: the one
    // caller is compaction cleaning up its own scratch file (§ 3.8), which
    // runs both after a cancel and on a job that never created it.
    virtual bool remove(const std::string& path, std::string& err) = 0;
    // Whether `path` names a file. C1 step 3b (#157): pointing the tape at a
    // folder opens the media that are there and creates none that are not, and
    // moving the media into one refuses rather than rename over a file. A name
    // is taken by **any** directory entry, a symlink included and whether or
    // not it points anywhere (#484: a dangling one counted as free).
    virtual bool exists(const std::string& path) const = 0;
    // Whether two spellings name one folder (#484): `/p/` and `/p`, and on a
    // real disk a symlink to it. The default compares the normalised spelling,
    // which is all a store without folders has.
    virtual bool same_dir(const std::string& a, const std::string& b) const;

    // The file's length in bytes, or -1 with `err`. Not in design § 4's list,
    // and **repair-on-open is why it is here** (C1 step 3, § 3.5): a header's
    // frame count means nothing without the length to check it against, and
    // the alternative -- a binary search of short `pread`s for the end -- is a
    // clever way to ask `fstat` a question `fstat` answers.
    virtual int64_t size(StoreHandle h, std::string& err) = 0;

    // Free space at `path`'s filesystem, or -1 when it cannot be told. REC is
    // refused under ~92 MB free (design § 7 question 4, answered yes).
    virtual int64_t free_bytes(const std::string& path) = 0;

    // "Make sure blocks exist for [off, off+bytes) **without moving the file's
    // length**" -- Linux `fallocate(FALLOC_FL_KEEP_SIZE)`. This is § 3.4's
    // fallback, and #158 is the measurement that called for it: on the bench
    // eMMC a *growing* medium's per-range commit ran 65-92 ms at p99 against
    // C6's 18 on a file `fio` had already laid out, and slowing the commits
    // down fifty times barely moved it -- the cost is in growing the file, not
    // in the rate. So the I/O thread keeps a lead of allocated blocks ahead of
    // its write head and the commit writes into blocks that already exist.
    //
    // **The length must not move.** Repair on open (§ 3.5) compares the file's
    // length with what its header claims and treats an excess as audio a cut
    // left behind; a lead that grew the length would look to it like exactly
    // that. `FALLOC_FL_KEEP_SIZE` is what keeps the two apart.
    //
    // False + `err` when the filesystem cannot do it -- which is not an error
    // a commit fails on: the medium simply grows the way it did before. The
    // default is that answer, so a store only implements this if it can.
    virtual bool allocate(StoreHandle, int64_t, int64_t, std::string& err) {
        err = "preallocation not supported";
        return false;
    }

    // Did the call that just failed fail for want of space (POSIX `ENOSPC`)?
    // Storage being **full** is the one refusal the tape has to tell from
    // every other: it ends the take with `tape_full {reason:"disk"}` (§ 3.7
    // reason 2) rather than counting an error and carrying on, and an error
    // *string* is not a thing to branch on. Set by the call that failed and
    // cleared by the next one that is tried, so it is only ever read straight
    // after a failure -- **on the thread that made the call** (#825): since
    // #637 a commit's `pwrite` runs beside other threads' store calls, and a
    // flag one of them could clear before the commit asked lost a full card.
    // The default is "no": a store that cannot run out of space never says it
    // has.
    virtual bool no_space() const { return false; }

    // "Not answering right now — come back later." A real card does not say
    // this: it blocks inside the call, which is what the I/O thread is for, so
    // `PosixStore` always answers false. It exists because **offline the I/O
    // thread is the render loop** (`tape_io.hpp` `service()`), and a `FakeStore`
    // that blocked there would stop the audio being rendered rather than
    // stalling the card. So a stall is modelled as the store declining to be
    // used for a while, which leaves the write queue growing and the pool
    // converting to queue — exactly the state T2 and T4 are about. Checked
    // before a commit or a fetch is started, never in the middle of one.
    virtual bool busy() const { return false; }

    // "These bytes are on the device and nobody will read them again": drop
    // their page-cache copy (§ 3.2 hint 2), so the kernel does not hold a
    // second copy of the pool -- 1.9 GB of cache on a 4 GB module is harmless
    // and on the 2 GB one C9 ranks first it is not. A hint, never an error:
    // the default does nothing and no caller checks.
    virtual void advise_dontneed(StoreHandle, int64_t, int64_t) {}
};

// The device's store: POSIX, on a real file, with § 3.2's three decisions --
// buffered I/O (no `O_DIRECT` in v1), `POSIX_FADV_RANDOM` on the fd so the
// kernel's read-ahead does not fight ours, and `POSIX_FADV_DONTNEED` after a
// range is durable. A short `pwrite` is retried, since POSIX allows one.
class PosixStore final : public TapeStore {
public:
    ~PosixStore() override;

    StoreHandle open(const std::string& path, bool create, std::string& err) override;
    void close(StoreHandle h) override;
    int64_t pread(StoreHandle h, void* dst, int64_t bytes, int64_t off, std::string& err) override;
    int64_t pwrite(StoreHandle h, const void* src, int64_t bytes, int64_t off,
                   std::string& err) override;
    bool fdatasync(StoreHandle h, std::string& err) override;
    bool truncate(StoreHandle h, int64_t bytes, std::string& err) override;
    bool rename(const std::string& from, const std::string& to, std::string& err) override;
    bool rename_new(const std::string& from, const std::string& to, std::string& err) override;
    bool sync_dir_of(const std::string& path, std::string& err) override;
    bool remove(const std::string& path, std::string& err) override;
    bool exists(const std::string& path) const override;
    bool same_dir(const std::string& a, const std::string& b) const override;
    int64_t size(StoreHandle h, std::string& err) override;
    int64_t free_bytes(const std::string& path) override;
    bool allocate(StoreHandle h, int64_t off, int64_t bytes, std::string& err) override;
    void advise_dontneed(StoreHandle h, int64_t off, int64_t bytes) override;
    // `errno == ENOSPC` from this thread's last `pwrite` or `fdatasync` --
    // the two calls that can meet a full card, the second because a buffered
    // write is only allocated when it is flushed. Per thread (#825).
    bool no_space() const override;

private:
    int fd_of(StoreHandle h) const;
    // #825: a commit reads the table (`fd_of`) with the tape's lock dropped
    // (#637) while another thread's `open` may grow it, so every touch of it
    // is under `fmu_` -- briefly: the lookup or the slot, never the card call.
    mutable std::mutex fmu_;
    std::vector<int> fds_;                       // handle -> fd (-1 = closed)
};

// The tests' store: every file is a byte vector in RAM, so a take, a repair or
// an ENOSPC is a few microseconds and leaves nothing behind.
//
// **Thread-safe since #167**, which is not decoration: once `TapeIo` stopped
// holding `mu_` across the card, the I/O thread and a control thread really can
// be inside a store at the same moment, which is what `PosixStore` always
// allowed (a `pread` at an explicit offset is atomic and an fd is stable) and
// what this one, a `std::map` of vectors, did not. One mutex over every call.
//
// `set_op_delay` is the other half of that ticket: a store whose reads and
// writes return in microseconds cannot show a lock held across the card, which
// is exactly why `test_compact_cancelled_live` passed on a fault the bench
// found in four cells out of four. The delay is taken **outside** the mutex, so
// it models a card that two threads can be waiting on rather than a global
// stall.
//
// Faults are injected per (operation, handle) and fire once each, in the order
// they were armed. The **deferred** kind the design names -- "the fdatasync for
// range 12 completes 750 blocks later" -- is `stall_blocks` (C1 step 2b): the
// store declines to be used until the tape's block counter has advanced that
// far, which is how a two-second card stall becomes a test that runs in
// milliseconds. Blocks, not seconds, because offline the host has no clock but
// does have rendered blocks (design § 4, "deterministic time").
class FakeStore final : public TapeStore {
public:
    enum class Op { kOpen, kRead, kWrite, kSync, kTruncate, kRename, kAllocate };

    FakeStore() = default;
    // `smu_` (#167) makes the implicit copy deleted, and `test_tape_pool.cpp`
    // copies a store to open a second deck over the same card. So the copy is
    // written out: everything but the mutex, under both.
    FakeStore(const FakeStore& o) { *this = o; }
    FakeStore& operator=(const FakeStore& o);

    StoreHandle open(const std::string& path, bool create, std::string& err) override;
    void close(StoreHandle h) override;
    int64_t pread(StoreHandle h, void* dst, int64_t bytes, int64_t off, std::string& err) override;
    int64_t pwrite(StoreHandle h, const void* src, int64_t bytes, int64_t off,
                   std::string& err) override;
    bool fdatasync(StoreHandle h, std::string& err) override;
    bool truncate(StoreHandle h, int64_t bytes, std::string& err) override;
    bool rename(const std::string& from, const std::string& to, std::string& err) override;
    bool rename_new(const std::string& from, const std::string& to, std::string& err) override;
    bool sync_dir_of(const std::string& path, std::string& err) override;
    bool remove(const std::string& path, std::string& err) override;
    int64_t size(StoreHandle h, std::string& err) override;
    int64_t free_bytes(const std::string& path) override;
    bool allocate(StoreHandle h, int64_t off, int64_t bytes, std::string& err) override;
    bool busy() const override;
    bool no_space() const override;             // per thread, as `PosixStore`'s

    // ---- the test's handles -------------------------------------------------
    // The tape's rendered-block counter (`Tape::blocks()`), which is this
    // store's clock. Without one a stall is measured in service() turns.
    void set_clock(const std::atomic<int64_t>* blocks) { clock_ = blocks; }
    // Stop answering for `blocks` of them, from now.
    void stall_blocks(int64_t blocks);
    // Make every `pread`/`pwrite` take this long (#167). Microseconds, so a
    // test that wants a card's hundreds of milliseconds can have them in a
    // millisecond of wall clock and still be a hundred times slower than the
    // bookkeeping it is competing with.
    void set_op_delay(std::chrono::microseconds d) {
        delay_us_.store(int64_t(d.count()), std::memory_order_relaxed);
    }
    // How many turns a caller was told to come back later.
    int64_t stalls() const { return stalls_; }
    // The next `n` calls to `op` fail with `err`. Counting down rather than a
    // flag so "the fdatasync of range 7 fails once" is expressible. An empty
    // `err` queues `n` calls that **succeed**, so "the third rename fails" is
    // `fail(kRename, "", 2); fail(kRename, "EXDEV")` (#157's rollback test).
    void fail(Op op, const std::string& err, int n = 1);
    void set_free_bytes(int64_t b) { free_ = b; }
    // The bytes of a file as they would be on the card, for the checksum and
    // WAV-parse assertions (T1, T8). Empty when the file does not exist.
    const std::vector<uint8_t>& bytes(const std::string& path) const;
    // The same bytes, copied under the lock. `bytes()` hands out a reference
    // into the map, which is safe with the I/O thread stopped -- which is how
    // every assertion in the suite reads it -- and is not safe while a
    // compaction is running (#167). Ask for the copy when one is.
    std::vector<uint8_t> bytes_copy(const std::string& path) const;
    bool exists(const std::string& path) const override;
    // How many bytes of each file have been through a completed `fdatasync` --
    // i.e. what survives a power cut. Written-but-unsynced bytes are in
    // `bytes()` and not here, which is the difference T8 exists to see.
    int64_t durable_bytes(const std::string& path) const;
    // Under the lock (#542): a test reads these while a compaction thread is
    // writing, and an unlocked `int64_t` read beside `++writes_` is a data race.
    int64_t syncs() const { std::lock_guard<std::mutex> g(smu_); return syncs_; }
    // The folders holding a rename no `sync_dir_of` has made durable since
    // (#591): what a power cut could still undo. Empty = every rename stands.
    std::vector<std::string> unsynced_dirs() const {
        std::lock_guard<std::mutex> g(smu_);
        return std::vector<std::string>(unsynced_dirs_.begin(), unsynced_dirs_.end());
    }
    int64_t writes() const { std::lock_guard<std::mutex> g(smu_); return writes_; }
    // A latch on the card (#542): the `n`-th `pwrite` from now (1 = the next)
    // stops inside the call -- before it takes `smu_` or counts, where
    // `set_op_delay` sleeps -- until `release_write()`. `wait_parked` returns
    // true once it has stopped there, false if no write got that far within
    // `limit` (a bound for a broken test, not a measurement). So a test can ask
    // what another thread may do while the card has the caller, and the answer
    // does not depend on how fast the scheduler was, which a delay's does.
    void park_write(int n);
    // The same latch on the `n`-th `fdatasync` (#637), so a test can hold a
    // commit inside its sync and ask what the control thread can still do.
    // `wait_parked` and `release_write` serve both.
    void park_sync(int n);
    bool wait_parked(std::chrono::milliseconds limit);
    void release_write();
    // The lead (#158): how many `allocate` calls a file took and how far past
    // byte 0 they reached. `allocate` deliberately does **not** touch
    // `bytes()` or `size()`, so a test can see that the lead left the file's
    // length -- the thing repair on open reads -- exactly where the take put
    // it.
    int64_t allocs(const std::string& path) const;
    int64_t allocated_to(const std::string& path) const;

private:
    struct File {
        std::vector<uint8_t> data;
        int64_t durable = 0;
        int64_t alloc_to = 0;                // the lead's high-water, in bytes
        int64_t allocs = 0;
    };
    bool take_fault(Op op, std::string& err);

    void nap() const;                        // `delay_`, outside `smu_`
    void park_point(bool sync = false);      // `park_write`/`park_sync`, outside `smu_`

    mutable std::mutex smu_;                 // every call below is under it
    std::atomic<int64_t> delay_us_{0};       // `set_op_delay`, read outside `smu_`
    std::map<std::string, File> files_;
    std::vector<std::string> open_;          // handle -> path ("" = closed)
    std::map<Op, std::vector<std::string>> faults_;
    std::set<std::string> unsynced_dirs_;    // `unsynced_dirs`
    int64_t free_ = int64_t(64) << 30;       // 64 GiB unless a test says less
    int64_t syncs_ = 0, writes_ = 0;
    const std::atomic<int64_t>* clock_ = nullptr;
    int64_t busy_until_ = -1;                // a block number, or -1
    mutable int64_t busy_turns_ = 0;         // service turns without a clock
    mutable int64_t stalls_ = 0;
    // `park_write`'s, on a mutex of their own so a parked write holds neither
    // `smu_` nor this one. Not copied: a copy is a second card, not a second
    // caller waiting on this one.
    std::mutex pmu_;
    std::condition_variable pcv_;
    int  park_in_ = 0;                       // writes left until the parked one
    int  park_sync_in_ = 0;                  // syncs left until the parked one (#637)
    bool parked_ = false, released_ = false;
};

}  // namespace arb::host
