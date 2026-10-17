---
tags: [spec, active, ui]
status: active
related: [[screen-sketches]], [[design-decisions]], [[emulator-spec]]
---

# UI Aesthetic Rules — Arbutus

Hard constraints for every rendered frame. This file is the **screenshot grader's rubric**: a screen that breaks a rule here fails verification regardless of function.

Created 2026-09-08 from grill session.

---

## 1. Canvas

- 320 × 240 px, 4:3. Rendered 1:1 into a numpy RGB framebuffer. Emulator scales by integer factor only.
- No alpha blending, no anti-aliasing, no gradients, no rounded corners, no drop shadows, no animation easing. Flat fills and 1 px rules only. Do not make it prettier than the hardware LCD will be.
  *[TBD, Elvin's call] (2026-10-14, #935; the whole-system review of 2026-10-13, I12): D103 makes the companion app's `CPU UNHAPPY: OVERLOAD` flash after 6 s of continuous overload (`companion-app-spec.md` § 13) and says the app's warning follows the device's. This page is the device's and is not the app's, but if the device's own flag is ever to flash, this rule needs an exception line saying so. Where the boundary sits is Elvin's, desk meeting M5; nothing here changes until he answers.*
- Everything pixel-grid aligned. No sub-pixel positions.

## 2. Type

- Bitmap pixel fonts only. Two sizes:
  - **small**: ~5×7 glyphs. Strips, param names/values, list items, labels.
  - **large**: ~8×12 glyphs. Header breadcrumb, big values (BPM, step number), menu titles.
- **Proportional advance.** Each glyph carries its own advance width (ink + tracking,
  default 1 px) rather than a fixed cell, so a narrow `I` does not sit in a pool of
  air. Values live in `emulator/ui/font.py` `_ADVANCE_SRC`; set them with
  `main.py --edit`. No kerning pairs — at this size a pair adjustment is a whole
  pixel and always too coarse.
- **Digits are tabular.** `0`–`9` share one advance so a changing numeric readout
  (BPM, step number, param value) never shifts sideways.
- No font smoothing. Text is the mode's ink on the mode's ground (§ 3): brown on cream in light, beige on brown in dark. A highlighted cell inverts them.
- Labels everywhere. If a control's meaning is contextual, the display states the current meaning (DDR-022, principle I-6). Icons may sit beside text, never replace it.
- Icon style: whimsical, same family as the keycap legends (metronome, floppy, brackets). Flat, ≤ 8×8 px in strips, ≤ 16×16 in main.

## 3. Colour

Rewritten 2026-10-03 (#334, Elvin pre-approved the colours): the screen wears **the website's
and the companion app's colours** — the site's beige `#fce0a7`, brown `#8c3c03` and green
`#1b5c22`, and the shades `app/ios/Arbutus/Look/Theme.swift` (`Palette`) derives from them. A
shade that is none of those is **two of them mixed**, the rule the site's stylesheet states
for its own derived shades. Values live in one module, `emulator/ui/palette.py`, as two
tables; no literal RGB tuples in page code. Where an older spec says *off-white* it means
**ink**, and *black* means **panel**.

### 3.1 Two modes, one ground each

| Mode | Ground (main area) | Panel (header, strips, param bar, key strip) | Text |
|---|---|---|---|
| **light** | the app's card cream `#fdeccb` | the same cream (the app's card) | brown `#8c3c03` |
| **dark** | brown `#8c3c03` (the app's dark ground) | `darkCard` `#6e2f02` | beige `#fce0a7` |

**The per-category tints are gone**: every page has the same ground. In dark mode the panel
is one step darker than the ground, as the app's dark card is.

**Which mode: the app's.** The paired phone's appearance reaches the device on the control
channel (`audio/host/PROTOCOL.md` § The phone's appearance) and the screen switches on the
next frame. With no phone the screen stays in the mode it last had — the device setting
`system.appearance` (`audio/library/README.md` § Settings) — and is **light the first time**.
FILE › SYSTEM's RESET does not touch it (#693): the mode follows the phone, not the box. The
mode changes between frames, never under one being drawn (#693).
The emulator's `V` key and `--appearance` stand in for the phone.

### 3.2 Knob colours

The four knob caps (Elvin, 2026-10-03, fixed). Used **only** for the dot, name and value of a
parameter a knob controls, and for text tied to that knob — never for a ground. Slot n
renders in knob (n mod 4)'s colour.

| Knob | Cap | Light | Dark |
|---|---|---|---|
| ENC1 | blue | `#0a4580` | `#b2cbec` |
| ENC2 | green (the site's) | `#1b5c22` | `#a0d5a5` |
| ENC3 | red | `#c72c2c` | `#f7b9b9` |
| ENC4 | orange | `#ad5f00` | `#f5bd7b` |

ENC3 was cream and is red, so no knob colour is the beige. ENC4 is set apart from the brown
text because on the print they are the same filament. **Dark mode lifts each knob to 4.5:1 on
the brown ground with its hue kept**: the app's own dark shades (`knobDarkHexes`, `#7fa8e0`
`#86c98c` `#f28b8b` `#f2ac5a`) are 3.1–3.9:1 there, which is under § 3.4's floor for 5×7 text
(run's call, 2026-10-03; the app's are measured on its darker card).

### 3.3 Roles

| Role (`palette.py`) | Use | Light | Dark |
|---|---|---|---|
| `INK` | un-coded text, glyphs, the playhead | brown `#8c3c03` | beige `#fce0a7` |
| `SUBTLE` | sublines, secondary text | ink-soft `#7a4a20` | `darkSoft` `#eccb93` |
| `ACCENT_BROWN` | shifted labels: the param bar while SHIFT is held, dual legends (DDR-005/006) | ink-soft `#7a4a20` | beige 70 % into brown `#daaf76` |
| `GREEN` | slot identity / persistent state (DDR-007) | green `#1b5c22` | `darkGreenInk` `#a2ab72` |
| `RED` | record armed / recording, warnings, the armed confirm line *[TBD, Elvin's call] (2026-10-14, #935, review I10): D98 also makes red **the one next input** (§ 4: a menu's commit key), a third meaning this row does not list; § 3.4 has only `PANEL` on `RED` for it. Whether red keeps three meanings is desk meeting M5's question* | bright red `#e0201b` | `#ff9480` |
| `DIM` | disabled / empty slot, `—` | brown 55 % into cream `#bf8b5d` | beige 50 % into brown `#c48e55` |
| `RULE` | rules, separators, boxes | brown 50 % into cream `#c49467` | beige 40 % into brown `#b97e45` |
| `INVERT_BG` / `INVERT_FG` | active / selected cell | brown / cream | beige / brown |
| `SHIFT_HIGHLIGHT` | behind the param bar while SHIFT is held | `--bg-deep` `#f5d18f` | the brown ground `#8c3c03` |
| `OVERLAY_BG` | HELP overlay | beige `#fce0a7` | `darkCard` `#6e2f02` |
| `LOOP_ON` / `LOOP_OFF` | TRACK's loop region, on / bypassed | green 12 % / 5 % into cream | green 25 % / 10 % into brown |
| `CLIP_FILL` / `CLIP_FILL_SEL` | a clip block, unselected / selected lane | beige / `--bg-deep` | brown 50 % into darkCard / darkCard |
| `CLIP_DIM` | a clip's waveform on an unselected lane | as `DIM` | as `DIM` |
| `REC_FILL` / `REC_WAVE` | behind the rolling take / its waveform | red 15 % into cream / `RED` | red 15 % into darkCard / `RED` |
| `METER` / `METER_PEAK` | a meter's body / its peak hold | green 30 % into cream / brown | darkGreenInk 45 % into brown / beige |

**Record red** is a bright red (Elvin: the clash with ENC3 "isn't super terrible, just make
that one bright red"), clearly brighter than ENC3's `#c72c2c`; the app's `warning` is the same
`#e0201b`. In dark mode it is lifted to read on brown, and kept coral (more saturated) so it is
not ENC3's pink.

### 3.4 Contrast

WCAG contrast of each role against each ground it sits on. Floors: **4.5:1 for text, 3:1 for
large glyphs, lines and rules**; an exception is listed with its reason. `tests/test_palette_modes.py`
holds the table's numbers as floors, so a palette edit that breaks one goes red.

| Role | Light, on cream (ground = panel) | Dark, on brown ground | Dark, on darkCard panel |
|---|---|---|---|
| `INK` | 6.53 | 5.92 | 7.90 |
| `SUBTLE` | 6.38 | 4.90 | 6.53 |
| `ACCENT_BROWN` | 6.38 | 3.76 ¹ | 5.01 |
| `GREEN` | 6.94 | 3.11 ² | 4.16 ² |
| `RED` | 4.11 ³ | 3.55 ³ | 4.73 |
| `ENC1` | 8.29 | 4.58 | 6.11 |
| `ENC2` | 6.94 | 4.54 | 6.06 |
| `ENC3` | 4.72 | 4.56 | 6.08 |
| `ENC4` | 4.09 ⁴ | 4.51 | 6.01 |
| `DIM` | 2.55 ⁵ | 2.66 ⁵ | 3.55 |
| `RULE` | 2.32 ⁶ | 2.22 ⁶ | 2.96 ⁶ |
| `INVERT_FG` on `INVERT_BG` | 6.53 | 5.92 | |
| `INK` on `OVERLAY_BG` | 5.92 | 7.90 | |
| `ACCENT_BROWN` / `INK` on `SHIFT_HIGHLIGHT` | 5.09 / 5.21 | 3.76 ¹ / 5.92 | |
| `INK` on `CLIP_FILL` / `CLIP_FILL_SEL` | 5.92 / 5.21 | 6.81 / 7.90 | |
| `PANEL` on `RED` (the red key's label, § 4, #818) | 4.11 ³ | 4.73 | |

Exceptions, each a run's call of 2026-10-03:

1. Dark `ACCENT_BROWN` on the brown ground is 3.76. The SHIFT labels show only while SHIFT is
   held since the one-row bar (#1231), on dark `SHIFT_HIGHLIGHT`, which
   is the brown ground, at 3.76 (#693). In the main area it
   also marks a mute badge and a COMP label. Whether the colour changes is Elvin's (the
   play-test checklist, R25).
2. Dark `GREEN` is the app's `darkGreenInk`, as #334 names it: 3.11 on the ground, 4.16 on
   the panel. Lifting it to 4.5 turns it grey-olive and loses the green.
3. Record red: bright and readable pull apart. Light `#e0201b` is 4.11 (Elvin asked for bright);
   dark `#ff9480` is 3.55 on the ground, 4.73 on the panel where the strip says `RECORDING`.
4. Light ENC4 `#ad5f00` is 4.09: Elvin's fixed value, kept.
5. `DIM` is meant to recede: disabled and empty things, `—`. About as far below ink as the old
   grey was.
6. Rules are separators, not information; at 3:1 the param grid's lines outweigh its text.
7. **Fills** (#693, review R25's call): a fill is read by its edge and its position, not as
   text, so no floor applies to it against the ground it sits on. What sits *on* a fill is
   held: `INK` on `CLIP_FILL` / `CLIP_FILL_SEL` is 5.92 / 5.21 light, 6.81 / 7.90 dark.

   | Fill | Light, on cream | Dark, on brown ground | Dark, on darkCard panel |
   |---|---|---|---|
   | `METER` | 1.62 | 1.66 | 2.21 |
   | `LOOP_ON` / `LOOP_OFF` | 1.20 / 1.08 | 1.10 / 1.05 | 1.22 / 1.27 |
   | `CLIP_FILL` / `CLIP_FILL_SEL` | 1.10 / 1.25 | 1.15 / 1.33 | 1.16 / 1.00 |
   | `REC_FILL` | 1.25 | 1.03 | 1.29 |

## 4. Layout constancy

- Zone heights never change between pages or states (see `screen-sketches.md` § Layout). Empty strips render blank, not collapsed.
- Overlays (HELP, hold-SPEED readout) draw over main only; header/strips stay.
- Menus are full-page covers but keep the header.
- **One next input is red** (Elvin, 2026-10-12, D98): a menu waiting for its commit draws that key's cell in the record red, so the path to finish is plain and the keys beside it read as cancel; a turn-to-act cell carries `▸`; a `SAVE THIS RACK`-style row above a browser's first folder is green (`control-inventory.md` § Menus). Elvin may draw a pixel glyph for `▸` later. *[TBD, Elvin's call] (2026-10-14, #935, review I11): the font has no `▸`, so the code draws `›` (`pages/file.py` `TURN_MARK`), which is also the header's breadcrumb separator (`USB/FILE › PROJECT`) on the same screen: one glyph with two meanings until a `▸` is drawn. The glyph is Elvin's (the `▸` he offered to draw, desk meeting M5); `ui/font.py` is not edited by a run.*

## 5. Grader checklist

A screenshot passes if all hold:
1. Header present, breadcrumb text matches page › subpage (› menu) state.
2. Dynamic strip line present (may be blank).
3. Param bar: 1 row × 4 cells (slots 0–3; with SHIFT held, slots 4–7 brown-accented on `SHIFT_HIGHLIGHT`), encoder dot colours correct, `—` for empty slots.
4. Key strip: 4 cells, active cell inverted, or blank.
5. The ground is the mode's ground (§ 3.1), the same on every page; colours are the mode's (§ 3.3).
6. No anti-aliased edges, no non-integer scaling artefacts, no gradients.
7. All visible text legible at 1:1 (no clipped glyphs, no overlap).
8. When a baseline PNG exists for this state, pixel diff = 0.

---

