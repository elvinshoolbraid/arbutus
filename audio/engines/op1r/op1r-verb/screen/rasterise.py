#!/usr/bin/env python3
"""op1r's reverb screen (the Kali art), rasterised once into layers (#1255).

    DYLD_FALLBACK_LIBRARY_PATH=$(brew --prefix cairo)/lib \\
        <venv with cairocffi + numpy>/bin/python rasterise.py [--check]

Reads op1r's own `kalireverb.svg` (audio/third_party/op1r-src, vendored unmodified) and draws
it with op1r's own SVG drawer, `harness/render.py`'s `draw_svg` (cairo, anti-aliased: the
drawer that made op1r's `reverb_preview.png`), at the OP-1's 1x, 320 x 160. Writes
`kali.npz` beside this file: the SVG's top-level elements in document order, cut into

  static  elements nothing switches, merged while consecutive
  toggle  one group the screen code shows or hides (karm1..6, kskull1..5, kfire1..5, kmix0..9)
  needle  ktime / kdiff / ktone, one sprite per angle on a grid of NEEDLE_STEP (in op1r's
          8192-per-turn units), rotated as op1_node_rotate would (Q10 integer maths)

each a premultiplied RGBA crop with its origin. The emulator (`emulator/ui/kali.py`) lays
them over black with cairo's OVER, which is what drawing them in order would have done.
Nothing at runtime needs cairo; this script is run again only if op1r's art changes.

--check also draws each of render.py's six reverb states directly with cairo and prints how
far the layered result is from it (the rasterisation's own error, before any comparison
with the preview picture).
"""
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIO = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))
SDK = os.path.join(AUDIO, "third_party", "op1r-src", "synth_sdk")
SVG = os.path.join(SDK, "plugins", "reverb", "kalireverb.svg")
GEOM = os.path.join(SDK, "plugins", "reverb", "kali_geom.h")
OUT = os.path.join(HERE, "kali.npz")

sys.path.insert(0, os.path.join(SDK, "harness"))
import render                          # noqa: E402  op1r's drawer: draw_svg, cairo
cairo = render.cairo

W, H = 320, 160
NEEDLE_STEP = 4                        # 8192ths of a turn: 0.18 degree, 1025 sprites per needle (1: exact, 27 MB in memory)
NEEDLES = ("ktime", "kdiff", "ktone")
TOGGLE = re.compile(r"^k(arm[1-6]|skull[1-5]|fire[1-5]|mix\d)$")
SINE = [int(round(32767.0 * math.sin(2 * math.pi * i / 8192.0))) for i in range(8192)]


def pivots():
    """kali_geom.h's needle pivots, Q10, as reverb.c sees them: OP1_Q10(v) = (int32_t)(v * 1024)."""
    q = {}
    for name, v in re.findall(r"#define KALI_(\w+) OP1_Q10\(([\d.]+)\)", open(GEOM).read()):
        q[name] = int(float(v) * 1024)
    return {"ktime": (q["TIME_X"], q["TIME_Y"]), "kdiff": (q["DIFF_X"], q["DIFF_Y"]),
            "ktone": (q["TONE_X"], q["TONE_Y"])}


def rotation(cx, cy, a):
    """op1_node_rotate's matrix (op1_plugin.h) for angle a, as the 9 Q10 words render.py reads.
    `>>` on a negative int floors in Python as the arithmetic shift does in C."""
    s, c = SINE[a & 8191] >> 5, SINE[(a + 2048) & 8191] >> 5
    return [c, s, 0, -s, c, 0, cx - ((cx * c - cy * s) >> 10), cy - ((cx * s + cy * c) >> 10), 1024]


def surface():
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    return surf, cairo.Context(surf)


def pixels(surf):
    """cairo's ARGB32 (native-endian BGRA on every machine this runs on) -> premultiplied RGBA."""
    surf.flush()
    a = np.frombuffer(surf.get_data(), np.uint8).reshape(H, surf.get_stride() // 4, 4)[:, :W]
    return a[:, :, [2, 1, 0, 3]].copy()


def draw(elements, nodes=None):
    surf, ctx = surface()
    for el in elements:
        render.draw_svg(ctx, el, nodes or {})
    return pixels(surf)


def crop(rgba):
    ys, xs = np.nonzero(rgba[:, :, 3])
    if not len(ys):
        return 0, 0, rgba[:0, :0]
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return int(x0), int(y0), rgba[y0:y1, x0:x1]


def layers():
    """The SVG's top level as (kind, name, x, y, pixels) in document order."""
    root = ET.parse(SVG).getroot()
    piv = pivots()
    out, run = [], []

    def flush():
        if run:
            x, y, px = crop(draw(run))
            out.append(("static", "static%d" % len(out), x, y, px))
            run.clear()

    for el in root:
        nid = el.get("id") or ""
        if nid in NEEDLES:
            flush()
            frames = [draw([el], {nid: rotation(*piv[nid], a)})
                      for a in range(0, 4096 + 1, NEEDLE_STEP)]
            union = np.max([f[:, :, 3] for f in frames], axis=0)
            ys, xs = np.nonzero(union)
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            out.append(("needle", nid, int(x0), int(y0),
                        np.stack([f[y0:y1, x0:x1] for f in frames])))
        elif TOGGLE.match(nid):
            flush()
            x, y, px = crop(draw([el]))
            out.append(("toggle", nid, x, y, px))
        else:
            run.append(el)
    flush()
    return out


def write(ls):
    meta = [{"kind": k, "name": n, "x": x, "y": y} for k, n, x, y, _ in ls]
    arrays = {"L%02d" % i: px for i, (_, _, _, _, px) in enumerate(ls)}
    np.savez_compressed(OUT, meta=np.array(json.dumps(
        {"needle_step": NEEDLE_STEP, "layers": meta})), **arrays)
    print("->", os.path.relpath(OUT, AUDIO), "%d layers, %d bytes" % (len(ls), os.path.getsize(OUT)))


def check():
    """The layered screen (emulator/ui/kali.py) against cairo drawing the same state directly."""
    sys.path.insert(0, os.path.join(AUDIO, "..", "emulator"))
    from ui import kali                                  # noqa: E402
    root = ET.parse(SVG).getroot()
    piv = pivots()
    for label, knobs, *_ in render.FX_STATES_BY_NAME["reverb"]:
        words = knobs or (16000, 20000, 16384, 12000)    # reverb's plugin.json defaults
        st = kali.state(words)
        nodes = {n: rotation(*piv[n], st["angles"][n]) for n in NEEDLES}
        surf, ctx = surface()
        ctx.set_source_rgb(0, 0, 0)
        ctx.paint()
        render.draw_svg(ctx, root, nodes, set(st["hidden"]))
        direct = pixels(surf)[:, :, :3].astype(int)
        fb = np.zeros((H, W, 3), np.uint8)
        kali.draw_words(fb, (0, 0, W, H), words)
        d = np.abs(fb.astype(int) - direct)
        print("%-44s max %3d  mean %.4f  pixels off by >2: %d" % (label, d.max(), d.mean(), int((d.max(2) > 2).sum())))


if __name__ == "__main__":
    if "--check" not in sys.argv:
        write(layers())
    else:
        check()
