// device.hpp — live audio through miniaudio (engine-spec.md §8.1).
//
// One playback device, optionally duplex so the `input` sidechain source and
// the mic have something to read. The callback deinterleaves into buffers
// allocated at start() and calls AudioHost::render_block; nothing in that path
// allocates, locks or does I/O (§3.7).
#pragma once

#include <string>

namespace arb::host {

class AudioHost;

class Device {
public:
    ~Device();
    // device-os-spec.md § 6.3 (#543): the ALSA PCM to open by name, e.g.
    // `hw:wm8904` on P3, so nothing (no `plug`) sits between the host and the
    // card. Empty = the backend's default device (a desktop, the bench's
    // snd-dummy). Linux only: anywhere else a name is refused at start().
    void set_pcm(const std::string& pcm) { pcm_ = pcm; }
    // #637: the one core the audio thread runs on, pinned at start() beside
    // SCHED_FIFO. -1 = leave its affinity alone (every desktop, the tests).
    void set_audio_core(int core) { audio_core_ = core; }
    bool start(AudioHost& host, bool capture, std::string& err);
    void stop();
    const std::string& name() const { return name_; }
    // What start() got of SCHED_FIFO + mlockall on Linux; empty elsewhere.
    const std::string& realtime() const { return realtime_; }

private:
    void* impl_ = nullptr;
    std::string pcm_;
    std::string name_;
    std::string realtime_;
    int audio_core_ = -1;
};

// #637: take `core` out of the **calling** thread's CPU set, so every thread it
// starts after this (the tape I/O thread, miniaudio's worker before start()
// pins it back) inherits a set without the audio core. Call it on the control
// thread before anything starts a thread. A set that would be left empty (a
// unit that gives the process `core` alone) is left as it was. Returns what it
// did, for the banner; empty off Linux or with `core` < 0.
std::string leave_core(int core);

}  // namespace arb::host
