"""Fixed zone rectangles for the 320x240 panel (screen-sketches.md § Layout).

Heights never change between pages or states (DDR-032; one param row and a
160 px main since #1231: an OP-1 engine screen is 320 x 160).
Rect = (x, y, w, h).
"""

from typing import Tuple

Rect = Tuple[int, int, int, int]

WIDTH = 320
HEIGHT = 240

HEADER_H = 16
STRIP_H = 12
PARAMS_H = 36
KEYSTRIP_H = 16
MAIN_H = HEIGHT - HEADER_H - STRIP_H - PARAMS_H - KEYSTRIP_H   # 160

HEADER: Rect = (0, 0, WIDTH, HEADER_H)
STRIP: Rect = (0, HEADER_H, WIDTH, STRIP_H)
MAIN: Rect = (0, HEADER_H + STRIP_H, WIDTH, MAIN_H)
PARAMS: Rect = (0, HEADER_H + STRIP_H + MAIN_H, WIDTH, PARAMS_H)
KEYSTRIP: Rect = (0, HEIGHT - KEYSTRIP_H, WIDTH, KEYSTRIP_H)

# param bar internals: one row of 4 cells (#1231). It shows slots 0-3; while
# SHIFT is held, the same cells show slots 4-7.
PARAM_CELL_W = WIDTH // 4            # 80
KEY_CELL_W = WIDTH // 4              # 80


def param_cell(col: int) -> Rect:
    return (col * PARAM_CELL_W, PARAMS[1], PARAM_CELL_W, PARAMS_H)


def key_cell(col: int) -> Rect:
    return (col * KEY_CELL_W, KEYSTRIP[1], KEY_CELL_W, KEYSTRIP_H)


# The LCD bezel is rounded (3.5 mm = 19.5 px at 320x240), so the four corners
# of the panel are not addressable. These are the safe left/right insets for
# the two rows that run right into a corner; everything between them clears the
# arc on its own.
HEADER_INSET = 18          # top bar, both ends
KEYSTRIP_INSET = 14        # bottom index-key labels, outer ends only
