---
tags: [emulator, chrome, input]
status: active
read_when: you change the window, the look-alike photo, key hotspots, device pixels, the keyboard / mouse / MIDI mapping, or the live look editor
index: emulator/CLAUDE.md
---

# The window, the photo and the input devices

> Moved verbatim from `emulator/CLAUDE.md` on 2026-10-08 (#440), headings unchanged, so a `§ <name>` citing that file finds its section here. The index of this folder's notes is `emulator/CLAUDE.md` § Read by task.

## Live look editor (`--edit`)

For the aesthetic pass. `main.py --edit` runs the emulator exactly as normal and
also starts a stdlib HTTP server on a daemon thread (127.0.0.1:8765,
`--edit-port` to move it), then opens the browser. **This window is the
preview** — every draw site reads `palette.X` and `font._GLYPHS` at call time,
so an edit lands on the next rendered frame, on whatever page you navigated to.
Play the demo in one window, tune the look in the other.

| Tab | What it does |
|---|---|
| palette | a swatch + hex field per role in `ui/palette.py`, grouped by `ui-aesthetic.md` §3. Live on drag. `↺` reverts one name to the source value. Two buttons pick the mode: the swatches edit that mode's table and the preview draws it (the instrument's own setting is untouched). **Save** writes both tables. |
| font | pick small (5×7) or large (8×12), click a glyph from the sheet, click or drag the grid to toggle pixels. Dark cells are the advance padding and can never carry ink. The preview strip re-renders any sample text as you edit. |
| spacing | per-glyph **advance** (the step to the next character), on the same tab. `Fit glyph` / `Fit whole font` set it from the ink plus `tracking` px. Three proof strings render underneath at real advances — judge the rhythm, not any single pair. |

### Proportional spacing

The font is proportional: `font._ADVANCE_SRC` holds per-glyph overrides and a
character absent from it uses `METRICS[size][2]`, so an empty table renders
exactly as the old monospaced font did. `text_width()` sums advances and
`draw.clip_text()` accumulates them — nothing else in the codebase reads the
advance, so pages needed no changes.

Digits are the exception: `0`–`9` share one advance (`font._TABULAR_SRC`,
the `digits` field in the editor) so BPM, step numbers and param values do not
shift sideways as they count. `III` is narrower than `888` by design.

There is no kerning-pair table and probably should never be one — at 5×7 a pair
adjustment is a whole pixel, which is far too coarse. Per-glyph advance is the
whole job.

**Save to source** writes back into `ui/palette.py` and `ui/font.py`. Only
entries that actually changed are rewritten, line by line, so comments, ordering
and the authored row widths stay byte-identical everywhere else. Nothing is
written until you press it; edits before that live only in the process.

Thread note: CPython rebinds a module attribute atomically and glyph arrays are
replaced wholesale, so the worst a race can do is render one frame with a mix of
old and new values.

`venv/bin/python tools/edit_server.py [port]` runs the editor with no emulator
attached — controls work, but there is no live preview.

## Chrome (phase 3)

The window **is** `docs/design/reference/lookalike-model.jpg` — the Sep 2026
grass re-shoot, **rectified**: warped by an affine fitted to the four corners of
the LCD bezel aperture so the panel is genuinely square-on, then laid out on a
5120x2880 16:9 canvas with the synth centred. The panel was tilted ~1 degree in
the raw frame, which is why the old axis-aligned LCD rect left a wedge of navy
showing down one side; after rectification the aperture is an exact axis-aligned
640x480, and the frame fills it. The warp is affine, not projective: the
vanishing points come out past 600k px, i.e. the shot is all but orthographic,
and the residual on the four corners is 0.47 px.

The canvas is wider than the photo, so the outer ~580 px each side is the grass
mirrored back on itself, cross-faded over 300 px into a 180-degree-flipped copy
(a plain mirror is continuous at the seam but obviously symmetric; the flip is
asymmetric but creases — the ramp gets both), under a mild edge falloff.

The framebuffer goes into the LCD rect from `chrome/screen_rect.json` at the
largest **integer** scale that fits, centred — `pygame.transform.scale`, never
`smoothscale` — and its corners are masked to the bezel's 3.5 mm radius so the
photo's cream shows through, as it does on the real panel. If the image has an
alpha channel (a PNG with the LCD punched out) the framebuffer is blitted
*under* the photo instead of over the rect; both paths are supported, the JPG
uses the over-the-rect path.

`M` switches between them live. `ChromeView.set_fb_multiple(k)` re-derives every
window-space measurement from the one scale, so 2× is exactly 1× doubled —
photo, hotspots, outlines and framebuffer together — and the window crops to the
display rather than growing past it. `--width` only picks the starting multiple.

### Device pixels (Retina)

macOS measures windows in **points**. The MacBook's scaled Retina mode reports
1710x1112 against a 2560x1664 panel, so a plain 1x SDL window is bilinearly
upscaled by the compositor (~1.497x here) — a soft photo and an LCD grid
resampled by a non-integer factor. `chrome/window.py` opens the window with
`pygame.Window(..., allow_high_dpi=True)` instead; `get_surface()` then hands
back a surface in **device** pixels (2x on this display), which is what the
whole of `chrome/` is measured in. This needs **pygame-ce** — upstream pygame
2.6 has no such flag, and `pygame.SCALED` cannot create a renderer on this cocoa
driver. It falls back to `set_mode` at ratio 1 when the flag is missing or the
video driver is `dummy` (headless verify runs, tests), so screenshots are
unchanged.

`ChromeView(device=ratio)` multiplies the chosen scale and nothing else, so
`--width` and `fb_multiple` stay in points: a "1x" LCD is 640 device px on
Retina — the same size on the glass as before, drawn at the panel's real
resolution. Mouse positions still arrive in points; `Window.to_px()` converts
them before hit-testing. The outline stroke scales with the ratio (`_t`).

At that resolution the 5120x2880 photo costs ~12 ms a frame to blit, so
`_background()` composites photo + black LCD well **once** per rescale and
`draw()` repaints only what moves: the LCD, the zoom pane and the key
highlights, restoring the rects it dirtied last frame from that composite
(`_mark`). ~0.8 ms a frame. The alpha-photo path (framebuffer *under* the photo)
has no static composite and still redraws in full.

The photo scale is **not** simply `--width / photo width`. `fit_scale()` picks
the largest scale at or under `--width` that lands the LCD on an exact multiple
of 320x240; any other scale letterboxes the framebuffer inside the bezel or
spills it over. The canvas is sized around that, so `--width` is effectively a
choice of LCD multiple: **2560 gives a 2560x1440 window with a 1:1 320x240 LCD
and grass to all four edges**, 5120 gives 640x480. `--scale F` still overrides
with a raw float. `--width` defaults to the display width, so the default run
fills the screen.

The window never exceeds the display: a scale whose photo is bigger than the
screen crops it rather than opening a window whose middle you cannot reach. At
1x that crop is centred on the **photo** — the whole synth in frame, which is
the point of the chrome. From a 2x LCD up it is centred on the **LCD** instead,
clamped to the photo's edges: at that scale the photo's centre is halfway down
the keybed with the screen off past the window edge.
`ChromeView.origin` carries that centring (negative when cropping) and
`chrome.OFFSET` — still **(0, 0)** — is an extra nudge on top, `--offset X Y`.
`photo_at` is the sum, `window_to_photo()` undoes it before scaling, and the
photo-space data in `keymap.json` / `screen_rect.json` is never touched.

The LCD gets **no drawn ring** — it sits in a real bezel with a real 3.5 mm
radius, and a stroked rectangle on top only fought it. The zoom pane keeps its
1 px ring (`SCREEN_BORDER`), because that one floats over the photo with nothing
to define its edge. `SCREEN_BORDER` lives in `chrome/__init__.py`, not
`ui/palette.py`: chrome guides are not device colours and must stay legible
whatever the UI palette becomes.

| Flag / key | Effect |
|---|---|
| `M` | toggle the whole chrome between a 1× (320×240) and 2× (640×480) LCD |
| `Z` | toggle the zoomed framebuffer pane over the photo's right half (integer 3×, or the largest that fits) |
| `--offset X Y` | translate the whole chrome (default `chrome.OFFSET`) |
| `--zoom` | start with that pane showing |
| `--zoom-only` | no photo at all — the phase-1 window, `--scale N` = integer scale |

Pressed keys get a straight-red ring (`OUTLINE_COLOUR`) round the hotspot with
the cap washed in low-opacity white inside it (`HILITE_FILL`, alpha 56) — both
kept in `chrome/__init__.py` with `SCREEN_BORDER` for the same reason. Stroke is
`ChromeView.OUTLINE_W` = 4 points (x the device ratio), and the highlight
survives ~2 frames after release so screenshots catch clicks. On the near-white
keycaps the wash barely reads and the ring does the work; the encoders and the
darker caps show it.

The stroke is drawn as a shape, not by stroking the stored polygon: pygame's
polygon width gives an uneven corner. `_draw_outlines` reads the bbox, takes
circle-vs-rounded-rect from `keymap.json`'s `shapes` block, and draws the ring *outside* the hotspot, so the inner edge
carries the cap's own 3.5 mm radius and the outer edge that plus the stroke —
which is what keeps the width even all the way round. pygame has no antialiased
thick primitives, so it is drawn at `OUTLINE_SS` = 4x and smoothscaled down;
rings are cached per shape, so a held key costs one blit.

The shape is **recorded, not inferred**. `rounded()` emits only corner-arc
vertices, so a *square* keycap's vertex radii vary by under 8 % — there is no
radius-spread threshold that admits ENC1 and rejects SYNTH, and the first
attempt at sniffing it drew every square key as a circle. There is no encoder rotation indicator
(emulator-spec §2).

The outline is placed with `photo_at`, the same transform as the photo. It used
to use `chrome.OFFSET` alone, which is identical *only* while the window is not
cropping — so every outline slid sideways the moment the window was capped to a
smaller display. `tests/test_phase3.py::test_outlines_follow_a_cropped_window`
pins it.

## Rebuilding from a new photo

`tools/rebuild_keymap.py RAW.png` does the whole chain — aperture edges,
rectifying affine, warp, px/mm, cap detection, naming, regularisation, encoder
rings — and writes the photo, `keymap.json` and `screen_rect.json`. It needs the
previous keymap in place (caps are named by nearest previous hotspot) and fails
loudly if it cannot place all 52. `--dry-run` measures and writes nothing.

**The method and its traps are written up in
`docs/design/reference/rectifying-the-photo.md`** — read that before touching
any of this. The short version of what bites: colour thresholds follow the
recess shadow and put the LCD edge 40 px out; blob extremes are not corners when
the corners are rounded; use an affine, not a homography, because the LCD is
small and a projective fit amplifies corner error across the panel; take px/mm
from the key pitch (13 intervals) rather than the faceplate (two edges in side
lighting); and fit the encoder recess by groove-vs-cream contrast, because
"darkest ring" finds the cast shadow.

## Hotspots and calibration

`chrome/keymap.json` = `{key name: [[x, y], ...]}` in **photo pixels** for all
52 keys of `app/keys.py` plus `ENC1..ENC4`. It was rebuilt from scratch against
the rectified photo (Sep 2026): every cap detected as a cream blob on the dark
green plate, named by nearest old hotspot, then regularised — one top and bottom
per row, and x snapped to a least-squares uniform pitch, which lets the one cap
the detector lost (NOTE_0, merged into the faceplate) fall out of the grid.

Because the photo is rectified, **photo pixels are now metric**:
`screen_rect.json` carries `px_per_mm` (11.15), measured as the key pitch over
13 intervals of the note row / 16 mm. It cross-checks to 0.6 % against the
faceplate at 238.2 mm wide, and the caps come out 15 mm. Keys are emitted as
rounded rects at the real **3.5 mm** corner radius (39 px), encoders as **10 mm**
radius circles (111.5 px) on the faceplate recess ring — coplanar with
everything else, unlike the knobs, which are raised and lean. The four ring
centres are fitted by scoring a dark-groove circle against the cream just inside
and outside it; raw darkness gets dragged off by the cast shadow on ENC3/ENC4,
the inside-outside contrast does not. They fit a straight line to +/-1.3 px:
pitch 32.13 mm, with a real 0.35 degree slope across the row. The fitted ring
radius lands at 10.1-10.4 mm, which is the independent check on the 10 mm.

*Moved from the root `CLAUDE.md` § Key Layout (audit A5, 2026-10-08), verbatim:* "Measured on the
print: 16.0 mm pitch, 15 mm caps, 3.5 mm cap radius, 6 mm encoder radius, 32.0 mm encoder pitch,
faceplate 238.2 mm. [[control-inventory]] was rebuilt from the photo (2026-09-08) and matches it; the
zone map in [[chassis-geometry]] predates the rectified photo (it carries no banner)." The 6 mm and
32.0 mm there disagree with the fitted 10 mm ring radius and 32.13 mm pitch above (perhaps the knob,
not the recess ring); the fit above is the measurement.

The black keys are the exception to the grid: piano spacing is not uniform, so
their x stays as detected, with only the cap widths regularised (wide oblong vs
the two round ones, NOTE_3 and NOTE_15).

```bash
venv/bin/python tools/calibrate_keymap.py            # walk every control
venv/bin/python tools/calibrate_keymap.py --only SYNTH
venv/bin/python tools/calibrate_keymap.py --image docs/.../panel.png
```

Per key: drag from the centre outward (rect), `4` then four clicks (polygon),
`c` then drag (circle — the default for the encoders), SPACE to keep what is
already there, BACKSPACE to step back, `s` to save, ESC to save and quit.

## Mouse and MIDI

| Input | Control |
|---|---|
| left-click a hotspot | key down; release = key up (so HELP-hold works by holding the mouse) |
| physical Shift | SHIFT, both edges — so SHIFT + click combos work |
| wheel over an encoder hotspot | that encoder, ±1 per notch |
| wheel anywhere else | ENC1 (phase-1 fallback) |
| MIDI note on/off | chromatic key press/release, velocity honoured |
| MIDI CC 16–19 | ENC1–4, relative |

Clicking an encoder hotspot does nothing — encoders turn, they don't push.

`midi.toml` (read once at boot, no hot-plug):

| Key | Meaning |
|---|---|
| `device` | substring of the input port name; `""` = first port |
| `channel` | 0–15, or `-1` for omni |
| `note_offset` | MIDI note that lands on chromatic key 0 (53 = F3) |
| `velocity` | `false` = every note at full level |
| `cc.enc1..enc4` | CC numbers for the four encoders |
| `cc.encoding` | `twos-complement` \| `offset-64` \| `signmag` |

Notes outside the 24-key range still play the active instrument; the `‹ ›`
octave keys transpose it. If `mido`/`python-rtmidi` are missing or no port
matches, one line is logged and the emulator carries on.

## Keyboard map (still live alongside mouse + MIDI)

| Emulator input | Control |
|---|---|
| F1–F9 | SYNTH DRUM TRACK MIXER SEQ INPUT HELP USB/FILE TEMPO |
| 1 2 3 4 | subpage / track keys 1–4 |
| 5 6 7 8 9 0 | slot keys A B C X Y Z; on TRACK the brown tape legends IN OUT LOOP REV SPEED FILTER |
| ← → | ‹ › (octave on SYNTH/DRUM, rewind/FF on TRACK) |
| Left/Right Shift | SHIFT |
| Space / Backspace / Return | PLAY / STOP / REC — on **every** page |
| SHIFT + Backspace | undo (1 level, tape ops) |
| Tab | SLICE (SHIFT+Tab = TAPE merge) |
| `-` / `=` | LIFT (SHIFT = lift all 4) / DROP |
| `a s d f g h j k l ;` | white keys F G A B C D E F G A (home row) |
| `w e r · y u · o p` | black keys above their neighbours, piano-aligned (F# G# A# · C# D# · F# G#) |
| ← → | octave (SYNTH/DRUM) · rewind/FF (TRACK) · SHIFT+← → on TRACK = snap marker to marker |
| mouse click + drag up/down on an encoder | that encoder, 6 px per step (internal input mode — MIDI keyboard USB port broken) |
| mouse wheel over an encoder | that encoder ±1 (ENC1 if not over one) |
| mouse click on an encoder that turns nothing | that encoder's **push** (#818; the pushes are matrix keys `ENC1_PUSH`–`ENC4_PUSH`, `COL11 × ROW0–3` on P3) |
| Ctrl (or Cmd) + `1`–`4` | push ENC1–ENC4 (#818) |
| `[` `]` | ENC2 − / + |
| `,` `.` | ENC4 − / + |
| `m` | 1× / 2× chrome (LCD 320×240 ↔ 640×480; window crops to the display) |
| `v` | light ↔ dark screen, as the phone would (#334); remembered in the settings |
| `z` | zoom pane toggle (`--zoom-only`: integer scale +1) |
| `x` (or `\`) | **panic** — all notes off on every instrument, every held control let go |
| Esc | quit |

`x` is the rescue hatch, not a feature of the device — it is one of the few
letters the device's own keyboard map leaves free. It calls `App.panic()`,
which silences both instruments, drops the note owners and clears SHIFT / HELP
and the key highlights. Transport and tape are untouched — it stops notes, not
the song. It does let go of the held tape gestures (TRACK's ‹ › scrub and
the REV / SPEED / FILTER holds, without committing them) and MIXER's solos (#623). The same call runs on `WINDOWFOCUSLOST`, because an unfocused window
never delivers the KEYUP and alt-tabbing mid-note used to leave the note
sounding with no key left to release it.

A note is released on the instrument that **sounded** it. `App._note_owner`
records that at note-on, and `Instrument._held` maps the key as played to the
note handed to the engine, so changing page (DDR-034 moves the active
instrument) or octave mid-note still releases the right voice. Both used to
leave the voice sounding: a drone under everything played afterwards, which is
how it was found (first play session).
`tests/test_edit_feel.py::TestNoNoteGetsStuck` pins it.

SEQ page (`F5`): `1` held = step entry from 0 (the box's REC soft-key: each
key/chord writes a step, `2` inserts a rest — or a **tie** with a key still
held — `3` undoes one, release ends entry, the next REC press overwrites from
0, max 99 steps). `←`/`→` do the same two jobs during entry and are octave
otherwise. All six slot keys A B C X Y Z recall on SEQ (`C` is **not** a HOLD
toggle — HOLD is an encoder param only). **The eight encoder cells are the
box's descriptor row**, so which encoder is HOLD depends on the box
(`arbutus/endless`: `DIV · SWING · HOLD · DIR / GATE · PATTERN · ROT · —`).
Tapping `F5` again arms/disarms; SHIFT+`F5` opens the sequencer engine menu.
The sequencer drives the *active instrument* and keeps running when you leave
the page (DDR-034).

TEMPO page (`F9`): ENC1 BPM · ENC2 CLICK LVL (0 = off) · ENC3 CLICK PITCH
(0 = silent) · ENC4 LINK; SHIFT gives BPM FINE · COUNT-IN · SYNC · —.
`←`/`→` nudge ±1 BPM, ±0.1 with SHIFT. Tapping `F9` 3+ times inside 2 s sets
the BPM by tap tempo, from any page. BPM moves the tape ruler and the
sequencer clock — and, with LINK on (the default, DDR-059), the tape's own
playback rate with them.

HELP (`F7`): tap = HELP page. Hold = overlay describing this page. Hold + any
key/encoder = overlay describing that control here; pages supply the copy via
`help_for(target)` and `pages/help.py` fills in the global fallbacks. Overlays
never navigate or change a value; release dismisses (and only a *bare* hold
lands on the HELP page).

A release goes to the page (or menu) that took its press, not to the page that
is up when the key comes back (`App._key_owner`, #623, the way `_note_owner`
does it for notes): leave TRACK with REV held and REV still lets go. Under HELP
only *presses* belong to the overlay; a key held from before HELP still
releases (the note stops, REC/STOP stop being held), and a key pressed under
HELP keeps its release there too. MIXER's hold-to-solo timer runs on MIXER only.

Tape keys on TRACK (control-inventory § tape keys): `5`=IN `6`=OUT `7`=LOOP,
`8`=REV `9`=SPEED `0`=FILTER. REV/SPEED/FILTER are hold-to-perform (ENC4 is the
live control and the ENC4 param cell relabels) and tap-to-commit while stopped.

Library note: a drum kit is a **folder** under `audio/library/drum/drum/<kit>/`
holding its own WAVs and a `kit.json` — an explicit pad → file map (§7.3), so
renaming a file can never silently re-map a kit. An engine declares
`"item_kind": "folder"` to make its library items directories instead of files.
