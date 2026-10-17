// sdk/wav.hpp, the box-side WAV reader, against the host's own (hostlib's
// arb::io): the two must accept the same files and read the same samples
// (#675, review m-audio-sdk row 7). `bake` writes 24-bit by default, and the
// box reader refused it -- a sampler could not play the instrument's own
// bounce, and `waveform` drew no peaks for it. No framework.
//
//   * 16-bit, 24-bit and float32 files written by arb::io::write load, and each
//     sample matches the host reader's mono mix of the same file
//   * WAVE_FORMAT_EXTENSIBLE is read by its sub-format, as audiofile.cpp does:
//     24-bit PCM loads, 32-bit *PCM* (no reader takes it) is refused, not read
//     as float
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include <unistd.h>

#include <audiofile.hpp>
#include <wav.hpp>

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

std::string tmpdir() {
    char t[] = "/tmp/arbutus-wav-XXXXXX";
    return mkdtemp(t);
}

arb::io::Audio stereo(std::size_t frames) {
    arb::io::Audio a;
    a.sample_rate = 44100;
    a.ch.assign(2, std::vector<float>(frames, 0.0f));
    for (std::size_t i = 0; i < frames; ++i) {
        a.ch[0][i] = 0.9f * std::sin(0.05f * float(i));
        a.ch[1][i] = -0.5f + float(i % 17) / 17.0f;
    }
    return a;
}

void test_each_depth_the_host_writes_loads_and_matches() {
    const std::string dir = tmpdir();
    for (int bits : {16, 24, 32}) {
        const std::string path = dir + "/t" + std::to_string(bits) + ".wav";
        CHECK(arb::io::write(path, stereo(300), bits).empty(), "write %d-bit", bits);
        arb::io::Audio host;
        CHECK(arb::io::read(path, host).empty(), "the host reads its own %d-bit file", bits);
        arb::wav::Sample box;
        const std::string e = arb::wav::load_mono(path, box);
        CHECK(e.empty(), "%d-bit: the box reader refused it: %s", bits, e.c_str());
        if (!e.empty()) continue;
        CHECK(box.sample_rate == 44100, "%d-bit: rate %u", bits, box.sample_rate);
        CHECK(box.data.size() == 300, "%d-bit: %zu frames", bits, box.data.size());
        float worst = 0.0f;
        for (std::size_t i = 0; i < box.data.size() && i < host.frames(); ++i) {
            const float want = 0.5f * (host.ch[0][i] + host.ch[1][i]);
            worst = std::max(worst, std::abs(box.data[i] - want));
        }
        CHECK(worst < 1e-6f, "%d-bit: the readers disagree by %g", bits, double(worst));
        std::remove(path.c_str());
    }
    rmdir(dir.c_str());
}

void put16(std::ofstream& f, uint16_t v) { f.put(char(v & 0xff)); f.put(char(v >> 8)); }
void put32(std::ofstream& f, uint32_t v) { put16(f, uint16_t(v & 0xffff)); put16(f, uint16_t(v >> 16)); }

// A mono WAVE_FORMAT_EXTENSIBLE file: the fmt chunk's 40-byte form, whose real
// format tag is the first two bytes of the sub-format GUID.
void write_extensible(const std::string& path, uint16_t sub, uint16_t bits,
                      const std::vector<uint8_t>& payload) {
    std::ofstream f(path, std::ios::binary);
    f.write("RIFF", 4); put32(f, uint32_t(4 + 8 + 40 + 8 + payload.size())); f.write("WAVE", 4);
    f.write("fmt ", 4); put32(f, 40);
    put16(f, 0xFFFE); put16(f, 1); put32(f, 48000); put32(f, 48000u * (bits / 8u));
    put16(f, uint16_t(bits / 8)); put16(f, bits);
    put16(f, 22); put16(f, bits); put32(f, 4);              // cbSize, valid bits, channel mask
    put16(f, sub);                                          // GUID: tag, then the fixed tail
    const uint8_t tail[14] = {0x00, 0x00, 0x00, 0x00, 0x10, 0x00, 0x80, 0x00,
                              0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71};
    f.write(reinterpret_cast<const char*>(tail), sizeof tail);
    f.write("data", 4); put32(f, uint32_t(payload.size()));
    f.write(reinterpret_cast<const char*>(payload.data()), std::streamsize(payload.size()));
}

void test_extensible_is_read_by_its_sub_format() {
    const std::string dir = tmpdir();
    // 24-bit PCM: +half scale, then -half scale.
    const std::string p24 = dir + "/ext24.wav";
    write_extensible(p24, 1, 24, {0x00, 0x00, 0x40, 0x00, 0x00, 0xC0});
    arb::wav::Sample s;
    std::string e = arb::wav::load_mono(p24, s);
    CHECK(e.empty(), "extensible 24-bit PCM refused: %s", e.c_str());
    CHECK(s.data.size() == 2 && s.data[0] == 0.5f && s.data[1] == -0.5f,
          "extensible 24-bit read %zu frames, first %f", s.data.size(),
          s.data.empty() ? 0.0 : double(s.data[0]));
    arb::io::Audio host;
    CHECK(arb::io::read(p24, host).empty(), "the host reads the same file");

    // 32-bit PCM under EXTENSIBLE is not float: the box reader used to take
    // the bit depth as the answer and read integers as floats.
    const std::string p32 = dir + "/ext32pcm.wav";
    write_extensible(p32, 1, 32, {0x00, 0x00, 0x00, 0x40});
    arb::wav::Sample q;
    CHECK(!arb::wav::load_mono(p32, q).empty(), "32-bit PCM must be refused, not read as float");
    arb::io::Audio host2;
    CHECK(!arb::io::read(p32, host2).empty(), "the host refuses it too");

    // 32-bit float under EXTENSIBLE still loads.
    const std::string pf = dir + "/ext32f.wav";
    write_extensible(pf, 3, 32, {0x00, 0x00, 0x00, 0x3F});   // 0.5f
    arb::wav::Sample fl;
    e = arb::wav::load_mono(pf, fl);
    CHECK(e.empty() && fl.data.size() == 1 && fl.data[0] == 0.5f, "extensible float: %s",
          e.c_str());
    for (const std::string& p : {p24, p32, pf}) std::remove(p.c_str());
    rmdir(dir.c_str());
}

}  // namespace

int main() {
    test_each_depth_the_host_writes_loads_and_matches();
    test_extensible_is_read_by_its_sub_format();
    std::printf("%s\n", failures ? "wav: FAILED" : "wav: all checks passed");
    return failures ? 1 : 0;
}
