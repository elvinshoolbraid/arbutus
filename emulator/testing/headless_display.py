"""DisplayDriver that keeps the last frame and can write it to a PNG.

Uses pygame.image (already a dependency); falls back to Pillow if pygame is
missing, and to a minimal zlib PNG writer if neither is available.
"""

import os
import struct
import zlib
from typing import Optional

import numpy as np

from drivers.display import DisplayDriver

try:
    import pygame
except ImportError:                                   # pragma: no cover
    pygame = None

try:
    from PIL import Image
except ImportError:                                   # pragma: no cover
    Image = None


def _write_png_raw(path: str, pixels: np.ndarray):
    """Minimal RGB8 PNG writer — no dependencies."""
    h, w, _ = pixels.shape
    raw = b"".join(b"\x00" + pixels[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


class HeadlessDisplay(DisplayDriver):
    def __init__(self, width: int = 320, height: int = 240):
        self.width = width
        self.height = height
        self.last_frame: Optional[np.ndarray] = None
        self.frame_count = 0

    def initialize(self) -> bool:
        return True

    def update(self, pixels: np.ndarray):
        self.last_frame = np.array(pixels, dtype=np.uint8, copy=True)
        self.frame_count += 1

    def should_quit(self) -> bool:
        return False

    def cleanup(self):
        pass

    def save_png(self, path: str) -> str:
        if self.last_frame is None:
            raise RuntimeError("no frame captured")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        arr = np.ascontiguousarray(self.last_frame, dtype=np.uint8)
        if pygame is not None:
            surf = pygame.image.frombuffer(arr.tobytes(),
                                           (self.width, self.height), "RGB")
            pygame.image.save(surf, path)
        elif Image is not None:                       # pragma: no cover
            Image.fromarray(arr).save(path)
        else:                                         # pragma: no cover
            _write_png_raw(path, arr)
        return path
