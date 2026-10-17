// sample_format.hpp — the sample format the host asks its device for
// (device-os-spec.md § 6.3.4, #743).
//
// The engine renders float. A desktop's default device (and the bench's
// snd-dummy) is asked for float and converts as it likes. A card opened by
// name (`--device hw:wm8904` on P3) is asked for 32-bit integers: the WM8904
// offers S16_LE, S20_3LE, S24_LE and S32_LE, not float, and miniaudio, refused
// float, takes the first entry of its own list that the card offers -- 16-bit.
// S24_LE is no better: with the codec as clock master its bit clock lands on
// 2.4576 MHz and the frame runs at ~48.19 kHz. S32_LE is exact (3.072 MHz).
// No miniaudio here, so the test links nothing but this header.
#pragma once

#include <cstdint>
#include <string>

namespace arb::host {

enum class WireFormat { f32, s32 };

// What Device::start() asks for: s32 on both sides for a named ALSA PCM,
// float for the backend's default device.
inline WireFormat wire_format(const std::string& pcm) {
    return pcm.empty() ? WireFormat::f32 : WireFormat::s32;
}

// The conversions miniaudio's own ma_pcm_f32_to_s32 / ma_pcm_s32_to_f32 do
// (clip to [-1, 1], scale by 2^31 - 1 out and 2^31 in), one sample at a time,
// inline in the callback: nothing allocated, no extra buffer. One difference: a
// NaN goes out as silence, where a cast of NaN to int is undefined.
inline int32_t f32_to_s32(float x) {
    double d = x;
    if (d != d) return 0;  // NaN is the only value not equal to itself
    d = d < -1.0 ? -1.0 : (d > 1.0 ? 1.0 : d);
    return int32_t(d * 2147483647.0);
}

inline float s32_to_f32(int32_t x) { return float(double(x) / 2147483648.0); }

}  // namespace arb::host
