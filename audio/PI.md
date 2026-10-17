# Stage 4b — `arbutus-audio` on the CM4

The brief for the Pi half of stage 4. **Run 2026-09-18 (#13)** — the numbers
are in `audio/README.md`, what differed from this brief is § 6. Stage 4a
built the emulator shim on the Mac (`emulator/notes/audio-host.md` § Native audio) and
stopped there on purpose: one question per build.

The question 4b answers is **"does it fit on the board?"** — not "does it work",
which the Mac already answered. Everything here is measurement and
configuration.

---

## 0. Before you start

- `docs/design/engine-spec.md` §9.5: **native builds on each target** for v1.
  Nothing is cross-compiled; the CM4 compiles its own boxes.
- `arbutus_os/CLAUDE.md` is the authority on reaching the board. The short
  version: USB ECM, static IP, `ssh elvindevelops@10.0.0.1` (mDNS is
  unreliable). **Never `pkill`** — it drops the ECM link (dwc2 bug); use
  `systemctl stop`.
- `sudo systemctl stop arbutus` first. The old Python ArbutusOS owns the audio
  device and the two will fight over it.
- The CM4 is a 4-core Cortex-A72 at 1.5 GHz. A "% of a core" number from the
  Mac table in `README.md` is worth roughly nothing here; that is the point of
  measuring.

---

## 1. Build

```bash
ssh elvindevelops@10.0.0.1
sudo apt update
sudo apt install -y cmake ninja-build g++            # g++ 12+ for C++20
g++ --version                                        # record it in the report

cd <your clone> && git pull                          # or rsync from the Mac
cmake -S audio -B audio/build -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build audio/build
ctest --test-dir audio/build --output-on-failure
```

Expect **51 tests, all offline** — they need no audio device, which is why this
step can run before anything is configured. A failure here is a real
portability bug (alignment, `char` signedness, `-ffast-math` differences) and is
worth more than any bench number: write it down.

`add_arbutus_engine()` emits `audio/build/boxes/<author>/<name>/linux-aarch64/`,
so the box folders now carry a second arch key. A box built only on the Mac is
refused on the Pi with `NO BUILD FOR THIS DEVICE` (§7.4) — that refusal is
worth provoking once, deliberately, to confirm it says the right thing.

---

## 2. Bench every box

```bash
RUN=audio/build/tools/arbutus-run/arbutus-run
for box in audio/build/boxes/arbutus/*/; do
    echo "== $box"
    $RUN "$box" --bench
done
```

`--bench` is best-of-five over 512 frames at 48 kHz with the box's own test
material (§9.3). Run it with the system otherwise idle and with the CPU
governor at `performance`:

```bash
sudo cpufreq-set -g performance     # or: echo performance | sudo tee \
  /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
vcgencmd measure_temp               # and again after the run: throttling lies
```

**Write the numbers into `audio/README.md`'s box table**, as two more columns
beside the Mac ones — `µs/block (CM4)` and `% core (CM4)`. Same table, one row
per box, so the two machines are read side by side. Note the compiler version,
the governor and the temperature under the table.

Then the host's own offline render, which is the number that actually matters
because it includes the pipeline, the rack and the mixer:

```bash
audio/build/host/arbutus-audio --offline --quiet --socket /tmp/a.sock \
  --library audio/build/boxes --library audio/library &
CTL=audio/tools/arbutus-ctl/arbutus-ctl.py
python3 $CTL --socket /tmp/a.sock load_patch \
  patch="$(cat audio/library/synth/osc/cold-glass.patch)"
python3 $CTL --socket /tmp/a.sock note on=true note=60 vel=0.9
time python3 $CTL --socket /tmp/a.sock render frames=480000   # 10 s of audio
python3 $CTL --socket /tmp/a.sock shutdown
```

Ten seconds of audio in under ten seconds of wall clock is the floor; the ratio
is the headroom. Repeat with `colourbass.rack` loaded (three engines in
series) — that is the heaviest thing in the library and the one most
likely to raise the flag.

---

## 3. Realtime configuration (`software-stack-v1.md` §7.3–7.5)

§7.4 was written when the audio thread lived *inside* the Python process.
`engine-spec.md` §8.1 replaced that with a separate process, so the same
settings now apply to `arbutus-audio` itself rather than to a thread:

```
# /boot/firmware/cmdline.txt — core 3 out of the Linux scheduler's reach
isolcpus=nohz,domain,managed_irq,3
irqaffinity=0-2
nohz_full=3
rcu_nocbs=3
```

```ini
# /etc/systemd/system/arbutus-audio.service
[Service]
ExecStart=/opt/arbutus/audio/arbutus-audio --library /opt/arbutus/boxes \
          --library /opt/arbutus/library
CPUAffinity=3
LimitRTPRIO=95
LimitMEMLOCK=infinity
```

The process must then set `SCHED_FIFO` (priority 80) and `mlockall(MCL_CURRENT
| MCL_FUTURE)` on its own audio thread. **That code does not exist yet** — the
Mac build never needed it, and adding it is stage 4b work, not a config change:
one `pthread_setschedparam` and one `mlockall` at device start in
`host/device.cpp`, both no-ops on Darwin, both failing loudly rather than
silently on Linux. `LimitMEMLOCK=infinity` without the `mlockall` call buys
nothing.

Measure before and after. The honest measurement is the host's own xrun
counter, which is already there and already pushed (§6.8):

```bash
# --follow prints everything the host pushes; `xrun` is one of them (§6.8)
python3 $CTL --socket $TMPDIR/arbutus-audio.sock --follow meters on=true hz=2
```

Run the heaviest rack live for five minutes and count. Zero xruns with the UI
running and the screen redrawing is "it fits". Anything else is a number to put
in the log, not a reason to tune blindly.

---

## 4. The emulator against it

ArbutusOS on the Pi is not the emulator, so `--audio=native` is not the target
here — but running the emulator on the Mac against an `arbutus-audio` on the
**Pi** would be a genuinely useful test of the protocol over TCP, and §8.2
already says the same message set is served over the LAN. It is **not in
scope**: the socket is a Unix socket today and adding a TCP listener is a
transport change with an auth question attached (§13.2).

---

## 5. Done means

1. `ctest --test-dir audio/build` — 51/51 on the CM4, and any failure
   understood rather than skipped.
2. Every box benched, the numbers in `audio/README.md`'s table beside the Mac
   ones, with compiler / governor / temperature recorded.
3. The host's offline render timed for a one-engine patch and for
   `colourbass.rack`, both written into the same place.
4. `SCHED_FIFO` + `mlockall` implemented and the core isolated, with an xrun
   count before and after over a five-minute live run.
5. A session log in the private log folder saying which of those numbers surprised you,
   and `arbutus_os/` given its staleness banner — §11 says that happens when
   `arbutus-audio` first runs on the Pi, and this is that moment.

---

## 6. As run (2026-09-18, #13, bench routine)

**Where.** CM4 on the official IO board, not the V0.1 board: `ssh arbutus-pi`
(`arbutus_os/CLAUDE.md` § Connect), not `10.0.0.1`. Debian 13 trixie, kernel
6.18 `-rpi-v8`, **GCC 14.2**, CMake 3.31. There was no old `arbutus` service to
stop — the eMMC was reflashed (#61).

**What differed from the brief, and why:**

- **164 tests, not 51** — the brief predates stages A–J2. All pass on the CM4.
  The one portability finding: GCC 14 reported three `-Wmismatched-new-delete`
  in `tests/test_events.cpp` (the replaced `operator delete` inlined into
  `std::allocator` and paired with an opaque `operator new`) — a false positive,
  fixed with `[[gnu::noinline]]`, so the zero-warning rule holds on GCC 14.
- **The refusal** was provoked by renaming a box's `linux-aarch64/` to
  `darwin-arm64/`: `NO BUILD FOR THIS DEVICE … missing …/linux-aarch64/osc.so`.
- **Bench and render are scripts now**, so P4 (#20) and the next board rerun
  them rather than retyping § 2: `audio/tools/bench-all.py` (every box, its own
  golden test's material, `--markdown` prints the table) and
  `audio/tools/bench-host.py` (four scenarios, `rt_allocs` per render, a peak
  per scenario so a silent render cannot pass as a fast one).
  `audio/tools/xrun-watch.py` is § 3's five-minute run.
- **The device is `snd-dummy`**, not HDMI or `snd-aloop`: HDMI will not open
  with no monitor (error 524), and `snd-aloop`'s jiffies timer (HZ 250) cannot
  clock a 128-frame period — `aplay` itself fails at 128 and underruns at 256.
  `sudo modprobe snd-dummy hrtimer=1 pcm_devs=1 pcm_substreams=2` (not persistent)
  plus `~/.asoundrc` on the Pi pointing `default` at `plug:hw:Dummy,0` both ways.
  A dummy device is clocked like a real one, so wake-ups and deadlines are real;
  it proves nothing about the WM8960's own latency or DMA.
- **`nohz_full=3` and `rcu_nocbs=3` were not set**: this kernel has neither
  `CONFIG_NO_HZ_FULL` nor `CONFIG_RCU_NOCB_CPU`, so both would be silently
  ignored. `cmdline.txt` gained `isolcpus=domain,managed_irq,3 irqaffinity=0-2`
  (backup `cmdline.txt.bak-20260918`); `/sys/devices/system/cpu/isolated` reads `3`.
  **It stays** — P3 (#19) writes the unit that uses it.
- **No `arbutus-audio.service` was installed** — that unit is P3's (#19). The
  realtime runs used a transient one with the brief's limits:
  `sudo systemd-run --uid=elvindevelops --setenv=HOME=… -p LimitRTPRIO=95
  -p LimitMEMLOCK=infinity -p CPUAffinity=3 …/arbutus-audio …`.
- **`mlockall` only under an unlimited `RLIMIT_MEMLOCK`**, and `SCHED_FIFO` is set
  from the control thread on miniaudio's worker thread (not inside the
  callback) — `audio/CLAUDE.md` § Defaults has both.
- **The xrun counter was blind to the thing this stage configures.** §6.8 timed
  `render_block` only, so a late *wakeup* — the failure `SCHED_FIFO` and
  isolation exist to prevent — never counted. Device under/overruns now count
  (miniaudio's `EPIPE` log line), and the 2 s window no longer drops xruns at its
  roll-over (it zeroed a counter, and a stall delays the poll that rolls it).
  Proven with `kill -STOP` for 200 ms, twice: 2 xruns. `PROTOCOL.md` § pushes
  says what it still cannot see.
- **The UI was not running**: there is none on the Pi until P3/P4. The load was
  four busy loops plus a 30 Hz `set_param` stream, and `cyclictest` measured the
  margin the xrun count could not.

**The device under systemd — P3b (#84, 2026-09-19).** `arbutus-audio.service` runs as
`arbutus`, which never read `elvindevelops`'s `~/.asoundrc`, across a reboot that unloads a
hand-loaded module — so the bench card is now **system-wide and persistent**, by
`tools/bench-pi-snd-dummy.sh` (bench scaffolding, deliberately **not** in
`arbutus_os/install.sh`, where the WM8960 is the card, device-os-spec § 6.3):
`/etc/modules-load.d/arbutus-bench-snd-dummy.conf` (`snd-dummy`),
`/etc/modprobe.d/arbutus-bench-snd-dummy.conf` (`hrtimer=1 pcm_devs=1 pcm_substreams=2`),
`/etc/asound.conf` (`default` → `plug:hw:Dummy,0` both ways); `~/.asoundrc` moved aside to
`~/.asoundrc.bak-20260919` so there is one default and not two. **vc4-hdmi was tried first
and is still not the answer:** both `vc4hdmi0`/`vc4hdmi1` enumerate, both connectors read
`disconnected`, and `aplay -D hw:`/`plughw:` on either fails with error 524 (`ENOTSUPP`) —
no sink, no PCM. After a reboot with nothing hand-loaded: `hrtimer=Y`, the unit opens the
card, `realtime: SCHED_FIFO 80, memory locked`, the audio thread on core 3, **0 xruns**.
Not `--offline` at any point: the dummy's clock drives real callbacks. Undo = delete the
three files and reboot. (The host's `-> <device>` banner is empty for ALSA `default` —
miniaudio reports no name for it; cosmetic.)

**Thermal.** Four cores flat out for five minutes took the bare CM4 from 47 to
**81 °C** and set the soft-temperature-limit bit (`vcgencmd get_throttled` =
`0x80000`); the arm clock was not reported capped. The audio load alone is a
fraction of one core, but a P3 enclosure with no heatsink path is a question for
the chassis, not for this stage.
