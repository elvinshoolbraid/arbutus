#!/usr/bin/env python3
"""Interactive hotspot calibrator for the look-alike photo.

Opens `docs/design/reference/lookalike-model.jpg` (or a replacement PNG) and
walks the controls in control-inventory.md order, writing polygons in **photo
pixel** coordinates to `emulator/chrome/keymap.json`.

Per key:
    drag   — press at the key centre, drag out to a corner, release
             (rect mode, the default; a rectangle centred on the press point)
    4      — switch this key to 4-corner mode, then click its four corners
    c      — circle mode: press at the centre, drag out to the radius
             (default for ENC1..ENC4)
    SPACE  — keep whatever the current keymap already has and move on
    BKSP   — step back one key
    s      — save now (also happens on ESC / window close)
    ESC    — save and quit

Run:
    venv/bin/python tools/calibrate_keymap.py
    venv/bin/python tools/calibrate_keymap.py --only SYNTH
    venv/bin/python tools/calibrate_keymap.py --image path/to/panel.png
"""

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pygame                                            # noqa: E402

from app.keys import note_key                            # noqa: E402
from chrome import KEYMAP_PATH, PHOTO_PATH, load_keymap  # noqa: E402

#: control-inventory.md order: left block, page keys, command row, chromatics,
#: encoders. This is the order the calibrator prompts in.
WHITE_IDX = (0, 2, 4, 6, 7, 9, 11, 12, 14, 16, 18, 19, 21, 23)
BLACK_IDX = (1, 3, 5, 8, 10, 13, 15, 17, 20, 22)

ORDER = (
    ["TEMPO", "SLICE", "PLAY", "STOP", "REC", "LIFT", "SHIFT", "DROP"]
    + ["SYNTH", "DRUM", "TRACK", "MIXER", "SEQ", "INPUT", "HELP", "FILE"]
    + ["1", "2", "3", "4", "LEFT", "RIGHT", "A", "B", "C", "X", "Y", "Z"]
    + [note_key(n) for n in BLACK_IDX]
    + [note_key(n) for n in WHITE_IDX]
    + ["ENC1", "ENC2", "ENC3", "ENC4"]
)

CIRCLE_BY_DEFAULT = {"ENC1", "ENC2", "ENC3", "ENC4"}
CIRCLE_POINTS = 16

RED = (255, 60, 60)
YELLOW = (250, 230, 90)
CYAN = (90, 230, 240)


def rect_poly(cx, cy, dx, dy):
    dx, dy = max(6, abs(dx)), max(6, abs(dy))
    return [[int(cx - dx), int(cy - dy)], [int(cx + dx), int(cy - dy)],
            [int(cx + dx), int(cy + dy)], [int(cx - dx), int(cy + dy)]]


def circle_poly(cx, cy, r, n=CIRCLE_POINTS):
    r = max(6, r)
    return [[int(round(cx + r * math.cos(2 * math.pi * i / n))),
             int(round(cy + r * math.sin(2 * math.pi * i / n)))]
            for i in range(n)]


def save(keymap, path, image, size):
    with open(path, "w") as f:
        json.dump({"image": image, "image_size": list(size),
                   "hotspots": {k: [[int(a), int(b)] for a, b in v]
                                for k, v in keymap.items()}},
                  f, indent=1, sort_keys=True)
    print(f"wrote {path} ({len(keymap)} hotspots)")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=PHOTO_PATH)
    ap.add_argument("--out", default=KEYMAP_PATH)
    ap.add_argument("--only", default="", help="recalibrate one key only")
    ap.add_argument("--width", type=int, default=1500, help="window width")
    args = ap.parse_args(argv)

    try:
        keymap = {k: [list(p) for p in v] for k, v in load_keymap(args.out).items()}
    except OSError:
        keymap = {}

    todo = [args.only] if args.only else list(ORDER)
    if args.only and args.only not in ORDER:
        print(f"unknown key {args.only!r}; known: {', '.join(ORDER)}")
        return 2

    pygame.init()
    photo = pygame.image.load(args.image)
    pw, ph = photo.get_size()
    scale = min(1.0, args.width / pw)
    win = (int(pw * scale), int(ph * scale))
    screen = pygame.display.set_mode(win)
    pygame.display.set_caption("Arbutus keymap calibrator")
    view = pygame.transform.smoothscale(photo.convert(), win)
    font = pygame.font.SysFont("menlo", 18)
    big = pygame.font.SysFont("menlo", 30, bold=True)

    def to_photo(pos):
        return (pos[0] / scale, pos[1] / scale)

    idx = 0
    mode = None                    # None -> per-key default, else "rect"/"circle"/"corners"
    corners = []
    anchor = None
    dirty = False
    running = True

    while running and idx < len(todo):
        name = todo[idx]
        cur_mode = mode or ("circle" if name in CIRCLE_BY_DEFAULT else "rect")

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_SPACE:
                    idx += 1
                    mode, corners, anchor = None, [], None
                elif ev.key == pygame.K_BACKSPACE:
                    idx = max(0, idx - 1)
                    mode, corners, anchor = None, [], None
                elif ev.key == pygame.K_4:
                    mode, corners = "corners", []
                elif ev.key == pygame.K_c:
                    mode = "circle"
                elif ev.key == pygame.K_r:
                    mode = "rect"
                elif ev.key == pygame.K_s:
                    save(keymap, args.out, os.path.relpath(args.image, os.path.dirname(HERE)), (pw, ph))
                    dirty = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if cur_mode == "corners":
                    corners.append(list(to_photo(ev.pos)))
                    if len(corners) == 4:
                        keymap[name] = [[int(a), int(b)] for a, b in corners]
                        dirty = True
                        idx += 1
                        mode, corners = None, []
                else:
                    anchor = to_photo(ev.pos)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and anchor:
                px, py = to_photo(ev.pos)
                dx, dy = px - anchor[0], py - anchor[1]
                if cur_mode == "circle":
                    keymap[name] = circle_poly(anchor[0], anchor[1],
                                               math.hypot(dx, dy))
                else:
                    keymap[name] = rect_poly(anchor[0], anchor[1], dx, dy)
                dirty = True
                anchor = None
                idx += 1
                mode = None

        screen.blit(view, (0, 0))
        for n, poly in keymap.items():
            pts = [(int(a * scale), int(b * scale)) for a, b in poly]
            pygame.draw.polygon(screen, CYAN if n == name else RED, pts, 2)
        for c in corners:
            pygame.draw.circle(screen, YELLOW,
                               (int(c[0] * scale), int(c[1] * scale)), 5)
        if anchor:
            mx, my = pygame.mouse.get_pos()
            ax, ay = int(anchor[0] * scale), int(anchor[1] * scale)
            if cur_mode == "circle":
                pygame.draw.circle(screen, YELLOW, (ax, ay),
                                   max(2, int(math.hypot(mx - ax, my - ay))), 2)
            else:
                dx, dy = abs(mx - ax), abs(my - ay)
                pygame.draw.rect(screen, YELLOW,
                                 pygame.Rect(ax - dx, ay - dy, 2 * dx, 2 * dy), 2)

        hud = f"{idx + 1}/{len(todo)}  {name}   [{cur_mode}]"
        tip = "drag=centre+extent  4=corners  c=circle  r=rect  SPACE=skip  BKSP=back  s=save  ESC=quit"
        screen.blit(big.render(hud, True, YELLOW, (0, 0, 0)), (12, 10))
        screen.blit(font.render(tip, True, YELLOW, (0, 0, 0)), (12, 48))
        pygame.display.flip()
        pygame.time.Clock().tick(60)

    if dirty or idx >= len(todo):
        save(keymap, args.out,
             os.path.relpath(args.image, os.path.dirname(HERE)), (pw, ph))
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
