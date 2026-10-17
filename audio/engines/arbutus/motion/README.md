# arbutus/motion

The IMU as a modulator (`modulation-spec.md` §3.2, §13). `caps: modulator`.
Reads `arb_host.motion[6]` — accelerometer x y z in **g**, gyro x y z in
**deg/s** (the LSM6DSO's units), sampled by the host on the control thread. No
I2C here. With no sensor fitted the host sends zeros, and **all-zero motion
gives four zero outputs**, so a route from a case without an IMU sits on its
knob; the page's `NO SENSOR` is the UI's business.

## Outputs

| id | Name | Range | What |
|---|---|---|---|
| `tilt_x` | TILT-X | ±1 | accel x in g, clamped ±1 (1 g = the case on its edge), smoothed by SMOOTH. Flat on a table = 0; **+ with the right edge down** (modulation-spec § 13's instrument frame; TILT-Y + front edge down, SPIN + clockwise from above) |
| `tilt_y` | TILT-Y | ±1 | the same for y |
| `shake` | SHAKE | 0 … 1 | magnitude of the acceleration after a 20 ms high-pass per axis, over SHK-SENS's full scale; peak-held, falling to 5 % in 150 ms |
| `spin` | SPIN | ±1 | gyro z over SPN-SENS's full scale, clamped, smoothed by SMOOTH |

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `smooth` | 1 … 2000 ms, log | 50 ms | One-pole on TILT and SPIN (SHAKE has its own decay). 1 ms is under a block, i.e. raw; 50 ms hides hand tremor without feeling late; 2 s turns tilting into a slow crossfade. Log: the useful end is short. |
| 1 | `shake_sens` (`SHK-SENS`) | 0 … 1 | 0.5 = 0.50 g | Full scale of SHAKE, `0.5 g · 8^(1−2x)`: 4 g (only a hard knock) … 0.06 g (a finger tap on the case). 0.5 g at centre is a firm tap. `format_param` draws the full scale: `0.50 g`. |
| 2 | `spin_sens` (`SPN-SENS`) | 0 … 1 | 0.5 = 250 dps | Full scale of SPIN, `250 deg/s · 8^(1−2x)`: 2000 dps (a flick) … 31 dps (a slow turn). 250 dps at centre is the LSM6DSO's finest range, a deliberate quarter-turn in about a third of a second. Draws `250 dps`. |

Slots 3–7 are empty. The two SENS knobs share one log curve so the centre is the
stated scale and each end is 8× either way.

## Choices worth knowing

- **Gravity is a 20 ms low-pass per axis.** SHAKE is what is left above it: a
  tap (5–20 ms) passes; turning the case over in half a second reads under 0.1
  at the default scale. It starts at the first reading, not at 0, so a case
  lying flat does not read as a 1 g jolt on load; and it starts again when the
  feed steps to or from all-zero (the sensor lost or back, #598), so a
  dropout is not a SHAKE either.
- SMOOTH also smooths SPIN: gyro noise is the same jitter as tilt's.
- Everything runs once per block with time constants in seconds, so the
  behaviour is the same at any block size (the output is still block-rate).

## Tests

`ctest -R motion` — two goldens, `motion_dropout` (#598: flat, all-zero, flat
again from `tests/dropout.npy`, `tests/make_dropout.py`; every output exactly 0,
against `motion_none.npy`; red before the re-prime, SHAKE 1.0 at the edge),
`--rt-guard`, `motion_format` (SENS readouts).
`host_modboxes` feeds motion through the host's offline `motion` message.

`motion.npy` is rendered from `tests/motion.npy`, written by
`tests/make_motion.py` (flat, a 0.2 s tilt to +0.7 g / −0.4 g, a 1.5 g tap at
0.4 s, a snap back, spin at +180 then −400 deg/s, a 12 Hz tremor), at
128-frame blocks, SMOOTH 20 ms. Frozen after checking by numbers: all finite and
in range; SHAKE exactly 0 while flat, ≤ 0.22 through the tilt ramp, 1.0 on the
tap and at 5 % 152 ms later; TILT settles at 0.6998 / −0.3997; SPIN 0.7155 at
180 dps (180/250 = 0.72) and clamps at −1 for −400; the tremor reaches SHAKE
0.08. `motion_none.npy` is 24000 frames of exact zeros with no `--motion`.

**Bench:** 0.04 µs/block, 0.00 % of a core at 512 frames / 48 kHz.
