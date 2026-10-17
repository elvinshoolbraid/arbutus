# arbutus/drum (v2)

24 pads on chromatic keys from **F3 (MIDI 53)**, one kit, one voice per pad,
**every parameter per pad** (`engine-spec.md` §15.3, DDR-053). A rewrite of
`emulator/engines/drum_sampler.py`.
`caps: audio_out, events_in`. `assets: ["kit"]`. `honours_env: true`.

## The decisions

**The kit is one asset, not 24.** `load_asset(h, "kit", path)` gets the resolved
§7.3 `kit.json`; the engine reads the `pads` map and opens the WAVs itself. A
descriptor declares at most `ARB_MAX_ASSETS` (3) keys and a pad map is
variable-length content, so one key naming a document is the only shape that
fits. Relative pad paths resolve against the `kit.json`'s own folder.

**Per-pad values live in the kit**, because they describe the sample:

```json
"pads": { "0": {"file": "kick.wav", "params": {"start": 0.1, "choke": 0.25}},
          "1": "snare.wav" }
```

Values are normalised 0..1 and keyed by id (§7.1); absent = default. The v1
string shape still loads as that file at defaults. Both are **format 1**: the
old shape is still read, so nothing had to be bumped. The UI edits the
**last-hit pad** and sends `set_note_param(note, idx, v)`; `set_param` on these
slots is ignored (§15.3). `get_param` reports the pad last set.

**No `PolyHost`.** A one-shot plays to the end whether or not the key is down;
PolyHost's release is the opposite. 24 fixed voices, a re-hit restarts that pad.

**ENV is an AD from the host block** (DDR-053: ATK · DEC, S/R greyed). `env_a`
is a linear attack (under one frame = instant), `env_d` an exponential fall to
−60 dB, both latched per hit. **Deviation:** DDR-053 says per pad; the eight
slots are already §15.3's per-pad list, so the host block's AD is global and
the ENV page edits it for the last-hit pad. The DRUM instrument's ENV defaults
are **a 0 s, d 4 s** — v1's click and v1's DECAY default — so the default kit
renders exactly as v1 (`drum_golden`, −150 dBFS, with `--env 0 4 0 0`).

## Parameters — per pad

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `start` | 0 … 1 | 0 | Offset into the sample — the knob that turns a kick into a click. |
| 1 | `end` | 0 … 1 | 1 | Region end. Never before START + 1 frame, so every position plays. |
| 2 | `pitch` | −12 … +12 st | 0 | ±1 octave: a kick into a tom, a hat into a tick, not the kit into an effect. |
| 3 | `level` | 0 … 1 | 0.8 | Pad trim; v1's default. |
| 4 | `play` | ENUM `ONE-SHOT` `GATE` `LOOP` | one-shot | One-shot ignores NOTE_OFF. Gate stops at NOTE_OFF. Loop cycles START…END while held and stops at NOTE_OFF (a held roll, not a sustain loop). |
| 5 | `rev` | ENUM `OFF` `ON` | off | END → START. Loop reverses with it. |
| 6 | `pan` | −1 … +1 | 0 | Balance: unity at centre (v1's mono-on-both), `cos` toward the far side — `host.pan`'s shape. |
| 7 | `choke` | ENUM `OFF` `1`…`4` | off | A hit fades every *other* sounding pad in the same group. Four groups: open/closed hat is one, and nobody needs five. |

Gate, loop release and choke all fade over **5 ms**, linear: long enough not to
click on a kick's body, short enough that a choked open hat reads as cut.

**Pitch** (#647, *run's call*): the instrument's bend and a MOD route to
`host.pitch` move every sounding pad's playback rate, live, on top of its
PITCH (`arb_host.pitch_st`, §15.10). Glide does not apply: a pad has no note to
glide from. Before #647 both did nothing on this box, so a route to
`host.pitch` on DRUM was silent.

## Tests

`ctest -R drum` — `drums_noise_live` (#647: a pad at pitch_st +12 ends in
half the time), `drum_golden` (v1 kit, v1 sound), `drum_pads_golden` (a
v2-shape kit: pitch, level, pan, REV, a gated loop and a choke group under a
2 ms / 300 ms AD), ragged blocks for both, `--rt-guard`. `drum_pads.npy` was
frozen from this box after npystat (peak 0.88, finite, DC 1e-3).
`ctest -R host_drum` drives it through the host: per-pad set/read-back,
survival across an engine rebuild, gate and loop against NOTE_OFF, choke,
`copy_pad`, v1 → v2 kit migration, and `--rt-guard` with `arbutus/transient`
in the rack. `ctest -R host_assets` covers `load_kit` refusals.

**Bench:** 1.71 µs/block, 0.02 % of a core at 512 frames / 48 kHz (v1 kit,
eight hits). A sample player is a table read.
