# arbutus/delay

The echo, and **the one stock box that reads the tempo**. `arb_host.tempo_bpm`
reaches every engine in a rack, so TIME can be a note value instead of a number
of milliseconds nobody can convert in their head while playing.
`caps: audio_in, audio_out`. `honours_env: false`.

`arbutus/comb` is already a delay line — one short enough to be heard as a
pitch. This is the same idea long enough to be heard as a rhythm, which is a
different instrument: the loop needs a filter, a saturator, a stereo matrix and
a way to get out of the way of the notes.

## Parameters

| Slot | id | Range | Default | Why that range |
|---|---|---|---|---|
| 0 | `time` | −1 … +1 (a two-sided knob) | `1/8` | **Centre is the shortest delay of each kind.** Right of centre: thirteen tempo divisions from `1/32` to `1 BAR`, ascending, straight *and* triplet *and* dotted — the dotted eighth is half of what anyone ever asks a delay for and the triplet is most of the other half. Left of centre: free time, log, **20 ms … 2 s**. 20 ms because below it the repeat stops being an echo and becomes a comb filter, which is `arbutus/comb`'s job; 2 s because a free time longer than that is a looper. One slot, both ways of asking; `format_param` prints which one you are on (`1/8`, `250 ms`, `1.20 s`). Measured: `1/4` at 120 BPM lands at 0.5000 s, `1/8` at 96 BPM at 0.3125 s, `1/8D` at 120 BPM at 0.3750 s, all to ±0.00 ms; the free ends land at 0.0200 s and 2.0000 s. |
| 1 | `feedback` | 0 … 0.95, **the top is `HOLD`** | 0.35 | Below the top the feedback ends on its own. Measured at `1/4` / 120 BPM: 0.20 → 6 repeats to −60 dB, 0.35 → 8, 0.60 → 15; 0.94 is 91 dB down after 39 s at 1/8. **The top position (`HOLD` on the readout) is a hold** (#779; Elvin, on D92: "infinite at same volume is a feature"): the loop's gain is exactly 1, so the repeats keep their level for ever and never grow, at every DRIVE and PING (measured: 60 s of repeats at 1/8, 120 BPM, within 0.00 dB, each repeat the one before sample for sample). What is in the line is frozen as it is: TONE, DRIVE and WOW leave the loop and the read lands on a whole sample, since each of them takes a little level off every pass (TONE 0.2 took 28 dB a minute, DRIVE 1 16 dB, WOW's in-between read 0.56 dB at 440 Hz); WOW stops where it is, so reaching the hold moves the read by at most half a sample. Notes played into the hold layer in; a pile-up is brought back under a soft ceiling (untouched up to 1.0, never past 2.0: a 0.9 note held for 10 s peaks at 1.95, where without it it reached 28). The ceiling limits only what the input adds, never what the loop brought round, so a loud loop keeps its level once the hand stops (#1027: the knee used to run again every pass, and a 0.6 note piled past it lost 1.5 dB in 20 s). TONE's filters keep running on the held repeats with their output set aside, so turning FDBK down resumes them where the sound is (#1027: they used to resume from where the hold began, a click that repeated). To clear the hold, turn FDBK down; there is still no CLEAR key on an effect tab (DDR-041). The range stays 0 … 0.95 so a saved patch's FDBK, stored as a knob position, means what it did; what was 0.95 is now the hold. At the hold the feedback matrix goes to PING's nearer end (below 0.5 two independent lines, from 0.5 the ping-pong), since only those two pass every part of the repeats whole: between them the part that differs side to side fades a little every pass (a repeat on one side only lost 3 dB at PING 0.5, #1027). The input's routing keeps PING as set. A MOD route or macro that brings FDBK to its top engages the hold at each peak and leaves it after, exactly as the knob does; nothing in a stock rack reaches it (`tape-echo.rack`'s FEED stops at 0.9). |
| 2 | `tone` | 0 … 1 | 0.5 | **One knob, two filters, both in the feedback loop**, so the colour compounds with every repeat the way a tape echo does while the *first* repeat is still the sound you played. Below centre a one-pole lowpass closes from 16 kHz to 400 Hz; above centre a one-pole highpass opens from 20 Hz to 800 Hz. **0.5 bypasses both and is bit-exact.** Measured at FDBK 0.85, repeat 6 against repeat 1: TONE 0 takes everything above 4 kHz down 121 dB; TONE 1 takes everything below 200 Hz down 87 dB; TONE 0.5 moves both bands by the same −7 dB, which is the feedback and nothing else. |
| 3 | `mix` | 0 … 1 | 0.3 | Dry/wet. **MIX 0 is bit-exact bypass** (measured: 0.000e+00 peak error against the dry input) — a delay on a tab you have not turned up must not colour anything. 0.3 as the default because a delay you cannot hear is a delay you will not find. |
| 4 | `ping` | 0 … 1 | 0 | Crossfades the feedback matrix from `[[1,0],[0,1]]` (two independent delays) to `[[0,1],[1,0]]` (true ping-pong), and the input with it: at 1 the input's mid goes into the left line only, so a centred source (every synth reaches the FX tab as one) starts left and bounces (#630: with L = R in both lines the swap did nothing). The matrix is `[[1−p, p],[p, 1−p]]`, whose largest gain is exactly 1 at every `p`, so PING can never make a stable FDBK setting run away; at FDBK's hold the matrix goes to its nearer end (the FDBK row says why, #1027). Default 0: the box must not invent a stereo image nobody asked for. |
| 5 | `wow` | 0 … 1 | 0.1 | The capstan. Two sines at **0.37 Hz and 1.73 Hz** (a ratio that never lines up inside a phrase, which is what stops it sounding like vibrato) move the read point by ±0.4 % of TIME plus a fixed ±0.12 ms, so a 20 ms slapback wobbles as well as a 2 s wash. Measured on a 0.5 s delay: WOW 0 = 0.00 ms of wander, 0.25 = 1.02 ms peak-to-peak, 0.50 = 2.04 ms, 1.00 = 4.10 ms. Default 0.1 — enough that the repeats are not photocopies, not enough to hear as pitch. |
| 6 | `drive` | 0 … 1 | 0 | `tanh` saturation **inside the loop**, crossfaded dry→wet by the knob so **0 is bit-exact bypass**, and divided by the shaper's slope at 0 so the loop's gain stays ≤ 1 at every level: DRIVE adds grit, not gain, and every FDBK setting dies away (#630; before it, the slope reached 7 and FDBK 0.35 with DRIVE 0.6 repeated for ever, against the FDBK row's own argument). Loud repeats are squeezed more than quiet ones, so with DRIVE up a loud phrase drops back faster than a soft one. The harmonic figures measured before #630 (third harmonic 12 dB down at 0.35, 9 dB at 1.0, a 1 kHz note at FDBK 0.8, repeat 6) were not re-measured. |
| 7 | `duck` | 0 … 1 | 0 | The **dry input** holds the wet path down and lets it up when the hand stops — the one control that keeps a long delay out of the way of the notes. `g = 1 − DUCK · env/(env + 0.08)`, a knee at about −22 dBFS with no threshold knob to set. Measured under a 0.5-amplitude note: DUCK 0.25 = −2.1 dB, 0.50 = −4.9 dB, 0.75 = −8.9 dB, 1.00 = **−16.8 dB** while playing, against −1.5 dB of loss on the tail that follows. **0 is bit-exact bypass.** |

## How it is built

* **TIME glides; it does not jump.** The delay length runs a one-pole toward
  its target with an 80 ms time constant, per sample, so changing TIME (by
  hand or from a macro) is a tape sweep instead of a click — and so the sweep
  is the same at every block size.
* **The line is sized once at `create()` for 4 s** and every delay is clamped
  inside it. A synced `1 BAR` therefore clamps below 60 BPM: measured at
  50 BPM it lands at 4.0001 s instead of 4.8 s. The alternative is allocating
  on a tempo change, which is an xrun (§3.7.1).
* **The read is linearly interpolated**, like `arbutus/comb`. That is what
  makes WOW a pitch wobble rather than a stutter, and what makes a TIME sweep
  sound like tape.
* **TONE and DRIVE are in the loop, DUCK is on the output.** A filter on the
  output would colour the first repeat and never compound; a duck inside the
  loop would change the decay time as you play.

## Tests

`ctest -R delay` — golden, ragged blocks (512 / 128 / 97 / 2048 / 1 agree),
`--rt-guard`, and `delay_bypass` (MIX 0 is the input bit for bit with every
other knob at its far end). PING on a centred source and the tail dying at
every DRIVE are `ctest -R fx_moves` (#630), and so is the hold (#779): 60 s
of repeats at DRIVE 0 and 1, PING 0 and 1, TONE 0.2 and 0.8, within 0.1 dB
and never above the first repeat, each repeat the one before sample for
sample, one click below the top dying away, and the ceiling on a held 0.9
note; #1027 added PING 0.25, 0.5 and 0.75 (a centred and a one-sided note),
a loud loop past the knee that keeps its level, and leaving the hold at a
dark TONE without a click. The `HOLD` readout is
`emulator/tests/test_fx_boxes.py`'s `test_delay_fdbk_top_reads_hold`.

`tests/golden/delay.npy` was **frozen from this box** — there is no Python
original; the emulator never had a delay. The freeze runs every path at once
(a synced TIME, a filtered and saturated loop, ping-pong, wow, ducking) at
**`--tempo 96`**, so the golden also pins that the delay time comes from the
host block and not from a constant. Re-frozen by #630 (PING now routes the
input, DRIVE no longer adds gain): peak 0.450, RMS 0.0295, DC +4.2e−04, all
finite over 72000 frames (`tests/tools/npystat.py`). The input is mono, so
both channels start equal; the frozen L−R peak is 0.238, so the golden now
sees PING (the same run at PING 0 fails it by −12.5 dBFS).

**Bench:** 14.26 µs/block, 0.13 % of a core at 512 frames / 48 kHz.

