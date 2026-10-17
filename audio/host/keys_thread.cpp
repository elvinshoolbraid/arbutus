// keys_thread.cpp — see keys_thread.hpp. The rules are KeysReader's; this
// file is the file descriptors and the waiting around them.
#include "keys_thread.hpp"

#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>

#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <sched.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "audio_host.hpp"
#include "keys_port.hpp"

namespace arb::host {

namespace {

constexpr int kFifoPriority = 70;             // below the audio thread's 80
constexpr int64_t kMs = 1'000'000;
constexpr int64_t kReopenNs = 2000 * kMs;     // a lost or missing port, retried
constexpr int64_t kPongNs = 500 * kMs;        // § 6.2.3 step 3
constexpr int kPingAttempts = 3;
constexpr int kResetLowMs = 10, kResetSettleMs = 100;

void nonblocking(int fd) { ::fcntl(fd, F_SETFL, ::fcntl(fd, F_GETFL) | O_NONBLOCK); }
void cloexec(int fd) { ::fcntl(fd, F_SETFD, FD_CLOEXEC); }

}  // namespace

int64_t KeysThread::now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

bool KeysThread::start(const Options& o, std::string& err) {
    if (!stopped_) return true;
    o_ = o;
    reader_.set_probe(o.probe);
    std::signal(SIGPIPE, SIG_IGN);   // a UI gone mid-write is a return value

    ::unlink(o_.socket.c_str());
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (o_.socket.size() >= sizeof addr.sun_path) {
        err = "keys: socket path is too long: " + o_.socket;
        return false;
    }
    std::strncpy(addr.sun_path, o_.socket.c_str(), sizeof addr.sun_path - 1);
    listen_fd_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (listen_fd_ < 0 || ::bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) < 0 ||
        ::listen(listen_fd_, 2) < 0) {
        err = "keys: " + o_.socket + ": " + std::strerror(errno);
        if (listen_fd_ >= 0) ::close(listen_fd_);
        listen_fd_ = -1;
        return false;
    }
    // arbutus-ui runs as the same user (`arbutus`); 0660 keeps it there.
    ::chmod(o_.socket.c_str(), 0660);
    cloexec(listen_fd_);
    nonblocking(listen_fd_);

    int p[2];
    if (::pipe(p) != 0) {
        err = std::string("keys: pipe: ") + std::strerror(errno);
        ::close(listen_fd_);
        listen_fd_ = -1;
        return false;
    }
    stop_r_ = p[0];
    stop_w_ = p[1];
    cloexec(stop_r_);
    cloexec(stop_w_);

    std::string why;
    reset_fd_ = keys_open_reset_line(&why);
    if (reset_fd_ < 0) {
        std::printf("keys: no RESET# line on GPIO%d (%s) — PING only\n", kResetGpio, why.c_str());
        std::fflush(stdout);
    }
    std::printf("keys: the ATmega on %s, the UI on %s\n", o_.port.c_str(), o_.socket.c_str());
    std::fflush(stdout);
    stopped_ = false;
    thread_ = std::thread([this] { run(); });
    return true;
}

void KeysThread::stop() {
    if (stopped_) return;
    const char b = 's';
    if (::write(stop_w_, &b, 1) < 0) {}   // a full pipe already says stop
    if (thread_.joinable()) thread_.join();
    ::close(stop_r_);
    ::close(stop_w_);
    stop_r_ = stop_w_ = -1;
    if (listen_fd_ >= 0) ::close(listen_fd_);
    listen_fd_ = -1;
    ::unlink(o_.socket.c_str());
    if (reset_fd_ >= 0) ::close(reset_fd_);
    reset_fd_ = -1;
    stopped_ = true;
}

std::string KeysThread::summary() const {
    char line[320];
    reader_.summary(line, sizeof line);
    return line;
}

// ---- KeysSink ------------------------------------------------------------------

bool KeysThread::note(int note, bool on, float vel, int32_t probe, int64_t t_ns) {
    return host_.push_key_note(note, on, vel, probe, t_ns);
}

bool KeysThread::ui(const char* line, std::size_t n) {
    if (ui_fd_ < 0) return false;
    // Non-blocking: EAGAIN (thousands of events unread) or a partial line is
    // a wedged UI, and a FIFO thread may not wait for it (§ 1.3.4 part 3).
    const ssize_t w = ::send(ui_fd_, line, n, 0);
    return w == ssize_t(n);
}

void KeysThread::port(const uint8_t* data, std::size_t n) {
    if (port_fd_ < 0) return;
    // A few bytes to a port opened non-blocking: the UART's FIFO takes them.
    if (::write(port_fd_, data, n) != ssize_t(n)) {
        std::printf("keys: a write to the ATmega failed (%s)\n", std::strerror(errno));
        std::fflush(stdout);
    }
}

void KeysThread::log(const char* line) {
    std::printf("%s\n", line);
    std::fflush(stdout);
}

// ---- the thread --------------------------------------------------------------------

void KeysThread::realtime() {
#if defined(__linux__)
    pthread_setname_np(pthread_self(), "arb-keys");
#elif defined(__APPLE__)
    pthread_setname_np("arb-keys");
#endif
    sched_param sp{};
    sp.sched_priority = kFifoPriority;
    const int e = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    // Slower keys, never no keys: a refusal is said once and the thread runs
    // SCHED_OTHER (§ 1.3.4 part 2).
    if (e != 0)
        std::printf("keys: SCHED_FIFO %d refused (%s), running SCHED_OTHER\n", kFifoPriority,
                    std::strerror(e));
    else
        std::printf("keys: SCHED_FIFO %d\n", kFifoPriority);
    std::fflush(stdout);
}

bool KeysThread::open_port(int64_t now) {
    std::string why;
    port_fd_ = keys_open_port(o_.port.c_str(), &why);
    if (port_fd_ < 0) {
        reopen_at_ = now + kReopenNs;
        if (!reopen_logged_) {
            std::printf("keys: %s cannot be opened: %s\n", o_.port.c_str(), why.c_str());
            std::fflush(stdout);
            reopen_logged_ = true;
        }
        reader_.port_lost(now);
        return false;
    }
    reopen_logged_ = false;
    if (!why.empty()) {
        std::printf("keys: %s\n", why.c_str());
        std::fflush(stdout);
    }
    return true;
}

void KeysThread::close_port() {
    if (port_fd_ >= 0) ::close(port_fd_);
    port_fd_ = -1;
}

// Sleeps `ms` unless asked to stop. False = stop.
bool KeysThread::wait(int ms) {
    pollfd p{stop_r_, POLLIN, 0};
    return ::poll(&p, 1, ms) == 0;
}

void KeysThread::reset_pulse() {
    if (reset_fd_ < 0) return;
    if (!keys_set_reset_line(reset_fd_, false)) {
        std::printf("keys: RESET# pulse failed: %s\n", std::strerror(errno));
        std::fflush(stdout);
        return;
    }
    wait(kResetLowMs);
    keys_set_reset_line(reset_fd_, true);
}

// Reads the port until a PONG or `until`; false on stop, a lost port or time.
bool KeysThread::wait_pong(int64_t until) {
    uint8_t buf[256];
    while (!reader_.pong()) {
        const int64_t left = until - now_ns();
        if (left <= 0) return false;
        pollfd p[2] = {{stop_r_, POLLIN, 0}, {port_fd_, POLLIN, 0}};
        if (::poll(p, 2, int(left / kMs) + 1) < 0 && errno != EINTR) return false;
        if (p[0].revents) return false;
        if (!p[1].revents) continue;
        const ssize_t n = ::read(port_fd_, buf, sizeof buf);
        if (n > 0) reader_.feed(buf, std::size_t(n), now_ns());
        else if (n == 0 || (errno != EAGAIN && errno != EINTR)) return false;
    }
    return true;
}

// § 6.2.3 with § 1.3.4's one change: on a start, PING first; the full reset
// sequence only if that goes unanswered, or when the watchdog asks.
bool KeysThread::handshake(bool reset_first) {
    reader_.handshake_begin();
    bool ok = false;
    if (!reset_first) {
        port(reinterpret_cast<const uint8_t*>("P"), 1);
        ok = wait_pong(now_ns() + kPongNs);
    }
    for (int i = 0; !ok && i < kPingAttempts && port_fd_ >= 0; ++i) {
        reset_pulse();
        if (!wait(kResetSettleMs)) break;
        reader_.handshake_begin();
        port(reinterpret_cast<const uint8_t*>("P"), 1);
        ok = wait_pong(now_ns() + kPongNs);
        if (!ok) {
            std::printf("keys: no PONG (attempt %d/%d)\n", i + 1, kPingAttempts);
            std::fflush(stdout);
        }
    }
    reader_.handshake_end(ok, now_ns());
    return ok;
}

void KeysThread::drop_ui(const char* why) {
    reader_.ui_drop(why);
    if (ui_fd_ >= 0) ::close(ui_fd_);
    ui_fd_ = -1;
}

void KeysThread::run() {
    realtime();
    if (open_port(now_ns())) handshake(false);
    uint8_t buf[256];
    for (;;) {
        int64_t now = now_ns();
        if (port_fd_ < 0 && now >= reopen_at_ && open_port(now)) handshake(false);
        now = now_ns();
        // An off the ring refused, retried as the audio thread drains it (#827).
        reader_.retry_offs(now);
        int64_t due = port_fd_ >= 0 ? reader_.next_deadline_ns() : reopen_at_;
        if (reader_.offs_due() && due > now + KeysReader::kRetryNs) due = now + KeysReader::kRetryNs;
        const int64_t left = due - now;
        const int timeout = left <= 0 ? 0 : int(left / kMs) + 1;

        pollfd p[4] = {{stop_r_, POLLIN, 0},
                       {port_fd_, POLLIN, 0},
                       {listen_fd_, POLLIN, 0},
                       {ui_fd_, POLLIN, 0}};
        if (::poll(p, 4, timeout) < 0 && errno != EINTR) break;
        if (p[0].revents) break;
        now = now_ns();

        if (port_fd_ >= 0 && p[1].revents) {
            const ssize_t n = ::read(port_fd_, buf, sizeof buf);
            if (n > 0) {
                reader_.feed(buf, std::size_t(n), now);
            } else if (n == 0 || (errno != EAGAIN && errno != EINTR)) {
                std::printf("keys: the ATmega's port closed (%s) — reopening every 2 s\n",
                            n == 0 ? "end of file" : std::strerror(errno));
                std::fflush(stdout);
                close_port();
                reader_.port_lost(now);
                reopen_at_ = now + kReopenNs;
            }
        }
        // The accept runs before the old UI's read, on purpose: a restarted UI
        // admitted here drops the old socket unread, so a `send` it queued is
        // dropped, not written to the ATmega (device-os-spec.md § 1.3.3; the
        // order host_keys_replace.py row 3b pins).
        if (p[2].revents) {
            const int fd = ::accept(listen_fd_, nullptr, nullptr);
            if (fd >= 0) {
                // The newest UI wins: a restarted UI whose old socket has not
                // closed yet is still *the* UI.
                if (ui_fd_ >= 0) drop_ui("a new UI connected");
                cloexec(fd);
                nonblocking(fd);
                ui_fd_ = fd;
                reader_.ui_connect();
            }
        }
        if (ui_fd_ >= 0 && p[3].fd == ui_fd_ && p[3].revents) {
            const ssize_t n = ::read(ui_fd_, buf, sizeof buf);
            if (n > 0) reader_.ui_feed(buf, std::size_t(n));
            else if (n == 0 || (errno != EAGAIN && errno != EINTR)) drop_ui("the UI went away");
        }
        // A write the UI did not take dropped it inside the reader.
        if (ui_fd_ >= 0 && !reader_.ui_connected()) {
            ::close(ui_fd_);
            ui_fd_ = -1;
        }
        if (port_fd_ >= 0 && reader_.tick(now_ns()) == KeysReader::Watchdog::Reinit) handshake(true);
    }
    // Stopping (§ 1.3.4 part 4): let go of what is held, close the UI and the port.
    drop_ui("arbutus-audio stopping");
    reader_.release_notes();
    // An off the ring still refuses gets 100 ms of the audio thread's draining.
    for (int i = 0; i < 100 && reader_.offs_due(); ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        reader_.retry_offs(now_ns());
    }
    close_port();
}

}  // namespace arb::host
