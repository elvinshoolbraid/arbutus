# arbutus/dbox

A drum box: 24 synthesised pads on the chromatic keys, and no samples anywhere.
`caps: audio_out, events_in`. `assets: kit`. `honours_env: **false**`.

`arbutus/drum` plays a kit you recorded. This one *is* the kit — every pad is a
small analogue-style voice, and the eight knobs shape the one you last hit. It
is the same page and the same gestures (§15.3: every parameter is `per_note`,
DRUM › ENGINE edits the last-hit key and sends `set_note_param`) with one
difference that goes all the way down to the host:

**a pad here has no file.** Its kit document is `{"params": {…}}` per pad and
nothing else. §15.3 gives the pad shape `{file, params}` and never says a kit
must be samples, so stage I1 taught `load_kit` to accept a pad that is
parameters only. Everything else about a kit is untouched — `set_note_param`
writes into it, `copy_pad` copies it, the document is still the save, and
`normalise_kit` still fills every `per_note` id from the descriptor.

## Parameters (all `per_note`)

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `mode` | ENUM `KICK` `SNARE` `TOM` `HAT` `CLAP` `RIM` | `KICK` | What the pad *is*. Six covers a kit; a seventh would be a variation of one of these, and a variation is what the other seven knobs are for. |
| 1 | `tune` | −24 … +24 st | 0 | Two octaves either way from the mode's own pitch (kick 55 Hz, snare 180/270 Hz, tom 110 Hz, hat 6 kHz, clap 1 kHz, rim 1.7/2.6 kHz). Wider is a different drum, not a tuned one. |
| 2 | `decay` | 0.02 … 2.5 s, log | 0.35 s | Seconds to −60 dB, **times the mode's own scale** (hat 0.22, rim 0.14, snare 0.55, clap 0.50, tom 0.90, kick 1.00). That scale is what makes one knob mean "long" on a kick *and* "long" on a hat: a hat ringing for the kick's 0.35 s is a cymbal. 0.02 s is a tick, not silence. Log, because the bottom half of the range is where a drum machine lives. |
| 3 | `snap` | 0 … 100 % | 45 % | The pitch drop at the front — up to 3 octaves on a kick over 30 ms, 1.4 on a tom, 1.0 on a snare, 0 on the modes that have no pitch to drop. It is the difference between a sine and a kick. |
| 4 | `tone` | 0 … 100 % | 50 % | Dark to bright, in every mode at once: it slides the body/noise balance *and* opens the noise filter (±1 octave around the mode's centre) together. One knob, one direction, six voices — a drum machine whose six pads have six different panels is six drum machines. |
| 5 | `drive` | 0 … 100 % | 15 % | `tanh` across the finished pad, crossfaded by the knob so **0 is bit-exact bypass**. Gain to 10×, which on a kick is the difference between a thump and a click that fills the bar. |
| 6 | `level` | 0 … 1 | 0.8 | Pad trim, the same default and meaning as `arbutus/drum`'s. |
| 7 | `pan` | −1 … +1 | 0 | A **balance**, not a pan law: unity at centre and `cos` toward the far side, like `arbutus/drum`'s PAN and the host's. |

Output trim is **0.55**, which lands a kick at the default LEVEL and full
velocity near 0.44 peak — `arbutus/drum`'s stock kit pads sit near 0.5.

## Two rules that are not knobs

* **`honours_env` is false, and that is the point of the box.** DDR-053 asked
  for a per-pad AD; `arbutus/drum` could not give it, because its eight slots
  were already §15.3's sample list, so it took the instrument's global AD
  instead (`engine-spec.md` §15.11's deviation). Here DECAY *is* per pad, so a
  global envelope on top could only make things shorter. **DRUM › ENV has
  nothing to do on this box** — the page still draws and still sends, and the
  engine ignores it. `NativeEngine.honours_env` is already in the emulator and
  reads false here; dimming the page from it is a UI change and was out of
  scope for stage I1.
* **A hat chokes a hat.** A second `HAT` pad cuts any other sounding one, which
  is what CHOKE does on `arbutus/drum` and what there is no ninth slot for here.
  The cut is drum's **5 ms linear fade** (#647; it was one sample, a click on
  an open hat still ringing). Nothing else chokes anything; a re-hit restarts
  its own pad and no other.
* **Pitch moves the body** (#647, *run's call*): the instrument's bend and a
  MOD route to `host.pitch` (`arb_host.pitch_st`, §15.10) move every sounding
  pad's sines, live, on top of TUNE and SNAP. The noise filters stay where
  TONE put them: they are the pad's colour, and a bend is a pitch. No glide: a
  pad has no note to glide from.

Note-off does nothing: every voice is a one-shot, and letting go of a kick must
not cut it.

## The kits

`audio/library/drum/dbox/` holds two, `dry_box` and `wide_box` — 24 pads each,
all six modes, laid out kick / snare / rim / clap / hats / toms and then
variations. They are ordinary format-1 `kit.json` documents with no `file` on
any pad, which is also the worked example of the shape.

`arbutus/drum` **refuses** one of these (it can open no sample), and this box
**accepts** a sampled kit — it ignores the files and plays the params it can
read. That asymmetry is deliberate: a synthesised kit is not a sample kit, but
neither of them should crash the other.

## Tests

`ctest -R dbox` — golden, ragged blocks, `--rt-guard`; `ctest -R
drums_noise_live` has the HAT choke's fade and a TOM an octave up at pitch_st
+12 (#647, both red on the engine before).
`ctest -R host_synthboxes` covers the fileless kit through the host:
`load_kit`, `normalise_kit` filling all eight ids, `set_note_param` on a pad
with no file behind it (audible: LEVEL 0.15 drops the peak from 0.397 to
0.074), `copy_pad`, and the refusal for a pad that is neither a file nor params.
`emulator/tests/test_synth_boxes.py` covers DRUM › ENGINE on it.

`tests/golden/dbox.npy` was **frozen from this box** over a bar of
`tests/kit.json` (pads 0–5 are the six modes, pad 6 a second hat so the choke is
in the golden): peak 0.622, RMS 0.089, DC +9.5e−04, all finite over 96000
frames (`tests/tools/npystat.py`). Each mode alone at the default, full
velocity: KICK 0.441, SNARE 0.313, TOM 0.414, HAT 0.622, CLAP 0.351, RIM 0.368.

**Bench:** 30.1 µs/block, 0.28 % of a core at 512 frames / 48 kHz (a bar of the
test kit, so several pads overlapping).
