"""Bitmap pixel fonts for the Arbutus UI.

Two hand-authored fonts, embedded as Python data:
  small: 5x7 glyph in a 6x8 cell (1 px tracking)
  large: up to 8x12 glyph in an 8x12 cell

No anti-aliasing, no scaling, no font files. Glyphs are stored as row
strings of '#' (ink) and '.' (paper). Python idiom note: a dict literal of
strings is the cheapest "binary blob" available without a data file.
"""

from typing import Dict, List, Optional, Tuple

import numpy as np

SMALL = "small"
LARGE = "large"

# Cell metrics: (glyph_w, glyph_h, advance_x, line_h)
METRICS = {
    SMALL: (5, 7, 6, 8),
    LARGE: (8, 12, 8, 12),
}

# --- proportional spacing -------------------------------------------------
# METRICS' advance_x is the *default*; a character listed here overrides it.
# What people call kerning is a pair-level correction on top of this — at 5x7
# a pair adjustment is a whole pixel, which is almost always too coarse, so
# there is no pair table and probably never should be. Per-glyph advance is
# the whole job. Absent = default, so an empty table renders exactly as the
# monospaced font did.
_ADVANCE_SRC: Dict[str, Dict[str, int]] = {
    SMALL: {
        'D': 7,
        'E': 7,
        'F': 7,
        'H': 7,
        'L': 7,
        'M': 7,
        'N': 7,
        'O': 7,
        'Q': 7,
        'S': 7,
        'U': 7,
    },
    LARGE: {
        'C': 9,
        'E': 9,
        'F': 9,
        'G': 9,
        'J': 9,
        'N': 9,
        'O': 9,
        'T': 10,
        'U': 9,
        'X': 9,
        'Y': 10,
    },
}

# Digits share one advance (tabular figures) so BPM, step numbers and param
# values do not shift sideways as they count. None = the METRICS default.
_TABULAR_SRC: Dict[str, Optional[int]] = {
    SMALL: None,
    LARGE: 9,
}

TABULAR = frozenset("0123456789")


def _rows(spec: str) -> List[str]:
    return spec.split("/")


# ---------------------------------------------------------------------------
# 5x7 small font. Every glyph is exactly 7 rows of 5 columns.
# ---------------------------------------------------------------------------

_SMALL_SRC: Dict[str, str] = {
    " ": "...../...../...../...../...../...../.....",
    "!": "..#../..#../..#../..#../..#../...../..#..",
    '"': ".#.#./.#.#./...../...../...../...../.....",
    "#": ".#.#./.#.#./#####/.#.#./#####/.#.#./.#.#.",
    "$": "..#../.####/#.#../.###./..#.#/####./..#..",
    "%": "##..#/##..#/...#./..#../.#.../#..##/#..##",
    "&": ".##../#..#./#.#../.#.../#.#.#/#..#./.##.#",
    "'": "..#../..#../...../...../...../...../.....",
    "(": "...#./..#../.#.../.#.../.#.../..#../...#.",
    ")": ".#.../..#../...#./...#./...#./..#../.#...",
    "*": "...../#.#.#/.###./#####/.###./#.#.#/.....",
    "+": "...../..#../..#../#####/..#../..#../.....",
    ",": "...../...../...../...../.##../.##../.#...",
    "-": "...../...../...../#####/...../...../.....",
    ".": "...../...../...../...../...../.##../.##..",
    "/": "....#/...#./...#./..#../.#.../.#.../#....",
    "0": ".###./#...#/#..##/#.#.#/##..#/#...#/.###.",
    "1": "..#../.##../..#../..#../..#../..#../.###.",
    "2": ".###./#...#/....#/...#./..#../.#.../#####",
    "3": "#####/...#./..#../...#./....#/#...#/.###.",
    "4": "...#./..##./.#.#./#..#./#####/...#./...#.",
    "5": "#####/#..../####./....#/....#/#...#/.###.",
    "6": "..##./.#.../#..../####./#...#/#...#/.###.",
    "7": "#####/....#/...#./..#../.#.../.#.../.#...",
    "8": ".###./#...#/#...#/.###./#...#/#...#/.###.",
    "9": ".###./#...#/#...#/.####/....#/...#./.##..",
    ":": "...../.##../.##../...../.##../.##../.....",
    ";": "...../.##../.##../...../.##../.##../.#...",
    "<": "...#./..#../.#.../#..../.#.../..#../...#.",
    "=": "...../...../#####/...../#####/...../.....",
    ">": ".#.../..#../...#./....#/...#./..#../.#...",
    "?": ".###./#...#/....#/...#./..#../...../..#..",
    "@": ".###./#...#/#.###/#.#.#/#.###/#..../.###.",
    "A": "..#../.#.#./#...#/#...#/#####/#...#/#...#",
    "B": "####./#...#/#...#/####./#...#/#...#/####.",
    "C": ".###./#...#/#..../#..../#..../#...#/.###.",
    "D": "###../#..#./#...#/#...#/#...#/#..#./###..",
    "E": "#####/#..../#..../####./#..../#..../#####",
    "F": "#####/#..../#..../####./#..../#..../#....",
    "G": ".###./#...#/#..../#.###/#...#/#...#/.####",
    "H": "#...#/#...#/#...#/#####/#...#/#...#/#...#",
    "I": ".###./..#../..#../..#../..#../..#../.###.",
    "J": "..###/...#./...#./...#./...#./#..#./.##..",
    "K": "#...#/#..#./#.#../##.../#.#../#..#./#...#",
    "L": "#..../#..../#..../#..../#..../#..../#####",
    "M": "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#",
    "N": "#...#/##..#/##..#/#.#.#/#..##/#..##/#...#",
    "O": "#####/#...#/#...#/#...#/#...#/#...#/#####",
    "P": "####./#...#/#...#/####./#..../#..../#....",
    "Q": "#####/#...#/#...#/#.#.#/#.##./#..##/###.#",
    "R": "####./#...#/#...#/####./#.#../#..#./#...#",
    "S": "#####/#..../#..../#####/....#/....#/#####",
    "T": "#####/..#../..#../..#../..#../..#../..#..",
    "U": "#...#/#...#/#...#/#...#/#...#/#...#/#####",
    "V": "#...#/#...#/#...#/#...#/#...#/.#.#./..#..",
    "W": "#...#/#...#/#...#/#.#.#/#.#.#/##.##/#...#",
    "X": "#...#/#...#/.#.#./..#../.#.#./#...#/#...#",
    "Y": "#...#/#...#/.#.#./..#../..#../..#../..#..",
    "Z": "#####/....#/...#./..#../.#.../#..../#####",
    "[": ".###./.#.../.#.../.#.../.#.../.#.../.###.",
    "\\": "#..../.#.../.#.../..#../...#./...#./....#",
    "]": ".###./...#./...#./...#./...#./...#./.###.",
    "^": "..#../.#.#./#...#/...../...../...../.....",
    "_": "...../...../...../...../...../...../#####",
    "`": ".#.../..#../...../...../...../...../.....",
    "a": "...../...../.###./....#/.####/#...#/.####",
    "b": "#..../#..../####./#...#/#...#/#...#/####.",
    "c": "...../...../.###./#..../#..../#..../.###.",
    "d": "....#/....#/.####/#...#/#...#/#...#/.####",
    "e": "...../...../.###./#...#/#####/#..../.###.",
    "f": "..##./.#.../.#.../####./.#.../.#.../.#...",
    "g": "...../...../.####/#...#/.####/....#/.###.",
    "h": "#..../#..../####./#...#/#...#/#...#/#...#",
    "i": "..#../...../.##../..#../..#../..#../.###.",
    "j": "...#./...../..##./...#./...#./#..#./.##..",
    "k": "#..../#..../#..#./#.#../##.../#.#../#..#.",
    "l": ".##../..#../..#../..#../..#../..#../.###.",
    "m": "...../...../##.#./#.#.#/#.#.#/#.#.#/#.#.#",
    "n": "...../...../####./#...#/#...#/#...#/#...#",
    "o": "...../...../.###./#...#/#...#/#...#/.###.",
    "p": "...../...../####./#...#/####./#..../#....",
    "q": "...../...../.####/#...#/.####/....#/....#",
    "r": "...../...../#.##./##.../#..../#..../#....",
    "s": "...../...../.####/#..../.###./....#/####.",
    "t": ".#.../.#.../####./.#.../.#.../.#..#/..##.",
    "u": "...../...../#...#/#...#/#...#/#..##/.##.#",
    "v": "...../...../#...#/#...#/#...#/.#.#./..#..",
    "w": "...../...../#...#/#.#.#/#.#.#/#.#.#/.#.#.",
    "x": "...../...../#...#/.#.#./..#../.#.#./#...#",
    "y": "...../...../#...#/#...#/.####/....#/.###.",
    "z": "...../...../#####/...#./..#../.#.../#####",
    "{": "...##/..#../..#../.#.../..#../..#../...##",
    "|": "..#../..#../..#../..#../..#../..#../..#..",
    "}": "##.../..#../..#../...#./..#../..#../##...",
    "~": "...../...../.##.#/#..#./...../...../.....",
    # extra glyphs required by the UI
    "→": "...../..#../...#./#####/...#./..#../.....",   # arrow right
    "›": "...../.#.../..#../...#./..#../.#.../.....",   # >
    "‹": "...../...#./..#../.#.../..#../...#./.....",   # <
    "●": "...../.###./#####/#####/#####/.###./.....",   # filled dot
    "○": "...../.###./#...#/#...#/#...#/.###./.....",   # hollow dot
    "▮": ".###./.###./.###./.###./.###./.###./.###.",   # block marker
    "—": "...../...../...../#####/...../...../.....",   # em dash
    "↳": "...../.#.../.#.../.#.#./.####/...#./.....",   # down-right arrow
    "·": "...../...../.##../.##../...../...../.....",   # middle dot
    "×": "...../#...#/.#.#./..#../.#.#./#...#/.....",   # multiply
    "█": "#####/#####/#####/#####/#####/#####/#####",   # full block
    "▓": "#.#.#/.#.#./#.#.#/.#.#./#.#.#/.#.#./#.#.#",   # shade
}

# ---------------------------------------------------------------------------
# 8x12 large font. Stored as (top_row_offset, rows). Rows may be narrower
# than 8; the remainder of the cell is paper. Advance is always 8 (monospace).
# ---------------------------------------------------------------------------

_LARGE_SRC: Dict[str, Tuple[int, str]] = {
    " ": (0, ""),
    "!": (1, "..##/..##/..##/..##/..##/..##/..##/..../..##/..##"),
    '"': (1, ".##.##/.##.##/.#..#."),
    "#": (2, ".#..#./.#..#./######/.#..#./.#..#./######/.#..#./.#..#."),
    "$": (1, "...#../.#####/#..#../#..#../.####./...#.#/...#.#/#####./...#../...#.."),
    "%": (1, "##...#/##..##/...###/..###./.###../###.../##..../#...##/#...##"),
    "&": (1, ".###../#...#./#...#./.###../.##.../#..#.#/#...##/#....#/#...#./.###.#"),
    "'": (1, "..##../..##../...#.."),
    "(": (1, "...##./..#.../.#..../.#..../#...../#...../.#..../.#..../..#.../...##."),
    ")": (1, ".##.../...#../....#./....#./.....#/.....#/....#./....#./...#../.##..."),
    "*": (3, "#.#.#/.###./#####/.###./#.#.#"),
    "+": (3, "...#.../...#.../...#.../#######/...#.../...#.../...#..."),
    ",": (9, ".##/.##/#.."),
    "-": (6, "######"),
    ".": (9, "##/##"),
    "/": (1, ".....#/.....#/....#./....#./...#../..#.../..#.../.#..../#...../#....."),
    "0": (1, ".#####./#######/##...##/##..###/##.####/####.##/###..##/##...##/#######/.#####."),
    "1": (1, "..##../.###../####../..##../..##../..##../..##../..##../######/######"),
    "2": (1, ".#####./#######/##...##/.....##/....###/...###./..###../.###.../#######/#######"),
    "3": (1, ".######/.######/....##./...##../..####./..#####/.....##/##...##/#######/.#####."),
    "4": (1, "##..##./##..##./##..##./##..##./##..##./#######/#######/....##./....##./....##."),
    "5": (1, "#######/#######/##...../##...../######./.######/.....##/##...##/#######/.#####."),
    "6": (1, ".#####./#######/##...##/##...../######./#######/##...##/##...##/#######/.#####."),
    "7": (1, "#######/#######/....###/...###./..###../.###.../.##..../.##..../.##..../.##...."),
    "8": (1, ".#####./#######/##...##/##...##/.#####./#######/##...##/##...##/#######/.#####."),
    "9": (1, ".#####./#######/##...##/##...##/#######/.######/.....##/##...##/#######/.#####."),
    ":": (3, "##/##/../../../../##/##"),
    ";": (3, "##/##/../../../../##/##/#."),
    "<": (2, "...#/..#./.#../#.../#.../.#../..#./...#"),
    "=": (4, "######/....../....../....../######"),
    ">": (2, "#.../##../.##./..##/..##/.##./##../#..."),
    "?": (1, ".####./#....#/#....#/.....#/....#./...#.../...#.../....../...#.../...#..."),
    "@": (1, "..###../.#...#./#..#..#/#.#.#.#/#.#.#.#/#..###./#....../#....../.#....#/..####."),
    "A": (1, ".#####./#######/##...##/##...##/##...##/#######/#######/##...##/##...##/##...##"),
    "B": (1, "######./#######/##...##/##...##/######./#######/##...##/##...##/#######/######."),
    "C": (1, ".#####./#######/##...##/##...../##...../##...../##...../##...##/#######/.#####."),
    "D": (1, "#####../######./##..###/##...##/##...##/##...##/##...##/##..###/######./#####.."),
    "E": (1, "#######/#######/##...../##...../######./######./##...../##...../#######/#######"),
    "F": (1, "#######/#######/##...../##...../######./######./##...../##...../##...../##....."),
    "G": (1, ".#####./#######/##...../##...../##.####/##.####/##...##/##...##/#######/.#####."),
    "H": (1, "##...##/##...##/##...##/##...##/#######/#######/##...##/##...##/##...##/##...##"),
    "I": (1, "######/######/..##../..##../..##../..##../..##../..##../######/######"),
    "J": (1, "..######/..######/....##../....##../....##../....##../....##../##..##../######../.####..."),
    "K": (1, "##...##/##..###/##.###./#####../####.../####.../#####../##.###./##..###/##...##"),
    "L": (1, "##...../##...../##...../##...../##...../##...../##...../##...../#######/#######"),
    "M": (1, "##....##/###..###/########/########/##.##.##/##....##/##....##/##....##/##....##/##....##"),
    "N": (1, "##...##/##...##/###..##/####.##/#######/##.####/##..###/##...##/##...##/##...##"),
    "O": (1, ".#####./#######/##...##/##...##/##...##/##...##/##...##/##...##/#######/.#####."),
    "P": (1, "######./#######/##...##/##...##/#######/######./##...../##...../##...../##....."),
    "Q": (1, ".#####./#######/##...##/##...##/##...##/##.#..#/##.##.#/##.###./##..###/.###.##"),
    "R": (1, "######./#######/##...##/##...##/######./#######/##...##/##...##/##...##/##...##"),
    "S": (1, ".#####/######/##..../##..../#####./.#####/....##/....##/######/#####."),
    "T": (1, "########/########/...##.../...##.../...##.../...##.../...##.../...##.../...##.../...##..."),
    "U": (1, "##...##/##...##/##...##/##...##/##...##/##...##/##...##/##...##/#######/.#####."),
    "V": (1, "##....##/##....##/##....##/##....##/##....##/.##..##./.##..##./..####../..####../...##..."),
    "W": (1, "##....##/##....##/##....##/##....##/##....##/##.##.##/##.##.##/########/###..###/##....##"),
    "X": (1, "##....##/##....##/.##..##./..####../...##.../...##.../..####../.##..##./##....##/##....##"),
    "Y": (1, "##....##/###..###/.######./..####../...##.../...##.../...##.../...##.../...##.../...##..."),
    "Z": (1, "#######/#######/....###/...###./..###../.###.../###..../##...../#######/#######"),
    "[": (1, ".###/.#../.#../.#../.#../.#../.#../.#../.#../.###"),
    "\\": (1, "#...../#...../.#..../.#..../..#.../...#.../...#.../....#./.....#/.....#"),
    "]": (1, "###./..#./..#./..#./..#./..#./..#./..#./..#./###."),
    "^": (1, "...#.../..#.#../.#...#./#.....#"),
    "_": (11, "#######"),
    "`": (1, ".#../..#."),
    "a": (5, ".####./.....#/.#####/#....#/#...##/.###.#"),
    "b": (1, "#...../#...../#...../#...../#####./#....#/#....#/#....#/#....#/#####."),
    "c": (5, ".####./#....#/#...../#...../#....#/.####."),
    "d": (1, ".....#/.....#/.....#/.....#/.#####/#....#/#....#/#....#/#....#/.#####"),
    "e": (5, ".####./#....#/######/#...../#....#/.####."),
    "f": (1, "..###./.#..../.#..../.#..../#####./.#..../.#..../.#..../.#..../.#...."),
    "g": (5, ".#####/#....#/#....#/#....#/.#####/.....#/.####."),
    "h": (1, "#...../#...../#...../#...../#####./#....#/#....#/#....#/#....#/#....#"),
    "i": (1, "..#.../..#.../....../....../.##.../..#.../..#.../..#.../..#.../.####."),
    "j": (1, "....#./....#./....../....../...##./....#./....#./....#./....#./#...#./.###.."),
    "k": (1, "##..../##..../##..##/##.###/#####./####../#####./######/##..##/##..##"),
    "l": (1, ".##.../..#.../..#.../..#.../..#.../..#.../..#.../..#.../..#.../.####."),
    "m": (5, "##.##./#.#.#./#.#.#./#.#.#./#.#.#./#.#.#."),
    "n": (5, "#####./#....#/#....#/#....#/#....#/#....#"),
    "o": (5, ".####./#....#/#....#/#....#/#....#/.####."),
    "p": (5, "#####./#....#/#....#/#....#/#####./#...../#....."),
    "q": (5, ".#####/#....#/#....#/#....#/.#####/.....#/.....#"),
    "r": (5, "#.###./##..../#...../#...../#...../#....."),
    "s": (5, ".#####/#...../.####./.....#/.....#/#####."),
    "t": (2, "..#.../..#.../..#.../#####./..#.../..#.../..#.../..#..#/...##."),
    "u": (5, "#....#/#....#/#....#/#....#/#...##/.###.#"),
    "v": (5, "#....#/#....#/#....#/.#..#./.#..#./..##.."),
    "w": (5, "#....#/#....#/#.##.#/#.##.#/##..##/#....#"),
    "x": (5, "#....#/.#..#./..##../..##../.#..#./#....#"),
    "y": (5, "#....#/#....#/#....#/#....#/.#####/.....#/.####."),
    "z": (5, "######/....#./...#../..#.../.#..../######"),
    "{": (1, "...##/..#../..#../..#../.#.../.#.../..#../..#../..#../...##"),
    "|": (1, "#/#/#/#/#/#/#/#/#/#/#"),
    "}": (1, "##.../..#../..#../..#../...#./...#./..#../..#../..#../##..."),
    "~": (5, ".##...#/#..#.#./....##."),
    "→": (4, "....#.../.....#../########/.....#../....#..."),
    "›": (1, "#..../##.../.##../..##./...##/...##/..##./.##../##.../#...."),
    "‹": (1, "....#/...##/..##./.##../##.../##.../.##../..##./...##/....#"),
    "●": (3, "..###../.#####./#######/#######/#######/.#####./..###.."),
    "○": (3, "..###../.#...#./#.....#/#.....#/#.....#/.#...#./..###.."),
    "▮": (1, "#####/#####/#####/#####/#####/#####/#####/#####/#####/#####"),
    "—": (6, "#######"),
    "↳": (3, "#....../#....../#...#../#....#./#######/#....#./#...#.."),
    "·": (6, "##/##"),
    "×": (4, "#...#/.#.#./..#../.#.#./#...#"),
    "█": (0, "########/########/########/########/########/########/########/########/########/########/########/########"),
    "▓": (0, "#.#.#.#./.#.#.#.#/#.#.#.#./.#.#.#.#/#.#.#.#./.#.#.#.#/#.#.#.#./.#.#.#.#/#.#.#.#./.#.#.#.#/#.#.#.#./.#.#.#.#"),
}


def _compile_small() -> Dict[str, np.ndarray]:
    out = {}
    for ch, spec in _SMALL_SRC.items():
        rows = _rows(spec)
        assert len(rows) == 7, ch
        bits = np.zeros((8, 6), dtype=bool)
        for y, row in enumerate(rows):
            assert len(row) == 5, (ch, row)
            for x, c in enumerate(row):
                if c == "#":
                    bits[y, x] = True
        out[ch] = bits
    return out


def _compile_large() -> Dict[str, np.ndarray]:
    out = {}
    for ch, (top, spec) in _LARGE_SRC.items():
        bits = np.zeros((12, 8), dtype=bool)
        rows = _rows(spec) if spec else []
        for i, row in enumerate(rows):
            y = top + i
            if y >= 12:
                break
            for x, c in enumerate(row[:8]):
                if c == "#":
                    bits[y, x] = True
        out[ch] = bits
    return out


_GLYPHS = {SMALL: _compile_small(), LARGE: _compile_large()}
_FALLBACK = "?"


def glyph(ch: str, size: str = SMALL) -> np.ndarray:
    table = _GLYPHS[size]
    return table.get(ch) if ch in table else table[_FALLBACK]


def advance(ch: str, size: str = SMALL) -> int:
    """Horizontal step after `ch`, in pixels."""
    if ch in TABULAR:
        tab = _TABULAR_SRC[size]
        return METRICS[size][2] if tab is None else tab
    return _ADVANCE_SRC[size].get(ch, METRICS[size][2])


def text_width(text: str, size: str = SMALL) -> int:
    return sum(advance(ch, size) for ch in text)


def line_height(size: str = SMALL) -> int:
    return METRICS[size][3]


def draw_text(fb: np.ndarray, x: int, y: int, text: str, color, size: str = SMALL) -> int:
    """Blit `text` at (x, y) top-left. Returns the x after the last glyph.

    No alpha, no AA: ink pixels are written, paper pixels are left alone.
    """
    h, w = fb.shape[0], fb.shape[1]
    col = np.array(color, dtype=np.uint8)
    cx = int(x)
    for ch in text:
        bits = glyph(ch, size)
        gh, gw = bits.shape
        # clip
        y0, y1 = max(0, y), min(h, y + gh)
        x0, x1 = max(0, cx), min(w, cx + gw)
        if y1 > y0 and x1 > x0:
            sub = bits[y0 - y:y1 - y, x0 - cx:x1 - cx]
            fb[y0:y1, x0:x1][sub] = col
        cx += advance(ch, size)
    return cx


def draw_text_scaled(fb: np.ndarray, x: int, y: int, text: str, color,
                     size: str = SMALL, scale: int = 2) -> int:
    """Same blit, every ink pixel replicated `scale`x in both axes.

    Pixel replication keeps the result on the pixel grid: no interpolation,
    no anti-aliasing (ui-aesthetic §1).
    """
    scale = max(1, int(scale))
    h, w = fb.shape[0], fb.shape[1]
    col = np.array(color, dtype=np.uint8)
    cx = int(x)
    for ch in text:
        bits = glyph(ch, size)
        big = np.repeat(np.repeat(bits, scale, axis=0), scale, axis=1)
        gh, gw = big.shape
        y0, y1 = max(0, y), min(h, y + gh)
        x0, x1 = max(0, cx), min(w, cx + gw)
        if y1 > y0 and x1 > x0:
            sub = big[y0 - y:y1 - y, x0 - cx:x1 - cx]
            fb[y0:y1, x0:x1][sub] = col
        cx += advance(ch, size) * scale
    return cx


# --- live editing (tools/edit_server.py) ----------------------------------
# `_GLYPHS` is read on every blit, so replacing an entry there changes the
# next rendered frame. Editing works on the padded cell (8x6 small, 12x8
# large); serialisation back to source trims to the authored shape.

CELL = {SMALL: (8, 6), LARGE: (12, 8)}      # (rows, cols) of the edit grid
INK = {SMALL: (7, 5), LARGE: (12, 8)}       # (rows, cols) the source may use


def charset(size: str = SMALL):
    """Every authored character for `size`, in source order."""
    src = _SMALL_SRC if size == SMALL else _LARGE_SRC
    return list(src.keys())


def get_grid(ch: str, size: str = SMALL) -> List[List[int]]:
    """The glyph as a row-major list of 0/1, sized CELL[size]."""
    rows, cols = CELL[size]
    bits = _GLYPHS[size].get(ch)
    if bits is None:
        return [[0] * cols for _ in range(rows)]
    return [[int(v) for v in row] for row in bits]


def set_grid(ch: str, size: str, grid) -> None:
    """Replace one glyph. Visible on the next rendered frame."""
    if size not in _GLYPHS:
        raise KeyError(size)
    rows, cols = CELL[size]
    bits = np.zeros((rows, cols), dtype=bool)
    for y, row in enumerate(grid[:rows]):
        for x, v in enumerate(row[:cols]):
            bits[y, x] = bool(v)
    _GLYPHS[size][ch] = bits


def serialize_small(bits: np.ndarray) -> str:
    """7 rows x 5 cols of '#'/'.' — the _SMALL_SRC form."""
    rows = []
    for y in range(7):
        rows.append("".join("#" if bits[y, x] else "." for x in range(5)))
    return "/".join(rows)


def serialize_large(bits: np.ndarray) -> Tuple[int, str]:
    """(top, spec) — the _LARGE_SRC form: leading blank rows become `top`,
    trailing blank rows and unused right-hand columns are dropped."""
    used = [y for y in range(12) if bits[y, :8].any()]
    if not used:
        return 0, ""
    top, bottom = used[0], used[-1]
    width = max((x + 1) for y in range(12) for x in range(8) if bits[y, x])
    rows = []
    for y in range(top, bottom + 1):
        rows.append("".join("#" if bits[y, x] else "." for x in range(width)))
    return top, "/".join(rows)


def snapshot():
    """{size: {ch: grid}} for everything currently loaded."""
    return {size: {ch: get_grid(ch, size) for ch in _GLYPHS[size]}
            for size in (SMALL, LARGE)}


# Pristine copy taken at import: writeback rewrites only what actually
# changed, so untouched source lines stay byte-identical.
_ORIGINAL = {size: {ch: bits.copy() for ch, bits in table.items()}
             for size, table in _GLYPHS.items()}


def dirty(size: str = None):
    """[(size, ch)] for every glyph whose bits differ from the source file."""
    sizes = (SMALL, LARGE) if size is None else (size,)
    out = []
    for s in sizes:
        for ch, bits in _GLYPHS[s].items():
            if not np.array_equal(bits, _ORIGINAL[s][ch]):
                out.append((s, ch))
    return out


def mark_clean() -> None:
    """Call after a successful writeback."""
    for s, table in _GLYPHS.items():
        for ch, bits in table.items():
            _ORIGINAL[s][ch] = bits.copy()


def revert(ch: str, size: str) -> None:
    _GLYPHS[size][ch] = _ORIGINAL[size][ch].copy()


# --- advance editing ------------------------------------------------------

def ink_width(ch: str, size: str = SMALL) -> int:
    """Columns actually inked, 0 for a blank glyph."""
    bits = _GLYPHS[size].get(ch)
    if bits is None or not bits.any():
        return 0
    return int(max(x + 1 for x in range(bits.shape[1]) if bits[:, x].any()))


def fitted_advance(ch: str, size: str = SMALL, tracking: int = 1) -> int:
    """ink + tracking — the working rule for a pixel font. Blank glyphs
    (space) have no ink to measure, so they keep whatever they have."""
    ink = ink_width(ch, size)
    return advance(ch, size) if ink == 0 else ink + tracking


def set_advance(ch: str, size: str, value) -> None:
    """Override one glyph's advance; None clears it back to the default.
    A digit is ignored here — digits move together, see set_tabular()."""
    if ch in TABULAR:
        return
    table = _ADVANCE_SRC[size]
    if value is None:
        table.pop(ch, None)
        return
    value = int(value)
    if not 1 <= value <= 24:
        raise ValueError(f"advance {value} out of range 1..24")
    if value == METRICS[size][2]:
        table.pop(ch, None)          # equal to default = no override stored
    else:
        table[ch] = value


def set_tabular(size: str, value) -> None:
    """The shared advance for 0-9. None = the METRICS default."""
    if value is not None:
        value = int(value)
        if not 1 <= value <= 24:
            raise ValueError(f"advance {value} out of range 1..24")
        if value == METRICS[size][2]:
            value = None
    _TABULAR_SRC[size] = value


def autofit(size: str, tracking: int = 1, only=None) -> int:
    """Set every glyph's advance from its ink. Returns how many moved."""
    moved = 0
    for ch in charset(size):
        if only is not None and ch not in only:
            continue
        if ch in TABULAR:
            continue
        want = fitted_advance(ch, size, tracking)
        if want != advance(ch, size):
            set_advance(ch, size, want)
            moved += 1
    if only is None or any(c in TABULAR for c in only):
        # digits move as one: the widest digit sets the column
        widest = max((ink_width(c, size) for c in TABULAR
                      if c in _GLYPHS[size]), default=0)
        if widest:
            set_tabular(size, widest + tracking)
            moved += 1
    return moved


def advances(size: str = SMALL) -> Dict[str, int]:
    """Effective advance for every authored character."""
    return {ch: advance(ch, size) for ch in charset(size)}


_ADVANCE_ORIGINAL = {s: dict(t) for s, t in _ADVANCE_SRC.items()}
_TABULAR_ORIGINAL = dict(_TABULAR_SRC)


def spacing_dirty() -> bool:
    return (_ADVANCE_SRC != _ADVANCE_ORIGINAL
            or _TABULAR_SRC != _TABULAR_ORIGINAL)


def mark_spacing_clean() -> None:
    for s, t in _ADVANCE_SRC.items():
        _ADVANCE_ORIGINAL[s] = dict(t)
    _TABULAR_ORIGINAL.update(_TABULAR_SRC)


def revert_spacing() -> None:
    for s in _ADVANCE_SRC:
        _ADVANCE_SRC[s] = dict(_ADVANCE_ORIGINAL[s])
    _TABULAR_SRC.update(_TABULAR_ORIGINAL)
