"""Drawing primitives on a numpy (240, 320, 3) uint8 framebuffer.

Flat fills and 1 px rules only. No alpha, no AA, no gradients (ui-aesthetic §1).
"""

from typing import Tuple

import numpy as np

from . import font as _font
from .zones import Rect

RGB = Tuple[int, int, int]

# --- C5b (#98): the render levers -----------------------------------------
# C5 (#78) profiled the UI on the CM4 and found five small changes that cut the
# frame from 52 % to 17 % of a core **without moving a pixel** (56,321 frames
# compared byte for byte). Three of them are here; the code as it was before
# them is `testing/render_reference.py`, and `tests/test_render_levers.py`
# holds each one to byte-equality with it.
#
# Both caches are keyed on *values*, so a palette rebind (`palette.set_color`)
# needs no invalidation — it simply asks for a different key. The text masks
# are not: they are built from `font.glyph` / `font.advance`, which the live
# look editor mutates, so `tools/edit_server.py` calls
# `invalidate_text_cache()` after every edit.

_ROW_CACHE_MAX = 256            # colour -> one (W, 3) row, ~1 kB each
_TEXT_CACHE_MAX = 4096          # (string, size) -> one ink mask
_COLOR_ROWS: dict = {}
_TEXT_MASKS: dict = {}


def _color_row(color: RGB, w: int) -> np.ndarray:
    """A (w, 3) row of `color`.

    NumPy broadcasting a (3,) value over an (h, w, 3) slice is a strided
    element-wise loop — a full 320x240 fill is ~0.5 ms — while assigning a
    (w, 3) row into the same slice is a memcpy per row. The row is cached
    because building it is the part that would cost.
    """
    key = tuple(color)
    row = _COLOR_ROWS.get(key)
    if row is None or row.shape[0] < w:
        if row is None and len(_COLOR_ROWS) >= _ROW_CACHE_MAX:
            _COLOR_ROWS.clear()
        row = np.tile(np.array(color, dtype=np.uint8), (max(w, 320), 1))
        _COLOR_ROWS[key] = row
    return row[:w]


def _text_mask(s: str, size: str):
    """(ink mask, total advance) for a whole string — exactly the union of the
    glyphs `font.draw_text` would blit, so one masked assignment replaces one
    clipped blit per character. Advances are >= 1 (`font.set_advance` enforces
    1..24), so every glyph lands at a non-negative offset in the mask."""
    key = (s, size)
    ent = _TEXT_MASKS.get(key)
    if ent is None:
        cx, mw, mh, parts = 0, 1, 1, []
        for ch in s:
            bits = _font.glyph(ch, size)
            parts.append((cx, bits))
            mw = max(mw, cx + bits.shape[1])
            mh = max(mh, bits.shape[0])
            cx += _font.advance(ch, size)
        mask = np.zeros((mh, mw), dtype=bool)
        for px, bits in parts:
            mask[:bits.shape[0], px:px + bits.shape[1]] |= bits
        if len(_TEXT_MASKS) >= _TEXT_CACHE_MAX:
            _TEXT_MASKS.clear()
        ent = _TEXT_MASKS[key] = (mask, cx)
    return ent


def invalidate_text_cache() -> None:
    """Drop the string masks. The live look editor (`run.sh --edit`) calls this
    after any glyph or advance change; nothing else mutates the font."""
    _TEXT_MASKS.clear()


def new_framebuffer(width: int = 320, height: int = 240) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def _clip(fb, x, y, w, h):
    H, W = fb.shape[0], fb.shape[1]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x) + int(w)), min(H, int(y) + int(h))
    return x0, y0, x1, y1


def fill_rect(fb: np.ndarray, x: int, y: int, w: int, h: int, color: RGB):
    x0, y0, x1, y1 = _clip(fb, x, y, w, h)
    if x1 > x0 and y1 > y0:
        fb[y0:y1, x0:x1] = _color_row(color, x1 - x0)


def fill(fb: np.ndarray, rect: Rect, color: RGB):
    fill_rect(fb, rect[0], rect[1], rect[2], rect[3], color)


def hline(fb: np.ndarray, x: int, y: int, w: int, color: RGB):
    fill_rect(fb, x, y, w, 1, color)


def vline(fb: np.ndarray, x: int, y: int, h: int, color: RGB):
    fill_rect(fb, x, y, 1, h, color)


def rect_outline(fb: np.ndarray, x: int, y: int, w: int, h: int, color: RGB):
    hline(fb, x, y, w, color)
    hline(fb, x, y + h - 1, w, color)
    vline(fb, x, y, h, color)
    vline(fb, x + w - 1, y, h, color)


def text(fb: np.ndarray, x: int, y: int, s: str, color: RGB, size: str = _font.SMALL) -> int:
    """Blit `s` at (x, y) top-left. Returns the x after the last glyph.

    One masked assignment from the cached ink mask, rather than
    `font.draw_text`'s clipped blit per character. `ui/font.py` is frozen
    (hard rule), so the cache lives here, around the call it replaces.
    """
    mask, adv = _text_mask(s, size)
    h, w = fb.shape[0], fb.shape[1]
    x = int(x)
    mh, mw = mask.shape
    y0, y1 = max(0, y), min(h, y + mh)
    x0, x1 = max(0, x), min(w, x + mw)
    if y1 > y0 and x1 > x0:
        fb[y0:y1, x0:x1][mask[y0 - y:y1 - y, x0 - x:x1 - x]] = color
    return x + adv


def text_centered(fb: np.ndarray, cx: int, y: int, s: str, color: RGB,
                  size: str = _font.SMALL) -> int:
    w = _font.text_width(s, size)
    return text(fb, cx - w // 2, y, s, color, size)


def text_width(s: str, size: str = _font.SMALL) -> int:
    return _font.text_width(s, size)


def clip_text(s: str, max_px: int, size: str = _font.SMALL) -> str:
    """Truncate so the string fits max_px. No ellipsis glyph juggling.

    Accumulates per-glyph advances rather than dividing by a fixed one — the
    font is proportional (font.py `_ADVANCE_SRC`).
    """
    total = 0
    for i, ch in enumerate(s):
        total += _font.advance(ch, size)
        if total > max_px:
            return s[:i]
    return s


def text_scaled(fb: np.ndarray, x: int, y: int, s: str, color: RGB,
                size: str = _font.SMALL, scale: int = 2) -> int:
    """Big text by integer pixel replication — no AA, no resampling."""
    return _font.draw_text_scaled(fb, x, y, s, color, size, int(scale))


def text_scaled_centered(fb: np.ndarray, cx: int, y: int, s: str, color: RGB,
                         size: str = _font.SMALL, scale: int = 2) -> int:
    w = _font.text_width(s, size) * int(scale)
    return text_scaled(fb, cx - w // 2, y, s, color, size, scale)


def scaled_width(s: str, size: str = _font.SMALL, scale: int = 2) -> int:
    return _font.text_width(s, size) * int(scale)


def plot(fb: np.ndarray, x: int, y: int, color: RGB):
    H, W = fb.shape[0], fb.shape[1]
    if 0 <= x < W and 0 <= y < H:
        fb[int(y), int(x)] = color


def polyline(fb: np.ndarray, points, color: RGB):
    """Integer Bresenham polyline — 1 px, no AA.

    The same walk as before C5b, but the points are collected and written with
    one fancy-index assignment instead of one `plot` call per pixel. Clipping
    is per point, exactly as `plot` did it, so a line that leaves the screen
    draws the same pixels it always drew.
    """
    H, W = fb.shape[0], fb.shape[1]
    xs: list = []
    ys: list = []
    for i in range(len(points) - 1):
        _line(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1],
              W, H, xs, ys)
    if xs:
        fb[ys, xs] = color


def _line(x0, y0, x1, y1, W, H, xs, ys):
    """Append the in-bounds pixels of one segment to `xs`/`ys`."""
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        if 0 <= x0 < W and 0 <= y0 < H:
            xs.append(x0)
            ys.append(y0)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def columns(fb: np.ndarray, x: int, top, length, color: RGB):
    """What `vline(fb, x + i, top[i], length[i], color)` for every i draws.

    One masked assignment instead of a `fill_rect` per pixel column — the shape
    both the waveform envelope and INPUT's level meter are made of. `top` and
    `length` are integer arrays; a column with `length <= 0` draws nothing,
    which is how a caller leaves a gap.
    """
    H, W = fb.shape[0], fb.shape[1]
    n = len(top)
    c0, c1 = max(0, x), min(W, x + n)
    if c1 <= c0:
        return
    top = top[c0 - x:c1 - x]
    length = length[c0 - x:c1 - x]
    rows = np.arange(H)[:, None]
    mask = (rows >= top[None, :]) & (rows < (top + length)[None, :])
    fb[:, c0:c1][mask] = color


def peaks(fb: np.ndarray, reply, x: int, y: int, w: int, h: int, color: RGB):
    """A `waveform` reply drawn as a filled envelope (engine-spec §15.6).

    `reply` carries `min` and `max` per bin, already reduced to the mono mix
    on the host's control thread — the samples never come over the socket, so
    this is the only shape a page ever draws a sample in.

    C5b: the same integer arithmetic as before, once over the whole width
    (`columns`) rather than a `vline` per column. `(i * n) // w` is
    `int(i * n / w)`: an exact quotient divides exactly in IEEE754 and every
    other lands in the same interval, and both floor for non-negative i.
    """
    lo = (reply or {}).get("min") or []
    hi = (reply or {}).get("max") or []
    n = min(len(lo), len(hi))
    if n == 0 or w <= 0 or h <= 1:
        return
    mid = y + h // 2
    half = h // 2 - 1
    b = np.minimum(n - 1, (np.arange(w) * n) // w)
    his = np.clip(np.asarray(hi[:n], dtype=float)[b], -1.0, 1.0)
    los = np.clip(np.asarray(lo[:n], dtype=float)[b], -1.0, 1.0)
    top = mid - (his * half).astype(np.int64)
    bot = mid - (los * half).astype(np.int64)
    columns(fb, x, np.minimum(top, bot), np.maximum(1, np.abs(bot - top)),
            color)
