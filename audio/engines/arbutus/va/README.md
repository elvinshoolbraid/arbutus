# arbutus/va

The subtractive voice. Two band-limited oscillators a few cents apart, a square
sub an octave down, a noise source, and a **four-pole resonant ladder with the
ENV page's own envelope on it**, eight voices, on `PolyHost<Voice, 8>` (§4).
`caps: audio_out, events_in`. `honours_env: true`.

`arbutus/osc` is one naive waveform and a level — right as a rewrite of the
Python, wrong as *the* analogue synth, because everything that makes a
subtractive patch is downstream of the oscillator. A `biquad` on the FX rack is
not the same instrument: the rack's filter is one per patch, after the mix,
with no per-voice envelope on it.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `wave` | ENUM `SAW` `PULSE` `SQR` `TRI` | `SAW` | Four detents, four timbres, and no width knob: `PULSE` is 25 % and `SQR` 50 %, which is the two pulse sounds anyone reaches for. A width knob would be live on two settings out of four and dead on the others. |
| 1 | `cutoff` | 80 Hz … 12 kHz, log | 2.2 kHz | The bottom stops at **80 Hz**, not 20: under a four-pole ladder a saw below that is a thud and then nothing, and "the bottom of the knob is mute" is exactly what DDR-044 forbids. At 80 Hz a C3 saw is −22 dB and muffled — quiet, but still a sound. The top is 12 kHz because past it the filter is doing nothing you can hear on any note the keyboard plays. Log, so the useful two octaves either side of 1 kHz are not four detents wide. |
| 2 | `res` | 0 … 1 | 0.2 | Mapped to ladder feedback **0 … 3.6** of a possible 4.0. Self-oscillation is a sine generator, not a filter, and it is loud; the last detent of a knob must not be a different instrument. 3.6 is still a hard squelch. Resonance takes the bottom out of a ladder, so the input is made up by `1 + 0.3·k` — a bass patch stays a bass patch as RES comes up. |
| 3 | `env` | −100 … +100 % | +35 % | ENV-F: the **ENV page's own ADSR**, moving the cutoff by up to ±5 octaves. Five is the whole useful sweep; beyond it the envelope simply parks the filter open or shut. Bipolar, because a filter that closes as the note starts is a sound (and it is the only way to get one without a second envelope). |
| 4 | `sub` | 0 … 1 | 0.25 | A square an octave down. A **balance**, not a gain (see below), so the top of the knob is a sub-heavy timbre and not a louder patch. |
| 5 | `noise` | 0 … 1 | 0 | White noise into the filter — breath, wind, the top of a snare. Same balance rule. Default 0: noise is a thing you add. |
| 6 | `spread` | 0 … 30 ct | 8 ct | Detune between the two oscillators, total (±half each). 30 ct is where two saws stop being one thick saw and start being two notes; 8 ct is the classic shimmer. This is the **engine's** spread and is unrelated to the ENV page's DETUNE, which stacks four *voices* in unison mode (DDR-052). |
| 7 | `drive` | 0 … 1 | 0.15 | `tanh` saturation **before** the filter, crossfaded dry→wet by the knob so **0 is bit-exact bypass** — the knob cannot add colour you did not ask for. Pre-filter, because that is where an overdriven oscillator sits in a real signal path and it is what makes resonance growl instead of whistle. |

**SUB and NOISE are a balance.** The three sources are summed and divided by
`1 + 0.7·(SUB + NOISE)`, so turning them up changes the timbre and not the
level. Summed raw, the top of two knobs would be 2.4× hotter — a volume control
nobody asked for. Voice trim is then **0.4**, in the same range as
`arbutus/osc`'s 0.35 and `arbutus/fm`'s 0.4: one note at full velocity peaks
near 0.4 and eight still have somewhere to go.

**No key tracking on the filter.** The cutoff is the number on the screen at
every note, which is what makes a bass patch stay a bass patch across the
keyboard. Tracking would be a ninth parameter to say how much.

## How it is built

* **Oscillators are polyBLEP.** The step in a saw or a pulse is replaced by a
  two-sample polynomial carrying the step's low frequencies and not its
  aliases — the cheapest correction that is audibly right to the top of the
  keyboard, which is what a CM4 running eight of these can afford. `TRI` is the
  square integrated (`4·inc` per sample is exactly ±1 over a half period), so
  it inherits the square's band-limiting and needs none of its own; it starts
  at −1, where an ideal triangle starts, rather than walking up to it.
* **Every wave is centred and unit-peak.** A 25 % pulse has a mean of −0.5, and
  a resonant filter fed an off-centre signal puts a DC term in the output. The
  mean is removed and the result scaled back to ±1, so all four waves reach the
  mixer at the same size.
* **The ladder is zero-delay.** Four TPT one-pole sections with the feedback
  *solved* rather than delayed one sample, so the resonant peak sits where the
  knob says at every cutoff instead of drifting sharp near Nyquist.
* **The filter envelope is read on a 32-frame grid, not once per block** (#56).
  A `tan()` per sample is not worth it on a CM4, but *one cutoff per block* is
  not the alternative: the block is the host's to choose — 128 on the device,
  anything a bake or a bench asks for elsewhere — so it made this box sound
  different at every block size, by **16 dBFS** between 512 frames and 1. The
  grid is `arb::kCtrlStep` in `sdk/poly.hpp`, counted from the voice's own
  note-on, which is on a real frame because `PolyHost` splits blocks at events.
  0.67 ms at 48 kHz, finer than the device's block, so the sweep is no coarser
  than it was before. `ctest -R ragged_va` is what checks it — and could not,
  until #56 fixed the harness itself.

## Tests

`ctest -R va` — golden, ragged blocks (512 / 128 / 97 / 2048 / 1 agree), `--rt-guard`.

`tests/golden/va.npy` was **frozen from this box** at its defaults — there is no
Python original to replay, because the emulator never had a filter with a
per-voice envelope on it. Before freezing: peak 0.570, RMS 0.107, DC −5.3e−05,
all finite over 96000 frames (`tests/tools/npystat.py`), and the cutoff knob
checked end to end with the envelope out of the way — 80 Hz −22 dB peak, 500 Hz
−5.4 dB, 12 kHz −1.4 dB on the same triad. **Refrozen 2026-09-23** for the
control grid above: peak 0.568, RMS 0.107, DC −5.3e−05 — the same to every digit
but the peak's fourth, which is what says the fix moved the sweep's timing by
≤ 0.67 ms and not the sound.

**Bench:** 41.6 µs/block, 0.39 % of a core at 512 frames / 48 kHz (three voices
sounding). The most expensive stock synth: three band-limited oscillators and a
four-pole ladder per voice. Eight voices is about 1 % of a core on this Mac.
