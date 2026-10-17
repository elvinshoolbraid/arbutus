"""The window, in device pixels.

macOS hands SDL a window measured in *points*. On the MacBook's scaled Retina
mode that is 1710x1112 against a 2560x1664 panel, so a 1x SDL window is
bilinearly upscaled by the compositor (~1.497x here): the photo goes soft and
the LCD's pixel grid is resampled by a non-integer factor.

SDL can open the window with a backing store at the panel's real resolution
(``SDL_WINDOW_ALLOW_HIGHDPI``), which pygame-ce exposes as
``pygame.Window(..., allow_high_dpi=True)``; ``get_surface()`` then hands back
a surface sized in *device* pixels. Everything in :mod:`chrome` is measured in
those, and only mouse positions (which SDL still reports in points) need
converting — :meth:`Window.to_px`.

Falls back to plain ``set_mode`` at ratio 1 when the flag is unavailable or the
video driver is the dummy one (headless verify runs, tests).
"""

import os
from typing import Tuple

import pygame


class Window:
    """A window whose ``surface`` is sized in device pixels.

    ``ratio`` is device px per point — 2.0 on Retina, 1.0 everywhere else.
    """

    def __init__(self, title: str, size_pt: Tuple[int, int],
                 hidden: bool = False):
        self._win = None
        self.ratio = 1.0
        w, h = max(1, int(size_pt[0])), max(1, int(size_pt[1]))

        if os.environ.get("SDL_VIDEODRIVER") != "dummy":
            try:
                self._win = pygame.Window(title, (w, h), allow_high_dpi=True,
                                          hidden=True)
                self.surface = self._win.get_surface()
                self.ratio = self.surface.get_width() / self._win.size[0]
                if not hidden:
                    self._win.show()
            except (AttributeError, TypeError, pygame.error):
                self._win = None          # upstream pygame, or no high-DPI

        if self._win is None:
            self.surface = pygame.display.set_mode((w, h))
            pygame.display.set_caption(title)

    def show(self):
        if self._win is not None:
            self._win.show()

    @property
    def size(self) -> Tuple[int, int]:
        """Surface size in device pixels."""
        return self.surface.get_size()

    def resize_px(self, size_px: Tuple[int, int]):
        """Resize to a device-pixel size, returning the size actually given."""
        w, h = max(1, int(size_px[0])), max(1, int(size_px[1]))
        if (w, h) == self.size:
            return self.size
        if self._win is not None:
            self._win.size = (max(1, round(w / self.ratio)),
                              max(1, round(h / self.ratio)))
            self.surface = self._win.get_surface()
        else:
            self.surface = pygame.display.set_mode((w, h))
        return self.size

    def to_px(self, pos) -> Tuple[float, float]:
        """SDL reports mouse positions in points; chrome works in device px."""
        return (pos[0] * self.ratio, pos[1] * self.ratio)

    def flip(self):
        if self._win is not None:
            self._win.flip()
        else:
            pygame.display.flip()
