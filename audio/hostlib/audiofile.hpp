// audiofile.hpp — minimal WAV and NPY, because the goldens are both.
//
// WAV: 16-bit PCM and float32, mono or stereo, read and written by hand. No
// library: the audio process ships on a Pi and every dependency is a thing that
// can fail to cross-compile.
//
// NPY: the emulator's goldens are numpy arrays (audio/tests/golden/python/*.npy)
// and one of them peaks above 1.0, so routing them through a 16-bit WAV would
// change the very numbers under test. Reading float32/float64 1-D and (2,N)
// arrays (and up to (6,N): a modulator's outputs, a motion feed) is fifty lines; that is cheaper than a conversion step nobody trusts.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace arb::io {

struct Audio {
    uint32_t sample_rate = 0;                 // 0 = the file did not say (npy)
    std::vector<std::vector<float>> ch;       // 1 or 2 channels (NPY: up to 6), equal length

    uint32_t frames() const { return ch.empty() ? 0u : uint32_t(ch[0].size()); }
};

// "" on success, else a human-readable reason.
std::string read(const std::string& path, Audio& out);
// The same parse, on bytes that never became a file -- what the tape's tests
// have (the medium is a byte vector in `FakeStore`) and what repair-on-open
// will read a header with. Tolerates a `data` size larger than the bytes
// present: it clamps to what is there, which is what a file still being
// written looks like (tape-streaming-design.md § 3.5).
std::string read_wav(const std::vector<uint8_t>& bytes, Audio& out);
std::string write(const std::string& path, const Audio& in, int wav_bits);
// The same write, made crash-safe: `<path>.tmp`, `fsync`, `rename` over
// `path`, then `fsync` of the directory that now holds the new name
// (device-os-spec.md § 7.3, #93). A cut anywhere in it leaves the *previous*
// contents of `path` whole -- an in-place write leaves a truncated file, and
// on the device `path` is the only on-disk copy of a take. Costs one flush to
// the card, so it is for the files a project is made of, not for goldens.
std::string write_durable(const std::string& path, const Audio& in, int wav_bits);

}  // namespace arb::io
