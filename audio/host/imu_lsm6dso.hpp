// IMU — ST LSM6DSO -> `arb_host.motion[6]` (device-os-spec.md § 6.5).
//
// The sensor is on I2C1 at 0x6A (SA0 -> GND), on the `IO3V3` rail, with `INT1`
// on GPIO16 and **no pull-up anywhere on that line** — INT1 high at power-up
// selects I3C and disables I2C (io-map § P3, datasheet review DS9). It belongs
// to `arbutus-audio` and not to the UI because modulation-spec §13 puts the
// read on the host's control thread at ~200 Hz and hands it to the audio
// thread as a host-block field; a reading that crosses a process boundary
// every 5 ms would be the socket's busiest message by an order of magnitude.
//
// Userspace `i2c-dev`, not the kernel's `st_lsm6dsx` IIO driver: the read is
// six registers, and an IIO buffer would add a kernel trigger, a character
// device and a second consumer for no gain (§ 6.5).
//
// **Nothing here allocates after `init`.** `sample()` is called from the
// control thread, but the values it writes are read by the audio thread
// through `Ck::Motion`, and the rule in `audio/CLAUDE.md` is that the path
// into the audio thread does not allocate. `tests/test_imu.cpp` counts
// `operator new` to prove it (the `test_events.cpp` pattern).
//
// The LSM6DSOX (P3's BOM fallback, #700) answers the same WHO_AM_I and has the
// same addresses and bit layouts for every register used here (DS12814 § 9),
// so this driver serves either part unchanged.
//
// This file has never talked to a chip. There is no P3 board.

#ifndef ARB_HOST_IMU_LSM6DSO_HPP
#define ARB_HOST_IMU_LSM6DSO_HPP

#include <cstddef>
#include <cstdint>

namespace arb::host {

// -- the register map (LSM6DSO datasheet § 9) -----------------------------
namespace lsm6dso {

constexpr uint8_t kAddress = 0x6A;      // SA0 -> GND; 0x6B if SA0 -> VDDIO
constexpr uint8_t kWhoAmI = 0x0F;
constexpr uint8_t kWhoAmIValue = 0x6C;

constexpr uint8_t kInt1Ctrl = 0x0D;
constexpr uint8_t kCtrl1Xl = 0x10;
constexpr uint8_t kCtrl2G = 0x11;
constexpr uint8_t kCtrl3C = 0x12;
constexpr uint8_t kCtrl9Xl = 0x18;
constexpr uint8_t kStatusReg = 0x1E;
constexpr uint8_t kOutxLG = 0x22;       // gyro X..Z then accel X..Z, 12 bytes
constexpr uint8_t kI3cBusAvb = 0x62;

// § 6.5: accel +-4 g, gyro +-500 dps, both at 208 Hz ODR, BDU on.
//   CTRL1_XL = ODR 0101 (208 Hz) | FS_XL 10 (+-4 g)
//   CTRL2_G  = ODR 0101 (208 Hz) | FS_G  01 (+-500 dps)
//   CTRL3_C  = BDU | IF_INC  (block data update: no half-updated pair)
//   CTRL9_XL = defaults | I3C_disable  (DS9: I3C must not win the bus)
//   INT1_CTRL= DRDY_XL | DRDY_G        (the line P3 routes to GPIO16)
constexpr uint8_t kCtrl1XlValue = 0x58;
constexpr uint8_t kCtrl2GValue = 0x54;
constexpr uint8_t kCtrl3CValue = 0x44;
constexpr uint8_t kCtrl9XlValue = 0xE2;
constexpr uint8_t kInt1CtrlValue = 0x03;
constexpr uint8_t kSwReset = 0x01;      // CTRL3_C bit 0, self-clearing

constexpr uint8_t kStatusXlda = 0x01;
constexpr uint8_t kStatusGda = 0x02;

// Sensitivity at the ranges above, straight off the datasheet's table:
// 0.122 mg/LSB and 17.50 mdps/LSB. Units out are **g and deg/s**, the offline
// `motion` message's convention (PROTOCOL.md § Offline mode), so a box's
// arithmetic is the same on a Mac and on the instrument.
constexpr float kAccelScale = 0.122f / 1000.0f;
constexpr float kGyroScale = 17.50f / 1000.0f;

//: how long after a bus error before the driver tries again (§ 6.5).
constexpr double kRetrySeconds = 5.0;

}  // namespace lsm6dso

/// One I2C target, as little of an interface as the driver needs.
///
/// Virtual because the only other implementation is the test's fake register
/// map; the call rate is 200 Hz, so the indirect call is not a cost anyone can
/// measure.
struct I2cBus {
    virtual ~I2cBus() = default;
    /// One register write. False = the transfer failed.
    virtual bool write(uint8_t reg, uint8_t value) = 0;
    /// `n` bytes from `reg` onwards (auto-increment is on, CTRL3_C IF_INC).
    virtual bool read(uint8_t reg, uint8_t* dst, std::size_t n) = 0;
};

/// How the part sits on a board: the map from the chip's axes to the
/// instrument's frame (modulation-spec.md § 13, #1048), so the same tilt reads
/// the same on every board. `out[k] = sign[k] * chip[src[k]]`, where `chip` is
/// the driver's own order (accel x, y, z, then gyro x, y, z, ST's Fig. 3 axes)
/// and `out` is `motion[]`. A signed permutation, so a part turned 90° is a
/// table, not new code.
struct ImuMount {
    const char* name;
    uint8_t src[6];
    int8_t sign[6];
};

namespace lsm6dso {

// The instrument frame (§ 13): accel x +1 g with the right edge straight
// down, y +1 g with the front (player's) edge straight down, z +1 g flat and
// face up; gyro x + while the right edge goes down, y + while the front edge
// goes down, z + turning clockwise seen from above. The chip reads +1 g on an
// axis pointing up and its gyro is right-handed, so for a part whose chip X/Y/Z
// point right/rear/up the map is x = -ax, y = ay, z = az, gyro x = gy (about
// the rear axis), y = gx (about the right axis), z = -gz.
//
// Board axes: KiCad's page seen from the top, +x to the right, up the page to
// the rear (place_p3.py `P()`). The footprint (LGA-14, pin 1 top left) puts
// chip X right, Y to the rear, Z out of the face it is on at rotation 0.

//: a top-side part at rotation 0 (a breakout, a later board).
inline constexpr ImuMount kMountTop = {"top", {0, 1, 2, 4, 3, 5}, {-1, 1, 1, 1, 1, -1}};
//: P3's `U12`: bottom side, rotation 0. KiCad mirrors a bottom part left to
//: right, so chip X points left, Y to the rear, Z down (180° about Y).
inline constexpr ImuMount kMountP3 = {"p3", {0, 1, 2, 4, 3, 5}, {1, 1, -1, 1, -1, 1}};
//: the chip's own axes, unmapped: for reading the part at bring-up.
inline constexpr ImuMount kMountChip = {"chip", {0, 1, 2, 3, 4, 5}, {1, 1, 1, 1, 1, 1}};

}  // namespace lsm6dso

/// The mount called `name` (`p3`, `top`, `chip`), or null.
const ImuMount* imu_mount(const char* name);

/// The LSM6DSO driver: init once, then `sample()` whenever data is ready.
///
/// Deliberately not RAII over the bus: the bus outlives the driver (the host
/// owns the file descriptor) and re-init after a bus error must not reopen it.
class Lsm6dso {
public:
    /// WHO_AM_I, soft reset, then the six configuration registers.
    ///
    /// `why` is set to a static string on failure — never allocated, never
    /// formatted; the caller logs it once (§ 6.5 Failure mode).
    bool init(I2cBus& bus, const char** why = nullptr);

    /// STATUS_REG says both the accelerometer and the gyroscope have new data.
    ///
    /// § 6.5 asks for `INT1` as a `gpiod` rising-edge event so the control
    /// thread sleeps between samples. **As built this is a status-register
    /// read instead** — see the note in `audio/CLAUDE.md` § IMU: the edge wait
    /// is an optimisation that saves wake-ups, it cannot be tested without a
    /// board, and the pin is configured here either way, so it is P3-arrival
    /// work rather than something to write blind.
    bool ready();

    /// Six floats: accel x, y, z in **g**, then gyro x, y, z in **deg/s**, in
    /// the instrument's frame through the mount (P3's unless told otherwise).
    /// False means the transfer failed and `out` was not touched.
    bool sample(float out[6]);

    /// The board's mount; the reference must outlive the driver (the tables
    /// above are constants).
    void set_mount(const ImuMount& m) { mount_ = &m; }
    const ImuMount& mount() const { return *mount_; }

    /// One turn of the host's poll loop on a healthy driver (#598): true when
    /// `out` holds what the engine should be sent. That is a fresh sample, or,
    /// on the turn the bus fails, six zeros: all-zero motion is "no sensor"
    /// (PROTOCOL.md § Motion), and without it the engine would hold the last
    /// tilt through the whole outage. False: nothing new, `out` untouched.
    bool feed(float out[6]);

    /// False after a failed transfer; `init` again to clear it.
    bool healthy() const { return ok_; }

    /// How many samples and how many failed transfers — the journal's numbers.
    long samples() const { return samples_; }
    long errors() const { return errors_; }

private:
    I2cBus* bus_ = nullptr;
    const ImuMount* mount_ = &lsm6dso::kMountP3;
    bool ok_ = false;
    long samples_ = 0;
    long errors_ = 0;
};

/// One message of a combined `I2C_RDWR` transfer: the kernel's `struct
/// i2c_msg` (`linux/i2c.h`) without the Linux header, so the shape a read
/// hands the kernel can be tested on any machine.
struct I2cMsg {
    uint16_t addr;
    uint16_t flags;     // 0 = write, kI2cMsgRead = read
    uint16_t len;
    uint8_t* buf;
};
constexpr uint16_t kI2cMsgRead = 0x0001;    // I2C_M_RD

/// A register read as **one** transfer (#418): a write of `*reg`, a repeated
/// START, then `n` bytes into `dst`, all at `address`. A plain `write()` then
/// `read()` is two transfers with a STOP between them, and the LSM6DSO asks
/// for a repeated START there (datasheet p18 § 5.1.1).
void i2c_register_read(uint8_t address, uint8_t* reg, uint8_t* dst,
                       uint16_t n, I2cMsg out[2]);

/// `/dev/i2c-N` through `i2c-dev`. Linux only; on Darwin `open()` returns
/// `false` and the host runs with `motion[]` at zero, which is the emulator's
/// behaviour and is correct there.
class LinuxI2c : public I2cBus {
public:
    ~LinuxI2c() override;
    /// `path` is `/dev/i2c-1` on the CM4 (§ 6.5, io-map § P3).
    bool open(const char* path, uint8_t address, const char** why = nullptr);
    void close();
    bool is_open() const { return fd_ >= 0; }

    bool write(uint8_t reg, uint8_t value) override;
    bool read(uint8_t reg, uint8_t* dst, std::size_t n) override;

private:
    int fd_ = -1;
    uint8_t address_ = 0;   // each I2C_RDWR message carries it
};

}  // namespace arb::host

#endif  // ARB_HOST_IMU_LSM6DSO_HPP
