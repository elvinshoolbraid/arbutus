// host/sample_format — the format Device::start() asks its device for, and the
// int32 conversions the callback does (device-os-spec.md § 6.3.4, #743). No
// framework (test_events.cpp is the pattern) and no device: what the real
// card negotiates is bring-up row 8.4.2's to read. test_config checks the
// ma_device_config start() hands ma_device_init (#811), through miniaudio's
// declarations only.

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>

#include <device_config.hpp>
#include <sample_format.hpp>

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

using namespace arb::host;

// Each input through a volatile, so the conversion runs at run time: folded at
// compile time, an out-of-range or NaN cast to int is whatever GCC chose, and
// a missing clip or NaN guard passed (seen, #743).
int32_t out(float x) {
    volatile float v = x;
    return f32_to_s32(v);
}

void test_format() {
    // P3's card by name: S32_LE, never miniaudio's 16-bit first choice.
    CHECK(wire_format("hw:wm8904") == WireFormat::s32, "a named PCM must ask for s32");
    CHECK(wire_format("hw:Dummy") == WireFormat::s32, "any named PCM asks for s32");
    // A desktop, the mock's host, the bench's default device: float, unchanged.
    CHECK(wire_format("") == WireFormat::f32, "the default device must stay float");
}

// #811: the fields themselves, both directions. wire_format() alone passed with
// start() asking for float on either side.
void test_config() {
    ma_device_config c{};
    CHECK(ask_device_for(c, "hw:wm8904", 48000, 64), "a named PCM converts int32");
    CHECK(c.playback.format == ma_format_s32, "named PCM, playback: format %d",
          int(c.playback.format));
    CHECK(c.capture.format == ma_format_s32, "named PCM, capture: format %d",
          int(c.capture.format));
    CHECK(c.playback.channels == 2 && c.capture.channels == 2, "stereo both ways");
    CHECK(c.sampleRate == 48000 && c.periodSizeInFrames == 64, "rate %u, period %u",
          c.sampleRate, c.periodSizeInFrames);

    ma_device_config d{};
    CHECK(!ask_device_for(d, "", 48000, 64), "the default device renders float");
    CHECK(d.playback.format == ma_format_f32, "default, playback: format %d",
          int(d.playback.format));
    CHECK(d.capture.format == ma_format_f32, "default, capture: format %d",
          int(d.capture.format));
}

void test_out() {
    CHECK(out(0.0f) == 0, "silence");
    CHECK(out(1.0f) == 2147483647, "full scale up: %d", out(1.0f));
    CHECK(out(-1.0f) == -2147483647, "full scale down: %d", out(-1.0f));
    // Clipped, never wrapped: a loud engine must not flip sign at the DAC.
    CHECK(out(1.5f) == 2147483647, "clip up: %d", out(1.5f));
    CHECK(out(-7.0f) == -2147483647, "clip down: %d", out(-7.0f));
    CHECK(out(std::numeric_limits<float>::infinity()) == 2147483647, "+inf clips");
    CHECK(out(std::numeric_limits<float>::quiet_NaN()) == 0, "NaN is silence");
    CHECK(out(0.5f) == 1073741823, "half: %d", out(0.5f));
    // 32 bits carry what 16 would lose: a step of 2^-20 survives.
    const float tiny = std::ldexp(1.0f, -20);
    CHECK(out(tiny) == 2047, "2^-20 -> %d", out(tiny));
}

void test_in() {
    CHECK(s32_to_f32(0) == 0.0f, "silence");
    CHECK(s32_to_f32(INT32_MIN) == -1.0f, "the most negative sample is -1");
    CHECK(s32_to_f32(1073741824) == 0.5f, "half");
    // Out and back is within float's precision across the range.
    for (float x = -1.0f; x <= 1.0f; x += 0.0625f) {
        const float y = s32_to_f32(f32_to_s32(x));
        CHECK(std::fabs(y - x) < 1e-6f, "round trip %g -> %g", double(x), double(y));
    }
}

}  // namespace

int main() {
    test_format();
    test_config();
    test_out();
    test_in();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("sample_format: all checks passed\n");
    return 0;
}
