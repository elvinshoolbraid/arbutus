"""Whole-frame renderer: fixed zones, then the page's main area.

Draw order: background tint, header breadcrumb, dynamic strip, param bar,
key strip, then page.render(main_rect). Overlays draw over main only.
"""

from typing import List, Optional, Tuple

import numpy as np

from . import draw, palette, zones
from .font import LARGE, SMALL


def _breadcrumb_text(app) -> str:
    return " › ".join(app.current_page.breadcrumb())


def _strip_text(app) -> str:
    tickers: List[Tuple[int, str, str]] = list(app.tickers())
    if not tickers:
        return ""
    tickers.sort(key=lambda t: t[0])
    if len(tickers) == 1:
        return tickers[0][1]
    return " · ".join(t[2] for t in tickers)


def _draw_header(fb, app):
    draw.fill(fb, zones.HEADER, palette.PANEL)
    inset = zones.HEADER_INSET
    txt = draw.clip_text(_breadcrumb_text(app), zones.WIDTH - 2 * inset, LARGE)
    draw.text(fb, inset, 2, txt, palette.INK, LARGE)
    draw.hline(fb, 0, zones.HEADER_H - 1, zones.WIDTH, palette.RULE)


def _draw_strip(fb, app):
    draw.fill(fb, zones.STRIP, palette.PANEL)
    txt = _strip_text(app)
    if txt:
        color = palette.RED if app.recording else palette.INK
        draw.text(fb, 3, zones.STRIP[1] + 2,
                  draw.clip_text(txt, zones.WIDTH - 6, SMALL), color, SMALL)
    draw.hline(fb, 0, zones.STRIP[1] + zones.STRIP_H - 1, zones.WIDTH, palette.RULE)


def _draw_param_cell(fb, col: int, slot, fallback, shifted: bool):
    """One cell of the param row: slot `col`, or slot `col + 4` in the brown
    SHIFT style while SHIFT is held (#1231); `fallback` is the plain slot
    under a SHIFT slot, for `↳ fine`."""
    x, y, w, h = zones.param_cell(col)
    slot_idx = col + (4 if shifted else 0)
    enc = palette.encoder_color(slot_idx)

    if shifted:
        draw.fill_rect(fb, x, y + 1, w - 1, h - 1, palette.SHIFT_HIGHLIGHT)

    # a ringed cell is one the current gesture is asking you to turn
    # (DDR-039 LIFT/DROP on the FX rack). Nothing else ever sets it.
    if getattr(slot, "highlight", False):
        draw.rect_outline(fb, x + 1, y + 2, w - 3, h - 3, palette.GREEN)

    dot = "○" if shifted else "●"
    label_color = palette.ACCENT_BROWN if shifted else enc
    y += 1                                  # under the bar's top rule

    if slot is None:
        # fine-tune fallback only where the slot below exists AND is tunable;
        # a DISCRETE slot (menu lists, mode pickers) has nothing to fine-tune.
        tunable = fallback is not None and not fallback.is_discrete()
        if shifted and tunable:
            draw.text(fb, x + 3, y + 3, "↳ fine", palette.ACCENT_BROWN, SMALL)
        else:
            draw.text(fb, x + 3, y + 3, "—", palette.DIM, SMALL)
        return

    dim = bool(getattr(slot, "dimmed", False))
    draw.text(fb, x + 2, y + 3, dot, palette.DIM if dim else enc, SMALL)
    name = draw.clip_text(slot.name, w - 12, SMALL)
    draw.text(fb, x + 9, y + 3, name, palette.DIM if dim else label_color, SMALL)
    val = draw.clip_text(slot.format_value(), w - 6, SMALL)
    draw.text(fb, x + 4, y + 13, val, palette.DIM if dim else palette.INK, SMALL)

    if not slot.is_discrete():
        bx, by, bw = x + 4, y + 24, w - 10
        draw.hline(fb, bx, by + 1, bw, palette.RULE)
        # clamped like the ghost: a value set past its range (`.value =`, a
        # hand-edited project) drew across the next cell (#1123)
        filled = int(round(max(0.0, min(1.0, slot.get_normalized())) * bw))
        if filled > 0:
            draw.fill_rect(fb, bx, by, filled, 3, enc)
        ghost = getattr(slot, "ghost", None)
        if ghost is not None:
            # `[TBD, Elvin's call]`: a one-pixel off-white rule is deliberately less
            # weight than the coloured knob bar, but remains legible at both
            # ends of the range.
            pos = int(round(max(0.0, min(1.0, float(ghost))) * (bw - 1)))
            draw.vline(fb, bx + pos, by - 1, 5, palette.INK)


def _draw_params(fb, app):
    draw.fill(fb, zones.PARAMS, palette.PANEL)
    draw.hline(fb, 0, zones.PARAMS[1], zones.WIDTH, palette.RULE)
    slots = app.current_page.slots()
    shifted = bool(app.shift_held)
    for col in range(4):
        if shifted:
            _draw_param_cell(fb, col, slots[col + 4], slots[col], True)
        else:
            _draw_param_cell(fb, col, slots[col], None, False)
    for col in range(1, 4):
        draw.vline(fb, col * zones.PARAM_CELL_W - 1, zones.PARAMS[1],
                   zones.PARAMS_H, palette.RULE)


def _draw_keystrip(fb, app):
    draw.fill(fb, zones.KEYSTRIP, palette.PANEL)
    draw.hline(fb, 0, zones.KEYSTRIP[1], zones.WIDTH, palette.RULE)
    page = app.current_page
    labels: List[Optional[str]] = page.strip_labels()
    active = page.active_strip_cell()
    red = getattr(page, "red_strip_cell", lambda: None)()
    prompt = getattr(app, "prompt", None)
    if prompt is not None:
        #: #821: a question's answers are on the keys it is asked with; a
        #: choice of two has no red key (D98)
        labels, active, red = list(prompt.choices), None, None
    for col in range(4):
        x, y, w, h = zones.key_cell(col)
        label = labels[col] if col < len(labels) else None
        if label is None:
            continue
        is_active = (active == col)
        is_red = (red == col)
        if is_red:
            # D98: the one next input, in the record red (#818)
            draw.fill_rect(fb, x, y + 1, w - 1, h - 1, palette.RED)
        elif is_active:
            draw.fill_rect(fb, x, y + 1, w - 1, h - 1, palette.INVERT_BG)
        # on red, the panel's own colour: cream in light mode, and in dark the
        # darker brown, 4.7:1 on the lifted red where INVERT_FG is 3.6 (#818)
        fg = palette.PANEL if is_red else (
            palette.INVERT_FG if is_active else palette.INK)
        # only the outermost cells touch a bezel corner
        left = zones.KEYSTRIP_INSET if col == 0 else 3
        right = zones.KEYSTRIP_INSET if col == 3 else 3
        txt = draw.clip_text(f"{col + 1} {label}", w - left - right, SMALL)
        draw.text(fb, x + left, y + 4, txt, fg, SMALL)
        if col:
            draw.vline(fb, x - 1, y, h, palette.RULE)


def _draw_help_overlay(fb, app):
    x, y, w, h = zones.MAIN
    draw.fill_rect(fb, x + 8, y + 6, w - 16, h - 12, palette.OVERLAY_BG)
    draw.rect_outline(fb, x + 8, y + 6, w - 16, h - 12, palette.INK)
    lines = app.help_lines()
    ty = y + 14
    for line in lines[:9]:
        draw.text(fb, x + 14, ty, draw.clip_text(line, w - 28, SMALL),
                  palette.INK, SMALL)
        ty += 10


def _draw_prompt(fb, app):
    """#821 item 7: a question over the main area, HELP's overlay shape; its
    first line red (the one thing on the screen asking for an answer)."""
    x, y, w, h = zones.MAIN
    draw.fill_rect(fb, x + 8, y + 6, w - 16, h - 12, palette.OVERLAY_BG)
    draw.rect_outline(fb, x + 8, y + 6, w - 16, h - 12, palette.INK)
    ty = y + 24
    for i, line in enumerate(app.prompt.lines[:6]):
        txt = draw.clip_text(line, w - 28, LARGE)
        draw.text(fb, x + 14, ty, txt, palette.RED if i == 0 else palette.INK,
                  LARGE)
        ty += 20


def render_frame(app, fb: Optional[np.ndarray] = None) -> np.ndarray:
    if fb is None:
        fb = draw.new_framebuffer(zones.WIDTH, zones.HEIGHT)
    page = app.current_page
    draw.fill_rect(fb, 0, 0, zones.WIDTH, zones.HEIGHT,
                   palette.GROUND)
    _draw_header(fb, app)
    _draw_strip(fb, app)
    page.render(fb, zones.MAIN, app)
    _draw_params(fb, app)
    _draw_keystrip(fb, app)
    if app.help_held:
        _draw_help_overlay(fb, app)
    elif getattr(app, "prompt", None) is not None:
        _draw_prompt(fb, app)
    return fb
