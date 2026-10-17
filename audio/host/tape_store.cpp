#include "tape_store.hpp"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <thread>

#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>

namespace arb::host {

namespace {
const std::vector<uint8_t> kNoBytes;

// `no_space()`'s answer, one per thread (#825): the thread whose `pwrite` or
// `fdatasync` met a full card asks straight after, and only its own call may
// have set or cleared what it reads. One for every store on the thread, which
// is the same thing: the question is only asked about the call just made.
thread_local bool t_no_space = false;

// `/p/`, `/p//` and `/p/.` are `/p`. `lexically_normal` keeps a trailing
// separator (`/p/` stays `/p/`), so it goes by hand; `/` stays `/`.
std::string normal_dir(const std::string& d) {
    std::string n = std::filesystem::path(d).lexically_normal().string();
    while (n.size() > 1 && n.back() == '/') n.pop_back();
    return n;
}

// The folder a file is in, normalised the same way, so `/p/tape-1.wav` and
// `/p//tape-1.wav` name one folder to `FakeStore`'s bookkeeping.
std::string dir_part(const std::string& path) {
    const std::size_t slash = path.find_last_of('/');
    if (slash == std::string::npos) return ".";
    return normal_dir(slash == 0 ? "/" : path.substr(0, slash));
}
}  // namespace

bool TapeStore::same_dir(const std::string& a, const std::string& b) const {
    return normal_dir(a) == normal_dir(b);
}

// #167: the delay is taken with `smu_` down, so two threads waiting on this
// "card" wait in parallel, the way they do on a real one.
FakeStore& FakeStore::operator=(const FakeStore& o) {
    if (this == &o) return *this;
    std::scoped_lock lk(smu_, o.smu_);
    delay_us_.store(o.delay_us_.load(std::memory_order_relaxed), std::memory_order_relaxed);
    files_ = o.files_;
    open_ = o.open_;
    faults_ = o.faults_;
    unsynced_dirs_ = o.unsynced_dirs_;
    free_ = o.free_;
    syncs_ = o.syncs_;
    writes_ = o.writes_;
    clock_ = o.clock_;                      // the caller re-points it; this is what it was
    busy_until_ = o.busy_until_;
    busy_turns_ = o.busy_turns_;
    stalls_ = o.stalls_;
    return *this;
}

void FakeStore::nap() const {
    const int64_t us = delay_us_.load(std::memory_order_relaxed);
    if (us > 0) std::this_thread::sleep_for(std::chrono::microseconds(us));
}

void FakeStore::park_write(int n) {
    std::lock_guard<std::mutex> g(pmu_);
    park_in_ = n;
    parked_ = released_ = false;
}

bool FakeStore::wait_parked(std::chrono::milliseconds limit) {
    std::unique_lock<std::mutex> lk(pmu_);
    return pcv_.wait_for(lk, limit, [this] { return parked_; });
}

void FakeStore::release_write() {
    std::lock_guard<std::mutex> g(pmu_);
    released_ = true;
    pcv_.notify_all();
}

void FakeStore::park_sync(int n) {
    std::lock_guard<std::mutex> g(pmu_);
    park_sync_in_ = n;
    parked_ = released_ = false;
}

void FakeStore::park_point(bool sync) {
    std::unique_lock<std::mutex> lk(pmu_);
    int& in = sync ? park_sync_in_ : park_in_;
    if (in <= 0 || --in > 0) return;
    parked_ = true;
    pcv_.notify_all();
    pcv_.wait(lk, [this] { return released_; });
}

bool FakeStore::take_fault(Op op, std::string& err) {   // `smu_` held
    auto it = faults_.find(op);
    if (it == faults_.end() || it->second.empty()) return false;
    err = it->second.front();
    it->second.erase(it->second.begin());
    return !err.empty();                             // "" is a call let through (`fail`)
}

void FakeStore::fail(Op op, const std::string& err, int n) {
    std::lock_guard<std::mutex> g(smu_);
    for (int i = 0; i < n; ++i) faults_[op].push_back(err);
}

StoreHandle FakeStore::open(const std::string& path, bool create, std::string& err) {
    std::lock_guard<std::mutex> g(smu_);
    if (take_fault(Op::kOpen, err)) return kNoHandle;
    if (!create && files_.find(path) == files_.end()) {
        err = "no such file: " + path;
        return kNoHandle;
    }
    files_.emplace(path, File{});
    // Reuse a closed slot so a long session does not grow the table.
    for (std::size_t i = 0; i < open_.size(); ++i) {
        if (open_[i].empty()) {
            open_[i] = path;
            return StoreHandle(i);
        }
    }
    open_.push_back(path);
    return StoreHandle(open_.size() - 1);
}

void FakeStore::close(StoreHandle h) {
    std::lock_guard<std::mutex> g(smu_);
    if (h >= 0 && std::size_t(h) < open_.size()) open_[std::size_t(h)].clear();
}

int64_t FakeStore::pread(StoreHandle h, void* dst, int64_t bytes, int64_t off, std::string& err) {
    nap();
    std::lock_guard<std::mutex> g(smu_);
    if (take_fault(Op::kRead, err)) return -1;
    if (h < 0 || std::size_t(h) >= open_.size() || open_[std::size_t(h)].empty()) {
        err = "bad handle";
        return -1;
    }
    if (bytes < 0 || off < 0) { err = "negative pread"; return -1; }
    const File& f = files_[open_[std::size_t(h)]];
    const int64_t have = int64_t(f.data.size());
    if (off >= have) return 0;                       // past the end: short, not an error
    const int64_t n = std::min(bytes, have - off);
    std::memcpy(dst, f.data.data() + off, std::size_t(n));
    return n;
}

int64_t FakeStore::pwrite(StoreHandle h, const void* src, int64_t bytes, int64_t off,
                          std::string& err) {
    park_point();
    nap();
    std::lock_guard<std::mutex> g(smu_);
    t_no_space = false;
    if (take_fault(Op::kWrite, err)) return -1;
    if (h < 0 || std::size_t(h) >= open_.size() || open_[std::size_t(h)].empty()) {
        err = "bad handle";
        return -1;
    }
    if (bytes < 0 || off < 0) { err = "negative pwrite"; return -1; }
    File& f = files_[open_[std::size_t(h)]];
    if (off + bytes > free_ + int64_t(f.data.size())) {
        // A full card, as the tape has to see it: the refusal is `ENOSPC` and
        // `no_space()` says so, which is what `tape_full {reason:"disk"}` is
        // branched on (T10). `set_free_bytes` is the whole fault injection --
        // no separate switch, because a store that says it has 8 MB free and
        // then takes 40 is not a card.
        err = "ENOSPC";
        t_no_space = true;
        return -1;
    }
    if (off + bytes > int64_t(f.data.size())) {
        // Growing the file spends free space, and `free_` is not refunded
        // until something truncates: a card that can refuse one write and then
        // accept the next for ever is not a card, and T10 is exactly the take
        // that runs the medium out part way through.
        free_ -= off + bytes - int64_t(f.data.size());
        f.data.resize(std::size_t(off + bytes), 0);
    }
    std::memcpy(f.data.data() + off, src, std::size_t(bytes));
    ++writes_;
    return bytes;
}

bool FakeStore::fdatasync(StoreHandle h, std::string& err) {
    park_point(true);
    std::lock_guard<std::mutex> g(smu_);
    if (take_fault(Op::kSync, err)) return false;
    if (h < 0 || std::size_t(h) >= open_.size() || open_[std::size_t(h)].empty()) {
        err = "bad handle";
        return false;
    }
    File& f = files_[open_[std::size_t(h)]];
    f.durable = int64_t(f.data.size());
    ++syncs_;
    return true;
}

bool FakeStore::truncate(StoreHandle h, int64_t bytes, std::string& err) {
    std::lock_guard<std::mutex> g(smu_);
    if (take_fault(Op::kTruncate, err)) return false;
    if (h < 0 || std::size_t(h) >= open_.size() || open_[std::size_t(h)].empty()) {
        err = "bad handle";
        return false;
    }
    if (bytes < 0) { err = "negative truncate"; return false; }
    File& f = files_[open_[std::size_t(h)]];
    free_ += int64_t(f.data.size()) - bytes;   // shrinking gives the space back
    f.data.resize(std::size_t(bytes), 0);
    f.durable = std::min(f.durable, bytes);
    return true;
}

// No fault injection: a test that wants a refused open, read, write, sync,
// truncate or rename has one, and arming `kRead` here would make every
// existing read fault fire a turn early -- on the repair rather than on the
// range the test meant.
int64_t FakeStore::size(StoreHandle h, std::string& err) {
    std::lock_guard<std::mutex> g(smu_);
    if (h < 0 || std::size_t(h) >= open_.size() || open_[std::size_t(h)].empty()) {
        err = "bad handle";
        return -1;
    }
    return int64_t(files_[open_[std::size_t(h)]].data.size());
}

bool FakeStore::rename(const std::string& from, const std::string& to, std::string& err) {
    std::lock_guard<std::mutex> g(smu_);
    if (take_fault(Op::kRename, err)) return false;
    auto it = files_.find(from);
    if (it == files_.end()) { err = "no such file: " + from; return false; }
    File moved = std::move(it->second);
    files_.erase(it);
    files_[to] = std::move(moved);
    for (std::string& p : open_)
        if (p == from) p = to;                       // an open handle follows the rename
    // Both folders' entries changed, and neither is durable until synced.
    unsynced_dirs_.insert(dir_part(from));
    unsynced_dirs_.insert(dir_part(to));
    return true;
}

bool FakeStore::sync_dir_of(const std::string& path, std::string& err) {
    (void)err;
    std::lock_guard<std::mutex> g(smu_);
    unsynced_dirs_.erase(dir_part(path));
    return true;
}

// The fake has no symlinks, so a taken name is a key in the map.
bool FakeStore::rename_new(const std::string& from, const std::string& to, std::string& err) {
    {
        std::lock_guard<std::mutex> g(smu_);
        if (files_.count(to) != 0) { err = "EEXIST"; return false; }
    }
    return rename(from, to, err);
}

bool FakeStore::exists(const std::string& path) const {
    std::lock_guard<std::mutex> g(smu_);
    return files_.count(path) != 0;
}

bool FakeStore::remove(const std::string& path, std::string& err) {
    std::lock_guard<std::mutex> g(smu_);
    auto it = files_.find(path);
    if (it == files_.end()) return true;             // already gone: not an error
    free_ += int64_t(it->second.data.size());
    files_.erase(it);
    (void)err;
    return true;
}

int64_t FakeStore::free_bytes(const std::string&) {
    std::lock_guard<std::mutex> g(smu_);
    return free_;
}

// The lead (#158), and the whole point of it in one line: this records the
// allocation and **does not touch `data`**, because `FALLOC_FL_KEEP_SIZE` does
// not move the file's length and repair on open reads that length.
bool FakeStore::allocate(StoreHandle h, int64_t off, int64_t bytes, std::string& err) {
    std::lock_guard<std::mutex> g(smu_);
    if (take_fault(Op::kAllocate, err)) return false;
    if (h < 0 || std::size_t(h) >= open_.size() || open_[std::size_t(h)].empty()) {
        err = "bad handle";
        return false;
    }
    if (off < 0 || bytes < 0) { err = "negative allocate"; return false; }
    File& f = files_[open_[std::size_t(h)]];
    f.alloc_to = std::max(f.alloc_to, off + bytes);
    ++f.allocs;
    return true;
}

int64_t FakeStore::allocs(const std::string& path) const {
    std::lock_guard<std::mutex> g(smu_);
    auto it = files_.find(path);
    return it == files_.end() ? 0 : it->second.allocs;
}

int64_t FakeStore::allocated_to(const std::string& path) const {
    std::lock_guard<std::mutex> g(smu_);
    auto it = files_.find(path);
    return it == files_.end() ? 0 : it->second.alloc_to;
}

void FakeStore::stall_blocks(int64_t blocks) {
    std::lock_guard<std::mutex> g(smu_);
    if (blocks <= 0) { busy_until_ = -1; busy_turns_ = 0; return; }
    if (clock_) busy_until_ = clock_->load(std::memory_order_acquire) + blocks;
    else { busy_until_ = -1; busy_turns_ = blocks; }
}

bool FakeStore::busy() const {
    std::lock_guard<std::mutex> g(smu_);
    if (busy_until_ >= 0 && clock_) {
        if (clock_->load(std::memory_order_acquire) < busy_until_) { ++stalls_; return true; }
        return false;
    }
    if (busy_turns_ > 0) { --busy_turns_; ++stalls_; return true; }
    return false;
}

const std::vector<uint8_t>& FakeStore::bytes(const std::string& path) const {
    std::lock_guard<std::mutex> g(smu_);
    auto it = files_.find(path);
    return it == files_.end() ? kNoBytes : it->second.data;
}

std::vector<uint8_t> FakeStore::bytes_copy(const std::string& path) const {
    std::lock_guard<std::mutex> g(smu_);
    auto it = files_.find(path);
    return it == files_.end() ? std::vector<uint8_t>() : it->second.data;
}

int64_t FakeStore::durable_bytes(const std::string& path) const {
    std::lock_guard<std::mutex> g(smu_);
    auto it = files_.find(path);
    return it == files_.end() ? 0 : it->second.durable;
}

// ---- PosixStore -------------------------------------------------------------

namespace {
std::string why(const char* what) {
    return std::string(what) + ": " + std::strerror(errno);
}
}  // namespace

bool FakeStore::no_space() const { return t_no_space; }

PosixStore::~PosixStore() {
    for (int fd : fds_)
        if (fd >= 0) ::close(fd);
}

bool PosixStore::no_space() const { return t_no_space; }

int PosixStore::fd_of(StoreHandle h) const {
    std::lock_guard<std::mutex> g(fmu_);
    if (h < 0 || std::size_t(h) >= fds_.size()) return -1;
    return fds_[std::size_t(h)];
}

StoreHandle PosixStore::open(const std::string& path, bool create, std::string& err) {
    const int flags = O_RDWR | (create ? O_CREAT : 0);
    const int fd = ::open(path.c_str(), flags, 0644);
    if (fd < 0) { err = why(path.c_str()); return kNoHandle; }
#if defined(POSIX_FADV_RANDOM)
    // We do our own read-ahead (§ 3.2 hint 3); two read-aheads fight, and the
    // kernel's would fetch a megabyte past every range we ask for.
    ::posix_fadvise(fd, 0, 0, POSIX_FADV_RANDOM);
#endif
    std::lock_guard<std::mutex> g(fmu_);
    for (std::size_t i = 0; i < fds_.size(); ++i)
        if (fds_[i] < 0) { fds_[i] = fd; return StoreHandle(i); }
    fds_.push_back(fd);
    return StoreHandle(fds_.size() - 1);
}

// The slot is freed under `fmu_` and the fd closed after it, so a slow close
// never holds the table.
void PosixStore::close(StoreHandle h) {
    int fd;
    {
        std::lock_guard<std::mutex> g(fmu_);
        if (h < 0 || std::size_t(h) >= fds_.size() || fds_[std::size_t(h)] < 0) return;
        fd = fds_[std::size_t(h)];
        fds_[std::size_t(h)] = -1;
    }
    ::close(fd);
}

int64_t PosixStore::pread(StoreHandle h, void* dst, int64_t bytes, int64_t off, std::string& err) {
    const int fd = fd_of(h);
    if (fd < 0) { err = "bad handle"; return -1; }
    if (bytes < 0 || off < 0) { err = "negative pread"; return -1; }
    int64_t done = 0;
    while (done < bytes) {
        const ssize_t n = ::pread(fd, static_cast<char*>(dst) + done,
                                  std::size_t(bytes - done), off + done);
        if (n < 0) {
            if (errno == EINTR) continue;
            err = why("pread");
            return -1;
        }
        if (n == 0) break;                       // past the end: short, not an error
        done += n;
    }
    return done;
}

int64_t PosixStore::pwrite(StoreHandle h, const void* src, int64_t bytes, int64_t off,
                           std::string& err) {
    t_no_space = false;
    const int fd = fd_of(h);
    if (fd < 0) { err = "bad handle"; return -1; }
    if (bytes < 0 || off < 0) { err = "negative pwrite"; return -1; }
    int64_t done = 0;
    while (done < bytes) {
        // POSIX allows a short write; a half-written range would be a hole in
        // the medium that nothing else would ever go back for.
        const ssize_t n = ::pwrite(fd, static_cast<const char*>(src) + done,
                                   std::size_t(bytes - done), off + done);
        if (n < 0) {
            if (errno == EINTR) continue;
            t_no_space = errno == ENOSPC;
            err = why("pwrite");
            return done > 0 ? done : -1;
        }
        if (n == 0) { err = "pwrite wrote nothing"; return done > 0 ? done : -1; }
        done += n;
    }
    return done;
}

bool PosixStore::fdatasync(StoreHandle h, std::string& err) {
    const int fd = fd_of(h);
    if (fd < 0) { err = "bad handle"; return false; }
#if defined(__APPLE__)
    // macOS `fdatasync` does not reach the drive's own cache; `F_FULLFSYNC`
    // does. The bench is Linux, but the Mac runs these tests.
    // The SDK declares no `fdatasync` at all; `fsync` is the fallback when
    // the filesystem refuses `F_FULLFSYNC`.
    if (::fcntl(fd, F_FULLFSYNC) == 0) return true;
    while (::fsync(fd) != 0) {
#else
    while (::fdatasync(fd) != 0) {
#endif
        if (errno == EINTR) continue;
        // A buffered write is only really allocated when it is flushed, so a
        // full card can arrive here and not at the `pwrite` that filled the
        // page cache. Both doors are watched; `pwrite` cleared the flag, so
        // setting it here cannot be a stale reading of an earlier write.
        t_no_space = errno == ENOSPC;
        err = why("fdatasync");
        return false;
    }
    return true;
}

int64_t PosixStore::size(StoreHandle h, std::string& err) {
    const int fd = fd_of(h);
    if (fd < 0) { err = "bad handle"; return -1; }
    struct stat st {};
    if (::fstat(fd, &st) != 0) { err = why("fstat"); return -1; }
    return int64_t(st.st_size);
}

bool PosixStore::truncate(StoreHandle h, int64_t bytes, std::string& err) {
    const int fd = fd_of(h);
    if (fd < 0) { err = "bad handle"; return false; }
    if (bytes < 0) { err = "negative truncate"; return false; }
    while (::ftruncate(fd, off_t(bytes)) != 0) {
        if (errno == EINTR) continue;
        err = why("ftruncate");
        return false;
    }
    return true;
}

bool PosixStore::rename(const std::string& from, const std::string& to, std::string& err) {
    if (::rename(from.c_str(), to.c_str()) != 0) { err = why("rename"); return false; }
    return true;
}

// The kernel's own no-replace rename, so nothing can take the name between a
// check and the move: `renameat2(RENAME_NOREPLACE)` on Linux (exFAT, ext4 and
// tmpfs all honour it), `renamex_np(RENAME_EXCL)` on a Mac. A filesystem that
// cannot (`EINVAL`) gets the check-then-rename the move did before #484 -- the
// move itself checked every name first, so only a race is left open there.
bool PosixStore::rename_new(const std::string& from, const std::string& to, std::string& err) {
#if defined(__linux__) && defined(RENAME_NOREPLACE)
    if (::renameat2(AT_FDCWD, from.c_str(), AT_FDCWD, to.c_str(), RENAME_NOREPLACE) == 0) return true;
    if (errno != EINVAL && errno != ENOSYS) { err = errno == EEXIST ? "EEXIST" : why("renameat2"); return false; }
#elif defined(__APPLE__) && defined(RENAME_EXCL)
    if (::renamex_np(from.c_str(), to.c_str(), RENAME_EXCL) == 0) return true;
    if (errno != EINVAL && errno != ENOTSUP) { err = errno == EEXIST ? "EEXIST" : why("renamex_np"); return false; }
#endif
    if (exists(to)) { err = "EEXIST"; return false; }
    return rename(from, to, err);
}

// Open the folder read-only and `fsync` it: the only way POSIX has to make a
// rename durable. `O_DIRECTORY` so a path whose folder is gone fails here
// rather than syncing something else.
bool PosixStore::sync_dir_of(const std::string& path, std::string& err) {
    const std::string dir = dir_part(path);
    const int fd = ::open(dir.c_str(), O_RDONLY | O_DIRECTORY);
    if (fd < 0) { err = why(("open " + dir).c_str()); return false; }
    int rc;
    while ((rc = ::fsync(fd)) != 0 && errno == EINTR) {}
    if (rc != 0) err = why(("fsync " + dir).c_str());
    ::close(fd);
    return rc == 0;
}

// `lstat`, not `stat`: a symlink is a taken name even when what it points at
// is not there (#484 finding 2), and `rename` would replace the link.
bool PosixStore::exists(const std::string& path) const {
    struct stat st{};
    return ::lstat(path.c_str(), &st) == 0;
}

// The same folder under two spellings, or through a symlink: the same inode on
// the same device. A folder that cannot be `stat`ed is compared by spelling.
bool PosixStore::same_dir(const std::string& a, const std::string& b) const {
    struct stat sa{}, sb{};
    if (::stat(a.c_str(), &sa) == 0 && ::stat(b.c_str(), &sb) == 0)
        return sa.st_dev == sb.st_dev && sa.st_ino == sb.st_ino;
    return TapeStore::same_dir(a, b);
}

bool PosixStore::remove(const std::string& path, std::string& err) {
    if (::unlink(path.c_str()) != 0 && errno != ENOENT) { err = why("unlink"); return false; }
    return true;
}

int64_t PosixStore::free_bytes(const std::string& path) {
    struct statvfs st {};
    if (::statvfs(path.c_str(), &st) != 0) return -1;
    return int64_t(st.f_bavail) * int64_t(st.f_frsize);
}

// § 3.4's lead (#158). `FALLOC_FL_KEEP_SIZE` allocates the blocks and leaves
// `i_size` alone, which is what keeps the medium's length the take's own
// (§ 3.5, and repair on open compares that length with the header's claim).
// Linux only: `fallocate(2)` is a Linux call, the bench and the device are
// Linux, and on a Mac -- where these tests also run -- the honest answer is
// "cannot", which every caller already handles by growing the file as before.
bool PosixStore::allocate(StoreHandle h, int64_t off, int64_t bytes, std::string& err) {
#if defined(__linux__) && defined(FALLOC_FL_KEEP_SIZE)
    const int fd = fd_of(h);
    if (fd < 0) { err = "bad handle"; return false; }
    if (off < 0 || bytes < 0) { err = "negative allocate"; return false; }
    if (bytes == 0) return true;
    while (::fallocate(fd, FALLOC_FL_KEEP_SIZE, off_t(off), off_t(bytes)) != 0) {
        if (errno == EINTR) continue;
        err = why("fallocate");
        return false;
    }
    return true;
#else
    (void)h; (void)off; (void)bytes;
    err = "fallocate: not this platform";
    return false;
#endif
}

void PosixStore::advise_dontneed(StoreHandle h, int64_t off, int64_t bytes) {
#if defined(POSIX_FADV_DONTNEED)
    const int fd = fd_of(h);
    if (fd >= 0 && off >= 0 && bytes > 0) ::posix_fadvise(fd, off, bytes, POSIX_FADV_DONTNEED);
#else
    (void)h; (void)off; (void)bytes;
#endif
}

}  // namespace arb::host
