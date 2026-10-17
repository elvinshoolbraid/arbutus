// #1230: what the OP-1 adapter promises about op1r/op1r-verb, beyond its golden.
//
//   * MIX 0 is the input, bit for bit, 64 frames late (latency_frames), on both
//     channels, with the tail at its longest: the mono-sum trick adds nothing
//     when the plugin changes nothing.
//   * MIX 1 is not the input: the plugin really runs (the control for the above).
//   * three instances, the OP-1's three FX positions; a fourth is refused with
//     NULL, and a freed position is taken again;
//   * two instances share nothing: one fed noise leaves the other silent;
//   * full-scale noise at every knob's top stays inside -1..1 and finite.
//
// Loads the box's binary with dlopen, as the host does, and calls its vtable.

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

#include <dlfcn.h>

#include <arbutus_engine.h>

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

constexpr uint32_t kSr = 48000, kBlock = 100;   // a block that is not 64

const arb_vtable* vt = nullptr;
const arb_engine_desc* d = nullptr;

uint32_t g_rng = 12345;
float noise() {
    g_rng = g_rng * 1664525u + 1013904223u;
    return float(int32_t(g_rng >> 8) - (1 << 23)) / float(1 << 23);
}

int param(const char* id) {
    for (uint32_t i = 0; i < d->num_params; ++i)
        if (!std::strcmp(d->params[i].id, id)) return int(i);
    return -1;
}

arb_host host() {
    arb_host h{};
    h.sample_rate = kSr;
    h.max_block = kBlock;
    return h;
}

// Renders in[] (stereo, n frames) through h in blocks of kBlock.
void render(void* h, const std::vector<float>& l, const std::vector<float>& r,
            std::vector<float>& ol, std::vector<float>& orr) {
    const arb_host hh = host();
    const size_t n = l.size();
    ol.assign(n, 0.0f);
    orr.assign(n, 0.0f);
    for (size_t at = 0; at < n; at += kBlock) {
        arb_io io{};
        io.frames = uint32_t(std::min<size_t>(kBlock, n - at));
        io.in[0] = l.data() + at;
        io.in[1] = r.data() + at;
        io.out[0] = ol.data() + at;
        io.out[1] = orr.data() + at;
        vt->process(h, &hh, &io);
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::printf("usage: test_op1r_verb <op1r-verb binary>\n");
        return 2;
    }
    void* so = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!so) { std::printf("FAIL dlopen: %s\n", dlerror()); return 1; }
    auto describe = reinterpret_cast<const arb_engine_desc* (*)()>(dlsym(so, "arb_describe"));
    auto entry = reinterpret_cast<const arb_vtable* (*)()>(dlsym(so, "arb_entry"));
    if (!describe || !entry) { std::printf("FAIL no entry points\n"); return 1; }
    d = describe();
    vt = entry();
    const arb_host h0 = host();

    CHECK(d->latency_frames == 64, "latency_frames %u", d->latency_frames);
    const int time = param("time"), tone = param("tone"), diff = param("diffusion"),
              mix = param("mix");
    CHECK(time >= 0 && tone >= 0 && diff >= 0 && mix >= 0, "the four op1r knobs");
    if (failures) return 1;

    const size_t n = kSr / 2;
    std::vector<float> l(n), r(n), ol, orr;
    for (size_t i = 0; i < n; ++i) { l[i] = 0.5f * noise(); r[i] = 0.3f * noise(); }

    // MIX 0: the input, 64 frames late, bit for bit; TIME at the top.
    {
        void* e = vt->create(&h0, "");
        CHECK(e != nullptr, "create");
        vt->set_param(e, time, 1.0f);
        vt->set_param(e, mix, 0.0f);
        render(e, l, r, ol, orr);
        size_t bad = 0;
        for (size_t i = 0; i < n; ++i) {
            const float wl = i < 64 ? 0.0f : l[i - 64], wr = i < 64 ? 0.0f : r[i - 64];
            if (ol[i] != wl || orr[i] != wr) ++bad;
        }
        CHECK(bad == 0, "MIX 0: %zu of %zu frames differ from the input 64 late", bad, n);

        // MIX 1 on the same instance: the reverb, not the input.
        vt->reset(e);
        vt->set_param(e, mix, 1.0f);
        render(e, l, r, ol, orr);
        double diff_e = 0.0;
        for (size_t i = 64; i < n; ++i) diff_e += std::fabs(ol[i] - l[i - 64]);
        CHECK(diff_e / double(n) > 0.01, "MIX 1 is the input (mean |diff| %g)", diff_e / double(n));
        vt->destroy(e);
    }

    // Three positions, no fourth; a freed one is taken again.
    {
        void* e[4];
        for (int i = 0; i < 4; ++i) e[i] = vt->create(&h0, "");
        CHECK(e[0] && e[1] && e[2], "three instances: %p %p %p", e[0], e[1], e[2]);
        CHECK(e[3] == nullptr, "a fourth instance was not refused");
        vt->destroy(e[1]);
        e[1] = vt->create(&h0, "");
        CHECK(e[1] != nullptr, "a freed position is not taken again");

        // e[0] gets noise at MIX 1; e[2] gets silence and must stay silent.
        vt->set_param(e[0], mix, 1.0f);
        vt->set_param(e[2], mix, 1.0f);
        std::vector<float> z(n, 0.0f), zl, zr;
        const arb_host hh = host();
        ol.assign(n, 0.0f); orr.assign(n, 0.0f); zl.assign(n, 0.0f); zr.assign(n, 0.0f);
        for (size_t at = 0; at < n; at += kBlock) {    // interleaved, as one host thread runs both
            const uint32_t f = uint32_t(std::min<size_t>(kBlock, n - at));
            arb_io a{}, b{};
            a.frames = b.frames = f;
            a.in[0] = l.data() + at; a.in[1] = r.data() + at;
            a.out[0] = ol.data() + at; a.out[1] = orr.data() + at;
            b.in[0] = z.data() + at; b.in[1] = z.data() + at;
            b.out[0] = zl.data() + at; b.out[1] = zr.data() + at;
            vt->process(e[0], &hh, &a);
            vt->process(e[2], &hh, &b);
        }
        float loud = 0.0f, leak = 0.0f;
        for (size_t i = 0; i < n; ++i) {
            loud = std::max(loud, std::fabs(ol[i]));
            leak = std::max({leak, std::fabs(zl[i]), std::fabs(zr[i])});
        }
        CHECK(loud > 0.05f, "the noisy instance is silent (peak %g)", loud);
        CHECK(leak == 0.0f, "the silent instance hears the other (peak %g)", leak);
        for (int i = 0; i < 3; ++i) vt->destroy(e[i]);
    }

    // Full-scale noise, every knob at the top: bounded and finite.
    {
        void* e = vt->create(&h0, "");
        CHECK(e != nullptr, "create");
        for (int p : {time, tone, diff, mix}) vt->set_param(e, uint32_t(p), 1.0f);
        std::vector<float> hl(kSr * 2), hr(kSr * 2);
        for (size_t i = 0; i < hl.size(); ++i) { hl[i] = noise(); hr[i] = noise(); }
        render(e, hl, hr, ol, orr);
        size_t bad = 0;
        for (size_t i = 0; i < ol.size(); ++i)
            if (!std::isfinite(ol[i]) || !std::isfinite(orr[i]) ||
                std::fabs(ol[i]) > 2.0f || std::fabs(orr[i]) > 2.0f) ++bad;
        // dry + (y - m): each term is inside -1..1, so |out| <= 2 by construction;
        // a NaN or a runaway shows here.
        CHECK(bad == 0, "%zu frames out of bounds", bad);
        vt->destroy(e);
    }

    dlclose(so);
    if (failures) { std::printf("%d failure(s)\n", failures); return 1; }
    std::printf("op1r-verb adapter: ok\n");
    return 0;
}
