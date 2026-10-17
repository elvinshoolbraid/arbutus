// wav.hpp — a read-only WAV loader for engines that play files.
//
// The library is 16-bit WAV only (repo CLAUDE.md § Library); 24-bit PCM and
// float32 are read too because that is what `bake` (24-bit by default) and
// `arbutus-run --out` write, and a sample recorded on the device should be
// playable without a conversion step. It accepts what the host's own reader
// (hostlib/audiofile.cpp) accepts, depth for depth (#675).
//
// Control thread only: this allocates and does file I/O, so it belongs in
// create() or load_asset() and nowhere near process() (§3.7.1).
//
// Stereo is mixed down to mono, which is what the emulator's sampler does
// (engines/sampler.py load_wav) and what keeps a sample one delay-free buffer
// per voice. Resampling is the caller's job: an engine that plays a file
// chromatically is already resampling and does not need it done twice.
#pragma once

#include <cstdint>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

namespace arb::wav {

struct Sample {
    std::vector<float> data;        // mono
    uint32_t sample_rate = 0;

    bool empty() const { return data.size() < 2; }
};

namespace detail {

inline uint32_t rd32(const unsigned char* p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
inline uint16_t rd16(const unsigned char* p) {
    return uint16_t(uint32_t(p[0]) | (uint32_t(p[1]) << 8));
}

}  // namespace detail

// "" on success, else a reason worth printing.
inline std::string load_mono(const std::string& path, Sample& out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return "cannot open " + path;
    std::vector<unsigned char> b((std::istreambuf_iterator<char>(f)),
                                 std::istreambuf_iterator<char>());
    if (b.size() < 44) return path + ": too short to be a WAV";
    if (std::memcmp(b.data(), "RIFF", 4) || std::memcmp(b.data() + 8, "WAVE", 4))
        return path + ": not a RIFF/WAVE file";

    uint16_t format = 0, channels = 0, bits = 0;
    uint32_t rate = 0;
    const unsigned char* data = nullptr;
    std::size_t data_len = 0;

    std::size_t p = 12;
    while (p + 8 <= b.size()) {
        const uint32_t n = detail::rd32(b.data() + p + 4);
        const std::size_t body = p + 8;
        if (body + n > b.size() && std::memcmp(b.data() + p, "data", 4)) break;
        if (!std::memcmp(b.data() + p, "fmt ", 4) && n >= 16) {
            format = detail::rd16(b.data() + body);
            channels = detail::rd16(b.data() + body + 2);
            rate = detail::rd32(b.data() + body + 4);
            bits = detail::rd16(b.data() + body + 14);
            // WAVE_FORMAT_EXTENSIBLE: the real tag opens the sub-format GUID.
            if (format == 0xFFFE && n >= 40) format = detail::rd16(b.data() + body + 24);
        } else if (!std::memcmp(b.data() + p, "data", 4)) {
            data = b.data() + body;
            // A truncated file still plays what it has; refusing a kit over a
            // missing last frame helps nobody.
            data_len = (body + n <= b.size()) ? n : b.size() - body;
        }
        p = body + n + (n & 1);
    }
    if (!data || !channels) return path + ": no fmt/data chunk";
    const bool is_float = (format == 3);
    if (!is_float && format != 1)
        return path + ": unsupported WAV format tag " + std::to_string(format);
    if (is_float ? bits != 32 : (bits != 16 && bits != 24))
        return path + ": unsupported bit depth " + std::to_string(bits);

    const std::size_t bytes = std::size_t(bits / 8);
    const std::size_t stride = bytes * channels;
    const std::size_t frames = stride ? data_len / stride : 0;
    out.sample_rate = rate ? rate : 48000u;
    out.data.assign(frames, 0.0f);
    for (std::size_t i = 0; i < frames; ++i) {
        double acc = 0.0;
        for (std::size_t c = 0; c < channels; ++c) {
            const unsigned char* q = data + i * stride + c * bytes;
            if (is_float) {
                float v;
                const uint32_t u = detail::rd32(q);
                std::memcpy(&v, &u, sizeof v);
                acc += double(v);
            } else if (bits == 24) {                 // sign-extend, as audiofile.cpp does
                int32_t v = int32_t(uint32_t(q[0]) | (uint32_t(q[1]) << 8) | (uint32_t(q[2]) << 16));
                if (v & 0x800000) v -= 0x1000000;
                acc += double(v) / 8388608.0;
            } else {
                acc += double(int16_t(detail::rd16(q))) / 32768.0;
            }
        }
        out.data[i] = float(acc / double(channels));
    }
    return {};
}

}  // namespace arb::wav
