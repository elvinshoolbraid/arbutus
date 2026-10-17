# arbutus/duck

A sidechain ducker, and the first box to use `side[]`. An envelope follower on
the sidechain port drives a gain on the audio port: whatever is on tape track 1
pushes everything else out of its way.
`caps: audio_in, audio_out, sidechain`. `honours_env: false`.

The **source** is a rack setting (§6.6) — `tape:1..4` or `input` — not a
parameter of this engine, so there is no source knob here. With no source
selected the host passes `side[]` as NULL and this engine is a wire; an effect
that silences itself because a source was never picked is worse than one that
does nothing visible.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `threshold` | −60 … 0 dBFS | −26 | Above 0 there is nothing to detect; below −60 the follower is chasing the noise floor of whatever is on tape. |
| 1 | `depth` | 0 … 1 | 0.85 | The **fraction of the overshoot removed**, which is one knob instead of the usual ratio/knee pair: `gain = (threshold/envelope)^depth`. 1 pins the sidechain's own level at the threshold (infinite ratio), 0 does nothing, and everything between is a usable amount of pump (DDR-044). |
| 2 | `attack` | 0.5 … 100 ms | 5 | **LOG.** The difference between 1 ms and 5 ms is the whole character; below 0.5 ms the follower tracks the waveform instead of the level. |
| 3 | `release` | 10 … 1000 ms | 150 | **LOG.** 150 ms is a pump that breathes at 120 bpm. The difference between 800 ms and 1 s is nothing, which is why the top of the range is cheap. |

Reduction stops at **−48 dB**, so the duck is a duck and not a gate.

Default: a clearly audible pump under a kick, recovering inside a beat at
120 bpm.

## Tests

`ctest -R duck` — golden, ragged blocks, `--rt-guard`.
`ctest -R host_sidechain` is the one that matters: it records a pulse onto tape
track 1 through the real host, sets `sidechain: "tape:1"` on this tab, holds a
note, and measures **−20.3 dB under the pulse and 0.0 dB after it**, against a
control run with no source that stays flat.

`tests/golden/duck.npy` was frozen from this box after checking peak, RMS, DC
and finiteness, and after measuring the gain envelope against a steady tone:
−14.9 dB under the hit, back to 0.0 dB by 167 ms at REL 60.

**Bench:** 3.74 µs/block, 0.04 % of a core at 512 frames / 48 kHz.
