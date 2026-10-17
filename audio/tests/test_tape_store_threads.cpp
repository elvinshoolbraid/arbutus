// host/tape_store.cpp's `PosixStore` with two threads in it at once (#825).
// No framework; test_tape.cpp is the pattern.
//
// #637 runs a commit's card calls -- `pwrite`, `fdatasync` -- with the tape's
// lock dropped, so another thread (a stopped `tape_edit`'s `persist_span`, a
// compaction) can be opening a medium in the same store at that moment. Two
// things in `PosixStore` were not made safe for that:
//
//   * **the handle table**: `open` grows `fds_` while the commit's thread reads
//     it in `fd_of`. Built with ThreadSanitizer where the compiler has it
//     (CMakeLists.txt), so the race is a report and a non-zero exit (66), not
//     a crash once in a thousand runs. Seen red on 1eca6a73's store: TSAN's
//     "data race" in `PosixStore::fd_of` against `push_back` in `open`.
//   * **ENOSPC**: `no_space()` was one flag for the store, cleared by every
//     `pwrite`. A commit whose write met a full card, then another thread's
//     write landing before the commit asked, read "not full", and the take was
//     not ended with `tape_full {reason:"disk"}`. The answer is now the asking
//     thread's own last call. Linux only: `/dev/full` is the full card. Seen
//     red on 1eca6a73's store ("no_space() lost").
#include "tape_store.hpp"

#include <condition_variable>
#include <cstdio>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <unistd.h>

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

using arb::host::kNoHandle;
using arb::host::PosixStore;
using arb::host::StoreHandle;

namespace fs = std::filesystem;

// #105: a gate run leaves /tmp as it found it.
struct TmpDir {
    fs::path p;
    TmpDir() {
        p = fs::temp_directory_path() / ("arbutus-storethreads-" + std::to_string(::getpid()));
        std::error_code ec;
        fs::remove_all(p, ec);
        fs::create_directories(p, ec);
    }
    ~TmpDir() {
        std::error_code ec;
        fs::remove_all(p, ec);
    }
};

// A commit's calls on one medium while another thread opens 256 handles (each
// a `push_back`, so the table reallocates under the reader) and closes them.
void test_open_during_commit() {
    TmpDir dir;
    PosixStore store;
    std::string err;
    const std::string medium = (dir.p / "tape-1.wav").string();
    const StoreHandle h = store.open(medium, true, err);
    CHECK(h != kNoHandle, "open: %s", err.c_str());
    if (h == kNoHandle) return;

    std::mutex mu;
    bool done = false;
    int bad = 0;
    std::thread commit([&] {
        std::vector<char> range(4096, 'x');
        std::string e;
        for (int i = 0;; ++i) {
            {
                std::lock_guard<std::mutex> g(mu);
                if (done && i > 0) break;
            }
            if (store.pwrite(h, range.data(), int64_t(range.size()), 4096 * (i % 8), e) !=
                    int64_t(range.size()) ||
                !store.fdatasync(h, e))
                ++bad;
        }
    });
    const std::string other = (dir.p / "tape-2.wav").string();
    std::vector<StoreHandle> opened;
    for (int round = 0; round < 4; ++round) {
        for (int i = 0; i < 256; ++i) {
            const StoreHandle o = store.open(other, true, err);
            if (o != kNoHandle) opened.push_back(o);
        }
        for (StoreHandle o : opened) store.close(o);
        opened.clear();
    }
    {
        std::lock_guard<std::mutex> g(mu);
        done = true;
    }
    commit.join();
    CHECK(bad == 0, "%d of the commit's calls failed beside the opens", bad);
    store.close(h);
}

// The commit's write meets a full card; another thread's write succeeds before
// the commit asks `no_space()`. The commit must still hear "full".
void test_no_space_is_the_callers() {
#if defined(__linux__)
    TmpDir dir;
    PosixStore store;
    std::string err;
    const StoreHandle full = store.open("/dev/full", false, err);
    CHECK(full != kNoHandle, "open /dev/full: %s", err.c_str());
    const StoreHandle ok = store.open((dir.p / "tape-2.wav").string(), true, err);
    CHECK(ok != kNoHandle, "open: %s", err.c_str());
    if (full == kNoHandle || ok == kNoHandle) return;

    std::mutex mu;
    std::condition_variable cv;
    int step = 0;                      // 1: the commit failed; 2: the other wrote
    bool commit_heard = false, other_heard = true;
    std::thread commit([&] {
        char b[16] = {};
        std::string e;
        const int64_t n = store.pwrite(full, b, sizeof b, 4096, e);
        std::unique_lock<std::mutex> lk(mu);
        CHECK(n < 0, "a write to /dev/full returned %lld", (long long)n);
        step = 1;
        cv.notify_all();
        cv.wait(lk, [&] { return step == 2; });
        commit_heard = store.no_space();
    });
    std::thread other([&] {
        {
            std::unique_lock<std::mutex> lk(mu);
            cv.wait(lk, [&] { return step == 1; });
        }
        char b[16] = {};
        std::string e;
        const int64_t n = store.pwrite(ok, b, sizeof b, 0, e);
        std::lock_guard<std::mutex> g(mu);
        CHECK(n == int64_t(sizeof b), "the other write: %s", e.c_str());
        other_heard = store.no_space();
        step = 2;
        cv.notify_all();
    });
    commit.join();
    other.join();
    CHECK(commit_heard, "no_space() lost: the commit's ENOSPC was cleared by another thread's write");
    CHECK(!other_heard, "no_space() leaked the commit's ENOSPC into the other thread's success");
    store.close(full);
    store.close(ok);
#else
    std::printf("skip: no /dev/full here\n");
#endif
}
}  // namespace

int main() {
    test_open_during_commit();
    test_no_space_is_the_callers();
    if (failures) {
        std::printf("tape_store_threads: %d failure(s)\n", failures);
        return 1;
    }
    std::printf("tape_store_threads: ok\n");
    return 0;
}
