// test_voice_golden.cpp — the Python voice goldens, replayed against the C++.
//
// engine-spec.md §9.4 makes audio/tests/golden/python/ the cross-implementation
// conformance suite. Four of those goldens are *voice DSP*, not whole engines:
// they were captured by driving ADSREnvelope, Oscillator, FMVoice and
// NoiseVoice directly, at parameters the shipped boxes deliberately cannot
// reach any more (DDR-044 re-ranged them -- arbutus/fm's RATIO is an ENUM and
// 2.5 is not on it).
//
// So this test drives the cores directly, exactly as
// emulator/tests/test_dsp_golden.py does, and the boxes wrap those same
// headers. The alternative -- widening the shipped ranges so a golden could be
// reproduced through the ABI -- would be letting the test dictate the
// instrument, which is the failure DDR-044 names.
//
// Tolerance is the §9.4 default, -100 dBFS peak error. The Python's own suite
// allows 3e-7 on the noise voice and 1e-12 on FM for the same reasons.

#include <cmath>
#include <cstdio>
#include <cstdint>
#include <limits>
#include <string>
#include <vector>

#include <arbutus_engine.h>
#include <poly.hpp>

#include <audiofile.hpp>

#include "../engines/arbutus/fm/src/fm_dsp.hpp"
#include "../engines/arbutus/noise/src/noise_dsp.hpp"
#include "../engines/arbutus/osc/src/osc_dsp.hpp"

namespace {

constexpr uint32_t kSr = 48000;
constexpr uint32_t kN = 512;            // test_dsp_golden.py's block
constexpr double kTolDb = -100.0;

std::string g_golden_dir;
int g_bad = 0;

std::vector<float> load(const std::string& name) {
    arb::io::Audio a;
    const std::string path = g_golden_dir + "/" + name + ".npy";
    if (std::string e = arb::io::read(path, a); !e.empty()) {
        std::printf("FAIL %s: %s\n", name.c_str(), e.c_str());
        ++g_bad;
        return {};
    }
    return a.ch.empty() ? std::vector<float>{} : a.ch[0];
}

void compare(const std::string& name, const std::vector<float>& got, double tol = kTolDb) {
    const std::vector<float> want = load(name);
    if (want.empty()) return;
    if (got.size() != want.size()) {
        std::printf("FAIL %s: %zu frames, golden has %zu\n", name.c_str(), got.size(), want.size());
        ++g_bad;
        return;
    }
    double peak = 0.0;
    for (std::size_t i = 0; i < want.size(); ++i)
        peak = std::max(peak, std::abs(double(got[i]) - double(want[i])));
    const double db = peak > 0.0 ? 20.0 * std::log10(peak)
                                 : -std::numeric_limits<double>::infinity();
    const bool pass = db <= tol;
    if (!pass) ++g_bad;
    std::printf("%s %-12s peak error %7.1f dBFS over %zu frames (tol %.1f)\n",
                pass ? "ok  " : "FAIL", name.c_str(), db, want.size(), tol);
}

// The ENV page values the Python test set on the envelope under test.
arb_host host_with(float a, float d, float s, float r) {
    arb_host h{};
    h.sample_rate = kSr;
    h.max_block = kN;
    h.env_a = a;
    h.env_d = d;
    h.env_s = s;
    h.env_r = r;
    return h;
}

// ---- ADSR ------------------------------------------------------------------
// test_adsr_through_every_phase / test_adsr_with_zero_length_segments.
std::vector<float> run_adsr(float a, float d, float s, float r, int on_blocks, int off_blocks) {
    const arb_host h = host_with(a, d, s, r);
    arb::Adsr env;
    std::vector<float> out;
    std::vector<float> buf(kN);
    env.gate_on(h);
    for (int b = 0; b < on_blocks; ++b) {
        env.render(buf.data(), kN, 1.0 / double(kSr));
        out.insert(out.end(), buf.begin(), buf.end());
    }
    env.gate_off();
    for (int b = 0; b < off_blocks; ++b) {
        env.render(buf.data(), kN, 1.0 / double(kSr));
        out.insert(out.end(), buf.begin(), buf.end());
    }
    return out;
}

// ---- oscillator ------------------------------------------------------------
// test_every_waveform: three blocks at 220.5 Hz, no envelope.
std::vector<float> run_osc(int wave) {
    arbosc::Osc osc;
    std::vector<float> out(3 * kN);
    for (int b = 0; b < 3; ++b) osc.generate(wave, 220.5, double(kSr), out.data() + b * kN, kN);
    return out;
}

// ---- FM --------------------------------------------------------------------
// test_fm_voice: note 57, vel 0.8, ratio 2.5, index 3.0, ADSR .004/.006/.7/.010,
// three blocks held then two released. The Python applies the envelope inside
// the voice; PolyHost applies it after, which is the same multiply.
std::vector<float> run_fm() {
    const arb_host h = host_with(0.004f, 0.006f, 0.7f, 0.010f);
    arbfm::Voice v;
    arb::Adsr env;
    v.start(57, 0.8f, 2.5, 3.0);
    env.gate_on(h);
    std::vector<float> out(5 * kN), buf(kN), e(kN);
    for (int b = 0; b < 5; ++b) {
        if (b == 3) env.gate_off();
        v.render(double(kSr), buf.data(), kN);
        env.render(e.data(), kN, 1.0 / double(kSr));
        for (uint32_t i = 0; i < kN; ++i) out[std::size_t(b) * kN + i] = buf[i] * e[i];
    }
    return out;
}

// ---- noise -----------------------------------------------------------------
// test_noise_voice_within_tolerance: np.random.seed(1234), note 60, vel 0.9,
// PINK, cutoff 4000, the envelope's own defaults, three blocks, never released.
std::vector<float> run_noise() {
    const arb_host h = host_with(0.01f, 0.1f, 0.7f, 0.2f);
    arbnoise::NpRandom rng;
    rng.seed(1234);
    arbnoise::Voice v;
    v.rng = &rng;
    v.lp_reset_per_render = true;          // the Python restarts it every block
    v.start(60, 0.9f, arbnoise::kPink, 4000.0);
    arb::Adsr env;
    env.gate_on(h);
    std::vector<float> out(3 * kN), buf(kN), e(kN);
    for (int b = 0; b < 3; ++b) {
        v.render(double(kSr), buf.data(), kN);
        env.render(e.data(), kN, 1.0 / double(kSr));
        for (uint32_t i = 0; i < kN; ++i)
            out[std::size_t(b) * kN + i] = buf[i] * e[i] * v.vel;
    }
    return out;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::printf("usage: test_voice_golden <audio/tests/golden/python>\n");
        return 2;
    }
    g_golden_dir = argv[1];

    compare("adsr", run_adsr(0.004f, 0.006f, 0.6f, 0.008f, 3, 3));
    compare("adsr_zero", run_adsr(0.0f, 0.0f, 0.5f, 0.0f, 1, 1));

    compare("osc_sine", run_osc(arbosc::kSine));
    compare("osc_square", run_osc(arbosc::kSquare));
    compare("osc_saw", run_osc(arbosc::kSaw));
    compare("osc_triangle", run_osc(arbosc::kTriangle));

    compare("fm_voice", run_fm());
    // -90, not -100, and §9.4 allows the per-test override. The measured error
    // is -97.8 dBFS and it has exactly one cause, which is not the noise:
    // the noise itself matches the Python to 5e-10. The ENV times cross the
    // ABI as float32 (arb_host::env_a), and float32(0.01) is 0.0099999998 --
    // just below the accumulated 480/48000 the envelope's clock reaches -- so
    // the attack ends one sample before the Python's double 0.01 ends it, and
    // the decay ramp is offset by one sample of its slope (0.3/4800 = 6.3e-5)
    // for the rest of the capture. The two goldens whose times round the other
    // way (adsr at 0.004/0.006, fm_voice at 0.004/0.006) land at -144.5.
    compare("noise_voice", run_noise(), -90.0);

    std::printf("%s\n", g_bad ? "voice goldens: FAILED"
                              : "voice goldens: the Python's voices, in C++");
    return g_bad ? 1 : 0;
}
