# arbutus/lfo

The stock LFO (`modulation-spec.md` §3.2). A **modulator**: no audio, four
control outputs per block, and every one of them read off **one phase**, so
SINE and SQR on the same tab stay locked and two LFO tabs a PHASE apart run in
quadrature. `caps: modulator, events_in` (events for RETRIG NOTE and FADE-IN).

## Outputs

| id | Name | Range | Shape |
|---|---|---|---|
| `sine` | SINE | ±1 | sine of the (SYM-skewed) phase |
| `tri` | TRI | ±1 | triangle; SYM leans it toward a ramp |
| `sqr` | SQR | ±1 | square; SYM is pulse width, MORPH rounds it |
| `sh` | S&H | ±1 | a new random value each cycle; MORPH glides between them |

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `rate` | −1 … 1, bipolar (§3.3) | 1/4 | **Centre is STOP** (±0.02, two detents, so it is findable by feel). Right: 14 tempo divisions `8 BAR 4 BAR 2 BAR 1 BAR 1/2 1/2T 1/4 1/4T 1/8 1/8T 1/16 1/16T 1/32 1/32T`, slowest beside STOP so the knob speeds up leaving the centre either way. No triplets above the half note: nobody counts a bar triplet. Left: free **0.05 … 50 Hz on a log curve**, because a rate is heard as a ratio. `format_param` draws `STOP`, `1/8`, `3.2 Hz`. Default 1/4 so a new route moves, in time, the moment it is made. |
| 1 | `morph` | 0 … 1 | 0 | The two stepped shapes are the ones that click; MORPH rounds them. SQR goes from hard square (0) to the sine (1) by soft-clipping `sine/morph`; S&H glides from the old value to the new over MORPH of the cycle (smoothstep). SINE and TRI are untouched — they have no corner to round. |
| 2 | `phase` | 0 … 360° | 0 | Offset of all four outputs. 90° on a second tab is quadrature; with RETRIG NOTE it is where every note starts. |
| 3 | `retrig` | ENUM `FREE` `NOTE` | FREE | FREE runs forever; NOTE restarts the phase (and draws a new S&H value) on every note-on the engine receives, sample-placed within the block. |
| 4 | `fade_in` | 0 … 5 s | 0 | Depth ramps linearly from 0 after each note-on — delayed vibrato. 5 s is the longest held note that is still a gesture rather than a patch change. 0 = off (no ramp ever). |
| 5 | `sym` | 5 … 95 % | 50 | Where the first half-cycle ends: TRI→ramp, SQR pulse width, a leaning SINE, one knob. Stops short of 0/100 % because those are a one-sample edge, which is a click, not a shape. |

Slots 6–7 are empty. Nothing is smoothed inside the box (rule 7): its knobs are
read once per block, which is the output's resolution anyway, and the target
engine smooths what the route sends it.

## Timing

One value per output per block, taken at the block's first frame (§7). A
note-on at frame 0 lands before that value; a later one lands after, and the
next block starts from it. Synced rates follow `host.tempo_bpm` but not the
song position: the phase is the LFO's own, restarted by RETRIG NOTE. S&H is
deterministic from `host.rng_seed` (xorshift32).

Because output is block-rate, **the output depends on block size** — there is
no ragged test, by design.

## Tests

`ctest -R lfo` — two goldens, `--rt-guard`, and `lfo_format` (the RATE readout
table, through the loaded box).

`arbutus-run` writes a modulator's output as one channel per output, each
block's value held across the block. `lfo_sync.npy`: 1/8 at 120 BPM, 512-frame
blocks, NOTE retrigger, FADE-IN 0.25 s, SYM 30 %, PHASE 90°, MORPH 0.3, seed 7.
`lfo_free.npy`: RATE −0.5 (1.47 Hz) at 128-frame blocks, seed 3. Both were
frozen from this box after checking by numbers: all finite, every output within
±1; the free sine's rising zero crossings are 32512 frames apart (48000 / 1.4735 =
32575, to within a block); the fade envelope climbs from 0.11 to 0.95 over
the first 0.3 s; the retrigger at frame 30100 puts SINE back to 0 on the next
block; S&H holds 108 distinct values across the retriggers and MORPH glides.

**Bench:** 0.05 µs/block, 0.00 % of a core at 512 frames / 48 kHz.
