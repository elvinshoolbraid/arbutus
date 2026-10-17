// #864: STEPS' AUTO readout formatted on one thread while process() enters
// and undoes steps on another, as the host's control and audio threads do
// (§15.1). Built with the engine's source compiled in and, where the compiler
// has it, under ThreadSanitizer, so reading the audio thread's own length from
// format_param is a reported race (exit 66), not a value that happens to look
// right. The readout must also stay a length the pattern could have had.

#include <atomic>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <thread>

#include <arbutus_engine.h>
#include <params.hpp>

namespace {

arb_event ev(uint16_t type, uint16_t a, float b) {
    arb_event e{};
    e.type = type;
    e.a = a;
    e.b = b;
    return e;
}

// One block of `in`, then STEPS' AUTO readout as format_param draws it.
const char* block(const arb_vtable* vt, void* h, arb_host& host, float auto_n,
                  const arb_event* in, uint32_t n, char* buf, uint32_t size) {
    float out[2][64];
    arb_event out_ev[64];
    uint32_t n_out = 0;
    arb_io io{};
    io.out[0] = out[0];
    io.out[1] = out[1];
    io.ev_in = in;
    io.n_ev_in = n;
    io.ev_out = out_ev;
    io.ev_out_cap = 64;
    io.n_ev_out = &n_out;
    io.frames = 64;
    vt->process(h, &host, &io);
    vt->format_param(h, 1, auto_n, buf, size);
    return buf;
}

// The readout after every block, on one thread (#1131): the count follows
// each entered step, each UNDO and CLEAR while REC is still held, not only
// when REC comes up. The threaded run below ends on REC up, so a box that
// published only there would pass it.
int stepwise(const arb_vtable* vt, arb_host& host, float auto_n) {
    void* h = vt->create(&host, "");
    if (!h) { std::fprintf(stderr, "create failed\n"); return 1; }
    int bad = 0;
    char buf[32], want[32];
    auto expect = [&](const char* what, int n, std::initializer_list<arb_event> in) {
        block(vt, h, host, auto_n, in.begin(), uint32_t(in.size()), buf, sizeof buf);
        std::snprintf(want, sizeof want, "AUTO %d", n);
        if (std::strcmp(buf, want) != 0) {
            std::printf("FAIL  %s: %s (want %s)\n", what, buf, want);
            ++bad;
        }
    };
    expect("REC down", 0, {ev(ARB_EV_SOFTKEY, 0, 1.0f)});
    for (int k = 1; k <= 5; ++k)
        expect("entry", k, {ev(ARB_EV_NOTE_ON, 60 + k, 1.0f), ev(ARB_EV_NOTE_OFF, 60 + k, 0.0f)});
    expect("UNDO", 4, {ev(ARB_EV_SOFTKEY, 2, 1.0f)});
    expect("UNDO", 3, {ev(ARB_EV_SOFTKEY, 2, 1.0f)});
    expect("CLEAR", 0, {ev(ARB_EV_SOFTKEY, 3, 1.0f)});
    expect("entry after CLEAR", 1, {ev(ARB_EV_NOTE_ON, 70, 1.0f), ev(ARB_EV_NOTE_OFF, 70, 0.0f)});
    expect("REC up", 1, {ev(ARB_EV_SOFTKEY, 0, 0.0f)});
    vt->destroy(h);
    return bad;
}

}  // namespace

int main() {
    const arb_engine_desc* d = arb_describe();
    const arb_vtable* vt = arb_entry();
    arb_host host{};
    host.sample_rate = 48000;
    host.max_block = 64;
    host.tempo_bpm = 120.0;
    void* h = vt->create(&host, "");
    if (!h) { std::fprintf(stderr, "create failed\n"); return 1; }

    constexpr int kBlocks = 20000;
    std::atomic<bool> done{false};
    int bad = stepwise(vt, host, arb::norm(d->params[1], 0.0f));

    // The audio thread: hold REC, enter a step a block for a while, UNDO some
    // back, release; the length climbs and falls the whole run.
    std::thread audio([&] {
        float out[2][64];
        arb_event out_ev[64];
        uint32_t n_out = 0;
        for (int b = 0; b < kBlocks; ++b) {
            arb_event in[2];
            uint32_t n = 0;
            const int phase = b % 40;
            if (phase == 0)       in[n++] = ev(ARB_EV_SOFTKEY, 0, 1.0f);       // REC down
            else if (phase < 20)  in[n++] = ev(ARB_EV_NOTE_ON, 60 + phase, 1.0f),
                                  in[n++] = ev(ARB_EV_NOTE_OFF, 60 + phase, 0.0f);
            else if (phase < 30)  in[n++] = ev(ARB_EV_SOFTKEY, 2, 1.0f);       // UNDO
            else if (phase == 39) in[n++] = ev(ARB_EV_SOFTKEY, 0, 0.0f);       // REC up
            arb_io io{};
            io.out[0] = out[0];
            io.out[1] = out[1];
            io.ev_in = in;
            io.n_ev_in = n;
            io.ev_out = out_ev;
            io.ev_out_cap = 64;
            n_out = 0;
            io.n_ev_out = &n_out;
            io.frames = 64;
            vt->process(h, &host, &io);
        }
        done.store(true, std::memory_order_relaxed);
    });

    // The control thread: draw STEPS at 0 as fast as it can.
    const float auto_n = arb::norm(d->params[1], 0.0f);
    long reads = 0;
    char buf[32];
    while (!done.load(std::memory_order_relaxed)) {
        vt->format_param(h, 1, auto_n, buf, sizeof buf);
        int n = -1;
        if (std::sscanf(buf, "AUTO %d", &n) != 1 || n < 0 || n > 19) {
            if (bad++ < 5) std::printf("FAIL  readout %s\n", buf);
        }
        ++reads;
    }
    audio.join();
    vt->format_param(h, 1, auto_n, buf, sizeof buf);
    // The run ends on REC up after 19 entered and 10 undone, a cycle that
    // started from a cleared pattern: nine steps, published by the last block.
    if (std::strcmp(buf, "AUTO 9") != 0) {
        std::printf("FAIL  after the run: %s (want AUTO 9)\n", buf);
        ++bad;
    }
    vt->destroy(h);
    std::printf("%s  %ld readouts beside %d blocks\n", bad ? "FAIL" : "PASS", reads, kBlocks);
    return bad ? 1 : 0;
}
