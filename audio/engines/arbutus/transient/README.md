# arbutus/transient

A transient shaper, as a rack box (DDR-053): the Field kept attack/gain/
release/timing on its drum voice; here the drum voice is an AD and the shaping
goes wherever a rack goes — drums, a plucked sampler, the master.
`caps: audio_in, audio_out`. `honours_env: false`. Latency 0.

## How

Three peak followers on the **stereo-linked** level `max(|L|, |R|)`:

| | attack | release |
|---|---|---|
| F (fast) | 0.5 ms | SPEED |
| S (slow attack) | SPEED | SPEED |
| T (slow release) | 0.5 ms | 4 × SPEED |

At an onset `a = (F − S)/F` rises toward 1 and falls back within about SPEED;
through a tail `t = (T − F)/T` rises toward 1. Both are the signal against
itself, so the gain

```
gain dB = 12 × (ATTACK × a + SUSTAIN × t)
```

is **level-independent** (a quiet hit is shaped like a loud one, no threshold
to set first — DDR-044) and **bounded**: ±100 % moves the level at most 12 dB.
One gain for both channels, so the image does not move.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `attack` | −100 … +100 % | 0 | + adds snap, − softens a hit to a swell. ±12 dB at the very front of an onset is plenty; more is a gate. |
| 1 | `sustain` | −100 … +100 % | 0 | + brings up the room and the ring, − tightens a boomy kick. |
| 2 | `speed` | 5 … 200 ms, **LOG** | 30 | How long "the transient" is: 5 ms a click, 30 ms a snare's crack, 200 ms a whole kick. The useful difference is at the bottom. |
| 3 | `mix` | 0 … 100 % | 100 | Parallel shaping without a parallel span. |
| 4 | `output` | −12 … +12 dB | 0 | SHIFT row. Trim for what a big ATTACK adds. |

**Default is a wire**: ATTACK 0 and SUSTAIN 0 skip the gain maths (exactly
1.0), MIX 100 % and OUTPUT 0 dB are exact. A rack can carry the box untouched.

ATTACK, SUSTAIN, MIX and OUTPUT glide over one host block (`arb::Smoothed`,
realtime rule 7); at rest the stored value is returned untouched, so the wire
stays exact. SPEED steps: it sets follower coefficients, read once per block.

## Tests

`ctest -R transient` — `transient_golden` (arbutus/drum's golden through
ATTACK 60 %, SUSTAIN −50 %, SPEED 20 ms, OUTPUT −3 dB; frozen after npystat:
peak 1.09, finite), `transient_transparent` (default output vs its input,
**−inf dBFS**), ragged blocks, `--rt-guard`. `ctest -R host_drum` puts it in a
DRUM rack: transparent within −100 dBFS against no rack, ATTACK +100 % takes a
snare's onset-peak/tail-RMS from 7.7 to 14.4.

Measured on the drum bar: ATTACK +100 % onset peak 0.71 → 2.66 (+11.5 dB),
−100 % → 0.21; SUSTAIN +100 % tail RMS 0.22 → 0.36.

**Bench:** 4.29 µs/block at ATTACK 60 / SUSTAIN −50 (3.57 at default), 0.04 %
of a core at 512 frames / 48 kHz.
