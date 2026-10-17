// device_config.hpp — what Device::start() asks its device for, filled into the
// ma_device_config it hands ma_device_init (device-os-spec.md § 6.3.4, #743).
//
// Its own header so a test can check the very fields the device is opened
// with, not only wire_format()'s answer (#811: a start() that asked for float
// whatever wire_format said passed every test). Includes miniaudio's
// declarations only: the implementation stays in device.cpp, so the test links
// nothing.
#pragma once

#include <cstdint>
#include <string>

#include <miniaudio.h>

#include "sample_format.hpp"

namespace arb::host {

// Fills the formats, channels, rate and period of `c` (made by
// ma_device_config_init); the device id, callback and user data stay start()'s.
// Returns whether the callback converts int32 (the s32 wire format).
inline bool ask_device_for(ma_device_config& c, const std::string& pcm, uint32_t sr,
                           uint32_t block) {
    const bool s32 = wire_format(pcm) == WireFormat::s32;
    const ma_format fmt = s32 ? ma_format_s32 : ma_format_f32;
    c.playback.format = fmt;
    c.playback.channels = 2;
    c.capture.format = fmt;
    c.capture.channels = 2;
    c.sampleRate = sr;
    c.periodSizeInFrames = block;
    return s32;
}

}  // namespace arb::host
