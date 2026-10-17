"""op1r's reverb screen, the Kali art, in OP1R-VERB's main zone (#1255).

A window onto the OP-1 engine's own screen, drawn as that engine draws it
(Elvin, 2026-10-15): anti-aliased and in op1r's colours, so this one screen is
outside ui-aesthetic's no-AA, palette-only rules (notes/ui-choices.md § #1255).
Everything around the window keeps them.

The art is op1r's `kalireverb.svg`, rasterised once by
`audio/engines/op1r/op1r-verb/screen/rasterise.py` into `kali.npz`: layers in
the SVG's order, each a premultiplied RGBA crop. `state()` is reverb.c's
`kali_screen_update` (lines 151-175 at the pinned commit) turned into which
groups are hidden and where each needle points; `draw()` lays the visible
layers over black. Missing npz (a tree without the art): `available()` is
False and the page draws its usual text.
"""

import json
import os
from typing import Dict, Optional, Sequence

import numpy as np

from .zones import Rect

NPZ = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "audio", "engines",
    "op1r", "op1r-verb", "screen", "kali.npz"))
W, H = 320, 160
LEVELS, MIX_LINES = 5, 10              # kali_geom.h

_layers: Optional[list] = None          # [(kind, name, x, y, pixels)], loaded once
_step = 16                             # the needle sprites' angle grid, from the npz
_last = None                           # (key, (H, W, 3) image): the knobs rarely move


def _load() -> list:
    global _layers, _step
    if _layers is None:
        _layers = []
        if os.path.exists(NPZ):
            with np.load(NPZ) as z:
                meta = json.loads(str(z["meta"]))
                for i, m in enumerate(meta["layers"]):
                    _layers.append((m["kind"], m["name"], m["x"], m["y"],
                                    z["L%02d" % i]))
                _step = meta["needle_step"]
    return _layers


def available() -> bool:
    return bool(_load())


def word(v: float) -> int:
    """A 0..1 knob as op1r's 0..32767 word: the adapter's knob_q15 (op1r_fx.hpp)."""
    return int(np.floor(min(max(v, 0.0), 1.0) * 32767.0 + 0.5))


def state(k: Sequence[int]) -> Dict:
    """reverb.c's kali_screen_update for knob words k (time, tone, diffusion, mix):
    the group ids it hides and each needle's angle in 8192ths of a turn.
    C's int division on non-negative ints is Python's //."""
    hidden = []
    arms = 1 + (k[0] * 5 + 16383) // 32767                       # 1..6
    hidden += ["karm%d" % (i + 1) for i in range(6) if not i < arms]
    lit = (k[3] * MIX_LINES + 16383) // 32767                    # 0..10, from the bottom
    hidden += ["kmix%d" % i for i in range(MIX_LINES) if not i >= MIX_LINES - lit]
    sk = (k[2] * (LEVELS - 1) + 16383) // 32767                  # 0..4: one level always shown
    fi = (k[1] * LEVELS + 16383) // 32767                        # 0 = none, 1..5
    hidden += ["kskull%d" % (i + 1) for i in range(LEVELS) if i != sk]
    hidden += ["kfire%d" % (i + 1) for i in range(LEVELS) if i != fi - 1]
    # needles: time blue (knob 0), diffusion white (knob 2), tone green (knob 1)
    angles = {n: k[i] * 4096 >> 15 for n, i in (("ktime", 0), ("kdiff", 2), ("ktone", 1))}
    return {"hidden": hidden, "angles": angles}


def _over(dst: np.ndarray, src: np.ndarray):
    """cairo's OVER of premultiplied `src` (h, w, 4) onto opaque `dst` (h, w, 3),
    in place, with pixman's rounded divide by 255."""
    t = dst.astype(np.uint32) * (255 - src[:, :, 3:4].astype(np.uint32)) + 128
    dst[:] = src[:, :, :3] + ((t + (t >> 8)) >> 8)


def render(k: Sequence[int]) -> np.ndarray:
    """The 320 x 160 screen for knob words k, cached on the state it draws."""
    global _last
    ls = _load()
    st = state(k)
    idx = {n: (a + _step // 2) // _step for n, a in st["angles"].items()}
    key = (tuple(st["hidden"]), tuple(sorted(idx.items())))
    if _last is not None and _last[0] == key:
        return _last[1]
    img = np.zeros((H, W, 3), np.uint8)
    hidden = set(st["hidden"])
    for kind, name, x, y, px in ls:
        if name in hidden:
            continue
        if kind == "needle":
            px = px[idx[name]]
        h, w = px.shape[:2]
        _over(img[y:y + h, x:x + w], px)
    _last = (key, img)
    return img


def draw_words(fb: np.ndarray, rect: Rect, k: Sequence[int]):
    x, y, w, h = rect
    fb[y:y + h, x:x + w] = render(k)[:h, :w]


def draw(fb: np.ndarray, rect: Rect, knobs: Sequence[float]):
    """The screen in `rect` (the 320 x 160 main zone) for 0..1 knobs."""
    draw_words(fb, rect, [word(v) for v in knobs])
