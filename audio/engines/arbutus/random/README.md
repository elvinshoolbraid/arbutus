# arbutus/random

Three kinds of chance off one clock (`modulation-spec.md` §3.2). A
**modulator**, `caps: modulator`. Every output is **deterministic** from SEED
(or, at SEED 0, the host's `rng_seed`), so a patch sounds the same twice.

## Outputs

| id | Name | Range | What |
|---|---|---|---|
| `sh` | S&H | ±1 | a new value every step; SLEW glides into it |
| `drift` | DRIFT | ±1 | a Catmull-Rom curve through a random point per step: continuous slope, never pauses on a value (a glided S&H does) |
| `walk` | WALK | ±1 | Brownian: moves every block by an increment ∝ √time, reflects off ±1 |

Three, not four: a fourth kind of chance would be one of these with another
knob, and a slot that is a copy is a slot a player has to learn.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `rate` | −1 … 1, bipolar (§3.3) | 1/8 | **Identical to `arbutus/lfo`'s RATE** (the code is copied, not shared — a box is its own source tree; change both or neither): centre ±0.02 = STOP, right = 14 tempo divisions 8 BAR … 1/32T, left = 0.05 … 50 Hz log. STOP freezes all three outputs. Default 1/8: audibly random the moment a route is made, and slower than the lfo's 1/4 is not. |
| 1 | `slew` | 0 … 1 | 0 | S&H only: the fraction of a step spent gliding (smoothstep) from the old value to the new. 0 is hard steps, 1 is never still. DRIFT and WALK are continuous already. |
| 2 | `range` | 0 … 1 | 1 | **How far the next value may land from the last**, as a fraction of the ±1 span: the next S&H value and DRIFT point are uniform in `[last − 2·RANGE, last + 2·RANGE] ∩ [−1, 1]`, and WALK's speed scales by it. 1 is classic independent S&H; 0.1 is a jitter that stays where it is — a humaniser, which depth alone cannot make. 0 freezes all three. |
| 3 | `seed` | INTEGER 0 … 999 | 0 = `HOST` | §3.2: an integer so a patch names one sequence exactly. 0 takes `arb_host.rng_seed`. Changing SEED restarts the sequence from its first value. 1000 seeds is more than anyone auditions. |

Slots 4–7 are empty. `format_param` draws RATE (`STOP`, `1/8`, `3.2 Hz`) and
SEED 0 as `HOST`.

## Timing

One value per output per block, taken at the block's first frame (§7), then
the step clock advances by the block. Synced rates follow `host.tempo_bpm` but
not the song position (as the lfo). A free 50 Hz at a 512-frame block runs
several steps per block and draws them all, so the sequence is the same at any
block size; the held values are not. WALK's per-step spread is ≈ 0.58·RANGE
before reflection, independent of block size on average, but its exact path
depends on it. S&H holds its first value from the first block; DRIFT and WALK
start at 0 so a new route does not jump its target.

## Tests

`ctest -R random` — two goldens, `--rt-guard`, `random_format` (RATE and SEED
readouts).

`random_sync.npy`: 1/16 at 120 BPM, 128-frame blocks, SLEW 0.4, RANGE 0.6, host
seed 11. `random_seed.npy`: free 4.8 Hz at 512 frames, SEED 42 over host seed 5.
Frozen after checking by numbers: all finite and within ±1; S&H constant through
the first step, 16 distinct step values, largest jump 0.91 (≤ 1.2 = 2·RANGE),
each glide done 2448 frames into a 6000-frame step (0.4, block-rounded); DRIFT's
largest per-block change 0.036; S&H steps every ~10048 frames at 4.8 Hz; SEED 42
renders identically under host seeds 5 and 99, and host seed 5 alone differs.

**Bench:** 0.03 µs/block, 0.00 % of a core at 512 frames / 48 kHz.
