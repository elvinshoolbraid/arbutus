// spsc.hpp — the one ring the host's threads talk over.
//
// Lifted out of `audio_host.hpp` when the tape I/O thread (C1 step 2) needed
// the same ring without including the whole host: `tape_io.hpp` is included
// *by* `audio_host.hpp`, so it cannot include it back.
#pragma once

#include <array>
#include <atomic>
#include <cstddef>

namespace arb::host {

// ---- a single-producer / single-consumer ring -------------------------------
template <class T, std::size_t N>
class Spsc {
public:
    bool push(const T& v) {
        const std::size_t w = w_.load(std::memory_order_relaxed);
        const std::size_t n = (w + 1) % N;
        if (n == r_.load(std::memory_order_acquire)) return false;   // full
        buf_[w] = v;
        w_.store(n, std::memory_order_release);
        return true;
    }
    // Producer side: how many more `push`es would succeed now. The consumer
    // only ever frees slots, so the answer can only grow before the next push.
    std::size_t free_slots() const {
        const std::size_t w = w_.load(std::memory_order_relaxed);
        const std::size_t r = r_.load(std::memory_order_acquire);
        return (r + N - w - 1) % N;
    }
    bool pop(T& v) {
        const std::size_t r = r_.load(std::memory_order_relaxed);
        if (r == w_.load(std::memory_order_acquire)) return false;   // empty
        v = buf_[r];
        r_.store((r + 1) % N, std::memory_order_release);
        return true;
    }

private:
    std::array<T, N> buf_{};
    std::atomic<std::size_t> w_{0}, r_{0};
};

}  // namespace arb::host
