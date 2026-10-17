#include "audiofile.hpp"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fstream>

#include <fcntl.h>
#include <unistd.h>

namespace arb::io {
namespace {

bool ends_with(const std::string& s, const char* suffix) {
    const std::size_t n = std::strlen(suffix);
    return s.size() >= n && s.compare(s.size() - n, n, suffix) == 0;
}

std::string slurp(const std::string& path, std::vector<uint8_t>& bytes) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return "cannot open " + path;
    f.seekg(0, std::ios::end);
    const std::streamoff n = f.tellg();
    if (n < 0) return "cannot size " + path;
    f.seekg(0);
    bytes.resize(std::size_t(n));
    if (n && !f.read(reinterpret_cast<char*>(bytes.data()), n)) return "short read on " + path;
    return {};
}

uint16_t rd16(const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); }
uint32_t rd32(const uint8_t* p) {
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
void wr16(std::ofstream& f, uint16_t v) { char b[2] = {char(v & 0xff), char(v >> 8)}; f.write(b, 2); }
void wr32(std::ofstream& f, uint32_t v) {
    char b[4] = {char(v & 0xff), char((v >> 8) & 0xff), char((v >> 16) & 0xff), char((v >> 24) & 0xff)};
    f.write(b, 4);
}


std::string write_wav(const std::string& path, const Audio& a, int bits) {
    if (bits != 16 && bits != 24 && bits != 32) return "wav bit depth must be 16, 24 or 32";
    std::ofstream f(path, std::ios::binary);
    if (!f) return "cannot write " + path;

    const uint16_t channels = uint16_t(a.ch.size());
    const uint32_t n = a.frames();
    const uint16_t bytes = uint16_t(bits / 8);
    const uint32_t data_len = n * channels * bytes;
    const uint16_t format = (bits == 32) ? 3 : 1;   // 16 and 24 are integer PCM

    f.write("RIFF", 4); wr32(f, 36 + data_len); f.write("WAVE", 4);
    f.write("fmt ", 4); wr32(f, 16);
    wr16(f, format); wr16(f, channels);
    wr32(f, a.sample_rate ? a.sample_rate : 48000u);
    wr32(f, (a.sample_rate ? a.sample_rate : 48000u) * channels * bytes);
    wr16(f, uint16_t(channels * bytes)); wr16(f, uint16_t(bits));
    f.write("data", 4); wr32(f, data_len);

    for (uint32_t i = 0; i < n; ++i)
        for (uint16_t c = 0; c < channels; ++c) {
            const float v = a.ch[c][i];
            if (bits == 16) {
                const float s = v < -1.0f ? -1.0f : (v > 1.0f ? 1.0f : v);
                wr16(f, uint16_t(int16_t(s < 0 ? s * 32768.0f - 0.5f : s * 32767.0f + 0.5f)));
            } else if (bits == 24) {
                const double s = v < -1.0f ? -1.0 : (v > 1.0f ? 1.0 : double(v));
                const int32_t q = int32_t(s < 0 ? s * 8388608.0 - 0.5 : s * 8388607.0 + 0.5);
                const char b3[3] = {char(q & 0xff), char((q >> 8) & 0xff), char((q >> 16) & 0xff)};
                f.write(b3, 3);
            } else {
                uint32_t u;
                std::memcpy(&u, &v, 4);
                wr32(f, u);
            }
        }
    // Close before testing: the last buffer is written out here, and a card
    // that fills during it fails only this call (#670). Left to the
    // destructor, that failure was discarded and a short file reported whole.
    f.close();
    return f ? std::string{} : "write failed on " + path;
}

// ---- NPY -------------------------------------------------------------------

std::string read_npy(const std::vector<uint8_t>& b, Audio& out) {
    if (b.size() < 10 || std::memcmp(b.data(), "\x93NUMPY", 6)) return "not an npy file";
    const uint8_t major = b[6];
    std::size_t hlen, hstart;
    if (major == 1) { hlen = rd16(b.data() + 8); hstart = 10; }
    else            { hlen = rd32(b.data() + 8); hstart = 12; }
    if (hstart + hlen > b.size()) return "npy header runs past the end";
    const std::string head(reinterpret_cast<const char*>(b.data() + hstart), hlen);

    const bool f8 = head.find("'<f8'") != std::string::npos || head.find("'f8'") != std::string::npos;
    const bool f4 = head.find("'<f4'") != std::string::npos || head.find("'f4'") != std::string::npos;
    if (!f4 && !f8) return "npy must be float32 or float64";
    if (head.find("'fortran_order': True") != std::string::npos)
        return "fortran-ordered npy is not supported";

    const std::size_t sp = head.find("'shape':");
    if (sp == std::string::npos) return "npy header has no shape";
    std::vector<std::size_t> shape;
    for (std::size_t i = head.find('(', sp); i < head.size() && head[i] != ')'; ++i)
        if (head[i] >= '0' && head[i] <= '9') {
            std::size_t v = 0;
            while (i < head.size() && head[i] >= '0' && head[i] <= '9') v = v * 10 + std::size_t(head[i++] - '0');
            shape.push_back(v);
            --i;
        }
    if (shape.empty() || shape.size() > 2) return "npy must be 1-D or 2-D";

    std::size_t channels = 1, frames = shape[0];
    if (shape.size() == 2) {
        // (C,N) when the first axis is small, (N,C) otherwise: a file with
        // six frames or seven channels is not a thing we have to read. Up to
        // six: a modulator's golden is one channel per output
        // (ARB_MAX_OUTPUTS = 4), and arbutus-run --motion is arb_host.motion[6].
        if (shape[0] <= 6) { channels = shape[0]; frames = shape[1]; }
        else if (shape[1] <= 6) { channels = shape[1]; frames = shape[0]; }
        else return "npy 2-D array is not (channels, frames)";
    }

    const std::size_t width = f4 ? 4 : 8;
    const std::size_t need = channels * frames * width;
    if (hstart + hlen + need > b.size()) return "npy data is shorter than its shape";
    const uint8_t* d = b.data() + hstart + hlen;

    auto value = [&](std::size_t k) -> float {
        if (f4) { uint32_t u = rd32(d + k * 4); float v; std::memcpy(&v, &u, 4); return v; }
        uint64_t u = 0;
        for (int i = 7; i >= 0; --i) u = (u << 8) | d[k * 8 + std::size_t(i)];
        double v;
        std::memcpy(&v, &u, 8);
        return float(v);
    };

    out.sample_rate = 0;                       // npy carries no rate; --sr decides
    out.ch.assign(channels, std::vector<float>(frames));
    const bool row_major_channels = (shape.size() == 2 && shape[0] == channels);
    for (std::size_t c = 0; c < channels; ++c)
        for (std::size_t i = 0; i < frames; ++i)
            out.ch[c][i] = value(row_major_channels ? c * frames + i : i * channels + c);
    return {};
}

std::string write_npy(const std::string& path, const Audio& a) {
    std::ofstream f(path, std::ios::binary);
    if (!f) return "cannot write " + path;
    std::string head = "{'descr': '<f4', 'fortran_order': False, 'shape': (" +
                       std::to_string(a.ch.size()) + ", " + std::to_string(a.frames()) + "), }";
    while ((10 + head.size() + 1) % 64) head += ' ';     // numpy wants 64-byte alignment
    head += '\n';
    f.write("\x93NUMPY", 6);
    const char ver[2] = {1, 0};
    f.write(ver, 2);
    wr16(f, uint16_t(head.size()));
    f.write(head.data(), std::streamsize(head.size()));
    for (const std::vector<float>& c : a.ch)
        for (float v : c) { uint32_t u; std::memcpy(&u, &v, 4); wr32(f, u); }
    // Close before testing: the last buffer is written out here, and a card
    // that fills during it fails only this call (#670). Left to the
    // destructor, that failure was discarded and a short file reported whole.
    f.close();
    return f ? std::string{} : "write failed on " + path;
}

}  // namespace

// ---- WAV -------------------------------------------------------------------

std::string read_wav(const std::vector<uint8_t>& b, Audio& out) {
    if (b.size() < 12 || std::memcmp(b.data(), "RIFF", 4) || std::memcmp(b.data() + 8, "WAVE", 4))
        return "not a RIFF/WAVE file";

    uint16_t format = 0, channels = 0, bits = 0;
    uint32_t rate = 0;
    const uint8_t* data = nullptr;
    std::size_t data_len = 0;

    std::size_t p = 12;
    while (p + 8 <= b.size()) {
        const uint32_t id = rd32(b.data() + p);
        uint32_t len = rd32(b.data() + p + 4);
        const std::size_t body = p + 8;
        if (body + len > b.size()) len = uint32_t(b.size() - body);   // tolerate a short tail
        if (id == 0x20746d66 /* "fmt " */ && len >= 16) {
            format = rd16(b.data() + body);
            channels = rd16(b.data() + body + 2);
            rate = rd32(b.data() + body + 4);
            bits = rd16(b.data() + body + 14);
            if (format == 0xFFFE && len >= 40)      // EXTENSIBLE: the real tag is in the GUID
                format = rd16(b.data() + body + 24);
        } else if (id == 0x61746164 /* "data" */) {
            data = b.data() + body;
            data_len = len;
        }
        p = body + len + (len & 1);
    }

    if (!channels || channels > 2) return "only mono and stereo WAV are supported";
    if (!data) return "no data chunk";
    if (format == 1 && bits != 16 && bits != 24) return "PCM WAV must be 16- or 24-bit";
    if (format == 3 && bits != 32) return "float WAV must be 32-bit";
    if (format != 1 && format != 3) return "unsupported WAV format tag";

    const std::size_t stride = std::size_t(channels) * (bits / 8);
    const std::size_t n = stride ? data_len / stride : 0;
    out.sample_rate = rate;
    out.ch.assign(channels, std::vector<float>(n));
    for (std::size_t i = 0; i < n; ++i)
        for (uint16_t c = 0; c < channels; ++c) {
            const uint8_t* s = data + i * stride + std::size_t(c) * (bits / 8);
            if (format == 1 && bits == 16) {
                out.ch[c][i] = float(int16_t(rd16(s))) / 32768.0f;
            } else if (format == 1) {
                int32_t q = int32_t(uint32_t(s[0]) | (uint32_t(s[1]) << 8) | (uint32_t(s[2]) << 16));
                if (q & 0x800000) q -= 0x1000000;
                out.ch[c][i] = float(q) / 8388608.0f;
            } else {
                const uint32_t u = rd32(s);
                float v;
                std::memcpy(&v, &u, 4);
                out.ch[c][i] = v;
            }
        }
    return {};
}

std::string read(const std::string& path, Audio& out) {
    std::vector<uint8_t> bytes;
    if (std::string e = slurp(path, bytes); !e.empty()) return e;
    if (ends_with(path, ".npy")) return read_npy(bytes, out);
    return read_wav(bytes, out);
}

std::string write(const std::string& path, const Audio& in, int wav_bits) {
    if (ends_with(path, ".npy")) return write_npy(path, in);
    return write_wav(path, in, wav_bits);
}

namespace {

// Flush one path's own data. `O_WRONLY`: POSIX lets `fsync` fail with EBADF on
// a descriptor that is not open for writing, and a directory cannot be opened
// that way -- which is why the directory below is a separate, read-only case.
std::string fsync_file(const std::string& path) {
    const int fd = ::open(path.c_str(), O_WRONLY);
    if (fd < 0) return "cannot reopen " + path + ": " + std::strerror(errno);
    const bool ok = ::fsync(fd) == 0;
    const std::string why = ok ? std::string{} : std::strerror(errno);
    ::close(fd);
    return ok ? std::string{} : "cannot flush " + path + ": " + why;
}

// The directory entry the rename created. Best effort on purpose: the bytes
// are already on the medium and the new name is already visible, so a
// filesystem that refuses this (some do) must not turn a completed save into
// a reported failure. What is lost if it refuses is only the guarantee about
// which of the two names survives a cut in the next moment.
void fsync_dir(const std::string& path) {
    const std::size_t slash = path.find_last_of('/');
    const std::string dir = (slash == std::string::npos) ? std::string(".")
                                                         : path.substr(0, slash ? slash : 1);
    const int fd = ::open(dir.c_str(), O_RDONLY);
    if (fd < 0) return;
    ::fsync(fd);
    ::close(fd);
}

}  // namespace

std::string write_durable(const std::string& path, const Audio& in, int wav_bits) {
    const std::string tmp = path + ".tmp";
    // Dispatch on the *destination's* suffix: `tmp` ends in `.tmp` and would
    // take the WAV branch whatever was asked for.
    std::string e = ends_with(path, ".npy") ? write_npy(tmp, in) : write_wav(tmp, in, wav_bits);
    if (e.empty()) e = fsync_file(tmp);
    if (!e.empty()) {
        ::unlink(tmp.c_str());
        return e;
    }
    if (::rename(tmp.c_str(), path.c_str()) != 0) {
        const std::string why = std::strerror(errno);
        ::unlink(tmp.c_str());
        return "cannot replace " + path + ": " + why;
    }
    fsync_dir(path);
    return {};
}

}  // namespace arb::io
