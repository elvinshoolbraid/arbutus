#!/usr/bin/env python3
"""Light and dark side by side: one contact sheet of verify shots (#334).

    venv/bin/python tools/verify.py --all --out /tmp/light
    venv/bin/python tools/verify.py --all --appearance dark --out /tmp/dark
    venv/bin/python tools/contact_sheet.py /tmp/light /tmp/dark out.png \\
        synth-page/01-engine track-record/04-clip-on-trk1 ...

Each entry is `<feature>/<shot>` under both directories. A row holds two
entries, each drawn light then dark at an integer scale (`--scale`, default 2)
with its name above. Nearest-neighbour only: the sheet is the pixels.
"""

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ui import draw                                            # noqa: E402
from ui.font import SMALL                                      # noqa: E402

W, H = 320, 240
GAP = 8
LABEL = 12
# the sheet around the shots: not device colours, so not ui/palette.py (the
# chrome keeps its own guides for the same reason)
SHEET_BG = (40, 40, 40)
LABEL_FG = (230, 230, 230)


def _read(path):
    import pygame                                              # noqa: PLC0415
    surf = pygame.image.load(path)
    return np.transpose(pygame.surfarray.array3d(surf), (1, 0, 2)).copy()


def build(light_dir, dark_dir, entries, scale=2, per_row=2):
    cell_w = 2 * W * scale + GAP
    cols = per_row
    rows = (len(entries) + cols - 1) // cols
    sheet_w = cols * cell_w + (cols + 1) * GAP
    row_h = LABEL * scale + H * scale + GAP
    sheet = np.zeros((rows * row_h + GAP, sheet_w, 3), dtype=np.uint8)
    sheet[:] = SHEET_BG
    for k, entry in enumerate(entries):
        r, c = divmod(k, cols)
        x0 = GAP + c * (cell_w + GAP)
        y0 = GAP + r * row_h
        label = np.zeros((LABEL, (2 * W * scale + GAP) // scale, 3), dtype=np.uint8)
        label[:] = SHEET_BG
        draw.text(label, 0, 2, entry + "   (light | dark)", LABEL_FG, SMALL)
        big = label.repeat(scale, 0).repeat(scale, 1)
        sheet[y0:y0 + big.shape[0], x0:x0 + big.shape[1]] = big
        for j, root in enumerate((light_dir, dark_dir)):
            img = _read(os.path.join(root, entry + ".png"))
            # a chrome shot is the whole window: every n-th pixel, to fit
            step = max(-(-img.shape[0] // H), -(-img.shape[1] // W))
            img = img[::step, ::step].repeat(scale, 0).repeat(scale, 1)
            x = x0 + j * (W * scale + GAP)
            y = y0 + LABEL * scale
            sheet[y:y + img.shape[0], x:x + img.shape[1]] = img
    return sheet


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("light")
    ap.add_argument("dark")
    ap.add_argument("out")
    ap.add_argument("entries", nargs="+", help="<feature>/<shot>")
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--per-row", type=int, default=2)
    a = ap.parse_args(argv)
    import pygame                                              # noqa: PLC0415
    sheet = build(a.light, a.dark, a.entries, a.scale, a.per_row)
    surf = pygame.surfarray.make_surface(np.transpose(sheet, (1, 0, 2)))
    pygame.image.save(surf, a.out)
    print("%s: %dx%d, %d entries" % (a.out, sheet.shape[1], sheet.shape[0],
                                    len(a.entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
