# arbutus/sampler (v2)

One WAV, played chromatically, on `PolyHost<Voice, 8>`. A rewrite of
`emulator/engines/sampler.py`, with the sample arriving as an **asset**:
`load_asset(h, "sample", path)` on the control thread (§3.2). A file is content,
not a parameter (DDR-037).
`caps: audio_out, events_in`. `assets: ["sample"]`. `honours_env: true`.

## Parameters (screen-sketches SYNTH › ENGINE)

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `start` | 0 … 1 | 0 | Normalised position. START never passes END — one knob pushes the other, so no setting plays silence. |
| 1 | `end` | 0 … 1 | 1 | |
| 2 | `loop_in` | 0 … 1 | **1** | Loop start, clamped inside START…END. |
| 3 | `loop_out` | 0 … 1 | **1** | Loop end, same. IN at or after OUT is **no loop** — the default, v1's LOOP OFF. |
| 4 | `root` | INTEGER 0 … 127 | 60 | The key that plays the sample at its own rate. A bass and a hi-hat do not share a sensible root. |
| 5 | `tune` | −12 … +12 st | 0 | ±1 octave. Further, the key already exists, and linear interpolation is not worth hearing. |
| 6 | `rev` | ENUM `OFF` `ON` | off | END → START, the loop reversed with it. Latched at note-on: flipping it under a ringing note would jump. |
| 7 | `fade` | 0 … 50 % | 0 | Seam crossfade as a fraction of the loop, so every position fits any loop (DDR-044). 0 = hard loop. Half the loop is the most a seam can borrow. |

**The loop plays while the key is held**; on release the voice plays on to END
under the envelope's release. Released mid-seam, it finishes the crossfade
first. FADE reads the material just before LOOP-IN (after LOOP-OUT in reverse),
so it is also capped by how much file is there: a loop starting on the file's
first frame is a hard loop.

**v1 → v2.** LEVEL is gone (the mixer and record level cover it); the voice
keeps v1's default **0.8 as a constant trim**, so patches and the golden keep
their level. v1's `LOOP FWD` over START…END is `loop_in = start`,
`loop_out = end` — except that it now lets go at release. The host drops a
patch's `level` and `loop` like any id the engine does not declare; the two
library patches had LOOP OFF, so they needed no migration (their dead ids were
removed; `deep-bottle`'s LEVEL 0.75 now plays at 0.8, +0.56 dB).

**Readouts** (`format_param`): START/END/LOOP-IN/LOOP-OUT in seconds of the
loaded file (`0.12 s`), ROOT as a note name (`C4` = 60).

## Format and resampling

- **16-bit PCM and 32-bit float WAV, mono or stereo, any rate.** Stereo is
  mixed to mono, as the emulator does.
- **Resampled on play by linear interpolation**, not on load: a chromatic
  sampler resamples every note but the root anyway, and the sample stays its
  size on disk. Audibly soft above about an octave up — the emulator's trade.

## Tests

`ctest -R sampler` — `sampler_golden` (v1 notes at defaults: unchanged,
−inf dBFS), `sampler_loop_golden` (reversed loop, 30 % FADE, releases; frozen
after npystat: peak 0.20, largest step 0.0079 = the sine's own), ragged blocks
for both, `--rt-guard`, `sampler_refuses_unknown_asset`.
`ctest -R host_sampler` uses a ramp: a hard seam steps 0.40, a 20 % FADE leaves
1.4e-4 (the ramp's slope) forward and reversed; release plays past LOOP-OUT to
END; readouts; v1 ids dropped.

**Bench:** 4.56 µs/block, 0.04 % of a core at 512 frames / 48 kHz.
