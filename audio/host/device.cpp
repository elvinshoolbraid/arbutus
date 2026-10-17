#include "device.hpp"

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <vector>

#if defined(__linux__)
#include <pthread.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/resource.h>
#endif

#define MINIAUDIO_IMPLEMENTATION
#define MA_NO_ENCODING
#define MA_NO_DECODING
#define MA_NO_GENERATION
#define MA_NO_RESOURCE_MANAGER
#define MA_NO_NODE_GRAPH
#include <miniaudio.h>

#include "audio_host.hpp"
#include "device_config.hpp"
#include "sample_format.hpp"

namespace arb::host {
namespace {

struct Impl {
    ma_device dev{};
    // Only when a PCM is named (§ 6.3): a context pinned to ALSA, because a
    // device id is backend-specific and the default context may pick another.
    ma_context ctx{};
    ma_device_id id{};
    bool has_ctx = false;
    AudioHost* host = nullptr;
    std::vector<float> il, ir, ol, orr;
    // § 6.3.4 (#743): the device's samples are int32 on both sides, converted
    // here; otherwise float.
    bool s32 = false;
    bool started = false;
};

// miniaudio's ALSA backend recovers from an underrun (write) or overrun (read)
// by itself and says so only as a debug log line. Counting that line is how a
// late wakeup reaches the §6.8 xrun count. Runs on the audio thread, once per
// lost period; a strcmp and an atomic add.
void on_log(void* user, ma_uint32 level, const char* msg) {
    if (level != MA_LOG_LEVEL_DEBUG || !msg) return;
    if (std::strncmp(msg, "EPIPE (", 7) == 0) static_cast<Impl*>(user)->host->count_device_xrun();
}

void callback(ma_device* d, void* out, const void* in, ma_uint32 n) {
    Impl* im = static_cast<Impl*>(d->pUserData);
    if (!im || !im->host) return;
    if (n > im->ol.size()) n = ma_uint32(im->ol.size());

    if (in) {
        if (im->s32) {
            const int32_t* src = static_cast<const int32_t*>(in);
            for (ma_uint32 i = 0; i < n; ++i) {
                im->il[i] = s32_to_f32(src[i * 2]);
                im->ir[i] = s32_to_f32(src[i * 2 + 1]);
            }
        } else {
            const float* src = static_cast<const float*>(in);
            for (ma_uint32 i = 0; i < n; ++i) { im->il[i] = src[i * 2]; im->ir[i] = src[i * 2 + 1]; }
        }
    }
    im->host->render_block(in ? im->il.data() : nullptr, in ? im->ir.data() : nullptr,
                           im->ol.data(), im->orr.data(), n);
    if (!out) return;
    if (im->s32) {
        int32_t* dst = static_cast<int32_t*>(out);
        for (ma_uint32 i = 0; i < n; ++i) {
            dst[i * 2] = f32_to_s32(im->ol[i]);
            dst[i * 2 + 1] = f32_to_s32(im->orr[i]);
        }
    } else {
        float* dst = static_cast<float*>(out);
        for (ma_uint32 i = 0; i < n; ++i) { dst[i * 2] = im->ol[i]; dst[i * 2 + 1] = im->orr[i]; }
    }
}

#if defined(__linux__)
// software-stack-v1 §7.4 / audio/PI.md § 3: the audio thread runs SCHED_FIFO
// and the process's memory is locked, so a page fault or a CFS time slice is
// never what makes a block late. Both are set from the control thread, on
// miniaudio's own worker thread, after init and before start -- nothing is
// done inside the callback. Each failure is printed and the host carries on:
// a laptop without rtprio still makes sound, it just says why it may crackle.
constexpr int kAudioFifoPriority = 80;

void go_realtime(Impl* im, int core, std::string& report) {
    if (ma_context_is_backend_asynchronous(im->dev.pContext)) {
        report = "realtime: backend owns its thread, SCHED_FIFO not set";
        std::fprintf(stderr, "arbutus-audio: %s\n", report.c_str());
    } else {
        sched_param sp{};
        sp.sched_priority = kAudioFifoPriority;
        const int e = pthread_setschedparam(im->dev.thread, SCHED_FIFO, &sp);
        if (e) {
            report = std::string("realtime: SCHED_FIFO refused (") + std::strerror(e) +
                     "; needs LimitRTPRIO>=80 or CAP_SYS_NICE)";
            std::fprintf(stderr, "arbutus-audio: %s\n", report.c_str());
        } else {
            report = "realtime: SCHED_FIFO " + std::to_string(kAudioFifoPriority);
        }
    }
    // #637: the audio thread alone on its core. The unit gives the process two
    // cores (`CPUAffinity=2-3`); `leave_core` has already taken the control
    // thread and everything it started off this one, so here the worker is
    // put back on it and only on it.
    if (core >= 0 && ma_context_is_backend_asynchronous(im->dev.pContext) == MA_FALSE) {
        cpu_set_t set;
        CPU_ZERO(&set);
        CPU_SET(core, &set);
        const int e = pthread_setaffinity_np(im->dev.thread, sizeof set, &set);
        if (e) {
            const std::string m = "core " + std::to_string(core) + " refused (" + std::strerror(e) + ")";
            report += ", " + m;
            std::fprintf(stderr, "arbutus-audio: realtime: %s\n", m.c_str());
        } else {
            report += ", core " + std::to_string(core);
        }
    }
    // Deviation from PI.md § 3 (audio/CLAUDE.md § Defaults): mlockall only under
    // an unlimited RLIMIT_MEMLOCK. With a finite limit MCL_FUTURE turns every
    // later mmap past it into ENOMEM -- the tape's lazy 184 MB medium
    // (host/tape.hpp) would fail on first record instead of the lock failing
    // here, once, where it can be read.
    rlimit rl{};
    if (getrlimit(RLIMIT_MEMLOCK, &rl) == 0 && rl.rlim_cur == RLIM_INFINITY) {
        if (mlockall(MCL_CURRENT | MCL_FUTURE) == 0) {
            report += ", memory locked";
        } else {
            const std::string m = std::string("mlockall failed (") + std::strerror(errno) + ")";
            report += ", " + m;
            std::fprintf(stderr, "arbutus-audio: realtime: %s\n", m.c_str());
        }
    } else {
        report += ", memory NOT locked (RLIMIT_MEMLOCK is finite; LimitMEMLOCK=infinity)";
        std::fprintf(stderr, "arbutus-audio: realtime: memory not locked: "
                             "RLIMIT_MEMLOCK is finite (LimitMEMLOCK=infinity)\n");
    }
}
#endif

}  // namespace

std::string leave_core(int core) {
#if defined(__linux__)
    if (core < 0 || core >= CPU_SETSIZE) return {};
    cpu_set_t set;
    CPU_ZERO(&set);
    if (sched_getaffinity(0, sizeof set, &set) != 0)
        return std::string("control thread: affinity unreadable (") + std::strerror(errno) + ")";
    if (!CPU_ISSET(core, &set)) return "control thread: off core " + std::to_string(core);
    CPU_CLR(core, &set);
    if (CPU_COUNT(&set) == 0)
        return "control thread: on core " + std::to_string(core) +
               " (the process has no other core; CPUAffinity=2-3)";
    // pid 0 is the calling thread, not the process: on Linux affinity is per
    // thread, which is what makes this the control thread's alone.
    if (sched_setaffinity(0, sizeof set, &set) != 0)
        return std::string("control thread: could not leave core ") + std::to_string(core) +
               " (" + std::strerror(errno) + ")";
    return "control thread: off core " + std::to_string(core);
#else
    (void)core;
    return {};
#endif
}

Device::~Device() { stop(); }

bool Device::start(AudioHost& host, bool capture, std::string& err) {
    auto im = new Impl();
    im->host = &host;
    const uint32_t mb = host.options().max_block;
    im->il.assign(mb, 0.0f);
    im->ir.assign(mb, 0.0f);
    im->ol.assign(mb, 0.0f);
    im->orr.assign(mb, 0.0f);

    ma_device_config c = ma_device_config_init(capture ? ma_device_type_duplex
                                                       : ma_device_type_playback);
    im->s32 = ask_device_for(c, pcm_, host.options().sr, host.options().block);
    c.dataCallback = callback;
    c.pUserData = im;

    // A named PCM is opened exactly as given (miniaudio passes an id that does
    // not start with ':' straight to snd_pcm_open), no ALSA `plug` layer, no
    // extra buffer. It is asked for S32_LE (sample_format.hpp says why); a card
    // without it gets the first of miniaudio's own list it offers, and miniaudio
    // converts the int32 the callback writes to that.
    if (!pcm_.empty()) {
#if defined(__linux__)
        const ma_backend alsa = ma_backend_alsa;
        if (ma_context_init(&alsa, 1, nullptr, &im->ctx) != MA_SUCCESS) {
            err = "no ALSA backend for --device " + pcm_;
            delete im;
            return false;
        }
        im->has_ctx = true;
        std::snprintf(im->id.alsa, sizeof im->id.alsa, "%s", pcm_.c_str());
        c.playback.pDeviceID = &im->id;
        c.capture.pDeviceID = &im->id;
#else
        err = "--device names an ALSA PCM (Linux only)";
        delete im;
        return false;
#endif
    }

    if (ma_device_init(im->has_ctx ? &im->ctx : nullptr, &c, &im->dev) != MA_SUCCESS) {
        err = capture ? "no duplex device (try --no-input)" : "no output device";
        if (!pcm_.empty()) err += " at " + pcm_;
        if (im->has_ctx) ma_context_uninit(&im->ctx);
        delete im;
        return false;
    }
    name_ = im->dev.playback.name;
    ma_log_register_callback(ma_device_get_log(&im->dev), ma_log_callback_init(on_log, im));
#if defined(__linux__)
    go_realtime(im, audio_core_, realtime_);
#endif
    if (ma_device_start(&im->dev) != MA_SUCCESS) {
        err = "device would not start";
        ma_device_uninit(&im->dev);
        if (im->has_ctx) ma_context_uninit(&im->ctx);
        delete im;
        return false;
    }
    im->started = true;
    impl_ = im;
    return true;
}

void Device::stop() {
    Impl* im = static_cast<Impl*>(impl_);
    if (!im) return;
    if (im->started) ma_device_uninit(&im->dev);
    if (im->has_ctx) ma_context_uninit(&im->ctx);
    delete im;
    impl_ = nullptr;
}

}  // namespace arb::host
