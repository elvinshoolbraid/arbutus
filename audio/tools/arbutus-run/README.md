# arbutus-run

Load a box, feed it, prove it. One tool, three jobs (spec §9.3): render an
engine offline, compare the result to a golden within a tolerance, and time it.
It is how the audio process is tested without the UI, and what an engine's
`ctest` calls.

```
arbutus-run <box> [--notes script.txt] [--in in.wav] [--side side.wav]
                  [--set id=value ...] [--at F:id=value ...] [--pitch F:st ...] [--asset key=path ...]
                  [--frames N] [--sr 48000] [--len N]
                  [--out out.wav] [--events-out events.json] [--golden ref.npy --tol -100] [--bench]
```

| Option | Meaning |
|---|---|
| `--notes FILE` | event script, format below |
| `--in FILE` | input audio, `.wav` or `.npy`; mono is duplicated to both channels |
| `--side FILE` | sidechain input, for engines with `ARB_CAP_SIDECHAIN` |
| `--motion FILE` | `host.motion[6]` per block: an `.npy` of shape `(6, rows)` — `ax ay az` in g, `gx gy gz` in deg/s. Row *r* is the block starting at frame *r*·`--frames`; the last row holds. Without it motion is zeros, as on a device with no IMU |
| `--set id=value` | a parameter **in the descriptor's units**, mapped to 0..1 through its curve. Repeatable |
| `--at F:id=value` | the same, set mid-render: before the first block that starts at or after frame *F*, as the host applies a moved knob between blocks. How a box's own test proves a parameter change (reverb SIZE during a tail, #630). Repeatable |
| `--pitch F:st` | `arb_host.pitch_st` (the instrument's bend plus a `host.pitch` route, in semitones) from the first block that starts at or after frame *F*, one value per block as the host hands it over; 0 until the first. How `drums_noise_live` bends noise, drum and dbox (#647). Repeatable |
| `--asset key=path` | content for an asset key the box declares (`sample`, `kit`, `wavetable`, `pattern`), handed to `load_asset` before the first block. Repeatable |
| `--frames N` | block size, default 512 |
| `--sr N` | sample rate; default is the input file's, else 48000 |
| `--len N` | total frames. Without `--in` it is the run's length; with `--in` it pads the input with silence (so an effect's tail can ring out) or cuts it short |
| `--tempo BPM` | host tempo, and what `b` means in a script. Default 120 |
| `--env A D S R` | the ENV page, seconds/level. Default `0.01 0.1 0.8 0.2` |
| `--env-mode M` | `poly` (default), `mono`, `legato`, `unison` |
| `--seed N` | `host.rng_seed`. Default 0 |
| `--out FILE` | write the output, `.wav` or `.npy` |
| `--events-out FILE` | write the events the box emitted as JSON, one object per line (a sequencer's golden, below) |
| `--wav-bits 16\|32` | output WAV depth. Default 32 (float) |
| `--golden FILE` | compare against a reference |
| `--tol DB` | peak-error tolerance in dBFS. Default −100 |
| `--bench` | µs/block, best of `--bench-runs` (default 5), and % of one core |
| `--describe` | print the descriptor and stop |
| `--rt-guard` | count allocations made inside `process()` |

Exit status is 0 when everything asked for passed, 1 otherwise. **Any NaN or
infinity in the output fails the run**, golden or not, and a golden comparison
counts a non-finite difference on either side as a failure (#626: the
comparison skipped NaN, so an all-NaN box passed every golden at −inf dBFS;
`ctest -R arbutus_run_catches_nan` is the negative control).
Every run also checks each event a box emits against the header's
`0 <= frame < frames` and fails on one stamped at or past its block's end
(#631: the four sequencers did it whenever a step landed on a block boundary).

## The event script

One event per line. `#` starts a comment.

```
<time> on  <note> <velocity>
<time> off <note>
<time> key <0..3> <1|0> [shift]
```

`<time>` is a frame number, or beats with a `b` suffix (`4b` is bar two at 4/4,
converted with `--tempo` and `--sr`); a negative time is refused. Note is
0..127, velocity 0..1. Lines may be in any order; they are sorted by time.
`key` presses (`1`) or releases (`0`) soft-key 0..3, with SHIFT held when
`shift` is 1: it is how a sequencer's golden drives the box's soft-keys
(engine-spec §3.6) without a host.

```
0      on  60 0.90
0      on  64 0.75
12000  off 60
2b     on  72 1.00
```

## Sequencer goldens

A sequencer makes events, not audio (§9.4), so its golden is the event list:
`--events-out run.json` writes one `{"frame", "type", "a", "b", "flags"}` object
per line, and the test compares that file with the committed one using CMake's
`compare_files` (`engines/arbutus/arp/CMakeLists.txt` is the pattern). One
object per line, so a diff points at the step that moved.

## Blocks

`--frames N` is the block size, but the run deliberately does not stay on it.
Every run opens with a **0-frame block** and ends with a **short one**, splitting
the last full block in two when the length divides evenly — §3.7.2 says an
engine handles any `frames <= max_block`, every call, including 0, and an engine
that only ever sees 512 has not been tested.

An engine whose state does not depend on where the blocks fall gives identical
output at 512, 128 and 97. `audio/tests/ragged.sh` asserts exactly that.

## Files

WAV (16-bit PCM and float32, mono or stereo) and NPY are both read and written;
the extension decides. NPY matters because the emulator's goldens are numpy
arrays and one of them peaks above 1.0 — routing it through a 16-bit WAV would
change the numbers under test. Reading covers float32 and float64, 1-D and
(channels, frames); writing is always float32.

A 1-D golden is compared against the output's left channel.

**Modulators** (`ARB_CAP_MODULATOR`) make no audio, so the output is their
`ctl_out` instead: one channel per declared output, each block's value held
across that block's frames. `--out`, `--golden`, `--bench` and `--rt-guard` then
work unchanged. NPY is read up to six channels, for this and for `--motion`. A modulator's output
depends on the block size by definition, so it has no ragged test.

## Refusals

A box that cannot load says exactly why and loads nothing (§7.4):

| Condition | Message |
|---|---|
| box `abi` ≠ host `ARB_ABI`, or `format` newer than 1 | `NEEDS FIRMWARE UPDATE` |
| no binary for this arch | `NO BUILD FOR THIS DEVICE` |
| manifest snapshot ≠ `arb_describe()`, bad manifest, missing symbols | `ENGINE BROKEN` |

The detail line after the message is for the author; the message itself is what
the device would show.

## --rt-guard

Counts live heap blocks either side of every `process()` call and reports any
net increase as a failure. It catches the common case — a `std::vector` grown in
the audio path — and misses a malloc/free pair inside one call. It is a smoke
alarm, not a proof.

macOS counts with `malloc_zone_statistics`; Linux/glibc — the CM4, the WSL bench
host, CI — with a counting allocator that this flag arms at startup and that
costs nothing when the flag is absent (#52, `hostlib/rt_alloc_hooks.c`). Any
other platform prints "rt-guard: unavailable on this platform" and passes.

The guard's own test is `ctest -R rt_guard_catches_allocation`: it renders
`arbutus-test/allocbox`, a fixture that `new`s every block, and demands both a
refusal and — on macOS and Linux — that the guard did *not* claim to be
unavailable. Every other `rt_guard_*` test proves the guard stays quiet, which a
broken guard also does; this one proves it can still speak.
