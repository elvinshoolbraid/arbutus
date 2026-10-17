# audio/ — conventions for agents

## Rules

- **`docs/design/engine-spec.md` is the authority** (§15 = ABI 2; its header, SDK, host surface and the MOD rack and the six stock modulator boxes are built), with `modulation-spec.md` for the MOD rack and `v1-spec-package.md` for what is v1 and in what order. Where it is silent, pick the
  simplest thing that works and record it. Where it says `[TBD, Elvin's call]`, the
  default you chose goes in the session report *and* in a comment at the place
  you chose it, so the next reader can find it without the report.
- **The emulator is native-only as of 2026-09-17 (stage N1).** `audio/` is the
  instrument's audio path, not a sibling experiment: `emulator/` runs
  `arbutus-audio` always and the Python engines are deleted. A change here
  changes what Elvin plays, so the emulator's suite (`emulator/run_tests.sh`) is
  part of this folder's gate. `arbutus_os/` is still not ours to touch.
  The Python voice goldens live in `audio/tests/golden/python/`.
- **Every deviation from the spec is logged**, in the report and in
  the private log folder. A silent deviation is a fork of the contract.
- No new dependencies. `third_party/` holds one single-header JSON library and
  that is the budget. No frameworks; the target is a CM4 and every dependency is
  a thing that can fail to build on it.
- `-Wall -Wextra` stays clean.
- **ABI, before the first release** (Elvin, 2026-09-17). ABI 2 has never shipped
  outside this repo, so a struct does not grow by appending into `_reserved`.
  A new field goes in as a **named field in its logical place**, the layout is
  recomputed with padding accounted for, every offset is re-pinned in
  `tests/abi_layout.h` (checked from C and C++), and every box is rebuilt.
  `ARB_ABI` stays **2**; the number moves only once something has shipped. The
  `_reserved` tails are post-release headroom, when the append-only rule returns.

## Realtime

`process()` allocates nothing, locks nothing, and does no I/O. This is not a
style preference: an allocation in the audio path is an xrun, and an xrun is the
CPU flag on the player's screen. Fixed-size arrays sized at `create()`, no
`std::vector::push_back`, no `std::string`, no logging.

`arbutus-run --rt-guard` asserts the allocation half of that on every test run.
It is a smoke alarm, not a proof — read `tools/arbutus-run/README.md` for what
it misses.

**The guard counts on Linux as well as on macOS since #52** (2026-09-18). It
used to be `malloc_zone_statistics` under `#if defined(__APPLE__)` and `-1`
everywhere else, so every Linux run — the #50 gate, #41's CI, and the CM4 that
the whole P1 bench happens on — printed "rt-guard: unavailable on this platform"
and passed. One implementation now lives in `hostlib/rt_alloc_count.{hpp,cpp}`
for both `arbutus-run` and `arbutus-audio`; on Linux/glibc it is a **counting
allocator** (`hostlib/rt_alloc_hooks.c`) and on macOS it is the same malloc zone
as before, unchanged. The negative control is
`ctest -R rt_guard_catches_allocation`: a fixture box that `new`s every block,
which the guard must refuse — and which fails loudly if a platform that claims
to count stops counting. See § Defaults for the shape and the deviations.

## Style

Comments say **why**, not what. Assume the reader can read C++. Terse. The
worked example is `sdk/template/src/engine.cpp`: every comment there either
points at a spec section or explains a decision that looks arbitrary.

Names come from the spec's vocabulary — box, host block, slot, rack, tab, macro.
"Plugin" and "primitive" are retired words.

## The envelope rule

The amp envelope is **per voice**, applied after the voice, never over the mixed
output. `emulator/notes/as-built.md` § Polyphony has the bug report; `sdk/poly.hpp` has
the implementation and `audio/tests/test_poly.cpp` pins it. Any engine that
mixes first and envelopes after is wrong, however good it sounds in isolation.

## Goldens

The sound is frozen. `emulator/tests/golden/` was captured from the Python
implementations and a C++ rewrite conforms to it within −100 dBFS peak error, or
it is not the same instrument. Regenerate a golden only when a sound change is
the intent, and say so in the commit.

**Never build this tree with `-march`/`-mtune` flags.** The goldens are
FMA-contracted: they were frozen on arm64, where the compiler contracts
`b0*x + b1*X1 + ...` into fma chains, and a box with poles tight enough to care
must write its recursion as explicit `std::fma` or it will not match off arm64
(`eq3`, #55). `audio/CMakeLists.txt` therefore sets no ISA flags, and CI runs at
the `-march=x86-64` baseline, which has no fma instruction — that is the
configuration the goldens are checked against on Linux. A well-meant
`-march=native` on a runner that happens to have FMA would make the golden tests
pass for a *different* reason than they pass on the Mac, and the check would stop
meaning what it says.

**Cross-built for a 512 MB Pi (#568, Elvin, 2026-10-05).** `tools/cross-build.sh` builds this
tree on the Linux bench host in a Debian trixie chroot with `audio/cmake/aarch64-trixie.cmake`
(Debian's aarch64 GCC 14.2.0-19, no ISA flags, `qemu-user` for `arb-manifest`) and rsyncs it
into the Pi's `audio/build`; `ctest` runs there. This rule still holds: the toolchain file adds
no `-march`. The chroot is for the Pi's glibc and libstdc++; the goldens would pass either way.

## Block size is the host's business (#56, 2026-09-23)

Two rules, both learned the same morning.

**Never put the thing being tested on the left of a pipe.** `tests/ragged.sh`
read `if "$RUN" … | sed "s/^/  $N: /"; then`, and a pipeline's exit status is
its *last* command's — `sed`, which always succeeds. So **all 24 `ragged_*`
tests passed unconditionally for as long as they had existed**, printing four
`FAIL` lines and then `ragged: all block sizes agree` in the same run. `/bin/sh`
has no `pipefail`: capture the status first (`out=$(cmd); rc=$?`) and filter the
output afterwards, which is what `rt_guard_alloc.sh` and `refusals.sh` already
did. A **negative control** now sits beside the 24:
`ctest -R ragged_catches_block_dependence` requires the script to refuse
`arbutus-test/blockbox`, a fixture whose output really does depend on the block
size, on the `rt_guard_catches_allocation` pattern. A green harness is not
evidence until something has made it go red.

**A Voice reads a running value on a grid, not once per call.** What the dead
harness was hiding: `arbutus/va` and `arbutus/wavetable` each read their
envelope once per `process()` call and held it across the block. The block is
the host's to choose — 128 on the device, whatever a bake, a bench or
`--block` asks for elsewhere (`engine-spec.md` §3.7 rule 2, §15.8) — so both
boxes sounded different at every block size: **16 dBFS** apart between 512
frames and 1 for `va`, **60 dBFS** for `wavetable`. That is not roundoff, it is
a different note. `modulation-spec.md` §7's "one value per block" is about
**modulators**, which the host drives; it is not a licence for an engine's own
state to depend on the block length.

The fix is `arb::kCtrlStep` / `ctrl_run` / `ctrl_cell_start` in `sdk/poly.hpp`:
a voice counts its own frames since note-on — absolute, because `PolyHost`
splits blocks at event frames — and recomputes on a fixed 32-frame grid of it,
the same trick `kPitchStep` plays for glide. The half that is easy to miss is
the `ctrl_cell_start` guard: a call can end mid-cell, so recomputing at the
start of *every* run would put the block size straight back in. Cache the
coefficient across calls. Both goldens were refrozen (the sweep's timing moves
by ≤ 0.67 ms); `npystat.py` says peak, RMS and DC are unchanged to the digits
their READMEs print, which is what says the sound did not move.

## CI (X1, #41)

`.github/workflows/gate.yml` runs `tools/gate.sh` on every push and on pull
requests to `main`, on `ubuntu-latest`, and fails on anything but `GATE PASS`.
Two deviations from what #41 literally asked for, both because the gate's own
contract wins:

- **ccache, not a cached `audio/build`.** The gate's contract is a *clean* build
  (`rm -rf audio/build`), so a restored build directory would be deleted one line
  later or would force `--no-clean` and weaken the gate. ccache keeps the clean
  build and still skips the compiles, and it replays each compile's stderr on a
  cache hit, so the warning count stays honest (verified 2026-09-18: 98 % hit
  rate, build 44 s -> 2 s, still `0 warnings`). `CMAKE_C[XX]_COMPILER_LAUNCHER`
  is read from the environment, so `gate.sh` needed no change.
- **the pip wheel cache, not a cached `emulator/venv`.** A restored venv
  hardcodes absolute paths and goes stale against a Python patch bump; the wheel
  cache avoids the same downloads and the same `python-rtmidi` source build
  without that trap.

**On a Mac, `steady_clock` is not Python's `monotonic_ns`** (#251, 2026-10-01): libc++
uses `CLOCK_MONOTONIC_RAW`, which counts sleep; Python uses `CLOCK_UPTIME_RAW`, which
does not. A test comparing the host's stamps with its own clock reads
`CLOCK_MONOTONIC_RAW` on `darwin` (`tests/host_probe.py`); Linux is one clock.

No libportaudio is installed on purpose: `sounddevice` is expected to fail to
import on a headless box and the emulator guards it, so installing it would send
CI down a path neither the routine's container nor the CM4 takes.

## The host: two threads, one direction each

`host/` is `arbutus-audio`. The control thread owns the state document, the
library and every `new`; the audio thread owns the graph and never allocates,
locks, logs or frees. Everything crosses between them on two SPSC rings:
commands down, pointers-to-retire back up. A chain is built complete on the
control thread and handed over as one pointer; the audio thread swaps it
between blocks and **crossfades the old one out over 256 frames**, then pushes
it to the retire ring for the control thread to delete.

That is the rule to preserve. If you find yourself wanting a mutex in
`render_block`, the thing you want is a new command.

**A reply never runs ahead of the ring** (#597). `handle` refuses a message
`busy` before it touches the mirror while `cmds_` has fewer than
`kAdmitHeadroom` (64) free slots, so the state document never claims a command
the audio thread did not get. A message that pushes more than ~24 commands
must raise the headroom; a message that pushes nothing may join `kNeverBusy`.

`--offline` runs both halves on the same thread through the same rings, so the
offline path the tests use is the real path, not a simulation of it.

**A third thread, only with `--tape-dir`** (C1 steps 2a, 2b and 5, `host/tape_io.hpp`):
the tape I/O thread, which writes each finished range of a take onto storage
and is the only thing in the process that touches a medium there. It is an
ordinary `SCHED_OTHER` thread on the unisolated cores — it blocks on the card
by design, and a realtime thread that blocks is a lie the scheduler believes.
The audio thread reaches it the same way it reaches the control thread: one
POD on an SPSC ring, no lock, no wake-up, no wait. Offline it is not a thread
at all — its loop body is a `service()` call per rendered block. Without
`--tape-dir` it does not exist, which is every caller until step 3.

**Where they run (#637, 2026-10-08).** `--audio-core N` (the device passes 3)
takes the control thread off core N before anything starts a thread, so the
tape I/O thread and miniaudio's worker inherit a set without it, and then pins
the worker alone back onto N beside its `SCHED_FIFO` (`host/device.cpp`
`leave_core`, `go_realtime`). The unit gives the process `CPUAffinity=2-3`, so
the control and I/O threads share core 2 with the UI and keys, and core 3 is
the audio thread's. Until #637 the unit pinned the **whole** process to 3, and
the "unisolated cores" above were a sentence, not a fact. Without the flag
nothing is pinned (every desktop, the emulator, the tests).

**A commit holds no lock across the card (#637).** The I/O thread used to hold
`mu_` from the drain through a commit's `pwrite`s and `fdatasync`, and the
control thread takes it in `set_clips`, `medium_frames` and `compact_state`:
a microSD sync of 10–140+ ms then stalled every client's notes. `commit` now
drops `mu_` around the card the way #167 made `compact_step` drop it
(`io_busy_`, now a count, keeps closers out; `committing_` keeps a second
commit out). `test_tape_io`'s `test_commit_sync_does_not_hold_the_lock` parks
the commit in its sync and asks; `set_commit_holds_lock` is its negative
control. **The take's peaks sidecar goes in the same window** (#826): the
flush commit that ends a take wrote `tape-N.peaks` (open, truncate, write)
after taking `mu_` back, a card stall right after STOP; it is now built from
the bins and written inside the window (`peaks_image`, `write_peaks`),
`test_peaks_sidecar_does_not_hold_the_lock` parking that write under the
same negative control. Still under `mu_`: a fetch's `pread`, and `repair`'s
one truncate, header write and sync, **only when a medium left unclean (a
power cut) is first opened**. It stays because `open_medium` has eight callers
that treat "opened" as "repaired" (a commit, a fetch, `set_dir`,
`compact_finish`, …); dropping the lock there needs an *opening* state every
one of them waits on, and the cost is once a medium after a cut, not every
STOP [run's call, #826].

Since step 2b it is also the only thing that **frees** a pool slot and the only
thing that **fetches** a range back, and since step 5 it is where read-ahead
lives. One turn of it does five things in this order: advance the grace period;
pop the commit ring into a queue and commit what the card will take; **plan the
wanted set** from the clip snapshot and the playhead, so every eviction this
turn reads this turn's horizon; serve the audio thread's own wants and then the
horizon's; then evict enough to keep the free list above its low-water mark. The one place the offline path deliberately
differs from the live one is that grace period, because it is a rule about two
threads and offline there is only one — see § Defaults.

**A fourth thread, only with `--keys` (#638, 2026-10-09; `device-os-spec.md` § 1.3.4):**
`arb-keys` (`host/keys_thread.cpp`) reads the ATmega's port and serves the UI's
`keys.sock`. Its one entry into the host is `push_key_note`, onto **its own**
ring (`keys_cmds_`, 256 deep), because `cmds_` already has its producer; the
audio thread drains it right after `cmds_`. The rules (snapshot, held map,
watchdog, the UI's forward rules) are `host/keys_reader.cpp`, a state machine
with no file descriptors whose CTest counts `operator new`; the port and the
GPIO line are `host/keys_port.cpp`, apart because `termios2` and `<termios.h>`
cannot share a file. It is started after `init`, so it inherits the set
`leave_core` left (off the audio core), and asks for `SCHED_FIFO` 70; refused
(every desktop, the tests) it runs `SCHED_OTHER` and says so once.
Deviations from § 1.3.4, *run's calls 2026-10-09* (#638's log):
`keys_matrix.hpp` marks a spare position **−2** (the spec says −1 for "never
sounds"), so the reader can drop a spare's line as `keys_service.KeyPort`
does without a second table; the exit line adds `N malformed lines`; journal
lines start `keys:` (not `arbutus-keys:`), so a rig waiting for the first
snapshot greps `keys: the UI's first snapshot`. **A counting test needs an
allocation the compiler cannot remove:** the first negative control,
`delete new int(1)`, read green, because GCC may elide a `new`/`delete` pair
(C++14); `int* volatile p = new int(1); delete p;` read red.

## The IMU (P6, #22) — `--imu /dev/i2c-1`

`host/imu_lsm6dso.*` is `device-os-spec.md` § 6.5's driver: an ST LSM6DSO on
I2C1 at `0x6A`, read from **the control thread** (not a thread of its own —
§ 1.2 adds no thread to this process) and handed to the audio thread as
`Ck::Motion`, the same command the offline `motion` message uses. So the audio
thread cannot tell a sensor from a test, and a MOD box's arithmetic is the same
on a Mac and on the instrument.

Things to know before touching it:

- **`--imu` is the only writer on a device.** The `motion` protocol message
  stays `--offline` only, because a client writing motion on a device would be
  a second writer racing the sensor.
- **Nothing in `sample()` allocates**, and `tests/test_imu.cpp` counts
  `operator new` over a second of sampling to prove it, the `test_events.cpp`
  pattern. The values go into the audio thread; the path into the audio thread
  does not allocate, even when the call itself is on the control thread.
- **A register read is one `I2C_RDWR` transfer** (#418): the address, a
  repeated START, the burst. `i2c_register_read` builds the two messages as
  the portable `I2cMsg` so `test_imu` can check their shape on a Mac; Linux
  copies them into the kernel's `struct i2c_msg`. Never on a board.
- **`motion[]` is in the instrument's frame, not the chip's** (#1048,
  modulation-spec § 13: TILT-X + right edge down, TILT-Y + front edge down,
  SPIN + clockwise from above). `sample()` maps the chip's axes through the
  board's `ImuMount`, a signed permutation; P3's (`kMountP3`, the default) is
  the bottom-side part at rotation 0. `--imu-mount top|chip` for anything else;
  `chip` is the raw axes, for finding a wrong sign at bring-up. On P3 the map
  changes z (flat reads −1 g on the chip) and gyro x/y; TILT-X, TILT-Y and SPIN
  come through the bottom mount with the frame's sign already.
- **The poll timeout drops from 20 ms to 5 ms when `--imu` is on**, because the
  ODR is 208 Hz and a 50 Hz poll would drop three samples in four. With no
  `--imu` the loop is exactly what it was.
- **A failure zeros the motion, once** (#598). `main.cpp` takes samples
  through `Lsm6dso::feed`, which on the turn the bus fails hands back six
  zeros (PROTOCOL.md's "no sensor"), so the engine does not hold the last tilt
  through the 5 s retries; `arbutus/motion` re-primes its gravity at that edge
  so the step is not a SHAKE. A reset that never clears fails `init`.
- **Whether a sensor is there is the UI's business too** (#116). `main.cpp`
  calls `host.set_motion_sensor(imu.healthy())` every turn of the loop and the
  host reports it as **`motion_sensor`** on `hello` and in the state document,
  from which the UI draws `NO SENSOR` on the `arbutus/motion` box and shows or
  hides FILE › SYSTEM's `KEY VEL`. It is in the *state* document and not only
  the handshake because § 6.5 gives "no WHO_AM_I" and "a bus error mid-run"
  the same answer on screen, so the value has to be able to change under a
  connected client; the setter marks the document dirty only when it moves.
  It is settled before the accept loop starts, so no handshake sees it
  mid-decision.

**Deviation from § 6.5, written down here because the spec says otherwise.**
§ 6.5 asks for `INT1` as a `gpiod` rising-edge event so the control thread
sleeps between samples; **as built the gating is a `STATUS_REG` read**
(`XLDA | GDA`). `INT1_CTRL` is still configured, so the pin does what io-map
§ P3 says it does and the edge wait can be added without changing anything
else. The reason: the edge wait saves wake-ups and nothing else — it cannot
change a sample — and it is the one part of this driver that a fake bus cannot
test, so writing it blind would be untested code on the critical path. It is
P3-arrival work, and the row in § Defaults below is where it is recorded. It
becomes a question for Elvin only if the wake-ups turn out to matter for battery
life (X6, #46) — at 208 Hz on a core that is already awake, they should not.

## Defaults chosen where the spec is silent or `[TBD, Elvin's call]`

Each of these is also commented at the place it is made. Changing one is a
one-line edit; they are defaults, not decisions.

| Thing | Spec | Default | Where |
|---|---|---|---|
| which `--library` root is written into | `PROTOCOL.md` said the **first**; no invocation ever passed them that way | the **last** (`AudioHost::write_root`, #62 item 3). Every caller passes the read-only boxes first and the player's library after them — `--library <slot>/boxes --library /data` in `arbutus-audio.service`, `[BOXES, LIBRARY]` in the emulator — so that a box dropped into the player's library cannot shadow a shipped one. *[TBD, Elvin's call] (2026-10-14, #935, review I14): D106 (one search path, `engine-spec.md` § 3.9) removes this reason; whether the rule goes is the review's G4, Elvin's, built with #840.* Against the first root a phone's `library {op}` rename, and a `clip_to_asset` with no `file`, resolved inside the A/B slot a `.fw` replaces | `host/audio_host.cpp` `write_root` |
| an engine's kind, for the last-of-a-kind refusal (#839, D106) | `companion-app-spec.md` § 12 names four kinds (synth, drum, effect, sequencer); the manifest has no field for it | from `caps`: `sequencer` → sequencer; else `audio_in` → effect; else `audio_out` → a **drum** when any param is `per_note` (§15.3: "a drum engine flags params `per_note`"), else a synth; else `modulator` (never refused). *Run's call, 2026-10-14*: no manifest change, and the stock boxes sort exactly (dbox and drum are the drums). The slot's boxes are `--library-fallback` since D125 (`engine-spec.md` § 3.9 *As built*), so the service passes one root and the row above holds for the emulator's two | `hostlib/library.cpp` `kind_of`, `host/audio_host.cpp` `library_op` |
| macro curves `exp` / `log` / `s` | §6.2 names them, defines none | `x²`, `√x`, `x²(3−2x)` | `host/rack.cpp` `curve_apply` |
| macro `depth` | §6.2 "scales the excursion (negative = invert around centre)" | scales about the centre of `[lo,hi]`, clamped to 0..1 | `host/rack.cpp` `render_chain` |
| xrun threshold | §6.8 `[TBD, Elvin's call: N in M s]` | flag at **3 in 2 s** — and `tape_late` is counted on **this same window and this same rule** (C1 step 7), because the two share one slot on the screen: `CPU UNHAPPY: OVERLOAD` (D103, #843; was `CPU NO HAPPY`) and `HARD DRIVE SLOW` | `host/audio_host.cpp` `kXrunWindowS` |
| `ppqn` | §3.3 `[TBD, Elvin's call]` | 24 | `host/audio_host.cpp` `init` |
| what counts as an xrun | §6.8 "the device says when it is over budget" | a block whose render outran its period **or** (stage 4b, #13) a period the device lost — miniaudio's ALSA `EPIPE (read/write)` debug line, counted by a log callback on the audio thread (ours is a `strncmp` and an atomic add; miniaudio itself formats the line and takes its log mutex — once per period already lost). A late wakeup renders on time and was invisible before. Blind spot: miniaudio sets the playback stop threshold to the boundary, so under `--no-input` a playback underrun raises nothing | `host/device.cpp` `on_log` |
| xrun window roll-over | not specified | the window is `total − base`, reported **before** it rolls; it was a second counter zeroed at the roll-over, which lost every xrun counted since the last poll — and a stall delays the poll (#13) | `host/audio_host.cpp` `poll` |
| realtime on Linux | software-stack-v1 §7.4, `PI.md` § 3: `SCHED_FIFO` 80 + `mlockall` "at device start" | `pthread_setschedparam` from the **control thread** on miniaudio's worker (`ma_device.thread`) after init, before start — nothing inside the callback; skipped with a message if the backend owns its thread. Each failure is printed and the host still plays; the banner prints what it got | `host/device.cpp` `go_realtime` |
| **deviation:** `mlockall` only when `RLIMIT_MEMLOCK` is unlimited | `PI.md` § 3 calls it unconditionally | with a finite limit `MCL_FUTURE` makes every later mapping past it `ENOMEM` — the tape's lazy 184 MB medium would fail on first record, far from the cause. So a finite limit prints "memory not locked" and locks nothing. Locked, the tape is resident: 8 recorded tracks = 1.5 GB of the CM4's RAM | same |
| **deviation:** reverb SIZE keeps the tail (#630) | the box was a direct rewrite of the emulator's `SchroederReverb`, whose `_build()` empties every line on any SIZE change | a SIZE change moves each line's read distance (a ring sized for SIZE 1) and keeps what is in it: one detent no longer cuts the tail dead, and a route or the SPACE macro on SIZE no longer keeps emptying it. At a fixed SIZE the numbers are the Python's (the conformance golden holds at −144.5 dBFS); the emulator's own Python reverb still clears. *The code review's recommendation (m-engines-fx row 4), built by the run* | `engines/arbutus/reverb/src/engine.cpp`, README § Notes |
| hot-swap fade | §8 says "no xrun", not how | 256 frames, 5.3 ms at 48 kHz | `host/audio_host.hpp` `kFadeFrames` |
| rack / patch param values | §6.7 shows bare numbers | **normalised 0..1**, per §3.2 | `host/PROTOCOL.md` |
| tape length | software-stack-v1 §8.2 | 8 min per track, `--tape-seconds` | `host/audio_host.hpp` `Options` |
| tape medium allocation | §8.2 sizes a track, not the tape | two modes, and the switch is `--tape-dir`. **Unbounded** (no `--tape-dir`, so the emulator and the device today): **lazy** — a medium is allocated when the track is first recorded or loaded (8 × 8 min stereo f32 = 1.5 GB eagerly, which the CM4 has not got), `ensure()` takes a whole track's slots at once, nothing is ever evicted. **Bounded** (C1 step 2b, `--tape-pool-seconds`, 120 track-seconds = 117 slots): every slot is allocated at init and never grown, `ensure()` only says the track *has* a medium, and a range becomes resident by being acquired for a take or fetched back off the card. The pool is bounded **only** with a medium on storage behind it, because a window that cannot refetch does not save memory — it loses audio on its first eviction | `host/tape.hpp` `init`, `ensure` |
| the RAM window's policy | nothing in §8; `tape-streaming-design.md` § 3.6 recommends | resident by **timeline distance from where the track is being read**, `hot()`; the victim is the furthest clean range, ties broken behind before ahead. **Never** evicted: the range a take is filling (`write_range`), a range in the write queue (the only copy of it), a range a control-thread reader has pinned, and — since step 5 — a range in the **wanted set**. The ±2-range guard band around a read position is what a host with no wanted set gets instead (row below). A track nothing has *read* is measured from the take's own head with **no** guard: the ranges behind a write head are finished and are the first to go (guarding them stalled an 8-range take against a 3-slot window) | `host/tape_io.cpp` `evict_to` |
| read-ahead | § 3.6 gives 4 s of horizon over a wanted set | **exactly that, as of C1 step 5**: the I/O thread keeps resident every range the clip lists read inside `[pos − behind, pos + ahead]` of the playhead, in the direction of travel, over all eight tracks, through `src_track` onto the medium a clip really reads, through the loop seam, ordered nearest-first with the **recording track first** (an overdub records its own playback, so a late range there is silence in the take) and capped at **half the pool**. Both horizons are **seconds of wall clock** — four ranges at 1x, sixteen at 4x — `--tape-readahead-seconds` 4 and `--tape-behind-seconds` 1. Step 2b's speculative one-range lead in `render_track` is **deleted**; what stays there is the *demand* ask, so a render that finds a range absent counts it late and asks for it, which is what makes `--tape-readahead-seconds 0` a real *before* switch. Distance is measured **per range**, from where that range sounds on the timeline and through the seam: per clip it is one number for every range of one long clip, and the cap then keeps the lowest-numbered ranges — under forward travel, the ones already played | `host/tape_io.cpp` `plan_wanted`, `serve_wanted` |
| the wanted set's two inputs | § 3.6 says "a snapshot of the clip lists it copies from the control thread under a mutex" | exactly that for the clips (`TapeIo::set_clips`, called wherever `Ck::SetClips` is pushed), and for the **playhead** a lock-free `Tape::set_view` — five relaxed stores a block from the audio thread, read field by field so the set can tear. Deliberate, and the reason it is not a seqlock: the wanted set is a *prediction*, so a torn read makes one turn's horizon a little wrong and the next turn corrects it, while a lock on the audio thread would be a real fault. Published even while stopped (§ 3.6 wants the window around a parked playhead so PLAY after a locate is warm) and it is always the **transport's** position, never the scrub cursor's — a scrub crosses minutes of tape and a horizon chasing it would evict everything PLAY is about to want | `host/tape.cpp` `set_view`, `host/audio_host.cpp` `render_block` |
| read-ahead against a take | § 3.6: "a stalled card converts the pool from read-ahead into write queue *first*, degrading playback to `tape_late` before it ends the take" | the I/O thread's **own** fetches — read-ahead and the audio thread's demand asks alike — stop at `top_up_free`'s low-water mark and evict to get a slot instead; only a control-thread reader may take the last free slot. Found by the test: without it, eight tracks fetching against an eight-slot pool ended the take at its **first** range boundary with the card answering perfectly, which is § 3.6's order exactly inverted | `host/tape_io.cpp` `take_slot`, `free_mark` |
| the guard band, after step 5 | not a spec question | **off wherever there is a wanted set**, and kept only where there is not (a host that has published no playhead, and `--tape-readahead-seconds 0`). The two together protected seven ranges of an eight-slot pool and left the evictor no victim at all — the band is a blunter protection over the same ground, not a second opinion | `host/tape_io.cpp` `evict_to` |
| the grace period's test switch | § 4's T5 asks for "disable the grace period (a test-only flag)" | `TapeIo::GracePolicy` — `kAuto` (production: live only), `kAlways` and `kNever`. **`kNever` cannot be shown to fire offline and that is not a gap in the test**: the fault the grace period prevents is a callback reading a slot being refilled, which is a *race*, and offline there is one thread. So `kAlways` is what the deterministic test asserts — the wait happening, in rendered blocks — and `kNever` is documented as the switch a **live** bench run would flip | `host/tape_io.hpp` `set_grace` |
| the slot grace period | § 3.6 "wait until the audio thread's block counter has advanced by two" | exactly that **live**, counted in rendered blocks (`Tape::tick`, so it means the same at every block size); **offline it is nothing**, because `service()` is a step of the render loop and no callback can be in flight. A control-thread `fetch` that finds every slot still inside its grace waits it out (64 ms of patience) rather than handing a reader silence — a bounce of silence would be the wrong answer to a question that was only early | `host/tape_io.cpp` `advance_grace`, `fetch` |
| a card that stops answering | § 3.7 gives `tape_full {reason:"stall"}` | the queue grows, every queued range holds its slot, and when the free list is empty `Tape::write` returns **short**: the host ends the take, pushes `tape_full {reason:"stall"}` and **keeps the transport rolling** ("a frozen screen is not acceptable, more latency is" — Elvin, #74). Modelled offline by `TapeStore::busy()`, which a real card never answers true to (it blocks inside the call, which is what the I/O thread is for) and `FakeStore::stall_blocks` does for a counted number of rendered blocks | `host/audio_host.cpp` `render_block`, `poll` |
| appending at a head inside a written range | not specified | the head's range is **fetched back first**, on the control thread, when a take arms or a `tape_edit` writes. Bounded, `acquire` hands out a zeroed slot, and the commit writes the whole range — so without the fetch the audio in front of the head is silently replaced by zeroes and the previous take is truncated to a range boundary. `tests/test_tape_pool.cpp` shows exactly that happening with the fetch removed | `host/audio_host.cpp` `tape_prepare_head` |
| a control-thread reader with a bounded pool | § 3.6 "fetch-then-read on the control thread" | `AudioHost::tape_run` — `run_at`, and on an absent range a blocking `fetch` — used by `bake`, `clip_to_asset`, `clip_peaks`, `tape_save` and `tape_edit`'s sources. It **pins** the range it returns so the evictor cannot take it while the caller copies out of it; one pin is enough because a reader holds one run at a time, and `tape_unpin()` ends it. **Step 6's streaming half is the row below**; its measurement was taken on the CM4 (#162 — 4.0 s for one bank of four lanes) and its follow-up is the peaks sidecar plus the UI's bins cap (#163), which is why `clip_peaks` is the one of the six that now has a path that never reaches here | `host/audio_host.cpp` `tape_run` |
| the pool's free list | not specified | one runtime-sized ring, produced by the I/O thread (which **zeroes** a slot on the way out, so an acquired range is silence without the audio thread ever touching 384 KiB) and consumed by **both** the audio thread (a take) and the I/O thread (a fetch). Taking is a CAS on the read index rather than `spsc.hpp`'s plain store: a fetch that could not reach the slots a take is not using would strand half the pool and starve playback with free slots in hand | `host/tape.cpp` `IndexRing` |
| tape range length | nothing in §8 divides a medium | **49,152 frames = 1.024 s = exactly 384 KiB = 96 pages** (`tape-streaming-design.md` § 3.1, Elvin 2026-09-23: yes). Not 48,000 — 384,000 B is 93.75 pages, so every fourth boundary would fall mid-page, the next range's write would read-modify-write a partial one and `O_DIRECT` would refuse the offset outright. At 384 KiB a range is whole pages and a whole number of every power-of-two cluster up to 128 KiB | `host/tape.hpp` `kRangeFrames` |
| the ruler and the medium's ceiling, in ranges | § 3.3 gives the ruler in minutes and the format's bound as "~2.9 h" | the ruler (`--tape-seconds`, 480 = 8 min; DDR-072's **floor**, not a wall) is **469 ranges = 184.4 MB a track** — 480 s is 468.75 ranges, so `ranges_for` rounds **up** and the last range is partial, clamped by the capacity rather than by its own end. Eight of those are the 1.46 GB P1 measured (#13), and one of them is #72's 180 MB zero-fill. The format's own ceiling is the `data` chunk's 32-bit size: **10,922 whole ranges = 3.1 h** a track, not § 3.3's ~2.9 h, which reads 4 GB as decimal. The pool rounds the other way — track-seconds **down** to whole ranges, 120 → **117 = 119.8 s = 46.0 MB** — and REC's floor is twice that (~92 MB) | `host/tape.hpp` `ranges_for`, `host/audio_host.cpp` `init` |
| an absent range on the audio thread | §8 has no absent state (the medium was one array) | **silence and a count**, never a null and never a wait: `render_track` sets bit *t* of `late_mask` and writes zeros for those frames — the clip's other frames in the block still sound. A clip reading **before frame 0** is the UI's geometry, not a range the card owed us, so it is silent and **not** counted; a clip on a track with no medium at all is the same | `host/tape.cpp` `render_track` |
| what a late range costs the player (step 7) | § 3.7 gives the push and the counter | `render_block` turns the mask into **one count per block**, whichever tracks were late, and `poll` pushes `tape_late {count, window_s, total, flag, tracks}` on **`xrun`'s own window and 3-in-2-s rule** — one alarm rule for both, because they share a slot on the screen. The UI draws **`HARD DRIVE SLOW`** there (Elvin, § 7 q2, 2026-09-23 — not the note's `TAPE LATE`). A **scrub** over an absent range is silent and **uncounted**: the mask is cleared without counting, so the fetch it asked for still happens and the next grain sounds, but dragging a cursor across a tape is not the instrument being late | `host/audio_host.cpp` `render_block`, `poll` |
| the other two `TAPE FULL`s (step 7) | § 3.7 gives three reasons and one word | three pushes, `tape_full {reason}` = `stall` / `format` / `disk`, and **two** words on screen (Elvin, § 7 q3): `CARD STALLED` for the first, `TAPE FULL` for the other two, because the player's next action differs. **`format` is new behaviour, not new wording** — the medium's own end used to stop the take growing and say nothing at all, leaving a take that looked like it was still recording; it now ends the take, and the bound is the `--tape-seconds` capacity, far under the WAV format's 10,922 ranges. `disk` is storage refusing a commit for want of space: the I/O thread sees it, the **control** thread ends the take, and the clip is cut back to the **committed prefix**, because the slots the pool still holds are evicted soon and would then read as silence | `host/audio_host.cpp` `render_block`, `poll`, `commit_take` |
| what `durable_end` means | not specified | the length of the **contiguous** prefix the card holds, so a commit extends it only when it wrote audio *and* started at or before where the prefix ended. Two commits fail that and both used to extend it: the empty flush commit at the end of a take that lands on a range boundary, and any commit past a range the card refused — which is the ENOSPC case, and would have claimed a whole 12-range take as durable when 8 ranges were on the card | `host/tape_io.cpp` `commit` |
| ENOSPC against every other refusal | § 3.7 reason 2 says "a `pwrite` returns `ENOSPC`" | **`TapeStore::no_space()`**, set by the call that failed and cleared by the next one tried, asked once in `TapeIo::note_error` — an error *string* is not a thing to branch on. `PosixStore` watches `pwrite` **and** `fdatasync`, because a buffered write is only allocated when it is flushed. `FakeStore`'s free space is **cumulative** now (a growing write spends it, a truncate gives it back), which is what makes a take run a card out part way through rather than for ever refusing the same write | `host/tape_store.{hpp,cpp}`, `host/tape_io.cpp` `note_error` |
| when REC is refused for space | § 3.7: "checked by the I/O thread on `arm` with `fstatvfs` and cached" (§ 7 q4 answered yes) | **at the `record` that starts the take, on the control thread, uncached** (`TapeIo::space_for_take`): `statvfs` is microseconds and REC is a key press, not a hot path — and an answer cached at `arm` is a reading of the card from before whatever filled it. The floor is **twice the pool** (~92 MB at 120 track-seconds). True is also the answer when there is no medium on storage or the filesystem will not say: an unanswerable question is not a refusal | `host/tape_io.cpp` `space_for_take`, `host/audio_host.cpp` `record` |
| `state.tape.pool` | § 3.7 names `{ranges, resident, queued, late_total}` | exactly that, and **always present** — `ranges` 0 is "no window, the whole tape is in RAM", which is every host until the medium moves onto storage, and is how a client tells that from a window with nothing in it. `state.tape.io.pool` is the *I/O thread's* view of the same pool (the free list, fetches, evictions) and exists only with a `--tape-dir`; the bench may read either, the UI reads `tape.pool` | `host/audio_host.cpp` `state_doc`, `tape_io_doc` |
| a fake card in the **host** | § 4 names `--tape-store posix\|fake` and no flag for where | both, as **tests-only** flags that say so in `--help`: `--tape-store fake` puts `FakeStore` behind the tape (media in RAM inside the process, nothing on the card) and `--tape-free-bytes N` says how much room it has — also honoured by `space_for_take` on a *real* card, so the REC floor is testable without filling a disk. Without them § 3.7's three reasons are not reachable from a host test at all, which is what T10 needs | `host/main.cpp`, `host/audio_host.cpp` `init` |
| how the host reads a medium | §8.3's mmap'd file implies one pointer per track | **`run_at` / `run_at_mut`**: a pointer to a frame plus how many frames are contiguous behind it, never past the range's end or the capacity. `run` also carries the two edge cases the per-frame `s < 0 \|\| s >= capacity` guards used to: before frame 0 it says how far to skip, past the end it is 0, which stops the caller. Bulk copies go through `read_frames` / `write_frames`, which zero-fill what is absent. There is no pointer over a whole medium any more — that is what lets a range live on the card | `host/tape.hpp`, `host/audio_host.cpp` |
| the tape's storage seam | §8.3 names files, not an interface | **`TapeStore`** (`host/tape_store.hpp`): open / pread / pwrite / fdatasync / truncate / rename / free_bytes, POSIX-shaped because the medium is written **in place**, a range at a time at its own offset (`tape-streaming-design.md` § 3.5 — DDR-068's temp-then-rename rule is for documents, and a rename-based medium is the 737 MB copy on SAVE the design rejects). `FakeStore` is the tests' one: files as RAM byte vectors, a durable mark only `fdatasync` moves, and armed one-shot faults. The **deferred** faults § 4 describes ("the fdatasync for range 12 completes 750 blocks later") are what a *bounded* pool's stall cover is measured with (T2, T4), so they land with the pool in step 2b; arming them earlier would be a schedule with nothing to exhaust. `PosixStore` is the device's: buffered I/O, `POSIX_FADV_RANDOM` on the fd and `POSIX_FADV_DONTNEED` after a range is durable (§ 3.2's three decisions), short `pwrite`s retried, and `F_FULLFSYNC` on macOS where `fdatasync` does not reach the drive | `host/tape_store.{hpp,cpp}`, `tests/test_tape.cpp` |
| the tape on storage | §8.3 names mmap'd `track_<n>.f32` files with a 64-byte header | **`--tape-dir DIR`** and `DIR/tape-1.wav` … `tape-8.wav`: the stereo float32 WAV `tape_save` already writes, written in place a range at a time by the **tape I/O thread** (`host/tape_io.{hpp,cpp}`, C1 step 2a). Not the mmap'd file the spec sketches — the medium has to be the project's own file (`OS-11` → DDR-072) so SAVE never copies audio, and mmap would put the page fault on the audio thread. `--tape-dir` is **empty by default**, and then no thread starts and nothing is written: the emulator, the device and every test are exactly as they were until step 3 makes the medium the project's file. [default chosen here: the design names `--tape-store posix\|fake` but no flag for *where*] | `host/main.cpp`, `host/audio_host.cpp` `init` |
| where the media live (C1 step 3b, #157) | Elvin's answer (a) on #157: SAVE **moves** the tape files into the project folder and recording carries on there; design § 6 step 3 asks for "`tape_load` as open-and-set-head, `tape_save` as flush-header-only" | **one message, `tape_dir {dir, move?}`**, rather than a `tape_move` and a `tape_open` — the two share every rule (no take open, a compaction cancelled first, the folder created) and differ in one flag. `move` renames medium **and sidecar** with the handles left open (they follow a rename; `FakeStore` models that), so the pool is untouched; it **refuses `exists`** rather than rename over a file already there, and a rename that fails part way is **rolled back**. Without `move` it is the open-in-place: every resident range retired through the evictor's grace path (`compact_finish`'s pattern), sidecars dropped *without* unlinking, each medium in the new folder opened and repaired, its claim the head. **A track with no file gets none** until its first commit — and since #475 a **read** never makes one either: `fetch_locked` opens without `create` and a track with no file reads as silence, because between LOAD's `tape_dir` and its `set_clips` the old clip lists fetched empty `tape-N.wav`s into the loaded folder [run's call, 2026-10-04] — `open_all`'s open question ("eight files or only the tracks that have audio") answered *only the tracks with audio*, since a project folder of empty WAVs is clutter a player sees over USB. **A host started with `--tape-dir` opens its folder the same way** — before this the head was 0 and a take wrote over a medium already there. **`tape_save` onto the medium's own path is a flush** (`in_place`), because the old copy put a 44-byte-header WAV over the medium through a rename, behind the I/O thread's open handle, and the next open refused it. `tape_load` stays as it was: it is how an old project's 44-byte WAV gets in — and since #297 it writes **through `write_frames`, a range at a time** (`tape_edit`'s shape), because with a bounded pool `run_at_mut` on a range nobody acquired is NULL: the load moved the head, wrote nothing, and the converted medium reopened empty (`host_tapedir.py`, seen red first). [run's calls, 2026-10-03] | `host/tape_io.cpp` `set_dir`, `host/audio_host.cpp` `tape_dir`, `adopt_media` |
| the live WAV header | §8.3's 64-byte header; nothing says what a file being written looks like | **4 KiB**: `RIFF`/`WAVE`, `fmt ` (IEEE float, 2 ch), `fact` (the frame count), a `JUNK` pad, `data` — payload at byte **4096**, so a range is a whole number of pages and of any cluster to 128 KiB. Rewritten in every commit and **lagging the data by one range**: `fdatasync` orders nothing, so a cut can land the header page without the data page, and the lag is what stops a file claiming audio that is not there. Exact, with no lag, at the end of a take. `tests/test_tape_io.cpp` proves it by building the file a cut would leave and showing it never over-claims — and that with the lag switched off it does, three times out of three | `host/tape_io.cpp` `write_header`, `commit` |
| repair on open | § 3.5 gives the rule, not what a file that is not ours is | **the head comes off the header's frame count, never the file's length**, and the two are made to agree before anything appends (C1 step 3a) — a head taken from a file longer than its claim writes over the tail a cut left behind, which is two takes interleaved inside one range. An **empty** file claims 0; one with **no `RIFF` at all** is ours, cut before any commit's header page landed, and is truncated to the header (by the lag rule that audio was never durable); one that is **`RIFF` but not ours** — the 44-byte header `tape_save` writes — is **refused and left byte for byte alone**, because its payload is not at byte 4096 and overwriting its header to save a reopen would destroy a take; **ours** is clamped to the whole frames really there, truncated to exactly the claim, rewritten and synced. Truncate **before** the header, so a cut inside the repair leaves a file the next open repairs the same way. `TapeStore` gains **`size()`** for it (not in design § 4's list: a claim means nothing without a length to check it against). `state.tape.io.repair` carries `media`, `lost_frames` — the larger of the two disagreeing numbers minus what survived, because the two cases lose from opposite ends, and the number the provisional clip (DDR-070) must shorten by — and `refused` | `host/tape_io.cpp` `repair` |
| the tape's `fallocate` lead | § 3.4 recommends **against** preallocation, reasoning a card's own allocation is cheap | **a lead of `kAllocLeadRanges` (4 = 4.1 s) ahead of the range being committed**, `FALLOC_FL_KEEP_SIZE`, asked for on every commit that carries audio (#158) — **and it bought nothing measurable on ext4**, which is the honest headline. Twenty CM4 cells: the median commit does not move (12.0–12.2 ms with the lead, 12.0–12.1 without) and 4–6 of 121 paced commits exceed 20 ms either way. It is kept because it costs nothing, because it is the standard shape for an appending writer, and because **step 4 is where it can matter** — whether the in-kernel exFAT driver implements `fallocate` at all is § 3.4's own unverified question and `prealloc.refused` is now how step 4 reads the answer. If exFAT shows nothing either, it should go; `--no-tape-prealloc` is the switch and no third measurement is owed. **What the commit actually costs is the `fdatasync`** — 93–95 % of it, and every commit that ran past 20 ms ran past it inside the sync — so § 3.3's sync cadence is the only lever left and it is a spec decision, not a code one. Two things about the shape of it: one range per commit rather than a chunk every N, because the target is a percentile and one commit in sixteen paying for sixteen ranges is the tail again; and **`KEEP_SIZE` is not an optimisation, it is the correctness condition** — repair on open reads the file's *length* and treats an excess over the header's claim as audio a cut left behind, so a lead that grew the length would be read as exactly that | `host/tape_io.cpp` `extend_alloc`, `host/tape_store.cpp` `PosixStore::allocate` |
| the per-range commit | not specified | the `fallocate` lead, one `pwrite` of the range at its offset, one `pwrite` of the header, one `fdatasync` — C6's measured pattern (#79: 0.54 ms + 10 ms on the eMMC) — then `POSIX_FADV_DONTNEED` on the extent. A data write that is refused returns **before** the header is written, so the header never claims a range the card did not take. `state.tape.io.commit_us` carries p50/p99/max over the last 4096 commits, which is the number `arbutus_os/bench/c1-pi.sh` takes on the CM4, **`sync_us` the `fdatasync` alone out of the same commits** (93–95 % of it, median and tail alike — the two `pwrite`s are C6's 0.54 ms and nothing else is in there), and **`over_20ms` / `over_100ms` counts, which are the statistic a 121-commit take can support**: `p99` of 121 is the 120th of 121 sorted, the second-largest sample, and twelve runs of identical code put it anywhere from 15 to 142 ms while the median held to ±0.5 ms — reading it as a percentile is what made #74 step 2 blame the growing file (#158) | `host/tape_io.cpp` `commit` |
| the audio thread's part in the write-behind | §3.7.1 (no I/O on the audio thread) | one POD on an SPSC ring per finished range (`spsc.hpp`, the same ring the commands use) and two relaxed adds — **no wake-up**: `notify_one` is a futex syscall and the I/O thread polls on a 5 ms cadence instead, which is nothing against a range's 1.024 s. A **full ring is counted (`dropped`) and retried next block**, never waited on; `tape_full {reason:"stall"}` is what step 2b raises instead | `host/audio_host.cpp` `queue_ranges`, `host/tape_io.cpp` `enqueue` |
| when the control thread waits for the card | §3.9 lets it | in exactly two places, both refused while a take is open: `tape_edit` and `tape_load`, which write the medium themselves and so commit their own span (`persist_span`). **Not** at the end of a take: `end_take` queues the tail with an exact header and the I/O thread takes it within a poll interval, and waiting there would put the control thread behind the card on every STOP — #93's freeze in a new place | `host/audio_host.cpp` `commit_take`, `tape_edit` |
| a take open at shutdown (#612) | not specified | **ended as STOP would**: after `device.stop()` (no audio thread left) `main.cpp` calls `AudioHost::end_open_take`, which queues the tail with an exact header, and `~TapeIo`'s final drain writes it. Before, the header stayed a range behind the audio (the lag rule) and the next open's repair truncated to it: a SIGTERM 1.4 s into a take reopened 0 frames. Without `--tape-dir` it changes nothing. Also #612: `TapeIo::stop()` returns early when `configure` never ran, because it drained through a null `Tape*` and every exit without `--tape-dir` segfaulted (139). `tests/host_exit.py`, seen red on the old code; since #992 it asserts the reopened length and bytes against a REC-off reference on all three exits (shutdown, SIGINT, SIGTERM), so dropping the exact partial range or `end_open_take` is red. *Run's call, 2026-10-06* | `host/main.cpp`, `host/tape_io.cpp` `stop` |
| the I/O thread offline | `--offline` runs both halves on one thread | the I/O thread's loop body becomes a `service()` call `render_offline` makes **once per rendered block**, outside the `--rt-guard` window (the guard counts the audio path and this is not it). So the offline path is the real path here too, and a test sees a commit land without a thread | `host/audio_host.cpp` `render_offline` |
| `arb::io::read_wav` exported | not a spec question | moved out of `audiofile.cpp`'s anonymous namespace so bytes that never became a file can be parsed — the tape's tests hold the medium as a byte vector in `FakeStore`, and repair-on-open (step 3) reads a header the same way. The parse already tolerated a `data` size larger than the bytes present, which is exactly what a file still being written looks like | `hostlib/audiofile.{hpp,cpp}` |
| pan law | not specified | equal power, −3 dB at centre (the emulator's) | `host/audio_host.cpp` `render_block` |
| mixer defaults | not specified | unity everywhere, limiter off — the host must not colour | same |
| UI event timing | §3.4 gives events a frame | a `note` lands at the start of the next block | `host/PROTOCOL.md` "Timing" |
| kit → engine | §7.3 says nothing about delivery | one asset, `load_asset(h,"kit",<resolved kit.json>)` | `host/audio_host.cpp` `load_kit` |
| asset path resolution | §7.2 says "library-relative" | doc `"dir"`, then each `--library` root, then cwd | `host/audio_host.cpp` `resolve_asset` |
| max asset keys per engine | not specified | `ARB_MAX_ASSETS` = 3 | `sdk/include/arbutus_engine.h` |
| ABI 2 struct layout | §15.1 "recompute `_reserved` with padding" | reordered once, no implicit padding; `_reserved` = param 14, desc 62, host 46, vtable 64; `arb_io` gains `_reserved[28]`. Stage B2 re-laid `arb_host` under the pre-release rule: `env_glide` `env_detune` `bend_range` `pitch_st` after `env_r`, 136 bytes | `sdk/include/arbutus_engine.h`, pinned in `tests/abi_layout.h` |
| `format_param` signature | §15.1 `(h, idx, buf, n)` | **`(h, idx, normalised, buf, n)`** — value passed in, pure, callable on the control thread while `process()` runs (deviation) | `sdk/include/arbutus_engine.h` |
| `format_param` in `state` | "optional `display` per param" | slot-level `display: {id: string}` beside `params`, absent when empty, 31 bytes max | `host/audio_host.cpp` `SlotState::to_json` |
| hello `motion_sensor` | § 6.5 says what the UI must draw, not how it learns it | an added field on the `hello` reply **and** in the state document (`proto` stays 1, #62): true when `--imu` opened an LSM6DSO and its last transfer succeeded. Fitted and answering are one flag because § 6.5 makes them one case on screen | `host/audio_host.cpp` `set_motion_sensor`, `host/PROTOCOL.md` § Handshake |
| hello `abi` | §8.2 "refuse on mismatch" | client may name `kMinClientAbi`..`ARB_ABI` (now 2..2 — the emulator shim speaks 2) and is answered at that level, plus `box_abi`; boxes still exactly `ARB_ABI` | `host/audio_host.hpp` `kMinClientAbi` |
| smoothing ramp | §15.7 "at least one block" | linear, exactly one host block (`max_block`); `prepare(host, ms)` for longer | `sdk/params.hpp` `Smoothed::prepare` |
| smoothing before the first block | not specified | a `set()` before the first `next()`/`advance()` lands immediately (patch loads do not fade in); `reset()` re-arms that | `sdk/params.hpp` `Smoothed::set` |
| keys.NOTE as a per-voice source | modulation-spec §3.2 "NOTE ±" | `(note − 60) / 60`, clamped to ±1 | `sdk/poly.hpp` `note_source` |
| `motion[6]` units | modulation-spec §13 names axes only | accel in g, gyro in deg/s (the LSM6DSO's own), zeros without an IMU; axes in § 13's instrument frame since #1048 (gyro x/y as the rates of TILT-X/TILT-Y: run's call) | `sdk/include/arbutus_engine.h` |
| ABI 2 load refusals | not specified | `ENGINE BROKEN` for: per_note without `set_note_param`, per_voice without `set_voice_mod`, modulator with 0 outputs or audio ports, outputs without the modulator cap; **#673:** a vtable missing any of the SDK's six required entries (`create destroy reset set_param get_param process`; the host calls `set_param` and `reset` with no NULL check), and a manifest field of the wrong type (`"format": "1"`, `"version": 1`), which once aborted the host at start-up. The library scan reads `id`/`version`/`kind` without throwing and still indexes a box with a string id, so loading it says `ENGINE BROKEN` instead of `NEEDS ENGINE` | `hostlib/box.cpp` `check` `open`, `hostlib/library.cpp` |
| new manifest fields | §15.1 "mirrors every new field" | `outputs` always present (`[]`), `per_voice`/`per_note` on every param; absent reads as false/empty | `sdk/manifest.hpp` |
| `arbutus-run --frames` default | §15.8 moves the device and emulator to 128 | stays 512: a benchmark/golden tool, and the bench table is at 512 | `tools/arbutus-run/main.cpp` |
| NaN in a test run (#626) | §9.4 "goldens compare within a tolerance" | a golden comparison counts a non-finite difference (either side) as a failure, and **any NaN or inf in a run's output fails it, golden or not** (the review offered the second as "consider"). `std::max(peak, NaN)` had kept `peak`, so an all-NaN box passed every golden and `ragged_*` test at −inf dBFS. Negative control: `ctest -R arbutus_run_catches_nan` (`tests/nanbox`, four ways, each mutant seen red). A negative event time is refused too. *Run's call, 2026-10-13* | `tools/arbutus-run/main.cpp` |
| `xrun.total` in the state document (#626) | PROTOCOL gives `total` on the push only | added beside `count` (`proto` stays 1), so `xrun-watch.py` counts a run's xruns as the last push's `total` minus the state's at the start; it reported the host's lifetime count, and the device's host runs from boot. *Run's call, 2026-10-13* | `host/audio_host.cpp` `state_doc`, `tools/xrun-watch.py` |
| MOD: several routes to one target | §6.2 defines one route | `d·curve(m)` terms **sum**, clamped once to the intersection of the windows (unset = `[0,1]`); disjoint windows land on the highest `lo` | `host/rack.cpp` `render_chain` `combine` |
| MOD: `lo > hi` | §6.2 silent | swapped; the window has no direction, depth's sign does that | `host/rack.cpp` `resolve_route` |
| MOD: knob outside `[lo, hi]` | §6.2 "knob still sets the centre" | the clamp wins: the target sits on the nearer bound | `host/rack.cpp` `combine` |
| MOD: route limit | §2.2 four rows, §10 `MOD RACK FULL` | ≤4 routes per rack including `keys`, ≤4 tabs; fifth refused `MOD RACK FULL` (`mod_rack_full`) | `host/audio_host.cpp` `route`, `add_mod_tab` |
| MOD: bypassed modulator | §2.3 "routes hold at the knob" | not run at all (resumes where it stopped); its routes vanish, window included. Rack bypass also forwards per-voice depth 0 | `host/rack.cpp` `render_chain` |
| FX bypass | DDR-049 names rack/node bypass | a bypassed node is a dry passthrough (parallel branches are gone, #273); the engine is not run and resumes where it stopped. Whole-rack bypass is dry | `host/rack.cpp` `render_chain` |
| MOD: `set_mod_tab_engine` and content | PROTOCOL said it clears params, sidechain and assets, always | it follows `load_engine`'s rule instead (#60): a **different** engine clears all three, the **same** engine keeps them and takes `assets` when the message carries one. That is the only door content has onto a tab that already exists — remove-and-re-add takes that source's routes with it | `host/audio_host.cpp` `set_mod_tab_engine`, `host/PROTOCOL.md` § MOD rack |
| a tab's name after its box changes (#329) | not specified; the browser applies on every detent (DDR-030), so a scroll swaps the box on a tab the first box named | **the name follows the box**: `set_tab_engine` / `set_mod_tab_engine` naming a **different** engine re-mint the tab's `instance` from the new engine, exactly as `add_tab` would (the tab's own old name counts as free, so a swap back to the same stem keeps `_1`), and carry it into every address that held the old one — the rack's macro targets, MOD routes' `fx.<instance>.<param>` targets, a MOD tab's routes' `source`. The other way, a label drawn from the box over a stale id, would leave the stored address (`BIQUAD_1.MIX` on a reverb) in every macro and route. The **same** engine keeps the name (#60's content door). *Run's call, 2026-10-04* | `host/audio_host.cpp` `set_tab_engine`, `set_mod_tab_engine` |
| MOD: block order | §7 "one value per block" | SEQ → modulators → routes → macros → ENGINE → FX; modulators get the engine's events | `host/rack.cpp` `render_chain` |
| MOD: `fx.<i>.<id>` under a macro | not specified | the macro's output is the knob the route swings around | `host/rack.cpp` `fx_bound` |
| MOD: ENV target ranges | §5 names env.a..r, glide, detune, no ranges | A 0.001–2 s, D 0.001–2 s, S 0–1, R 0.005–4 s (the emulator's ENV encoders), new notes only; GLIDE 0–2 s (new glides), DETUNE 0–50 ct (read every block). The `env` message and patch clamp glide/detune to the same, BEND range to 0–24 st | `host/rack.hpp` `kEnvMin/kEnvMax`, `kBendRangeMax` |
| ENV SHIFT row defaults | DDR-052 names GLIDE · DETUNE · BEND | glide 0 s, detune **12 ct** (the old fixed ±6 ct, so goldens and old patches do not move), bend_range 2 st (MIDI's); absent from a patch = these | `host/audio_host.hpp` `InstState`, `sdk/poly.hpp` `kUnisonSpreadCents` |
| DETUNE meaning | DDR-052 "unison spread" | total spread, outermost voice to outermost, 4 voices evenly placed; live (every block), not snapshotted | `sdk/poly.hpp` |
| GLIDE shape | DDR-052 "portamento in mono/legato" | linear in semitones, constant time for any interval, from the pitch the voice is sounding (release tail and a glide in flight included; silence does not glide); time taken at note-on; poly and unison ignore it | `sdk/poly.hpp` `mono_retarget` |
| MONO/LEGATO note change (#646) | §15 "legato returns do not [retrigger]"; nothing on phase or velocity | a new note on the voice still sounding goes to `Voice::retarget(note, vel, host, retrigger)` when the voice has one (osc, fm, va, wavetable), else `start()` as before: oscillator phase, ladder, DC blocker, noise and DRIFT run on. **Velocity: a legato change keeps the phrase's, MONO takes the new note's**; MONO restarts va's filter envelope / wavetable's SWEEP from where they are, legato leaves them. Unison still restarts in phase (finding 8, a sound call for #15). *Run's call, 2026-10-07*: a velocity step is a level step, the click legato exists to avoid; **kept by Elvin, 2026-10-07 (answering R22: "keep"), on #15**. MONO (and a note landing in a release, a retrigger in either mode) reaches the new velocity by a **1 ms linear ramp** (`arb::kRetargetRampMs`, #828: the shortest whole millisecond the click renders pass; 0.5 ms left osc at 1.22x), not a gain step. Proof: `ctest -R legato_click` (equal and unequal velocities, the pitch after a change and back on the held note) | `sdk/poly.hpp` `start_slot`, `kRetargetRampMs`, each box's `retarget` |
| ENV MODE changed while notes sound (#674) | DDR-052 names the modes; nothing on a change mid-note | crossing **poly ↔ the mono family** (mono, legato, unison) releases every held voice and clears the held-note stack, in `PolyHost` itself (`set_family`, at the next block or note-on), so the chord goes into its release and the next note starts clean; mono ↔ legato ↔ unison changes nothing held. *Run's call, 2026-10-08*: in the SDK rather than an all-notes-off sent by the host on `env {mode}`, because it covers every `PolyHost` engine and every caller of `note_on`, with no message added. A 0-frame block leaves the pitch_st ramp alone (engine-spec §3.7.8). Proof: `test_poly` `test_env_mode_change_releases_the_held_voices`, `test_zero_frame_block_and_reset` | `sdk/poly.hpp` `set_family`, `process` |
| the SDK's edges for outside authors (#675) | §4 lists PolyHost's Voice calls; §3.9 "id … is the folder it lands in"; §15.7 rule 8 | **`Voice::reset()` is required** (part of `VoiceLike`): the `= V{}` fallback nulled the parameter pointer poly.hpp tells authors to keep, and the next block dereferenced it (`tests/boxes/abi2`'s Voice had exactly that). **`sdk/wav.hpp` reads what `hostlib`'s reader reads**: 16- and 24-bit PCM, float32, EXTENSIBLE by its sub-format (32-bit PCM refused by both). **`arb-manifest` refuses an id that is not `<author>/<target>`**, the folder `add_arbutus_engine` builds into. **Equal versions keep the root order** (`std::stable_sort`), so the shipped root wins by rule. **Rule 8 does not cover a parameter glide**: a ramp advanced once a block may step at the block (the template's DETUNE and TONE); written beside rule 8 in the README and §15.7. The template's ragged and rt-guard tests moved into its own `CMakeLists.txt`, so a copy carries them. *Run's calls, 2026-10-13* (required rather than dropping the fallback: rule 5 wants all state cleared, so the author has to say what that is). Proof: `test_poly`, `test_manifest`, `test_params`, `test_wav`, `manifest_id`, each guard mutated red | `sdk/poly.hpp`, `sdk/wav.hpp`, `sdk/tools/arb_manifest.cpp`, `hostlib/library.cpp`, `sdk/template/` |
| pitch_st smoothing | modulation-spec §7 "never zips" | linear ramp across the block from the last block's value; renders in 32-frame runs (`kPitchStep`) while a glide or the ramp moves, one run otherwise; glide runs on an absolute frame grid (ragged-invariant); first block after reset lands | `sdk/poly.hpp` `render_segment` |
| PolyHost pitch hook | §4 names `set_detune` | `Voice::set_pitch(cents)` = unison + glide + pitch_st before every render; `set_detune` kept (note-on, spread only) | `sdk/poly.hpp` |
| bend ramp | DDR-052 "ramped over GLIDE, snapping back on release" | position moves at 0 → full bend in the GLIDE **knob's** seconds (constant rate, so a wheel follows with bounded lag), release the same; GLIDE 0 lands in the block; per instrument; `pitch_st = position × bend_range + host.pitch` | `host/audio_host.cpp` `render_block` |
| `host.pitch` knob | §5 "±24 st, bipolar" | normalised, 0.5 = 0 st, `(v − 0.5) × 48` **added** to the bend; `host_param {pitch}` sets it; session state like level/pan | `host/rack.cpp` `render_chain`, `host/rack.hpp` `kPitchSpanSt` |
| MOD: `host.level` / `host.pan` | §5 names them | level = linear gain, knob default 1; pan = balance, unity at centre, `cos` toward the far side, knob 0.5; ramped across the block; knobs set by `host_param`, not in the patch | `host/audio_host.cpp` `render_instrument` |
| `ARB_EV_BEND` | §15.1 / §15.6 define it | **removed** before release (deviation): bend is `arb_host.pitch_st`, so an engine has one pitch mechanism; `bend` is a host message | `sdk/include/arbutus_engine.h` |
| MOD: `keys` before any note | §3.2 silent | VEL 0, NOTE 0; both follow the notes the ENGINE receives (after SEQ) | `host/rack.hpp` `ModRun` |
| MOD: per_voice routes | §8 `set_voice_mod(idx, src, depth)` | depth only; curve and window ignored for these routes; depths of several routes sum | `host/rack.cpp` `build_mod_ctl` |
| MOD: unset window in the document | §6.1 `null`, §11 omits | omitted on output; `null` or absent reads as unset; `set_route` with `null` clears | `host/rack.cpp` `ModDoc::to_json` |
| MOD: live values | §9 ghost, no transport | `meters` push gains `mod: {target: value}` for the active instrument; per_voice routes report the last note | `host/audio_host.cpp` `poll` |
| MOD: stale routes | not specified | a route whose target disappears stays in the document, inert, like a macro target; a patch routing to an unserved address is refused | `host/rack.cpp` `build_mod_ctl` |
| MOD: message names | §15.6 names `route`/`unroute`/`set_route` | plus `add_mod_tab` `remove_mod_tab` `set_mod_tab_engine` `set_mod_bypass` `host_param`, reply type `mod`; `route.index` atomically reinserts a lifted route at a row | `host/PROTOCOL.md` |
| host `--rt-guard` | §3.7.1 | offline, counts heap blocks across `render_block`, `rt_allocs` on `render` replies | `host/audio_host.cpp` `render_offline` |
| `live_blocks()` off macOS | not specified | **#52:** a counting allocator, not `mallinfo2` (not RT-safe, #50) and not `/proc` (page-granular). `hostlib/rt_alloc_hooks.c` defines the malloc family in the *executable* — ELF interposition then routes the whole process, dlopen'd boxes included — and forwards to glibc's `__libc_*`, so nothing is replaced and an uncounted path is still correct. No `dlsym(RTLD_NEXT)`, so no calloc-recursion trap; two constant-initialised statics, so no init-order hole; one relaxed atomic add per block, so it is RT-safe. Armed once from `main()` under `--rt-guard` only. Semantics match macOS exactly: **live blocks, process-wide, before/after the block**, so an alloc+free pair inside one block is not caught on either platform | `hostlib/rt_alloc_count.{hpp,cpp}`, `hostlib/rt_alloc_hooks.c` |
| `live_blocks()` bias | not specified | glibc-internal allocations (`strdup`, stdio buffers) bind to libc's hidden alias and never reach the hooks, while a later `free()` from our code does, so the delta can go negative; the count is reported with a fixed `+2^24` offset so it cannot be misread as the `-1` that means "cannot count". Only differences are meaningful — nothing prints the absolute | `hostlib/rt_alloc_hooks.c` |
| **deviation:** counter linked in, not `LD_PRELOAD`ed | #52 offered either | linked into `arbutus-run` **and** `arbutus-audio`. `LD_PRELOAD` would leave the production binary untouched, but `arbutus-audio` is itself what takes `--rt-guard` (six host suites drive it, and P1 benches it on the CM4), so preloading would mean re-exec'ing the audio process with a doctored environment — a second, fragile startup path on the one binary the bench measures. Cost with the guard off: one relaxed load of a flag and a branch per `malloc`/`free`. The audio thread allocates nothing by contract, so the realtime path itself pays nothing | `hostlib/CMakeLists.txt` |
| **deviation:** `add_arbutus_engine(... ROOT)` | not specified | new optional argument, so #52's deliberately-allocating fixture builds into `<build>/rtguard/` instead of `<build>/boxes/`. A test box under `ARB_BOX_ROOT` would be indexed by the host and listed by the UI — and this one violates §3.7.1 on purpose | `sdk/CMakeLists.txt`, `tests/CMakeLists.txt` |
| lfo RATE | §3.3 "8 bars … 1/32T", "0.05 … 50" | dead zone ±0.02 = STOP; 14 divisions (straight + triplet from 1/2 down); free Hz log; default 1/4 | `engines/arbutus/lfo/src/engine.cpp` |
| lfo SHAPE-MORPH, SYM | §3.2 names them | MORPH rounds SQR toward sine and glides S&H; SYM 5–95 % skews the phase for all four (TRI→ramp, pulse width); id `morph`, label `MORPH` (8 chars) | same |
| modulator golden format | not specified | `arbutus-run` writes one channel per output, block value held; NPY reads up to 6 channels (4 outputs, 6 motion axes) | `tools/arbutus-run/main.cpp` |
| sidechain `self` / `master` | modulation-spec §4 "one block late" | delay lines of exactly `max_block` frames (= previous block at the fixed block; ragged-stable); `self` taps the bus after FX and host.level/pan, `master` after master gain + limiter; `self` cleared on instrument switch; FX tabs take both | `host/audio_host.cpp` `render_block` |
| sidechain `tape:1..8` | modulation-spec §4 | all eight (stage E1); an unrecorded track is silence, not an error | `host/rack.cpp` `side_from` |
| MOD tab sidechain message | §4 "same selector" | `set_mod_sidechain {index, source}`, route table swap only; refused without `ARB_CAP_SIDECHAIN` | `host/audio_host.cpp` |
| MOD / FX tab assets | not specified | `"assets": {key: path}` on a `mod` or rack tab, `add_mod_tab` and `add_tab`, resolved like a patch's (`dir` of the message, rack or patch); written back absolute, omitted when none; `set_mod_tab_engine` / `set_tab_engine` clear them | `host/rack.cpp` `mod_from_json` `rack_from_json` |
| motion feed for tests | §13: host reads the IMU | offline-only `motion {accel?, gyro?}` (omitted axis = 0, held); `arbutus-run --motion` `.npy` (6, rows), row r = block at r·`--frames`, last row holds | `host/audio_host.cpp`, `tools/arbutus-run/main.cpp` |
| `panic` and MOD tabs | not specified | `panic` also `reset()`s MOD tabs, so envelope's held-note count cannot stick | `host/audio_host.cpp` `Ck::AllNotesOff` |
| follower | §3.2 names outputs/params | output = the block's last frame; ENV peak(\|L\|,\|R\|)·GAIN one-pole; FAST fixed 0.5 / 30 ms; INV = 1−2·ENV; GATE on ENV (post-GAIN) > THRESH, held HOLD; ATTACK 0.1–500 ms, RELEASE 5–5000 ms (log), GAIN −12…+36 dB, THRESH −60…0 dB, HOLD 0–1000 ms | `engines/arbutus/follower/src/engine.cpp` |
| random | §3.2 "S&H, DRIFT, WALK, —" | 3 outputs; RATE copied from lfo (default 1/8); SLEW = S&H glide fraction; RANGE = max jump from the last value (2·RANGE); DRIFT Catmull-Rom through random points; WALK Brownian ∝√t, reflecting; SEED INTEGER 0–999, 0 = host `rng_seed`, a change restarts the sequence | `engines/arbutus/random/src/engine.cpp` |
| envelope | §3.2 TRIG/LOOP/CURVE, no numbers | global; output = block's last frame; A/D 1 ms–10 s, R 1 ms–20 s (log); attack takes A·(1−start); GATE-IN detector instant/50 ms, opens −30 dBFS, closes −33; FREE+LOOP cycles A→D→A, FREE alone = A, D, R once on load/reset/switch; CURVE ±1, k = 6·\|c\| exponential | `engines/arbutus/envelope/src/engine.cpp` |
| motion | §13 "gravity components", "~150 ms" | TILT = accel x/y clamped ±1; gravity = 20 ms low-pass per axis, SHAKE = \|accel − gravity\| / scale, peak-hold τ 50 ms (5 % at 150 ms); SENS knobs `mid·8^(1−2x)`, mid 0.5 g / 250 dps; SMOOTH 1–2000 ms (default 50) on TILT and SPIN | `engines/arbutus/motion/src/engine.cpp` |
| stepmod timing | §3.2 silent | transport playing: song-locked (`song_frame`); stopped: free-running on tempo, note-on restarts at step 1; value at block start; GATE first half of a step | `engines/arbutus/stepmod/src/engine.cpp` |
| stepmod pattern / ranges | §3.2 "entered like an endless pattern" | asset `pattern` = `{"format":1,"kind":"stepmod","values":[±1 × 1..16]}`, wraps under STEPS; none = ramp −1…+1 across STEPS; DIV = endless's list plus 1 BAR, 1/2 (default 1/16); DIR RAND = hash(step, rng_seed); RANGE scales STEP | same |
| drum ENV | DDR-053 "per-pad AD" | **deviation**: host block `env_a`/`env_d`, global, latched per hit (the 8 slots are §15.3's per-pad list; no room for ATK/DEC per pad). Linear attack (< 1 frame = instant), exponential decay to −60 dB at `env_d` (v1 DECAY). DRUM instrument ENV defaults **a 0 s, d 4 s** (v1) | `engines/arbutus/drum/src/engine.cpp`, `host/audio_host.hpp` `kDrumEnvA/D` |
| drum choke / gate / loop release | §15.3 names PLAY, CHOKE | 5 ms linear fade; CHOKE fades every *other* sounding pad in the group; LOOP cycles START…END while held and stops (fades) on release like GATE; **dbox's HAT choke takes the same 5 ms fade** (#647; it cut in one sample) | `engines/arbutus/drum/src/engine.cpp` `kCutFadeS`, `engines/arbutus/dbox/src/engine.cpp` `kCutFadeS` |
| pitch on the boxes off a note's pitch (#647) | §15.10 "engines not on PolyHost may read `pitch_st` directly"; nothing per box | **drum**: `pitch_st` scales every sounding pad's playback rate, live; **dbox**: it scales every sounding pad's sines, live, the noise filters left where TONE put them; **noise**: `set_pitch` (bend, `host.pitch`, glide, unison spread) scales the cutoff as the note does, and CUTOFF and TYPE are read every block instead of latched at note-on. No glide on drum or dbox (a pad has no note to glide from). *Run's call, 2026-10-13*: a route to `host.pitch` was silent on all three, a knob that does nothing; with pitch_st 0 every box is bit-exact (goldens hold). Proof: `ctest -R drums_noise_live` | `engines/arbutus/{drum,dbox,noise}/src/` |
| drum PAN | §15.3 names it | −1…+1 balance: unity at centre (v1), `cos` toward the far side, like `host.pan` | same |
| kit pad shape | §15.3 `{file, params}` | both it and v1 `"0": "file.wav"` read, so **`format` stays 1**; host reports every pad as `{file, params}` with all `per_note` params filled (defaults from the descriptor) — that document is the save; **no `save_kit`** | `host/audio_host.cpp` `load_kit` `normalise_kit` |
| `set_note_param` addressing | §15.6 `{slot, note, idx, value}` | `pad` 0..23 or `note` = pad + 53 (F3); `idx` or `id`; ENGINE slot only; refused without a kit file on that pad; replayed into every rebuilt engine | `host/audio_host.hpp` `kKitBaseNote` |
| `copy_pad` | §15.3 LIFT/DROP pad copy | `{from, to}` pads; copies file + params, reloads the kit (crossfade) | `host/audio_host.cpp` |
| sampler loop | screen-sketches `LOOP-IN · LOOP-OUT` | clamped inside START…END; IN ≥ OUT = no loop; defaults **1 / 1** (v1 LOOP OFF); loops while held, on release plays to END (finishes a seam crossfade first); v1 LOOP FWD = IN START, OUT END but lets go at release | `engines/arbutus/sampler/src/engine.cpp` |
| sampler FADE | "crossfade length" | 0…50 % of the loop, equal-gain, reads material before LOOP-IN (after OUT in reverse), capped by what the file has there | same |
| sampler LEVEL removal | screen-sketches drops it | constant voice trim **0.8** (v1 default): patches and golden keep their level; the host drops unknown param ids from a patch (not a §7.4 refusal) | same, `host/audio_host.cpp` `build_slot` |
| sampler `format_param` | §15.1 | positions in seconds of the loaded file (`%.2f s`, `%.1f s` ≥ 10 s), ROOT a note name (60 = C4); load_asset-time state counts as fixed | same |
| transient | DDR-053 names the box | F 0.5 ms / SPEED, S SPEED / SPEED, T 0.5 ms / 4×SPEED on max(\|L\|,\|R\|); gain dB = 12 × (ATTACK·(F−S)/F + SUSTAIN·(T−F)/T); SPEED 5–200 ms log, default 30; default is bit-exact passthrough | `engines/arbutus/transient/src/engine.cpp` |
| `waveform` | §15.6 "peak envelope" | min/max per bin of the mono mix (channels averaged); bins default 256, clamped 1..4096; cached per path+bins, invalidated by size/mtime, ≤ 64 entries; control thread only | `host/audio_host.cpp` `waveform` |
| tape medium writes | §8.2 "from the current transport position" | **append-only** (deviation): a take and every `tape_edit` land at the track's head, so a region a clip reads is never rewritten — that is what makes the UI's clip algebra (move, copy, slice, one undo) safe. `used_frames` in `state`; a full medium stops the take growing | `host/tape.hpp`, `host/audio_host.cpp` `Ck::Record` |
| clip source track | not specified | `src_track` on a clip and in `set_clips`, default its own track: LIFT on one track and DROP on another keeps its sound | `host/tape.hpp` `Clip` |
| `clip_peaks` | not specified (the UI drew the samples) | min/max per bin over **both channels** (the emulator drew max abs of the stereo clip per pixel); bins 1..65536, default 256; cached for settled regions only, ≤ 256 entries. The reply carries **`source`** — `sidecar` or `audio` — since #163, because a cache-warm bench cell cannot tell a hit from a fast card by wall time. Since **#169** it also takes a **batch**, `{reqs:[…]}` → `peaks:[…]` in request order, because TRACK draws four lanes at once and a round trip costs 2.1–2.5 ms on the CM4 with this host's own reply cache warm. `peaks_one` is the one implementation, so a batched element gets the same cache, sidecar and scan; **at most 64** (the reply is built whole in memory, so a longer want-list is the caller's to chunk) and a **bad element refuses the whole batch** naming `reqs[<i>]`, because the caller built every element in one pass from its own geometry. A cache hit answers with the **requested** `track`: the key is the *source* track, so a hit used to carry whichever destination had populated it | `host/audio_host.cpp` `clip_peaks`, `peaks_one` |
| the peaks reply's precision | not a spec question | **the shortest text that round-trips the float**, `manifest::nice`, unchanged — and #169's two-decimal proposal was **measured and refused**. It does halve the bytes (a 300-bin reply 7,211 → 3,379 B) and it does **not** leave the picture alone: a bin is drawn as `int(max(\|min\|,\|max\|) × amp)` with `amp` 7 px in a TRACK lane, so an error of 0.005 crosses a drawn boundary for **1 bin in 300** on a three-minute clip (16 at amp 10, 33 at 20) and moves **8 of 309 verify shots by 2–4 px**. The pixel-exact way to shrink this message is fewer numbers rather than shorter ones — one magnitude array instead of `min` and `max`, which is exactly what the UI draws — and that is a change to what the reply *means*: **#172**, Elvin's | `host/audio_host.cpp` `peaks_one` |
| the peaks sidecar (#163) | § 3.6 recommends it as C1 step 6's follow-up *if* a redraw costs more than a frame, and it does (4.0 s a bank, #162) | **16 min/max pairs per range** (`kPeakBins`, 3072 frames a bin, 128 B a range, ~60 KB an eight-minute track), worked out by the I/O thread on every range it **commits or fetches** — the one moment the whole range is already in its hands — held in RAM and written beside the medium as **`tape-N.peaks`**. It is a **cache and nothing depends on it**: `peaks_for` refuses what it cannot answer exactly and `clip_peaks` then scans the audio as it always did. Three refusals: a question **finer** than a bin; a span past the medium's **durable prefix** (which is what excludes a range the pool has written and the card has not taken — the append-at-a-written-head case, where the bins exist and are a take out of date); and a range with no bins yet. Written **on the commit that ends a take** and at `stop()`, not at SAVE as § 3.6 says — `tape_save` writes a *copy* somewhere else until step 3b and which file the medium is is #157's; the take's end is the same instant for the medium and needs no decision. Not synced, and discarded on open unless its frame count matches the header the repair just made exact. **A compaction drops it, file included** (every offset in that medium has moved) and it heals itself, because the scan that then answers fetches and a fetch computes bins. The envelope is **inclusive, not exact**: an output bin takes whole sidecar bins, so one straddling its edge widens it by up to 3072 frames of neighbouring audio — invisible at one bin a pixel, and why agreement is asserted on aligned spans. `--no-tape-peaks` is the *before* switch; `state.tape.io.peaks` carries `ranges`, `hit`, `miss`. **Measured on the bench CM4 (#163's bench cell, 2026-09-24, both arms off one boot): a bank of four 180 s clips 3,908 ms → 55 ms and 255 MB off the card → 0; per clip 936–1,041 ms (card + scan) → 117–124 ms (scan, warm) → 3.2 ms (the sidecar).** The cap's own share is separable — the host-cache-warm cell is 31 ms in *both* arms against #162's 164–174 ms. The fall-through is proved on the board too: asked finer than a bin the sidecar refuses, reads 63.7 MB off the card and reports `miss`. **What is left of a redraw is the reply, not the tape** — a `clip_peaks` round trip is 2.1–2.5 ms even answered out of `peak_cache_`, so ~13 ms of a 35 ms redraw is four messages of 600 JSON numbers (**#169**, half done 2026-09-25: the four messages are **one** batch, the numbers are still full precision and why is the row below; the CM4 cells are **#171**) | `host/tape_io.cpp` `note_peaks`, `peaks_for`, `save_peaks_locked`, `load_peaks` |
| `tape_edit` | not specified | one message for the audio-changing clip ops: sources mixed into a run, `copy`/`reverse`/`speed`/`filter`, appended at the head, reply says where. The DSP is `emulator/app/tape.py`'s. Refused while a take is open; `tape_full` when it will not fit | `host/audio_host.cpp` `tape_edit` |
| `tape_edit`'s working set (C1 step 6) | § 3.6: "the working set is a handful of ranges, not the edit's length, so a 5-minute reverse does not need 300 slots" | exactly that: **one destination range at a time** — mix that range's run window out of the sources, apply the op, write, commit, release — where it used to build the whole result in two buffers the length of the edit (~230 MB for a five-minute reverse) and hold every destination range resident until the last was durable. The arithmetic does not move: `reverse` reads its source ranges last-to-first, `filter` carries its pole across the seam, `speed` indexes the run by the same global frame and takes its output in pieces small enough that a large `factor` cannot widen the window. **Bounded, the old shape lost audio silently** — five ranges against a three-slot window wrote nothing, committed the zeroes of never-resident ranges, and replied `len` as though it had worked (reachable only with a `--tape-dir`, so never on a device) | `host/audio_host.cpp` `tape_edit` |
| the head's range during a streaming edit | not a spec question | **fetched *and* held** until the first chunk commits it. `tape_prepare_head` alone was enough while every source was read before the write; streaming reads sources between the fetch and the write, one of those fetches evicts it, and then `acquire` hands out a zeroed slot and the commit writes it over the audio in front of the head — the same fault step 2b named, by a different door. Four edits in a row against a three-slot window is what shows it: the second takes the tail of the first | `host/audio_host.cpp` `tape_edit` |
| a source reading past the destination track's head | not specified | **silence.** Reads and writes interleave now, so a source running past the head would read the edit's own output back; past the head is what the next take overwrites, not audio the tape claims — and it is what an unwritten medium returned anyway. Only the destination track is clamped; another track's medium is nothing this edit is writing | `host/audio_host.cpp` `tape_edit` |
| a chunk the pool cannot give a slot for | § 3.7 has `TAPE FULL` for the medium, nothing for the window | one `flush` and one retry (the I/O thread tops the free list up every turn, so "momentarily empty" is not "cannot be written"), then the edit **ends there** and the reply's `len` is what really landed — a shorter clip rather than a claim on frames the medium has not got. `TapeIo::release_span` takes the dirty mark back off the range that was not written, because a range left marked is one the evictor may never take. `tape_full` is still the answer when nothing at all landed | `host/audio_host.cpp` `tape_edit`, `host/tape_io.cpp` `release_span` |
| tape playhead vs clock | §3.3 has one song position | two: the clock (`song_frame`) always runs forward at 1x, the tape playhead (`tape_pos`) takes SPEED, REV and the loop — DDR-016's "BPM is the ruler" applied to varispeed, and the emulator's split | `host/audio_host.cpp` `render_block` |
| loop | story 10, not in §8.2 | `transport {op:"loop", in, out, on}`, wraps the tape playhead only; recording through the seam keeps appending, so a take may be longer than the loop | same |
| SPEED / REV | DDR-017, DDR-059 | `tape_speed {speed, reverse}`, 0.25…4x, nearest-lower frame (no interpolation, the emulator's); REV release lands at anchor + elapsed, as `hold_rev` did; a take ignores SPEED and REV and rides the LINK factor alone (stage E1) | same |
| FILTER hold | DDR-026 | `tape_filter`, one-pole LP/HP over the **tape mix only**, live while held, state cleared on release | same |
| stopped tape / scrub | not specified | stopped the tape is silent (it used to repeat the block under the playhead); `scrub {position, frames, reseed}` plays one grain with 64-frame fades, `reseed: false` extends the running one | same |
| metronome | §8.2 has no click | `metronome {level, pitch}`, rendered into the **master only** (never the record tap) from the clock: beat b at b x 60/BPM, 30 ms sine, squared decay, downbeat an octave up — `emulator/pages/tempo.py` `Metronome._burst`, and a pure function of the frame. **Beats sit on the song grid**, where the emulator counted them from PLAY | same |
| MASTER COMP | DDR-027 | `mixer {comp}`, the emulator's `app/mixer.Compressor` number for number (threshold 1−0.85a, ratio 1+7a, 5 ms / 120 ms, makeup 1+0.6a), one gain per block, reported as `comp_gr` on `meters`; not applied by `bake` | same |
| INPUT MONITOR | not specified | `monitor {on, gain}`, the live input into the master, never the tap; off by default (a laptop feeds back) | same |
| `tape_compact` (C1 step 8) | § 3.8 "the UI says what to keep, the host moves the bytes and hands back the map" | exactly that, and **two messages on the wire**: the request replies `started` and the map arrives as a push, because a 2 x ruler medium is ~7 s of copying and § 3.8's rule is that SAVE *starts* a compaction rather than waiting for one. The keep set is sorted, coalesced and clamped to the medium's own claim; each kept span lands on a **range boundary** of the new medium, so a range never straddles two source spans (which is why `frames` can exceed the kept lengths). The copy reads the **card**, not the pool — `compact_start` flushes first — and writes `tape-N.wav.compact`, which the control thread renames over in one step, reopening the medium and dropping every resident range of the track. Refused `RECORDING` / `PLAYING` / `BUSY` / `NO TAPE DIR`; PLAY or REC arriving **cancels**, the scratch file goes and the old medium was never touched. **The copy does not hold the tape's lock across the card (#167)** — it did until 2026-09-25, and on the bench that made all three promises above false: the `started` reply came at 7.09 s of a 7.14 s job, a fresh connection's `hello` took 4.7–5.3 s and PLAY two seconds in cancelled nothing, because the copy's thread sat in the kernel holding `mu_` for 97 % of the job's wall time and 9 % of its processor time. So `compact_step` drops `mu_` around every `pread`/`pwrite` pair and re-reads its own state after each (a cancel may have landed), `dead_frames` comes back from `compact_start` rather than from a second `compact_freed()` call, and `compact_cancel` puts the state down **before** it waits, so the cancel lands inside a slice and costs one range of card (~27 ms) instead of the job. The price of dropping the lock is that a handle can be in use by a thread that does not hold `mu_`, so everything that closes one — the cancel, `compact_clear`, `compact_finish`, `stop` — waits an in-flight slice out through `wait_io`. `FakeStore` is thread-safe and has a per-call delay for the same ticket; **`set_compact_holds_lock` is the *before* switch** that puts the old discipline back and makes the test go red (26 writes of 24 after the cancel, `compact_state()` blocked 117 ms, against 4 of 24 and 0 µs), because `test_compact_cancelled_live` was green throughout the fault and #56 says a green harness is not evidence. **The bench acceptance is met (#170, 2026-09-25)**: on the CM4, two runs of the same 369 MB cells, a fresh connection's `hello` during the copy is **0.00 s** against 4.7–5.3, the `started` reply **2–3 ms** against 7.09 s of 7.14, PLAY two seconds in really does cancel — scratch gone, medium byte for byte, both runs — and the uninterrupted wall time did not move (7.9/7.1 s front, 6.8/7.5 interleaved, 3.3/3.6 at the shipped ruler), which is the point: the lock came off the card and not off the work. The cancel itself costs **a slice in flight**, not the 27 ms one range would be — PLAY's own reply was 104 ms and 25 ms — because `wait_io` waits out a `pwrite` that may have only just begun. The UI half — remapping its clips, lift buffer and undo record through the map, and the SAVE trigger — landed as #164 | `host/tape_io.cpp` `compact_*`, `host/audio_host.cpp` `tape_compact` |
| `dead_frames` in `state` | § 3.8 "the host suggests it in `state`" | frames of a track's medium **no clip on any track references**, swept over all eight clip lists because a clip may read another track's medium (`src_track`). It can only **over-report**: the UI holds the lift buffer and the one-level undo record (DDR-054/055), which this process has never seen, so the host's answer is "worth compacting" and the keep set stays the UI's | `host/audio_host.cpp` `dead_frames` |
| tape persistence | software-stack-v1 §8.3 (mmap files) | `tape_save` / `tape_load` per track, `used_frames` of the medium as a stereo f32 WAV; the clip list stays the UI's JSON. Load the medium *before* `set_clips` | `host/audio_host.cpp` |
| `tape_save`'s durability | device-os-spec § 7.3 asks for temp-then-rename and does not say where | **`arb::io::write_durable`** (#93): `<path>.tmp`, `fsync`, `rename`, then a **best-effort** `fsync` of the directory — the bytes are on the medium and the new name is visible by then, so a filesystem that refuses that last step must not turn a completed save into a reported failure; what is lost is only which of the two names survives a cut in the next moment. Both writers **close the stream before testing it** (#670): the last buffer goes out on close, and a card that fills there used to fail only in the destructor, unseen, so a short `.tmp` was renamed over the good take. `tape_save` is its **only** caller, so goldens, bounces and library exports keep the plain writer and pay no flush. **It does not contradict the row above it**: this is the whole-file writer, and a range of a take in flight is still written **in place** at its own offset, because a rename-based *medium* is the 737 MB copy on SAVE the streaming design rejects. The two rules are about two different writers | `hostlib/audiofile.{hpp,cpp}`, `host/audio_host.cpp` `tape_save` |
| overdub take | DDR-056 "a take sums the instrument with what the track plays" | the tap adds that track's *rendered* block to the instrument bus, so a take is what you heard; the commit **absorbs** (split, drop the covered part, keep the remainders) so `state` never overlaps, and the same clip goes out as a `take` push rather than leaving the UI to diff two lists | `host/audio_host.cpp` `render_block`, `absorb` |
| LINK reference | DDR-059 gives LINK, not what it is relative to | `tape.link.base_bpm`, the tempo the medium is written at, default 120 and moved only by `tempo {base_bpm}` (a project load). Factor = `bpm / base_bpm` with LINK on, 1 with it off — so off is the tape's own pitch, which is what DDR-059's off state is for | `host/audio_host.hpp` `link_factor` |
| a take under LINK | not specified | resampled by the factor on the way in (linear, phase continuous across blocks) so the medium always sits at `base_bpm` and the clip covers the timeline it was played over. At factor 1 it is the straight copy it always was, sample for sample. The interpolation clamps at the block edge rather than keeping the previous block's last frame | `host/audio_host.cpp` `render_block` |
| `clip_to_asset` path | §15.6 gives the message, not the path | `<last --library root>/synth/sampler/tape-<n>-NNN.wav` (`mix-NNN.wav` for `"all"`), **16-bit** per the library rule, library rescanned after; `file` overrides. One track bounces raw at clip gains, `"all"` through the mixer | `host/audio_host.cpp` `clip_to_asset` |
| per-track EQ shape | DDR-062 names LOW/MID/HIGH ± 12 dB and HP/LP, no frequencies | RBJ biquads: LOW shelf at **200 Hz**, HIGH shelf at **3 kHz**, MID a peak at MID-F (100–8000 Hz, default 1000) with Q (0.2–8, default 0.7), HP/LP one biquad each at Q 0.707. dB and Hz on the wire, not normalised: this is host DSP, not an engine | `host/mixer.hpp` |
| EQ "off" | not specified | HP at its bottom (20 Hz) and LP at its top (20000 Hz) are **bypassed**, not a filter at the band edge; three bands at 0 dB and both filters off is bit-exact bypass, and the state is cleared when a track's EQ goes flat | same |
| EQ's place | §15.5 "tape → per-track EQ → mixer" | between the track and the mix **only**: the medium, the record tap's own playback, sidechain `tape:n` and a one-track `clip_to_asset` are all upstream of it. Applied on the audio thread; coefficients recomputed in `apply_cmd` (trig, no allocation) | `host/audio_host.cpp` `Ck::Eq` |
| mute / solo | DDR-061 names the gestures | `mixer {track, mute\|solo}`; **solo wins** (any solo mutes the rest, including a track's own mute); the gate is on the mix only, so a muted track still bounces and still feeds a sidechain. The track meter is pre-gate, so a muted track still shows life | `host/mixer.hpp` `mix_gate` |
| MASTER DRIVE | DDR-062 names DRIVE and DRIVE TONE | soft clipper: `tanh(g·x)/tanh(g)` with g = 1 + 9·DRIVE, crossfaded dry→wet by DRIVE so 0 is bit-exact bypass. TONE tilts what reaches the shaper around **700 Hz** — 0 = lows only, 1 = highs only, 0.5 = flat and exactly reconstructing (so TONE alone does nothing at DRIVE 0). It can exceed 0 dBFS; that is what LEVEL and the limiter are for | `host/mixer.hpp` `DriveStage` |
| MASTER BAL | DDR-062 names BAL | a **balance**, −1..+1, unity at centre and `cos` toward the far side — `host.pan`'s law, not the tracks' equal-power pan, so the default is untouched | `host/mixer.hpp` `bal_gains` |
| COMP REL | DDR-062 names the knob | release in **seconds**, 0.02–1, default **0.12** = v1's fixed value, so the sound does not move. Known quirk kept from the emulator: the envelope coefficient is `exp(−1/(sr·tc))` applied **once per block**, so the audible release also scales with the host block — the knob works, its labelled time does not (`mixer.py`'s arithmetic, frozen by DDR-027). **Measured (audit Q1, 2026-09-18):** `exp(−1/(sr·tc))` is a *per-frame* coefficient applied *per block*, so the time constants are `tc × block` — at 128 frames the 5 ms attack is **≈0.64 s** and the 120 ms release **≈15 s**; at 512, ≈2.6 s / ≈61 s. A sound change to fix, so it is an issue, not an audit edit (#67, **closed by Elvin with no comment** — the call is unrecorded). **L4 (#141, 2026-09-23) made it a divergence as well as a quirk**: the device runs `--block 64` and the emulator 128, so on the box the attack is ≈0.32 s and the 0.12 release ≈7.7 s — half the emulator's, with the same knob in the same place. Off by default (`a_comp_ > 0.001f`), so no golden, no conformance test and no default sound moves, and `ragged.sh` cannot see it because it tests boxes and this is the host's master strip. It is the **only** block-dependent term there: the limiter's envelope is inside the per-frame loop | `host/audio_host.cpp` `render_block` |
| clock event order | §3.4 hands engines events in frame order | a **stable insertion sort** (`host/events.hpp`), no allocation; it replaced a `std::stable_sort` that allocated its merge buffer on the audio thread every playing block under libstdc++ — invisible to `--rt-guard`, which counts net growth (#52). `tests/test_events.cpp` counts `operator new` (audit Q1) | `host/audio_host.cpp` `build_events` |
| the master rack | §15.5 `load_rack {instrument:"master"}` | a **third bus** beside SYNTH and DRUM (`kMasterBus`), so every rack message takes `instrument:"master"` unchanged; `ChainInput::bus` feeds the mix in where an engine's output would be. Reported under `state.mixer.fx`/`tabs`/`macros`. No ENGINE, SEQ, MOD rack or patch: those messages are refused rather than falling back to the active instrument | `host/audio_host.cpp`, `host/rack.cpp` `render_chain` |
| master strip order | §15.5 "rack → drive → comp → level" | exactly that, which moves LEVEL **after** COMP (it was before, stage 2). The click and the input monitor are added after the rack and before DRIVE: a reverb on the master must not smear the metronome | `host/audio_host.cpp` `render_block` |
| the mixdown's strip | PROTOCOL.md "COMP is not baked" | `bake` and `clip_to_asset "all"` take the EQ, mute/solo, gain, pan, DRIVE, LEVEL and BAL; **COMP and the master rack are not baked** — COMP is a performance knob and MASTER FX on a bounce is FILE › PROJECT's switch (J1) | `host/audio_host.cpp` `bake`, `clip_to_asset` |
| endless PATTERN rotation | §15.4 "PATTERN (ENUM of rhythm masks, SHIFT+turn rotates)" | **deviation**: rotation is its own slot, `ROT` (INTEGER 0–15), in the next SHIFT position. `set_param(idx, 0..1)` carries no modifier across the ABI — only a soft-key event has a SHIFT flag (DDR-041) — so SHIFT+turn is not expressible by an engine | `engines/arbutus/endless/src/engine.cpp` |
| endless PATTERN masks | §15.4 names no masks | eight 16-step masks (`OFF 4/4 8THS OFFB 3-3-2 CLAVE GALLOP EVERY3`), bit *i* = step *i*; a cleared bit turns that step into a rest **without moving it**. The mask runs on its own free step counter, not the step index, so a 5-step phrase precesses through it. `OFF` is all bits set, so it is not a special case | same |
| endless GATE default | §15.4 gives 10–100 % | **100 %** (legato, a step ends where the next begins) = what the box did before GATE existed, so `tests/golden/events.json` did not move. The whole early-note-off path is skipped at 100 % and under a tie, frame for frame | same |
| tied notes | §15.4 "hold keys + ›" | `›` is the REST soft-key, so REST has two meanings told apart by the hand: nothing held = a rest, keys still down = a **tie**, a step that emits and silences nothing so the step before it keeps sounding. One key, not a fifth soft-key (DDR-041 allows four). Ties are FWD-only; under REV/PNG/RAND a tie plays as a rest | `endless` and `pattern` |
| the `pattern` asset | §15.4 "a pattern is an asset (`pattern` key on the seq box)" | `{"format":1,"kind":"pattern","root":60,"pattern":[[60,64],"tie",[]]}` — a step is a list of MIDI notes, `[]` a rest, the string `"tie"` a tie. `root` is what `endless` transposes from (absent = the first note played) and is **ignored by `pattern`**, which plays absolute pitch. Declared by `endless` and `pattern`; control thread only | `engines/arbutus/{endless,pattern}/src/engine.cpp` |
| arp / euclid soft-keys | DDR-041 allows ≤4 labelled keys | **none**: neither box stores anything, so every gesture it has is already a knob and a key duplicating an encoder is worse than a blank strip. `pattern` declares `endless`'s four, same ids and same behaviour, so one UI drives both | `engines/arbutus/{arp,euclid}` |
| arp HOLD | not specified | the last key **up** latches the chord; the next key **down** starts a fresh one (otherwise a latched arp only grows). `n_held` is the chord, `n_down` the keys actually down | `engines/arbutus/arp/src/engine.cpp` |
| arp RATCHET | not specified | the step is divided into 1–4 equal hits of the same note, each with its own GATE; SWING is applied to the step, then divided | same |
| euclid necklace | §15.4 names the box only | the closed form `(i·PULSES) mod STEPS < PULSES` — Bjorklund's necklace **up to rotation** (E(5,8) comes out `x.x.xx.x`), with no recursion, allocation or table, which is what §3.7.1 needs from a query on the audio thread. Rotation is a knob, so the difference is one detent of ROT | `engines/arbutus/euclid/src/engine.cpp` |
| euclid ACCENT / HOLD | not specified | ACCENT drops the off-downbeat onsets to `1 − 0.7·accent` (0.3 at the top, never 0 — a silent onset is what PULSES is for); the downbeat is `step % STEPS == 0`, so ROT decides whether it is an onset at all. HOLD with nothing yet played latches **middle C**, so the box is auditionable with no hand on a key | same |
| pattern STEPS = 0 | not specified | `AUTO` — the grid is the pattern's own length, so the box behaves like `endless` until told otherwise. `format_param` prints `AUTO <n>` with what it currently resolves to, because `0 STEPS` on the screen would be a lie | `engines/arbutus/pattern/src/engine.cpp` |
| pattern pitch | §15.4 names the box only | **absolute**: the key is a run key, not a transpose (that is `endless`'s job), and OCT ±2 is the only pitch control. That is the shape a pattern *file* wants, which is why it is the box H2's browser hands one to | same |
| SEQ default box | not a spec question | `pages/seq.py` `DEFAULT_BOX = "endless"`. H1 put `arp` before `endless` alphabetically and folder 0 was the default, so a fresh SEQ slot would have changed engine because a box was added | `emulator/pages/seq.py` |
| H2: a pattern reaches the SEQ slot | §8.2 had no message that hands a pattern down; §7.2's `seq.assets` needs a whole patch | **`load_engine` takes `assets` (and `dir`)**, resolved like a patch's: present replaces the slot's whole map, `{}` clears it, absent leaves it alone. The id is unchanged, so the slot's parameters survive. N1's `_replay_pattern` bridge is **deleted** — a pattern now reaches `arp` and `euclid`, which declare no soft-keys and could never be replayed into | `host/audio_host.cpp` `load_engine`, `host/PROTOCOL.md` § Assets |
| H2: a pattern that was only entered | not specified | written to `$TMPDIR/arbutus-pattern-<inst>-<pid>.json` and handed down as that path, the way `load_kit` hands down a resolved kit. It becomes a library item only when a patch is promoted, which writes `seq/<box>/<slug>.seq` and points `seq.assets.pattern` at it. **Removed at exit** since #105 — not when the `NativeSequencer` goes, because the host re-reads that path on every rebuild of the box | `emulator/app/native_sequencer.py`, `audio/library/README.md` § Patterns |
| SOURCE mic / line / usb | §15.2 names four | the one input stream this process opened; which jack feeds it is the codec's on the device, so the three read the same buffer. `input_source {source}` and the box's SOURCE knob are one value in both directions, and a patch carrying SOURCE selects it as it loads | `host/audio_host.cpp` `set_input_source`, `adopt_input_source` |
| SOURCE resample | §15.2 "the master bus minus the armed track" | **deviation**: also minus the instrument bus (otherwise `live-input` hears its own output and it is a unity-gain loop, not a bounce), the click and the monitor (a bounce carries neither). One block late, like `master`. The subtraction is exact through the mixer's gains, COMP, LEVEL, BAL and the limiter; it does not un-mix DRIVE, the master rack or the FILTER hold | `host/audio_host.cpp` `render_block`, `kBufResampleCut` |
| the live input into an engine | §15.2 "`create()` opens the host's input stream" | **deviation**: a box cannot open the device — the host owns it. The ENGINE slot's `side[]` carries the selected source to a box that declares `ARB_CAP_SIDECHAIN`, which is the port an FX tab's sidechain already uses. `arbutus/live-input` is the only box that asks | `host/rack.cpp` `render_chain`, `ChainInput::engine_side` |
| MOD `input` source under SOURCE | modulation-spec §4 "whatever INPUT is set to" | the selected source, `resample` included — one selector, one answer | `host/audio_host.cpp` `render_instrument` |
| MONITOR under SOURCE | not specified | the **jack**, never `input_source`: monitoring `resample` is the speakers playing themselves | `host/audio_host.cpp` `render_block` |
| live-input GAIN | screen-sketches gives `SOURCE · GAIN · — · —`, no range | −24…+24 dB (not a 0..8 multiplier): a trim on a live mic is aimed in dB. INPUT's own GAIN is the capture path's and is a separate knob | `engines/arbutus/live-input/src/engine.cpp` |
| I1: a **fileless kit pad** | §15.3 gives the pad shape `{file, params}` and does not say a kit must be samples | a pad that is `{"params": {…}}` with **no `file`** loads: `arbutus/dbox` is a synthesised kit, so there is nothing to open. `set_note_param`, `copy_pad`, `normalise_kit` and the document-is-the-save rule are all unchanged; a pad that is neither a file nor an object is still `ENGINE BROKEN`. `kit_pad_file` returns "" and `waveform` on such a pad is `not_found`, which the UI already handles (it draws `NO SAMPLE`) | `host/audio_host.cpp` `load_kit` |
| I1: a patch's carried assets | §7.2 silent; `load_engine` clears params and assets when the id changes | `load_patch` carries the engine's assets over (so a patch onto DRUM keeps the kit) **only when the engine id is unchanged**. Bug found by the first stage-I1 patch: a box that takes no assets, loaded after a sampler patch, was refused `UNKNOWN ASSET sample` | `host/audio_host.cpp` `load_patch` |
| va ranges | DDR-044 "the author bounds them" | CUTOFF 80 Hz–12 kHz log (below 80 a four-pole ladder on a saw is silence); RES 0–1 mapped to ladder k 0–**3.6** of a possible 4.0, so the top detent is not a sine oscillator; ENV-F bipolar ±5 octaves off the ENV page's own ADSR; SUB and NOISE are a **balance** (÷ `1 + 0.7·(SUB+NOISE)`); DRIVE crossfaded, 0 bit-exact; trim 0.4 | `engines/arbutus/va/src/engine.cpp` |
| va oscillators / filter | not specified | polyBLEP saw and pulse, TRI = the integrated square (starts at −1, leak 0.9995); every wave DC-removed **and** rescaled to ±1, because a resonant filter fed an off-centre pulse puts DC in the output; the ladder is four zero-delay TPT one-poles with the feedback solved, not delayed; the filter envelope is read on `arb::kCtrlStep`, a **32-frame grid of the voice's own frames since note-on**, not once per `process()` call (#56) — once per call made the box sound different at every block size, 16 dBFS between 512 and 1 | same, `sdk/poly.hpp` `ctrl_run` |
| string ranges / model | not specified | two waveguide lines per voice; DECAY 0.05–8 s log is **seconds to −60 dB of the fundamental per note** (worked out from that note's period and DAMP's loss at it; holds to C7 at DAMP 0 and to C5 at DAMP 0.35 for 2 s; above, the 0.9999 loop-gain ceiling, which is also the loop's DC gain, lets DAMP shorten the top: README); the fraction of a sample is a **first-order allpass**, not a linear read (#647: the read's loss made the top two octaves ring 0.4–1.2 s for 2 s); DAMP 0–1 → one-pole 1…0.15 with its phase delay **at the note's f0** taken out of the line, so DAMP does not detune (within 2 ct to C7 up to DAMP 0.75; the DC figure had C7 +15 ct); POS 2–50 % (past 50 mirrors); the excitation is written *behind* the write pointer over exactly the line's samples, with its DC removed; BOW saturates the loop (`tanh`) and is fed a first difference of noise, one state per string (#647). *Run's call, 2026-10-13* (#647): fixing rather than narrowing the claims, so DAMP's loss is now counted and the default notes ring longer (golden refrozen). Proof: `ctest -R string_tuning`; trim 0.7 | `engines/arbutus/string/src/engine.cpp` |
| wavetable asset + aliasing | §3.2 names assets, nothing names a table format | asset key `wavetable` = a 16-bit mono WAV of `frames × 2048` samples, one cycle per frame, ≤ 64 frames, written by `tools/mkwavetable.py`; `load_asset` builds a **mip chain** (level k = the frame at `2048>>k`, 15-tap half-band FIR applied circularly before each decimation) and the oscillator takes the longest level whose top harmonic fits under the sample rate, with `1 + 2·|WARP| + 2·FOLD` of headroom. Energy below the fundamental at C7 on a 64-harmonic frame: −52.6 dB | `engines/arbutus/wavetable/src/engine.cpp` |
| wavetable knobs | not specified | WARP is Casio phase distortion (pivot 5–95 %, period untouched) and is **not mean-preserving**, so the oscillator sum runs through a ~15 Hz DC blocker (−3 dB at 15.3 Hz, `0.998`; written "~32 Hz" until #647), always on; FOLD is a sine folder crossfaded from bit-exact bypass; DRIFT is a **per-voice** slow wander of POS at a rate derived from the note (the MOD rack is global except `keys`, so nothing else can do it); slot 7 is deliberately empty; POS has `format_param` (`6.7/15`, the frame) | same |
| dbox | v1-spec-package lists it as a drum box; nothing else specifies it | 24 synthesised pads, all eight params `per_note`: MODE (KICK SNARE TOM HAT CLAP RIM) · TUNE ±24 st · DECAY 0.02–2.5 s log **× a per-mode scale** · SNAP / TONE · DRIVE · LEVEL · PAN. `honours_env` is **0** — DECAY is per pad, which is what DDR-053 wanted and §15.11 could not give, so DRUM › ENV is inert on this box. A `HAT` pad chokes any other sounding `HAT` (no ninth slot for CHOKE); note-off does nothing; trim 0.55 | `engines/arbutus/dbox/src/engine.cpp` |
| SYNTH / DRUM default box | not a spec question | `pages/synth.py` `DEFAULT_BOX = "fm"`, `pages/drum.py` `= "drum"`. `arbutus/dbox` sorts before `arbutus/drum` and folder 0 was the default, so DRUM would have changed engine because a box was added — the same trap H1 hit on SEQ | `emulator/pages/synth.py`, `drum.py` |
| I2: `delay` TIME | not specified; §15.5 gives an effect the host block, which carries `tempo_bpm` | one **two-sided knob**, `arbutus/lfo`'s shape in a second place: centre is the shortest delay of each kind, right of it thirteen tempo divisions (`1/32` … `1 BAR`, straight, triplet **and dotted**), left of it free time 20 ms … 2 s log. `format_param` prints which. The line is sized at `create()` for **4 s**, so a synced `1 BAR` clamps below 60 BPM rather than allocating on a tempo change | `engines/arbutus/delay/src/engine.cpp` |
| I2: `delay` loop | not specified | TONE (one knob, a lowpass below centre and a highpass above, both bypassed at 0.5) and DRIVE (`tanh`, crossfaded from bit-exact) are **inside** the feedback path, so repeats darken and dirty while the first one is still the sound you played; DUCK is on the output and reads the **dry** input; TIME glides with an 80 ms one-pole so a knob turn is a tape sweep, not a click; PING crossfades the feedback matrix `[[1−p, p],[p, 1−p]]`, whose largest gain is 1 at every `p` | same |
| I2: `chorus` voice spacing | not specified | voices are **0.37 of an LFO cycle apart, not evenly spaced**: evenly spaced taps are a set SPREAD can rotate onto itself, and two voices at 180° (or three at 120°) gave the two channels the identical tap set — measured L/R correlation **+1.000** at exactly the widest setting. The wet sum is `1/√VOICES`, not `1/VOICES`, because decorrelated taps add as powers | `engines/arbutus/chorus/src/engine.cpp` |
| I2: `crusher` BITS / clock | not specified | BITS is **2 … 16** continuous — 1 bit is a mid-tread mute for anything under half scale (DDR-044) and 16 is the library's own depth, not a transparency claim. DRIVE's pre-gain is undone exactly on the way out, so it moves the signal against the grid and the clipper and is **not a level control**. One hold clock for both channels; JITTER moves the hold *period* (±60 %) and its xorshift32 advances **once per hold**, seeded from `rng_seed`, which is what keeps it ragged-stable | `engines/arbutus/crusher/src/engine.cpp` |
| I2: `crusher` slots 6-7 | DDR-044 allows ≤8 | **six parameters**, two cells deliberately empty — `arbutus/osc`'s argument (§14), and `arbutus/wavetable`'s | same |
| I2: `drive` oversampling | §12 "let latency happen" | the shaper runs at **2×** behind two **31-tap** half-band FIRs, so `latency_frames` is **15** and the box delays its own dry path by the same 15 before the DRIVE crossfade and before MIX — a box that mixes a delayed wet with an undelayed dry comb-filters itself. Measured: worst in-band fold **−21.3 dB** against **−14.3 dB** with no oversampling (7 kHz, HARD, DRIVE 1). 15 taps was tried first and dropped 20 kHz by 3.8 dB | `engines/arbutus/drive/src/engine.cpp` |
| I2: `drive` BIAS / SAG | not specified | BIAS is an offset in the **shaper's** units (0 … 0.8), not the signal's: multiplied by DRIVE it pins the shaper and the top of the knob is a mute (measured before it was fixed). SAG is an **output** dip with a 150 ms release, not a reduction of drive — pulling the drive down changes nothing audible once the shaper is saturated. A 12 Hz DC blocker sits on the shaped path only, so MIX 0 and DRIVE 0 stay bit-exact | same |
| I2: `eq3` vs the track EQ | DDR-062 gives the mixer's per-track EQ; nothing names a rack EQ | a **different** box: ±18 dB, **all three corner frequencies movable**, makeup on slot 7, and **no HP/LP** (the track EQ has them and `arbutus/biquad` is one). A band at exactly 0 dB is **skipped**, not run as a unity biquad, so flat is bit-exact and a re-opened band starts from silence; coefficients are recomputed once per block, where the trig is safe | `engines/arbutus/eq3/src/engine.cpp` |
| I2: an FX tab's readout | §15.1 `format_param` | **bug fixed in the UI**: the host has always put `display` on an FX tab (`SlotState::to_json`), but `pages/rack.py` built its cells without it, so `arbutus/delay`'s TIME drew `0.33` where the box says `1/8`. The ENGINE page and a MOD tab already passed it | `emulator/pages/rack.py` `_engine_slots` |
| J1: BOUNCE's MASTER FX | screen-sketches § FILE gives the switch; §8.2 has no message for it | `clip_to_asset {track:"all", master_fx:true}`. A mixdown leaves COMP and the master rack out (F1); this puts the rack back in. The rack is **rebuilt, not borrowed** — `voice_[kMasterBus].cur` is the audio thread's and rendering it from the control thread is the race the command queue exists to prevent — from the same §6.7 document and the same macro positions, and run in `max_block` chunks. No events, not playing, no sidechains: a bounce has no clock and no live buses, so `side_ptrs` leaves those NULL and a box that asked simply sees no `side[]` | `host/audio_host.cpp` `master_fx_offline` |
| J1: bounce latency | §12 "the host reports latency and compensates nothing" | it still compensates nothing **live**; offline it must, or a rack with `arbutus/drive` in it prints the whole song 15 frames late. The window is rendered `latency` frames long and the first `latency` frames of the rack's output are dropped. `latency` is the **sum** over unbypassed tabs, which is exact because tabs only run in series (engine-spec § 6.5; parallel groups were cut 2026-10-02; review R20, from t273's review) | `host/audio_host.cpp` `master_fx_latency` |
| J2: dot-prefixed folders | §3.9 "`<root>/<author>/<name>/` is the one tree"; nothing says what to skip | the box scan **does not descend into a folder whose name starts with `.`**. `audio/library/` is a `--library` root and holds `.originals/` (the unconverted masters a sample was made from) and, since DDR-055, `.trash/` — where a browser LIFT that was never dropped puts an item. Indexing either makes the soft delete not a delete. `rescan` is the message a file op sends afterwards, and `host_files.py` is its first coverage | `hostlib/library.cpp` `Library::rescan` |
| P6: IMU data-ready | § 6.5 "`INT1` … read with `gpiod` edge events" | **`STATUS_REG` polling** at the loop's 5 ms cadence; `INT1_CTRL` is configured either way. See § The IMU above for why, and what it costs (wake-ups, nothing else) | `host/imu_lsm6dso.cpp` `Lsm6dso::ready` |
| P6: IMU retry | § 6.5 "reported once, retried every 5 s" | the report is a `static bool` in the loop, so a bus that is dead for an hour is one journal line and not 720,000 | `host/main.cpp` |
| `meters` values | §8.2 "block peaks" | peaks **since the last push** (deviation): the take's envelope on TRACK is drawn from them and a block peak sampled at 30 Hz misses most of the take | `host/audio_host.cpp` `poll` |
| D2: where the TCP rules live | device-os-spec § 9.4 states them, not where | **the transport, not `AudioHost`** (`tcp_gate` in `host/main.cpp`): `handle()` has no connection context and must not grow one, because the message set is identical on both sockets and *who may send what* is a property of the wire. Consequence: the 10 Hz `meters` cap is applied by rewriting `hz` before the host sees it | `host/main.cpp` `tcp_gate` |
| D2: the `meters` reply | §8.2 gives no reply fields | carries the accepted `on` and `hz` back, the way `eq` hands back its clamped block. Added because the TCP cap is otherwise invisible to the app — it asks 30 and gets 10 with nothing to read | `host/audio_host.cpp` |
| D2: when the token is read | § 9.4 "bound only when `token` exists" | **once, at start**. `FORGET PHONES` regenerating it restarts `arbutus-audio`, which D4 has to do anyway to drop the connections the old token opened; re-reading per connection would be a stat per accept for nothing | `host/main.cpp` |
| D2: what `library {op}` paths are relative to | #62 says "library-relative", with several `--library` roots possible | the **last** root (`write_root()`), the writable library, the same choice `clip_to_asset` makes when it invents a path (corrected 2026-10-13 by review R35, from the whole-system review's B5: this row said "first" until then, the bug #62 item 3 fixed; see the row "which `--library` root is written into") | `host/audio_host.cpp` `library_op` |
| D2: dot components in a `library {op}` path | #62 silent; device-os-spec § 9.3 refuses them **at the HTTP edge** | **allowed here**: `.trash/` is a real folder and putting an item back means naming it, which is what the UI's undo group replays. `..` and absolute paths are still `BAD PATH`. The refusal stays at the HTTP server's own boundary, where `/library/…` is a public namespace | same |
| D2: `delete` on stock | #62 refuses stock for `move`/`rename` only | **refused for all four ops**. A shipped patch that can be deleted but not renamed would be the stranger rule, and `stock.json` is a claim about what the image ships, not about which gestures are allowed | same |
| renaming the open project (#821) | #297 refuses every op on the tape's folder, "the one place it is refused"; D99 has the phone rename the open project | **`rename` of that folder itself is done**, not refused: `TapeIo::rename_dir` drains, renames the folder no-replace under `mu_` after `wait_io`, refuses `COMPACTING` like `set_dir`, and re-points `dir_` (the open handles follow a rename, so nothing reopens); refused `RECORDING` during a take and on a `FakeStore`. Pushed as `tape_dir_renamed {from, to}`, which the UI follows. A folder above it, `move`, `trash`, `delete` and its files stay refused. *Run's call, 2026-10-13*: a folder rename and not `tape_dir {move}`'s file-by-file move, because the phone names the project and the project is the folder | `host/tape_io.cpp` `rename_dir`, `host/audio_host.cpp` `library_op` |
| D2: a `trash` name collision | #62 gives `EXISTS` as a refusal | `trash` **renames** (`name-2.patch`), alone among the four: two lifts of one name both have to survive, which is `free_path` in `emulator/app/library_ops.py`. `rename` and `move` still refuse, because there the caller named the destination | same |
| D2: `bake {trim}` with `frames` | not specified | **refused**. `frames` names an absolute end and `trim` chooses both ends; one would have to quietly win, and a bounce that silently ignored an argument is worse than one that will not run | `host/audio_host.cpp` `bake` |
| D2: the bounce tail | companion-app §4 "`latency_frames` + a fixed 2 s" | read literally: the file is the clip span **plus** that tail, and `lat` more frames are rendered on top so the latency compensation has material to drop off the front (`clip_to_asset`'s arithmetic). **Only with `master_fx`** — with the rack off there is nothing ringing to keep | same |
| D17: a departed UI's map (#412) | companion-app § 10.1: no arrows on a screen that is not there | the connection that published the map is remembered **only if it said `hello`**, and its close resets `page` to `null`, empties `elements` and pushes that. A one-shot publisher (`mock --ui`, the app's mock tests, which never say `hello`) owns nothing, so its map stands; that is what keeps the developer path and `LearnMockTests` working. *Run's call, 2026-10-04*. Also: `actions {last}` is clamped as a double before the cast (1e300 was undefined behaviour), and the 30 s window is `--feed-window-ms` for tests | `host/main.cpp` `ui_feed_gone` |
| the live stream's shape (#853) | the ticket gives `live {on}`, merge-patch pushes, "at most ~20 a second", `frame {rev}` | **one coalescing check every 50 ms** while anyone subscribes (`Live::kPeriod`), nothing built with nobody subscribed; the document is `AudioHost::live_doc()` (host) with the UI's `ui_situation` half merged over it (RFC 7396 both ways, `merge_diff` in `main.cpp`); **no nulls** (a null in `ui_situation` is refused), **rounded** (playhead 0.1 s, levels 0.01) so a still instrument pushes nothing; **tracks keyed `"1"`..`"8"`**, not an array, so one clip is a one-track patch; bar/beat in **4/4** at the current tempo (v1 has no metre); `frame` at most every **200 ms**, numbered by the **host** (two publishers both start at 1); `live {watchers}` to every client so the UI hashes frames only while watched; the D17 `action`/`elements` pushes go to `live` subscribers too. "The four slots" read as each instrument's ENGINE and SEQ (the app's StateMirror), "in front" as the active instrument. *Run's calls, 2026-10-13* | `host/main.cpp` `Live`, `live_*`; `host/audio_host.cpp` `live_doc` |
| a one-shot publisher against a UI session (#853) | #412 gives a UI session's map to that session | a `ui_situation` from a connection that never said `hello` (the mock's stand-in screen, `--ui`) **does not replace** a half a UI session owns: the session republishes only on a change, so the stand-in landing between the UI's publish and the next patch erased the UI's octave (seen 1 in 3 in `arbutus_os` `test_ui_feed`). *Run's call, 2026-10-13* | `host/main.cpp` `live_handle` |
| a wrong-typed field, a line with no end (#596) | PROTOCOL.md: one reply per message; nothing on input size | a `json::exception` from **anywhere** in dispatch (`tcp_gate`, the UI feed, `handle`) is `bad_request` `BAD MESSAGE` with `for`/`req`, caught once in `main.cpp` rather than type-checking each field, so the readers that already fall back to a default keep doing so (`tempo {bpm:"fast"}` is still `ok`). Input without a newline is capped at **8 MiB** (`kMaxPending`'s number, run's call) and **64 KiB** for a TCP peer before the token, then `MESSAGE TOO LONG` and a close; a closing connection's further input is not read as messages. *Run's call, 2026-10-06*. `tests/host_badinput.py`, seen red on the old host (abort, and two connections never closed) | `host/main.cpp` `kMaxLine` |
| P9: a client that stops reading | §8.2 silent; PROTOCOL.md says pushes go to every client | accepted sockets are **non-blocking** (they were blocking by omission — `flush()` already handled `EAGAIN` and `poll` already asked for `POLLOUT`), so a full socket costs its own client and nobody else; a client with **more than 8 MiB** waiting is closed with one stderr line. Found on the CM4 when a sleeping bench client froze the UI's `note` for 8.7 s; `tests/host_slowclient.py` pins both halves | `host/main.cpp` `flush`, `kMaxPending` |
| `arbutus-ctl`'s waits on a line in pieces (#732) | PROTOCOL.md silent; the client's own contract | **a deadline, not a socket timeout**: `Conn.recv(deadline)` works out the time left before every read and raises `socket.timeout` past it, a partial line kept in `_buf`. `wait_push`, `drain` **and `request`** pass their own deadline; the socket's timeout bounds one read, so a push in pieces closer than it held each past its stated time (~450 ms for a 0.1 s wait). `request` was not in the ticket and had the same fault: *run's call, 2026-10-13*. `recv()` with no deadline is unchanged for its other callers. `tests/ctl_push_wait.py`, each call site seen red | `audio/tools/arbutus-ctl/arbutus_ctl.py` `recv` |
| the state push's timing (#74) | PROTOCOL.md "pushed to every client after any edit" | **once a burst settles**: the push goes when no request has come for **8 ms**, or **50 ms** after the document first went dirty while requests keep coming, and replies no longer wait behind it. It was built after every editing request, before the reply left — 2–3 ms on the CM4, plus ~1.75 ms for the UI to parse 14 KB — so each message of a STOP or a SAVE cost ~6 ms and STOP's frame ran 60–95 ms (C1 step 3b's measurement, `arbutus_os/bench/c1-3b-pi.sh`). 1 ms of settle was measured too short: the UI's own gaps inside one gesture (its take adoption polls every 5 ms) let the document be built mid-gesture. **Offline a `render` lets a pending push go** (it is the clock, sent once a block back to back, and the UI follows another client's transport from the push, #99; `test_a_second_client_rolls_the_tape_and_the_ui_follows` went red without it), so offline is one push a rendered turn as before and only a live burst coalesces. `tests/host_statepush.py`, seen red on the old loop (40 pushes for 40 edits). *Run's call, 2026-10-05* | `host/main.cpp` |

## Temp files are RAM on the device (#105, 2026-09-22)

`/tmp` on the CM4 is a **tmpfs**, so anything left there is memory that never comes
back until a reboot, and it counts against the headroom C2 (#75) measured. A gate run
had left 733 MB.

- **`load_kit`'s resolved kit** (`host/audio_host.cpp`) is still
  `$TMPDIR/arbutus-kit-<inst>-<pid>.json`, one file per instrument per process, written
  fresh on every kit load. It cannot go when the next kit arrives — the path *is* the
  `"kit"` asset and the host re-reads it whenever that box is rebuilt — so `AudioHost`
  keeps the list it wrote (`temp_kits_`) and **the destructor removes them**. `main.cpp`
  holds the host by value and SIGTERM sets the stop flag rather than `_exit`, so a
  `systemctl stop` runs it. A SIGKILL does not, which is what `arbutus_os/tests/support.py`
  does on purpose; that file sweeps the killed pid's kits itself.
- **The test scaffolding** left 176 `arbutus-audio-test-*` directories a gate run.
  `tests/hostcase.py` ties its directory to the **`Host` object**, through a
  `weakref.finalize`, rather than to `close()`: `host_clips.py` closes one host and loads
  a WAV it wrote into the next, so removing at shutdown would delete a file still in use.
  `host_rack.py` and `host_tcp.py` had the same leak in plainer form and now use
  `try`/`finally`, like `host_files.py` always did.
- `ARBUTUS_KEEP_TMP=1` keeps all of it for a post-mortem.

`host_drum.py` ends with the two assertions that pin this: the resolved kit is gone once
the host exits, and the test host's directory is gone once the object does.

**And "once the object does" means reference counting, not a collection (#179,
2026-09-25).** `weakref.finalize` runs when the object is collected *or* at interpreter
exit, so a `Host` caught in a reference cycle still loses its directory eventually and
leaves nothing behind — but the assertion above is deliberately stronger than
"eventually", and a cycle turns it into a coin toss. It was red on the CM4 5 runs of 5
and green in WSL 5 of 5 for as long as nobody ran `tools/gate.sh --pi`, with nothing
between the two machines but how many objects each run had allocated before that line.

The cycle was not in `Host`. It was in `arbutus-ctl`: both of `connect()`'s retry loops
kept the last failure as an **exception object**, and an exception's `__traceback__`
refers to the frame that caught it — so the name that outlives the `except` block is a
cycle round `connect()`'s own frame, and `f_back` keeps every frame below it, including
`Host.__init__`'s, whose local `self` is the host. They keep `str(e)` now. The rule for
anything new in `audio/tools/` or `tests/`: **an `except ... as e` value that outlives
its block is a cycle** — Python clears that name at the end of the block for exactly
this reason — so store the message, or clear `__traceback__` if the object is wanted.

Two tests hold it, and both have been seen to fail:

- `host_protocol.py` has the machine-independent one — a canary object in the frame that
  calls `connect()`, with the cycle collector **off**, must die with the call. Mutating
  `str(e)` back to `e` turns it red in WSL, where the directory assertion stayed green.
- `host_drum.py`'s directory assertion now runs with the collector off too, and has a
  negative control beside it (#56): a host held in a cycle on purpose must survive the
  very same `del`, and go on the collection.

## Adding an engine, in five lines

```bash
cp -r sdk/template engines/arbutus/<name>        # 1. copy the worked example
$EDITOR engines/arbutus/<name>/src/engine.cpp    # 2. rename the id, write Voice
$EDITOR engines/arbutus/<name>/CMakeLists.txt    # 3. add_arbutus_engine(<name> ...) + tests
echo 'add_subdirectory(engines/arbutus/<name>)' >> CMakeLists.txt   # 4. (in the right place)
cmake --build build && ctest --test-dir build -R <name>             # 5.
```

`engine.json` in the box folder holds only the authored fields (`author`,
`description`); `add_arbutus_engine()` generates the rest from `arb_describe()`
at build time, so the §3.8 snapshot cannot drift from the binary.

**A box under a new author folder** (`engines/<author>/`, not `arbutus/`) also needs its path
in `tools/review/review.py`'s `MODULES`, or the ops suite's orphan check goes red in the land
gate (#1230's run paid a second ~9 min gate; retro notes, review R44). Run
`python3 tools/review/review.py coverage --orphans` before landing.

A box is done when it has: a golden (`--golden`, tolerance −100 dBFS), a ragged
test (`tests/ragged.sh` — same samples at 512, 128 and 97), a `--rt-guard`
test, and a README of one screen naming every parameter's range and *why* that
range. If the golden is frozen from the C++ rather than replayed from the
Python, run `tests/tools/npystat.py` over it first and say in the commit what
you checked.

**A range without a measurement is an opinion** (stages I1 and I2). A box with
no Python original has nothing to conform to, so its README names the number
that settles each bound — the repeats to −60 dB, the alias floor, the L/R
correlation, the peak error against the dry input. Half of I2's ranges moved
*because* the measurement contradicted the first guess.

## Assets: content is not a parameter

A sample or a kit does not cross the ABI as a float 0..1. `arb_vtable` has a
seventh entry (appended inside ABI 1's reserved bytes; a named field since ABI 2):

```c
int (*load_asset)(void* h, const char* key, const char* path);
```

**Control thread only.** The host calls it after `create()` and before the
engine is in the graph. It may allocate and it may read files; `process()` still
may not. NULL means "this engine takes no assets".

An engine declares its keys in `arb_describe()` (`arb::set_assets(d, {"sample"})`),
they land in `engine.json` as `"assets": [...]`, and the host refuses a document
naming any other key with `UNKNOWN ASSET <key>` before opening a file (§7.4).
`ARB_MAX_ASSETS` is 3, which is why a **kit is one asset** — the resolved
`kit.json` — and not one asset per pad.

Offline: `arbutus-run <box> --asset sample=/path/to.wav`.

## Rule 7: smoothing, and where it is not yet

Realtime rule 7 (§15.7): parameter changes glide over at least one block, with
`arb::Smoothed<float>` (`sdk/params.hpp`); `sdk/template` shows both a per-frame
gain and block-rate voice parameters. **The stock boxes do not smooth
yet** (`arbutus/transient`, added in stage D, and `arbutus/live-input`, added in
stage G, do: their gain terms are per frame) — deferred from ABI 2 stage A, not
forgotten. None of their goldens would
move (goldens hold parameters still, and `Smoothed` at rest is bit-exact).
Since #630 a box's own test can move a parameter mid-render (`arbutus-run --at
F:id=value`; `tests/fx_moves.py` does it for reverb SIZE), and most of the work is a sound choice
rather than a mechanical edit: biquad and noise filter coefficients,
comb/satcomb TUNE (a delay glide is a pitch sweep), reverb SIZE (since #630 it
moves the read distance and keeps the tail; it used to clear the network, so a
glide is now possible but not built), fm INDEX/sampler/drum values read
at note-on, and LEVEL on the PolyHost synths (moves after the mix). The stage-I1
boxes are the same: `va`'s CUTOFF and RES are per block already (one `tan()` a
block, which is the ramp's granularity anyway), `string`'s DECAY and DAMP are
read per block, `wavetable`'s POS crossfades between frames continuously (its SWEEP and DRIFT on the same `kCtrlStep` grid since #56), and
`dbox` latches everything at the hit like `drum`. The stage-I2 effects are
mostly the same: their gains and coefficients are per block, and the one that
would click — `arbutus/delay`'s TIME — carries **its own** 80 ms one-pole glide
rather than `Smoothed`, because a delay time has to sweep like tape and one
block would be a step. When a box gains real smoothing, add a host test that
turns a knob mid-note, like `host_abi2.py`'s.

## The host must not colour the sound

`ctest -R host_pipeline` runs the template synth into the biquad through the
rack and compares it to the same two boxes back to back in `arbutus-run`, within
−100 dBFS. Any gain, pan law, limiter or dither that creeps into the default
path breaks it, and that is the point: the default mixer is unity and the
limiter is off.

