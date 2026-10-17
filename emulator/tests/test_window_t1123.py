"""#1123, the desktop window's rows (findings 2026-10-06-m-emu-ui-drivers.md):
row 1, the zoom pane placed by the crop's origin; row 3, the dirty-rect list
that grew every frame with an alpha photo; row 9, the encoder drag that sent
-1 for a 1 px jitter down."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.environ["SDL_VIDEODRIVER"] = "dummy"

import numpy as np                                         # noqa: E402
import pygame                                              # noqa: E402

import chrome as C                                         # noqa: E402
import main as M                                           # noqa: E402


def _window():
    pygame.init()
    pygame.display.set_mode((64, 64))


class TestZoomPaneCropped(unittest.TestCase):
    """Row 1: on a cropped window the pane must sit inside the window."""

    def _pane(self, view):
        """The pane as drawn: what turning the zoom on changes in the window.

        The pane is drawn over a 4 px WELL frame, so the changed area is the
        pane grown by 4 on each side, even where it lies over the LCD."""
        dest = pygame.Surface(view.size)
        fb = np.zeros((C.FB_H, C.FB_W, 3), np.uint8)
        fb[:] = (1, 2, 3)
        view.draw(dest, fb)
        before = pygame.surfarray.array3d(dest)
        view.toggle_zoom()
        view.draw(dest, fb)
        xs, ys = np.nonzero((pygame.surfarray.array3d(dest) != before).any(axis=2))
        self.assertTrue(len(xs), "no pane drawn in the window")
        drawn = pygame.Rect(xs.min(), ys.min(),
                            xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
        # whole: a pane cut by the window's edge shows a smaller frame
        zw, zh = view.zoom_rect[2:]
        self.assertEqual(drawn.size, (zw + 8, zh + 8), "pane cut off")
        return drawn.inflate(-8, -8)

    def test_pane_inside_the_window_and_clear_of_the_lcd(self):
        _window()
        # the MacBook's numbers from the finding
        view = C.ChromeView(max_width=1710, max_size=(3420, 2224), device=2.0)
        self.assertLess(view.photo_at[0], 0, "window should be cropping")
        pane = self._pane(view)
        win = pygame.Rect(0, 0, *view.size)
        self.assertTrue(win.contains(pane), "%s outside %s" % (pane, win))
        lcd = pygame.Rect(view.shift(view.fb_rect))
        self.assertFalse(pane.colliderect(lcd), "pane covers the LCD")

    def test_pane_inside_the_window_at_2x(self):
        _window()
        view = C.ChromeView(max_width=1710, max_size=(3420, 2224), device=2.0)
        view.set_fb_multiple(2)
        pane = self._pane(view)
        self.assertTrue(pygame.Rect(0, 0, *view.size).contains(pane))

    def test_uncropped_pane_unchanged(self):
        """With no crop the origin is (0, 0): the pane stays where it was."""
        _window()
        view = C.ChromeView(max_width=1400)
        self.assertEqual(view.photo_at, (0, 0))
        self.assertEqual(tuple(self._pane(view)), tuple(view.zoom_rect))


class TestAlphaDirty(unittest.TestCase):
    """Row 3: with an alpha photo the repaint list must not grow per frame."""

    def test_dirty_list_bounded(self):
        _window()
        view = C.ChromeView(max_width=800)
        view.has_alpha = True            # the probe's way: the photo is a JPEG
        view.toggle_zoom()
        view.press("SYNTH")
        dest = pygame.Surface(view.size)
        fb = np.zeros((C.FB_H, C.FB_W, 3), np.uint8)
        for _ in range(3):
            view.draw(dest, fb)
        per_frame = len(view._dirty)
        for _ in range(100):
            view.draw(dest, fb)
        self.assertEqual(len(view._dirty), per_frame)
        self.assertLessEqual(per_frame, 4)


class TestDragSteps(unittest.TestCase):
    """Row 9: a drag counts the same both ways, ENC_DRAG_PX per step."""

    def test_symmetric(self):
        px = M.ENC_DRAG_PX
        self.assertEqual(px, 6)
        for d in range(0, px):
            self.assertEqual(M.drag_steps(100, 100 + d), 0, "down %d px" % d)
            self.assertEqual(M.drag_steps(100, 100 - d), 0, "up %d px" % d)
        self.assertEqual(M.drag_steps(100, 100 - px), 1)
        self.assertEqual(M.drag_steps(100, 100 + px), -1)
        self.assertEqual(M.drag_steps(100, 100 + 2 * px + 1), -2)
        self.assertEqual(M.drag_steps(100, 100 - 2 * px - 1), 2)


class TestEncoderPushes(unittest.TestCase):
    """#1180 (t818 row 4): Ctrl/Cmd + 1-4 and a still click reach the App as
    ENCn_PUSH, press then release."""

    class _App:
        def __init__(self):
            self.got = []

        def on_key(self, key, down):
            self.got.append((key, down))

    def setUp(self):
        self.app = self._App()
        self.p = M.EncoderPushes(self.app)

    def test_ctrl_digit_presses_and_its_release_lets_go(self):
        for i, mod in enumerate((pygame.KMOD_CTRL, pygame.KMOD_META) * 2):
            key = pygame.K_1 + i
            self.assertTrue(self.p.key(key, True, mod))
            # the modifier let go first: the digit still releases the push
            self.assertTrue(self.p.key(key, False, 0))
            self.assertEqual(self.app.got[-2:], [(M.ENC_PUSH_KEYS[i], True),
                                                 (M.ENC_PUSH_KEYS[i], False)])

    def test_a_bare_digit_is_not_a_push(self):
        self.assertFalse(self.p.key(pygame.K_2, True, 0))
        self.assertFalse(self.p.key(pygame.K_2, False, 0))
        self.assertFalse(self.p.key(pygame.K_5, True, pygame.KMOD_CTRL))
        self.assertEqual(self.app.got, [])

    def test_a_still_click_is_a_push(self):
        self.p.click(2)
        self.assertEqual(self.app.got, [(M.ENC_PUSH_KEYS[2], True),
                                        (M.ENC_PUSH_KEYS[2], False)])


if __name__ == "__main__":
    unittest.main()
