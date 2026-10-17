"""Single source of colour for the whole UI (ui-aesthetic.md §3).

No literal RGB tuples anywhere else in the codebase. Since #334 (2026-10-03)
the screen wears the website's and the companion app's colours — their beige,
brown and green, the shades `app/ios/Arbutus/Look/Theme.swift` derives from
them, and the four knob colours — in two modes:

  * **light** (the default, and the first boot's): the app's card cream as the
    ground, brown text;
  * **dark**: the app's dark mode, brown ground and beige text.

Which one is showing follows the paired phone (`appearance`, PROTOCOL.md
§ The phone's appearance); `App.set_appearance` is the one caller of
`set_mode` in the running instrument.

Each mode is one table below. `set_mode()` binds every name in the chosen
table as a module attribute, so a draw site keeps writing `palette.INK` and
gets the current mode's value at call time — the same mechanism the live
editor (`tools/edit_server.py`) has always relied on.

Where a shade is not one of the app's, it is two of the site's colours mixed,
the rule the site's stylesheet states for its derived shades; the comment says
which two. Contrast against each ground is in ui-aesthetic.md § 3.4.
"""

from typing import Dict, Tuple

RGB = Tuple[int, int, int]

MODES = ("light", "dark")

# --- light: the site's page, the app's light mode ------------------------
LIGHT: Dict[str, RGB] = {
    # grounds
    "GROUND": (253, 236, 203),          # --cap, the app's card cream
    "PANEL": (253, 236, 203),           # header, strips, param bar: the app's card (= ground)
    "OVERLAY_BG": (252, 224, 167),      # HELP overlay: --beige, the app's ground
    "SHIFT_HIGHLIGHT": (245, 209, 143), # behind the SHIFT row: --bg-deep
    # text and rules
    "INK": (140, 60, 3),                # --brown: un-coded text, glyphs
    "SUBTLE": (122, 74, 32),            # --ink-soft: sublines, secondary text
    "ACCENT_BROWN": (122, 74, 32),      # shifted labels: --ink-soft (DDR-005/006)
    "DIM": (191, 139, 93),              # disabled / empty: brown 55 % into cap
    "RULE": (196, 148, 103),            # rules / separators: brown 50 % into cap
    # the four knobs (§3.2), Elvin 2026-10-03
    "ENC1": (10, 69, 128),              # blue
    "ENC2": (27, 92, 34),               # the site's green
    "ENC3": (199, 44, 44),              # red
    "ENC4": (173, 95, 0),               # orange
    # state (§3.3)
    "GREEN": (27, 92, 34),              # --green: slot identity, persistent state
    "RED": (224, 32, 27),               # record / warning: bright red (#334)
    "INVERT_BG": (140, 60, 3),          # active cell: brown ...
    "INVERT_FG": (253, 236, 203),       # ... with cap-cream text
    # tape / mixer surfaces
    "LOOP_ON": (226, 219, 183),         # loop region, on: green 12 % into cap
    "LOOP_OFF": (242, 229, 195),        # loop region, bypassed: green 5 % into cap
    "CLIP_FILL": (252, 224, 167),       # clip, unselected lane: --beige
    "CLIP_FILL_SEL": (245, 209, 143),   # clip, selected lane: --bg-deep
    "CLIP_DIM": (191, 139, 93),         # waveform on an unselected lane: as DIM
    "REC_FILL": (249, 205, 177),        # behind the rolling take: RED 15 % into cap
    "REC_WAVE": (224, 32, 27),          # the rolling take's waveform: RED
    "METER": (185, 193, 152),           # meter body: green 30 % into cap
    "METER_PEAK": (140, 60, 3),         # meter peak hold: --brown
}

# --- dark: the app's dark mode -------------------------------------------
DARK: Dict[str, RGB] = {
    # grounds
    "GROUND": (140, 60, 3),             # --brown, the app's dark ground
    "PANEL": (110, 47, 2),              # header, strips, param bar: darkCard
    "OVERLAY_BG": (110, 47, 2),         # HELP overlay: darkCard
    "SHIFT_HIGHLIGHT": (140, 60, 3),    # behind the SHIFT row: the ground, on the panel
    # text and rules
    "INK": (252, 224, 167),             # --beige
    "SUBTLE": (236, 203, 147),          # darkSoft
    "ACCENT_BROWN": (218, 175, 118),    # shifted labels: beige 70 % into brown
    "DIM": (196, 142, 85),              # disabled / empty: beige 50 % into brown
    "RULE": (185, 126, 69),             # rules / separators: beige 40 % into brown
    # the four knobs, lighter: the app's knobDark shades lifted to 4.5:1 on
    # the brown ground, hue kept (run's call, #334)
    "ENC1": (178, 203, 236),            # blue
    "ENC2": (160, 213, 165),            # green
    "ENC3": (247, 185, 185),            # red
    "ENC4": (245, 189, 123),            # orange
    # state
    "GREEN": (162, 171, 114),           # darkGreenInk: green 40 % into beige
    "RED": (255, 148, 128),             # record / warning, lifted to read on brown
    "INVERT_BG": (252, 224, 167),       # active cell: beige ...
    "INVERT_FG": (140, 60, 3),          # ... with brown text
    # tape / mixer surfaces
    "LOOP_ON": (112, 68, 11),           # loop region, on: green 25 % into brown
    "LOOP_OFF": (128, 63, 6),           # loop region, bypassed: green 10 % into brown
    "CLIP_FILL": (125, 54, 2),          # clip, unselected lane: brown 50 % into darkCard
    "CLIP_FILL_SEL": (110, 47, 2),      # clip, selected lane: darkCard
    "CLIP_DIM": (196, 142, 85),         # waveform on an unselected lane: as DIM
    "REC_FILL": (132, 62, 21),          # behind the rolling take: RED 15 % into darkCard
    "REC_WAVE": (255, 148, 128),        # the rolling take's waveform: RED
    "METER": (150, 110, 53),            # meter body: darkGreenInk 45 % into brown
    "METER_PEAK": (252, 224, 167),      # meter peak hold: --beige
}

TABLES: Dict[str, Dict[str, RGB]] = {"light": LIGHT, "dark": DARK}

_mode = "light"


# --- the live editor's groups (tools/edit_server.py) ----------------------
GROUPS = (
    ("Grounds", ("GROUND", "PANEL", "OVERLAY_BG", "SHIFT_HIGHLIGHT")),
    ("Text and rules", ("INK", "SUBTLE", "ACCENT_BROWN", "DIM", "RULE")),
    ("Knob colours (§3.2)", ("ENC1", "ENC2", "ENC3", "ENC4")),
    ("State (§3.3)", ("GREEN", "RED", "INVERT_BG", "INVERT_FG")),
    ("Tape / mixer surfaces", ("LOOP_ON", "LOOP_OFF", "CLIP_FILL", "CLIP_FILL_SEL",
                               "CLIP_DIM", "REC_FILL", "REC_WAVE",
                               "METER", "METER_PEAK")),
)

EDITABLE = tuple(name for _, names in GROUPS for name in names)

assert set(EDITABLE) == set(LIGHT) == set(DARK), "every role in both modes"


def _rebuild_derived() -> None:
    global ENCODER
    ENCODER = (ENC1, ENC2, ENC3, ENC4)        # noqa: F821 — bound by set_mode


def set_mode(mode: str) -> None:
    """Bind every name in `mode`'s table. Visible on the next rendered frame."""
    global _mode
    if mode not in TABLES:
        raise ValueError(f"palette mode {mode!r}: expected one of {MODES}")
    _mode = mode
    globals().update(TABLES[mode])
    _rebuild_derived()


def mode() -> str:
    return _mode


def encoder_color(slot_idx: int) -> RGB:
    """Slot n always renders in encoder (n mod 4)'s colour."""
    return ENCODER[slot_idx % 4]


set_mode("light")


# --- live editing (tools/edit_server.py) ----------------------------------
# An edit lands in the current mode's table and on the module name, so the next
# frame shows it and a mode switch keeps it. `dirty()` is the current mode's,
# `dirty_entries()` both modes' — the writeback saves all of them.

def get_color(name: str) -> RGB:
    if name not in EDITABLE:
        raise KeyError(name)
    return TABLES[_mode][name]


def set_color(name: str, rgb) -> None:
    """Rebind one palette name in the current mode."""
    if name not in EDITABLE:
        raise KeyError(name)
    value = tuple(max(0, min(255, int(c))) for c in rgb)
    if len(value) != 3:
        raise ValueError(f"{name}: expected 3 channels, got {rgb!r}")
    TABLES[_mode][name] = value
    globals()[name] = value
    _rebuild_derived()


def snapshot() -> Dict[str, RGB]:
    return {name: TABLES[_mode][name] for name in EDITABLE}


_ORIGINAL: Dict[str, Dict[str, RGB]] = {}


def _capture_original() -> None:
    for m, table in TABLES.items():
        _ORIGINAL[m] = dict(table)


_capture_original()


def dirty() -> Tuple[str, ...]:
    """Names whose current-mode value differs from the source file."""
    return tuple(n for n in EDITABLE if TABLES[_mode][n] != _ORIGINAL[_mode][n])


def dirty_entries() -> Tuple[Tuple[str, str], ...]:
    """(mode, name) for every edit not yet written back, both modes."""
    return tuple((m, n) for m in MODES for n in EDITABLE
                 if TABLES[m][n] != _ORIGINAL[m][n])


def mark_clean() -> None:
    _capture_original()


def revert(name: str) -> None:
    set_color(name, _ORIGINAL[_mode][name])
