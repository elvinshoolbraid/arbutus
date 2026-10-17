// keys_thread.hpp — `arb-keys`, the audio host's key thread (#638,
// device-os-spec.md § 1.3.4): the ATmega's port, its RESET# line, keys.sock
// and one poll() around a KeysReader. Started only with `--keys PORT`.
#pragma once

#include <cstdint>
#include <string>
#include <thread>

#include "keys_reader.hpp"

namespace arb::host {

class AudioHost;

class KeysThread final : KeysSink {
public:
    struct Options {
        std::string port;          // --keys
        std::string socket;        // --keys-socket, or beside --socket
        bool probe = false;        // --key-probe
    };

    explicit KeysThread(AudioHost& host) : host_(host), reader_(*this) {}
    ~KeysThread() { stop(); }

    // Binds keys.sock and starts the thread (which opens the port and
    // handshakes on its own). False and `err` only when the socket cannot be
    // bound; a missing port is the thread's to retry.
    bool start(const Options& o, std::string& err);
    // Releases what it holds, closes everything, joins. Idempotent.
    void stop();
    // The exit line (§ 1.3.4 part 4), once stopped.
    std::string summary() const;

private:
    bool note(int note, bool on, float vel, int32_t probe, int64_t t_ns) override;
    bool ui(const char* line, std::size_t n) override;
    void port(const uint8_t* data, std::size_t n) override;
    void log(const char* line) override;

    void run();
    void realtime();
    bool open_port(int64_t now);
    void close_port();
    bool handshake(bool reset_first);
    bool wait_pong(int64_t until);
    void reset_pulse();
    void drop_ui(const char* why);
    bool wait(int ms);             // false = asked to stop
    static int64_t now_ns();

    AudioHost& host_;
    // KeysReader's sink is this object, and the reader is constructed with it:
    // keep `reader_` after every member the sink calls touch.
    Options o_;
    int stop_r_ = -1, stop_w_ = -1;
    int listen_fd_ = -1, ui_fd_ = -1, port_fd_ = -1, reset_fd_ = -1;
    int64_t reopen_at_ = 0;
    bool reopen_logged_ = false;   // a missing port is said once per outage
    bool stopped_ = true;
    std::thread thread_;
    KeysReader reader_;
};

}  // namespace arb::host
