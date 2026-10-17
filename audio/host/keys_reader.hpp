// keys_reader.hpp — the key thread's state machine (#638, device-os-spec.md
// § 1.3.4 part 2): the ATmega's lines in, notes on a ring and events to the
// UI out. No file descriptors and no clock of its own: the thread (step 4)
// owns the port, the GPIO line, keys.sock and poll(), feeds bytes and the time
// in here, and implements KeysSink. Every rule below is `arbutus_os/
// keys_service.py`'s and `fastnote.FastNotes`'s, moved, not changed (§ 1.3.3).
//
// Nothing here allocates after construction: fixed line buffers, snprintf into
// the stack, the sink's virtual calls. tests/test_keys_reader.cpp counts
// operator new over 10 000 lines to keep it that way.
#pragma once

#include <cstddef>
#include <cstdint>

#include "keys_matrix.hpp"

namespace arb::host {

// What the state machine drives. The thread implements it over the ring,
// the UI's socket and the port; the test implements it over arrays.
struct KeysSink {
    // A note for the audio thread (Ck::Note). `probe` >= 0 is the bench stamp
    // (PROTOCOL.md § Timing) taken at `t_ns`, the parse. False = the ring is
    // full: the note did not go.
    virtual bool note(int note, bool on, float vel, int32_t probe, int64_t t_ns) = 0;
    // One line for the UI, newline included. False = it did not go whole
    // (EAGAIN, a partial write, a closed socket): the UI is treated as gone.
    virtual bool ui(const char* line, std::size_t n) = 0;
    // Bytes for the ATmega.
    virtual void port(const uint8_t* data, std::size_t n) = 0;
    // A journal line, on edges only (never per key).
    virtual void log(const char* line) = 0;

protected:
    ~KeysSink() = default;
};

class KeysReader {
public:
    static constexpr int64_t kIdlePingNs = 2'000'000'000;   // § 6.2.3's watchdog
    static constexpr int kMissedPongs = 3;
    static constexpr std::size_t kLineMax = 64;              // § 1.3.4 part 2
    static constexpr std::size_t kUiLineMax = 256;
    static constexpr uint8_t kDebounceReads = 4;             // `B 4`, uart_input.py

    // What the snapshot (§ 1.3.1) lets a key do; the UI sends it as `snap`.
    struct Snapshot {
        bool sound = false;
        int transpose = 0;
        float velocity = 1.0f;
    };

    struct Counters {
        long sounded = 0;        // notes pushed, presses and releases
        long forwarded = 0;      // key and enc events the UI took
        long ui_drops = 0;
        long refused_quiet = 0;  // presses the snapshot said no to
        long ring_full = 0;      // presses and releases the ring refused
        long malformed = 0;      // lines that did not parse (#613)
    };

    enum class Watchdog { None, Reinit };

    explicit KeysReader(KeysSink& sink, bool probe = false) : sink_(sink), probe_(probe) {
        for (auto& row : held_)
            for (int16_t& h : row) h = -1;
        for (auto& row : off_due_)
            for (int16_t& h : row) h = -1;
    }
    KeysReader(const KeysReader&) = delete;
    KeysReader& operator=(const KeysReader&) = delete;

    // --key-probe (bench only): a press carries uart_input.py's stamp.
    void set_probe(bool on) { probe_ = on; }

    // ---- the port ----------------------------------------------------------
    // Bytes as read(); any split. `now_ns` is CLOCK_MONOTONIC at the read.
    void feed(const uint8_t* data, std::size_t n, int64_t now_ns);

    // The handshake is the thread's (it writes `P` and waits); these bracket
    // it. Between them a PONG sets pong() and KEYBOARD_READY is expected.
    void handshake_begin();
    bool pong() const { return pong_; }
    // The outcome: configures and says KEYBOARD OK, or says NO KEYBOARD.
    void handshake_end(bool ok, int64_t now_ns);
    // The port read 0 or failed: nothing held is held now, NO KEYBOARD.
    void port_lost(int64_t now_ns);
    // § 6.2.3 (4): debounce, backlight (the UI's last `L`), scan, encoders.
    void configure();

    // Quiet for 2 s -> `P`; three unanswered -> every held key released and
    // Reinit, which the thread answers with the full reset handshake.
    Watchdog tick(int64_t now_ns);
    int64_t next_deadline_ns() const;

    // ---- the UI ------------------------------------------------------------
    // A new connection (the thread has dropped any older one first).
    void ui_connect();
    // The connection is gone: every note this reader holds is released and
    // nothing sounds until the next UI's first `snap`. `why` is journalled.
    void ui_drop(const char* why);
    bool ui_connected() const { return ui_; }
    // Bytes from the UI: `snap` and `send` lines.
    void ui_feed(const uint8_t* data, std::size_t n);

    // ---- note-offs the ring refused ---------------------------------------------
    // A held note is never forgotten before its off is on the ring (#827): an
    // off the ring refused waits here, and the thread retries it each time
    // round its loop, within kRetryNs while any wait. A press is refused while
    // one does, so an off can never land after a later on of its note.
    static constexpr int64_t kRetryNs = 1'000'000;
    void retry_offs(int64_t now_ns);
    bool offs_due() const { return n_off_due_ > 0; }

    // ---- the exit line ------------------------------------------------------
    // Releases every held note (the thread is stopping).
    void release_notes();
    const Counters& counters() const { return n_; }
    const Snapshot& snapshot() const { return snap_; }
    uint8_t brightness() const { return brightness_; }
    bool ok() const { return status_ == Status::Ok; }
    // "keys fast notes: …", the same shape as arbutus-keys's line.
    int summary(char* buf, std::size_t n) const;

private:
    enum class Status { Unknown, Ok, NoKeyboard };

    void line(char* s, std::size_t n, int64_t now_ns);
    void key(int r, int c, bool pressed, int64_t now_ns);
    bool sound(int r, int c, bool pressed, int32_t probe, int64_t now_ns);
    void forward_key(int r, int c, bool pressed, bool sounded, int32_t probe, int64_t now_ns);
    bool to_ui(const char* s, int n);
    void release_held(int64_t now_ns);
    bool note_off(int r, int c, int note, int64_t now_ns);
    void unrequested_reset(int64_t now_ns);
    void set_status(Status s);
    void send_status();
    void ui_line(char* s, std::size_t n);
    void say(const char* fmt, ...) __attribute__((format(printf, 2, 3)));

    static constexpr int R = keys::kRows, C = keys::kCols;

    KeysSink& sink_;
    bool probe_;

    char buf_[kLineMax + 1]{};
    std::size_t len_ = 0;
    bool overflow_ = false;
    char ubuf_[kUiLineMax + 1]{};
    std::size_t ulen_ = 0;
    bool uoverflow_ = false;

    bool down_[R][C]{};          // held at the ATmega (named positions only)
    bool sent_down_[R][C]{};     // presses this UI connection was sent
    int16_t held_[R][C]{};       // the note pushed for a held key, or -1
    int16_t off_due_[R][C]{};    // a released key's off the ring refused, or -1
    int n_off_due_ = 0;

    Snapshot snap_{};
    bool ui_ = false;
    bool snapped_ = false;
    uint8_t brightness_ = 255;
    Status status_ = Status::Unknown;

    bool handshaking_ = false;
    bool pong_ = false;
    int missed_ = 0;
    int64_t last_line_ns_ = 0;
    int64_t last_ping_ns_ = 0;

    Counters n_{};
};

}  // namespace arb::host
