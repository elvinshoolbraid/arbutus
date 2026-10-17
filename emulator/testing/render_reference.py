"""The render primitives as they were *before* C5b (#98), kept as the reference.

C5 (#78) measured where the UI's frame goes on the CM4 and found five small
changes to how the screen is drawn that cut it from 52 % to 17 % of a core
**without moving a pixel** (56,321 frames compared byte for byte). C5b landed
them in `ui/draw.py`, `pages/input.py` and `app/native_audio.py`.

A claim of "pixel-identical" is only worth something if there is still
something to compare against, so the old bodies live on here, verbatim, and
`tests/test_render_levers.py` holds each landed lever to byte-equality with
them. They are also what `arbutus_os/bench/c5_levers.py` patches back in on the
bench, so C5's A/B keeps measuring new code against old rather than against
itself.

Nothing in the emulator imports this module at run time.
"""

import numpy as np

from ui import font as _font
from ui import palette


# --- ui/draw.py -----------------------------------------------------------

def fill_rect(fb, x, y, w, h, color):
    H, W = fb.shape[0], fb.shape[1]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x) + int(w)), min(H, int(y) + int(h))
    if x1 > x0 and y1 > y0:
        fb[y0:y1, x0:x1] = np.array(color, dtype=np.uint8)


def plot(fb, x, y, color):
    H, W = fb.shape[0], fb.shape[1]
    if 0 <= x < W and 0 <= y < H:
        fb[int(y), int(x)] = np.array(color, dtype=np.uint8)


def vline(fb, x, y, h, color):
    fill_rect(fb, x, y, 1, h, color)


def polyline(fb, points, color):
    """Integer Bresenham polyline — 1 px, no AA."""
    for i in range(len(points) - 1):
        _line(fb, points[i][0], points[i][1],
              points[i + 1][0], points[i + 1][1], color)


def _line(fb, x0, y0, x1, y1, color):
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        plot(fb, x0, y0, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def peaks(fb, reply, x, y, w, h, color):
    """A `waveform` reply drawn as a filled envelope (engine-spec §15.6)."""
    lo = (reply or {}).get("min") or []
    hi = (reply or {}).get("max") or []
    n = min(len(lo), len(hi))
    if n == 0 or w <= 0 or h <= 1:
        return
    mid = y + h // 2
    half = h // 2 - 1
    for i in range(w):
        b = min(n - 1, int(i * n / w))
        top = mid - int(max(-1.0, min(1.0, float(hi[b]))) * half)
        bot = mid - int(max(-1.0, min(1.0, float(lo[b]))) * half)
        if bot < top:
            top, bot = bot, top
        vline(fb, x + i, top, max(1, bot - top), color)


# --- ui/font.py (called through draw.text; font.py itself is frozen) -------

def draw_text(fb, x, y, text, color, size=_font.SMALL):
    """One clipped blit per glyph. `ui/font.py` still holds this body — the
    lever is the mask cache `draw.text` wraps it in, so this is a copy of
    `font.draw_text` rather than something the landed code removed."""
    h, w = fb.shape[0], fb.shape[1]
    col = np.array(color, dtype=np.uint8)
    cx = int(x)
    for ch in text:
        bits = _font.glyph(ch, size)
        gh, gw = bits.shape
        y0, y1 = max(0, y), min(h, y + gh)
        x0, x1 = max(0, cx), min(w, cx + gw)
        if y1 > y0 and x1 > x0:
            sub = bits[y0 - y:y1 - y, x0 - cx:x1 - cx]
            fb[y0:y1, x0:x1][sub] = col
        cx += _font.advance(ch, size)
    return cx


# --- pages/input.py -------------------------------------------------------

def draw_meter(self, fb, mic, x, y, w, h):
    """`InputPage._draw_meter` before C5b — one `plot` or `vline` per column."""
    from ui import draw
    mid = y + h // 2
    if mic is None:
        draw.hline(fb, x, mid, w, palette.RULE)
        return
    hist = mic.meter
    n = len(hist)
    amp = h // 2 - 1
    for i in range(w):
        v = float(hist[int(i * n / max(1, w))]) if n else 0.0
        half = int(self._meter_scale(v) * amp)
        if half <= 0:
            plot(fb, x + i, mid, palette.RULE)
        else:
            vline(fb, x + i, mid - half, half * 2, palette.encoder_color(0))
    t = int(self._meter_scale(float(self.p_thresh.value)) * amp)
    col = palette.RED if mic.armed else palette.DIM
    for edge in (mid - t, mid + t):
        for i in range(x, x + w, 4):
            plot(fb, i, edge, col)
