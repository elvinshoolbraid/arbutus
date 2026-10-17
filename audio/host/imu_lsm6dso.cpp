// See imu_lsm6dso.hpp. device-os-spec.md § 6.5, io-map § P3.

#include "imu_lsm6dso.hpp"

#include <cstring>
#include <initializer_list>

#if defined(__linux__)
#include <fcntl.h>
#include <sys/ioctl.h>
#include <unistd.h>
// <linux/i2c-dev.h> gives I2C_SLAVE and I2C_RDWR, <linux/i2c.h> the message
// struct; the smbus helpers need libi2c, which this driver does not use. A
// write is a plain write() of the register and its value; a read is one
// I2C_RDWR transfer (i2c_register_read, #418).
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
static_assert(I2C_M_RD == arb::host::kI2cMsgRead, "I2C_M_RD moved");
#endif

namespace arb::host {

namespace {

/// Two's-complement 16-bit little-endian, which is how every LSM6DSO output
/// register pair is laid out (datasheet § 9.16).
inline int16_t le16(const uint8_t* p) {
    return int16_t(uint16_t(p[0]) | (uint16_t(p[1]) << 8));
}

}  // namespace

const ImuMount* imu_mount(const char* name) {
    for (const ImuMount* m : {&lsm6dso::kMountP3, &lsm6dso::kMountTop,
                              &lsm6dso::kMountChip})
        if (name && std::strcmp(name, m->name) == 0) return m;
    return nullptr;
}

void i2c_register_read(uint8_t address, uint8_t* reg, uint8_t* dst,
                       uint16_t n, I2cMsg out[2]) {
    out[0] = I2cMsg{address, 0, 1, reg};
    out[1] = I2cMsg{address, kI2cMsgRead, n, dst};
}

bool Lsm6dso::init(I2cBus& bus, const char** why) {
    using namespace lsm6dso;
    bus_ = &bus;
    ok_ = false;
    auto fail = [&](const char* msg) {
        if (why) *why = msg;
        return false;
    };

    uint8_t who = 0;
    if (!bus.read(kWhoAmI, &who, 1)) return fail("no answer on the I2C bus");
    if (who != kWhoAmIValue) return fail("WHO_AM_I is not an LSM6DSO");

    // A soft reset first: the part may be mid-configuration from a previous
    // run of the host (systemd restarts it, § 4.2), and a half-configured IMU
    // reads plausible numbers, which is worse than none.
    if (!bus.write(kCtrl3C, kSwReset)) return fail("soft reset refused");
    // A part whose SW_RESET never clears is not configured and not healthy
    // (#598): a stuck reset would otherwise be marked present.
    bool reset_done = false;
    for (int i = 0; i < 100 && !reset_done; ++i) {
        uint8_t c3 = 0;
        if (!bus.read(kCtrl3C, &c3, 1)) return fail("reset never completed");
        reset_done = !(c3 & kSwReset);
    }
    if (!reset_done) return fail("reset never completed");

    // DS9: I3C must be disabled before anything else claims the bus.
    if (!bus.write(kCtrl9Xl, kCtrl9XlValue)) return fail("CTRL9_XL refused");
    if (!bus.write(kCtrl3C, kCtrl3CValue)) return fail("CTRL3_C refused");
    if (!bus.write(kCtrl1Xl, kCtrl1XlValue)) return fail("CTRL1_XL refused");
    if (!bus.write(kCtrl2G, kCtrl2GValue)) return fail("CTRL2_G refused");
    if (!bus.write(kInt1Ctrl, kInt1CtrlValue)) return fail("INT1_CTRL refused");

    samples_ = 0;
    errors_ = 0;
    ok_ = true;
    if (why) *why = "";
    return true;
}

bool Lsm6dso::ready() {
    using namespace lsm6dso;
    if (!ok_ || bus_ == nullptr) return false;
    uint8_t status = 0;
    if (!bus_->read(kStatusReg, &status, 1)) {
        ok_ = false;
        ++errors_;
        return false;
    }
    return (status & (kStatusXlda | kStatusGda)) == (kStatusXlda | kStatusGda);
}

bool Lsm6dso::sample(float out[6]) {
    using namespace lsm6dso;
    if (!ok_ || bus_ == nullptr) return false;
    // Twelve bytes in one burst: gyro X/Y/Z then accel X/Y/Z, with BDU on so
    // the pair cannot be half of one sample and half of the next.
    uint8_t raw[12];
    if (!bus_->read(kOutxLG, raw, sizeof raw)) {
        ok_ = false;
        ++errors_;
        return false;
    }
    // motion[] is accel first (PROTOCOL.md § Offline mode `motion`), the
    // registers are gyro first; the swap is here, once.
    float chip[6];
    for (int k = 0; k < 3; ++k) {
        chip[k] = float(le16(raw + 6 + 2 * k)) * kAccelScale;
        chip[3 + k] = float(le16(raw + 2 * k)) * kGyroScale;
    }
    // Chip axes -> the instrument frame (modulation-spec § 13, #1048).
    const ImuMount& m = *mount_;
    for (int k = 0; k < 6; ++k) out[k] = float(m.sign[k]) * chip[m.src[k]];
    ++samples_;
    return true;
}

bool Lsm6dso::feed(float out[6]) {
    if (!ok_) return false;
    if (ready() && sample(out)) return true;
    if (ok_) return false;                  // healthy, no new data yet
    for (int k = 0; k < 6; ++k) out[k] = 0.0f;   // failed on this turn
    return true;
}

// -- the Linux bus --------------------------------------------------------

LinuxI2c::~LinuxI2c() { close(); }

bool LinuxI2c::open(const char* path, uint8_t address, const char** why) {
    address_ = address;
#if defined(__linux__)
    close();
    fd_ = ::open(path, O_RDWR | O_CLOEXEC);
    if (fd_ < 0) {
        if (why) *why = "cannot open the i2c device";
        return false;
    }
    if (::ioctl(fd_, I2C_SLAVE, int(address)) < 0) {
        close();
        if (why) *why = "the i2c address is busy (a kernel driver has it?)";
        return false;
    }
    if (why) *why = "";
    return true;
#else
    (void)path;
    if (why) *why = "i2c-dev is Linux only";
    return false;
#endif
}

void LinuxI2c::close() {
#if defined(__linux__)
    if (fd_ >= 0) ::close(fd_);
#endif
    fd_ = -1;
}

bool LinuxI2c::write(uint8_t reg, uint8_t value) {
#if defined(__linux__)
    if (fd_ < 0) return false;
    const uint8_t buf[2] = {reg, value};
    return ::write(fd_, buf, sizeof buf) == ssize_t(sizeof buf);
#else
    (void)reg;
    (void)value;
    return false;
#endif
}

bool LinuxI2c::read(uint8_t reg, uint8_t* dst, std::size_t n) {
#if defined(__linux__)
    if (fd_ < 0 || n == 0 || n > 0xFFFF) return false;
    // The register address, a repeated START, the burst: one transfer. The
    // LSM6DSO's auto-increment (IF_INC) walks the rest of the burst for us.
    I2cMsg ours[2];
    i2c_register_read(address_, &reg, dst, uint16_t(n), ours);
    struct i2c_msg msgs[2];
    for (int k = 0; k < 2; ++k) {
        msgs[k].addr = ours[k].addr;
        msgs[k].flags = ours[k].flags;
        msgs[k].len = ours[k].len;
        msgs[k].buf = ours[k].buf;
    }
    struct i2c_rdwr_ioctl_data data;
    data.msgs = msgs;
    data.nmsgs = 2;
    return ::ioctl(fd_, I2C_RDWR, &data) == 2;
#else
    (void)reg;
    (void)dst;
    (void)n;
    return false;
#endif
}

}  // namespace arb::host
