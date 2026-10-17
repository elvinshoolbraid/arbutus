// arbutus/reverb — the emulator's Schroeder reverb, in C++.
//
// A direct rewrite of emulator/engines/effects.py SchroederReverb: four
// parallel feedback combs into two series allpasses, delay lengths tuned at
// 44.1 kHz and scaled by both the run's sample rate and SIZE. Kept
// arithmetic-for-arithmetic identical to the Python -- same truncation of the
// scaled delay lengths, same float32 state, same order of accumulation, same
// 0.25 after the combs -- so tests/golden/reverb.npy, captured from the Python
// class by tests/tools/make_reverb_golden.py, stands as the conformance golden
// (engine-spec.md §9.4).
//
// The Python vectorises each comb over a block with `idxs = (arange(M)+pos) % N`
// and writes back to the same indices it read. Because M < N that is exactly a
// circular buffer of length N whose read and write pointer are the same slot:
// every read is the sample written N steps ago. The loop below writes a ring
// sized for the largest SIZE and reads N behind it, which gives the same
// numbers at a fixed SIZE and lets SIZE move without emptying the network.
// There is no block-size dependence in either.
//
// One deliberate difference: the Python rebuilds (and empties) the network on
// every SIZE change, so one encoder detent cut the tail dead and a route on
// SIZE kept emptying it (#630). Here a SIZE change only moves the read
// distance; the tail carries on. The golden is a fixed SIZE and still holds.
//
// Mono in the Python, stereo here: two independent chains with the same
// parameters. A mono input therefore gives two identical channels, which is
// what makes the 1-D golden comparable against the left one.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <new>
#include <vector>

#include <arbutus_engine.h>

#include <desc.hpp>
#include <params.hpp>

namespace {

constexpr int kNumCombs = 4;
constexpr int kNumAp = 2;
constexpr int kCombDelays[kNumCombs] = {1116, 1188, 1277, 1356};
constexpr int kApDelays[kNumAp] = {556, 441};
constexpr float kApFb = 0.5f;

arb_engine_desc make_desc() {
    arb_engine_desc d{};
    d.abi = ARB_ABI;
    arb::set_field(d.id, sizeof d.id, "arbutus/reverb");
    arb::set_field(d.version, sizeof d.version, "1.0.0");
    d.caps = ARB_CAP_AUDIO_IN | ARB_CAP_AUDIO_OUT;
    d.honours_env = 0;
    d.latency_frames = 0;
    d.num_params = 2;
    // The Python's own defaults, and they are already tuned (DDR-044): SIZE
    // moves both the delay lengths and the comb feedback together, so every
    // position is a room rather than a ringing artefact.
    d.params[0] = arb::param_lin("size", "SIZE", 0.0f, 1.0f, 0.5f, 0.01f, "");
    d.params[1] = arb::param_lin("mix",  "MIX",  0.0f, 1.0f, 0.25f, 0.01f, "");
    return d;
}

const arb_engine_desc& desc() {
    static const arb_engine_desc d = make_desc();
    return d;
}

// One channel's worth of the network. Buffers are sized once at create() for
// the largest SIZE; only the *used* length changes afterwards.
struct Net {
    std::vector<float> comb[kNumCombs], ap[kNumAp];
    int comb_len[kNumCombs]{}, ap_len[kNumAp]{};   // the read distance, per SIZE
    int comb_pos[kNumCombs]{}, ap_pos[kNumAp]{};   // the write slot, ring of size()

    void alloc(double sr) {
        const double smax = (sr / 44100.0) * 1.5;         // SIZE = 1
        for (int i = 0; i < kNumCombs; ++i)
            comb[i].assign(std::size_t(double(kCombDelays[i]) * smax) + 2, 0.0f);
        for (int i = 0; i < kNumAp; ++i)
            ap[i].assign(std::size_t(double(kApDelays[i]) * smax) + 2, 0.0f);
    }

    // _build()'s lengths: the Python's truncation of the scaled delays.
    void resize(double sr, float size) {
        const double scale = (sr / 44100.0) * (0.5 + double(size));
        for (int i = 0; i < kNumCombs; ++i)
            comb_len[i] = std::clamp(int(double(kCombDelays[i]) * scale), 1, int(comb[i].size()));
        for (int i = 0; i < kNumAp; ++i)
            ap_len[i] = std::clamp(int(double(kApDelays[i]) * scale), 1, int(ap[i].size()));
    }

    // Silence, pointers back to zero: reset() and a sample-rate change. The
    // Python allocates fresh arrays here; clearing is the same thing without
    // the allocation §3.7.1 forbids.
    void clear() {
        for (int i = 0; i < kNumCombs; ++i) {
            std::fill(comb[i].begin(), comb[i].end(), 0.0f);
            comb_pos[i] = 0;
        }
        for (int i = 0; i < kNumAp; ++i) {
            std::fill(ap[i].begin(), ap[i].end(), 0.0f);
            ap_pos[i] = 0;
        }
    }

    // The sample written `len` steps ago, in a ring of buf.size().
    static float& behind(std::vector<float>& buf, int pos, int len) {
        const int r = pos - len;
        return buf[std::size_t(r < 0 ? r + int(buf.size()) : r)];
    }

    float step(float x, float comb_fb) {
        float wet = 0.0f;
        for (int i = 0; i < kNumCombs; ++i) {
            const float o = x + comb_fb * behind(comb[i], comb_pos[i], comb_len[i]);
            comb[i][std::size_t(comb_pos[i])] = o;
            comb_pos[i] = (comb_pos[i] + 1) % int(comb[i].size());
            wet += o;
        }
        wet *= 0.25f;
        for (int i = 0; i < kNumAp; ++i) {
            const float delayed = behind(ap[i], ap_pos[i], ap_len[i]);
            ap[i][std::size_t(ap_pos[i])] = wet + kApFb * delayed;
            wet = -wet + delayed;
            ap_pos[i] = (ap_pos[i] + 1) % int(ap[i].size());
        }
        return wet;
    }
};

struct Engine {
    float  norm[2]{};
    double sr = 48000.0;
    float  size = 0.5f, mix = 0.25f;
    float  built_size = -1.0f;
    double built_sr = 0.0;
    Net    ch[2];

    explicit Engine(double rate) : sr(rate > 0.0 ? rate : 48000.0) {
        for (Net& n : ch) n.alloc(sr);
        for (uint32_t i = 0; i < desc().num_params; ++i)
            set_param(i, arb::norm(desc().params[i], desc().params[i].def));
    }

    // A macro may drive this every block from the audio thread, so the new
    // lengths are worked out in process(), only when SIZE really moved.
    void set_param(uint32_t idx, float n) {
        if (idx >= desc().num_params) return;
        norm[idx] = arb::clamp01(n);
        const float v = arb::denorm(desc().params[idx], norm[idx]);
        if (idx == 0) size = v; else mix = v;
    }

    void reset() { built_sr = 0.0; }            // next block clears

    void process(const arb_host& host, arb_io& io) {
        const double rate = host.sample_rate ? double(host.sample_rate) : sr;
        if (rate != built_sr) {
            for (Net& n : ch) n.clear();
            built_sr = rate;
            built_size = -1.0f;
        }
        if (size != built_size) {
            for (Net& n : ch) n.resize(rate, size);
            built_size = size;
        }
        const float comb_fb = float(0.76 + double(size) * 0.1);
        const float dry = float(1.0 - double(mix));
        const float wetg = mix;

        for (int c = 0; c < 2; ++c) {
            const float* in = io.in[c];
            float* out = io.out[c];
            if (!in) { std::fill_n(out, io.frames, 0.0f); continue; }
            Net& n = ch[c];
            for (uint32_t i = 0; i < io.frames; ++i) {
                const float x = in[i];
                out[i] = x * dry + n.step(x, comb_fb) * wetg;
            }
        }
    }
};

void* e_create(const arb_host* host, const char*) {
    return new (std::nothrow) Engine(host ? double(host->sample_rate) : 48000.0);
}
void  e_destroy(void* h) { delete static_cast<Engine*>(h); }
void  e_reset(void* h) { static_cast<Engine*>(h)->reset(); }
void  e_set_param(void* h, uint32_t idx, float n) { static_cast<Engine*>(h)->set_param(idx, n); }
float e_get_param(void* h, uint32_t idx) {
    return idx < desc().num_params ? static_cast<Engine*>(h)->norm[idx] : 0.0f;
}
void  e_process(void* h, const arb_host* host, arb_io* io) {
    static_cast<Engine*>(h)->process(*host, *io);
}

const arb_vtable kVtable = {
    e_create, e_destroy, e_reset, e_set_param, e_get_param, e_process,
    nullptr,                 // load_asset: takes no assets (§3.2)
    nullptr,                 // set_note_param: no per_note params (§15.3)
    nullptr,                 // format_param: the host draws every readout
    nullptr,                 // set_voice_mod: no per_voice params
    {}
};

}  // namespace

extern "C" ARB_EXPORT const arb_engine_desc* arb_describe(void) { return &desc(); }
extern "C" ARB_EXPORT const arb_vtable* arb_entry(void) { return &kVtable; }
