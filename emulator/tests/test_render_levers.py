"""C5b (#98): every render lever, byte-equal against the code it replaced.

C5 (#78) found five changes to the UI's render that cut it from 52 % to 17 %
of a CM4 core **without moving a pixel**, and proved it by comparing 56,321
frames drawn both ways on the bench. That proof lives on a Pi and in a log. It
is re-made here as a unit test: `testing/render_reference.py` holds the old
bodies verbatim, and each case draws the same call both ways into two
framebuffers and asserts `np.array_equal`.

The last case is the **negative control** — the same comparison against a
deliberately wrong primitive, so "these frames are equal" is a test that has
been seen to fail (`docs/design/audit-2026-09-18.md`, S15/#56).
"""

import os
import random
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from testing import render_reference as REF                  # noqa: E402
from ui import draw, font, palette                           # noqa: E402


def fb():
    return np.zeros((240, 320, 3), dtype=np.uint8)


COLORS = [(0, 0, 0), (255, 255, 255), palette.INK, palette.RED,
          palette.GREEN, palette.ENC1, palette.ENC4, (1, 2, 3)]


class Fills(unittest.TestCase):
    """Lever `fill` — `fill_rect` and `plot`."""

    def test_every_rectangle_lands_where_it_did(self):
        rnd = random.Random(11)
        a, b = fb(), fb()
        for _ in range(600):
            x, y = rnd.randint(-40, 340), rnd.randint(-40, 260)
            w, h = rnd.randint(-2, 120), rnd.randint(-2, 90)
            c = rnd.choice(COLORS)
            draw.fill_rect(a, x, y, w, h, c)
            REF.fill_rect(b, x, y, w, h, c)
        self.assertTrue(np.array_equal(a, b))

    def test_a_full_screen_fill_is_the_same_bytes(self):
        a, b = fb(), fb()
        draw.fill_rect(a, 0, 0, 320, 240, palette.GROUND)
        REF.fill_rect(b, 0, 0, 320, 240, palette.GROUND)
        self.assertTrue(np.array_equal(a, b))

    def test_the_cached_row_is_not_shared_into_the_framebuffer(self):
        """A fill must copy: writing the frame must not edit the cache."""
        a = fb()
        draw.fill_rect(a, 0, 0, 10, 10, palette.RED)
        a[0, 0] = (0, 0, 0)
        b = fb()
        draw.fill_rect(b, 0, 0, 10, 10, palette.RED)
        self.assertEqual(tuple(int(v) for v in b[0, 0]), tuple(palette.RED))

    def test_every_plot_lands_where_it_did(self):
        rnd = random.Random(12)
        a, b = fb(), fb()
        for _ in range(800):
            x, y = rnd.randint(-5, 325), rnd.randint(-5, 245)
            c = rnd.choice(COLORS)
            draw.plot(a, x, y, c)
            REF.plot(b, x, y, c)
        self.assertTrue(np.array_equal(a, b))


class Lines(unittest.TestCase):
    """Lever `line` — `polyline`."""

    def test_random_polylines_draw_the_same_pixels(self):
        rnd = random.Random(13)
        for _ in range(60):
            a, b = fb(), fb()
            pts = [(rnd.randint(-30, 350), rnd.randint(-30, 270))
                   for _ in range(rnd.randint(1, 8))]
            c = rnd.choice(COLORS)
            draw.polyline(a, pts, c)
            REF.polyline(b, pts, c)
            self.assertTrue(np.array_equal(a, b), pts)

    def test_a_single_point_draws_nothing_either_way(self):
        a, b = fb(), fb()
        draw.polyline(a, [(10, 10)], palette.RED)
        REF.polyline(b, [(10, 10)], palette.RED)
        self.assertTrue(np.array_equal(a, b))
        self.assertEqual(int(a.sum()), 0)

    def test_a_polyline_wholly_off_screen_writes_nothing(self):
        a, b = fb(), fb()
        pts = [(-50, -50), (-10, -80), (400, -20)]
        draw.polyline(a, pts, palette.RED)
        REF.polyline(b, pts, palette.RED)
        self.assertTrue(np.array_equal(a, b))
        self.assertEqual(int(a.sum()), 0)


class Text(unittest.TestCase):
    """Lever `text` — the per-(string, size) ink mask."""

    SAMPLES = ["", " ", "A", "SYNTH", "0123456789", "BATT 12%",
               "TURN A PARAM TO COPY ITS ADDRESS", "-1.25 dB", "NO ENV · DBOX",
               "III", "888", "WWWWWWWW"]

    def each(self, x, y, s, size):
        a, b = fb(), fb()
        ra = draw.text(a, x, y, s, palette.INK, size)
        rb = REF.draw_text(b, x, y, s, palette.INK, size)
        self.assertEqual(ra, rb, f"advance for {s!r}")
        self.assertTrue(np.array_equal(a, b), f"{s!r} at {(x, y)} {size}")

    def test_the_samples_at_both_sizes(self):
        for size in (font.SMALL, font.LARGE):
            for s in self.SAMPLES:
                self.each(4, 20, s, size)

    def test_clipped_at_every_edge(self):
        for size in (font.SMALL, font.LARGE):
            for x, y in ((-30, 10), (300, 10), (10, -4), (10, 236),
                         (-1, -1), (319, 239), (-400, 10), (10, 400)):
                self.each(x, y, "SYNTH 12", size)

    def test_every_authored_character(self):
        for size in (font.SMALL, font.LARGE):
            chars = "".join(sorted(font.charset(size)))
            for i in range(0, len(chars), 16):
                self.each(3, 30, chars[i:i + 16], size)

    def test_random_strings(self):
        rnd = random.Random(14)
        alphabet = "".join(sorted(font.charset(font.SMALL)))
        for _ in range(120):
            s = "".join(rnd.choice(alphabet)
                        for _ in range(rnd.randint(0, 20)))
            self.each(rnd.randint(-20, 300), rnd.randint(-6, 236), s,
                      font.SMALL)

    def test_a_glyph_edited_live_is_drawn_on_the_next_frame(self):
        """`--edit` mutates `font._GLYPHS`; the mask cache must be dropped."""
        ch = "A"
        before = font.get_grid(ch, font.SMALL)
        self.addCleanup(font.revert, ch, font.SMALL)
        self.addCleanup(draw.invalidate_text_cache)
        a = fb()
        draw.text(a, 4, 20, "AAA", palette.INK)       # fills the cache
        grid = [[1] * len(before[0]) for _ in before]
        font.set_grid(ch, font.SMALL, grid)
        draw.invalidate_text_cache()
        b, c = fb(), fb()
        draw.text(b, 4, 20, "AAA", palette.INK)
        REF.draw_text(c, 4, 20, "AAA", palette.INK)
        self.assertTrue(np.array_equal(b, c))
        self.assertFalse(np.array_equal(a, b), "the edit did not show at all")

    def test_an_advance_edited_live_moves_the_next_frame(self):
        ch = "A"
        self.addCleanup(font.revert_spacing)
        self.addCleanup(draw.invalidate_text_cache)
        a = fb()
        first = draw.text(a, 4, 20, "AAA", palette.INK)
        font.set_advance(ch, font.SMALL, font.advance(ch, font.SMALL) + 3)
        draw.invalidate_text_cache()
        b, c = fb(), fb()
        second = draw.text(b, 4, 20, "AAA", palette.INK)
        REF.draw_text(c, 4, 20, "AAA", palette.INK)
        self.assertTrue(np.array_equal(b, c))
        self.assertEqual(second, first + 9)

    def test_the_cache_has_a_ceiling(self):
        """A page that draws a fresh string every frame must not grow the
        process for ever — the cache is dropped whole rather than evicted."""
        self.addCleanup(draw.invalidate_text_cache)
        draw.invalidate_text_cache()
        for i in range(draw._TEXT_CACHE_MAX + 50):
            draw._text_mask(f"S{i}", font.SMALL)
        self.assertEqual(len(draw._TEXT_MASKS), 50)


def reply(rnd, n):
    lo = [-rnd.random() * 1.4 for _ in range(n)]
    hi = [rnd.random() * 1.4 for _ in range(n)]
    return {"min": lo, "max": hi}


class Peaks(unittest.TestCase):
    """Lever `cols`, half one — `draw.peaks`."""

    def test_random_envelopes(self):
        rnd = random.Random(15)
        for _ in range(120):
            a, b = fb(), fb()
            n = rnd.choice([1, 2, 7, 64, 128, 256, 400])
            x, y = rnd.randint(-20, 300), rnd.randint(-10, 220)
            w, h = rnd.randint(1, 300), rnd.randint(2, 100)
            r = reply(rnd, n)
            draw.peaks(a, r, x, y, w, h, palette.ENC2)
            REF.peaks(b, r, x, y, w, h, palette.ENC2)
            self.assertTrue(np.array_equal(a, b), (n, x, y, w, h))

    def test_the_degenerate_replies(self):
        for r in (None, {}, {"min": [], "max": []}, {"min": [0.0]},
                  {"min": [0.0], "max": []}):
            for w, h in ((0, 40), (-3, 40), (60, 1), (60, 0), (60, 40)):
                a, b = fb(), fb()
                draw.peaks(a, r, 10, 10, w, h, palette.ENC2)
                REF.peaks(b, r, 10, 10, w, h, palette.ENC2)
                self.assertTrue(np.array_equal(a, b), (r, w, h))

    def test_a_flat_envelope_is_the_one_pixel_rule(self):
        r = {"min": [0.0] * 8, "max": [0.0] * 8}
        a, b = fb(), fb()
        draw.peaks(a, r, 10, 100, 64, 40, palette.ENC2)
        REF.peaks(b, r, 10, 100, 64, 40, palette.ENC2)
        self.assertTrue(np.array_equal(a, b))
        self.assertEqual(int((a.any(axis=2)).sum()), 64)


class Mic:
    def __init__(self, meter, armed=False):
        self.meter = meter
        self.armed = armed


class Meter(unittest.TestCase):
    """Lever `cols`, half two — `InputPage._draw_meter`.

    Driven through a stand-in rather than a real page: the method reads only
    `self._meter_scale` (a staticmethod) and `self.p_thresh.value`, so a page
    built against a running host would prove nothing extra and cost a process.
    """

    def page(self, thresh):
        from pages.input import InputPage
        import types
        return types.SimpleNamespace(
            _meter_scale=InputPage._meter_scale,
            p_thresh=types.SimpleNamespace(value=thresh)), InputPage

    def test_random_meters(self):
        rnd = random.Random(16)
        for _ in range(150):
            shim, InputPage = self.page(rnd.random() * 0.5)
            n = rnd.choice([0, 1, 5, 64, 120, 300])
            mic = Mic([rnd.random() ** 2 for _ in range(n)],
                      armed=rnd.random() < 0.5)
            x, y = rnd.randint(-10, 280), rnd.randint(0, 200)
            w, h = rnd.randint(1, 300), rnd.randint(2, 60)
            a, b = fb(), fb()
            InputPage._draw_meter(shim, a, mic, x, y, w, h)
            REF.draw_meter(shim, b, mic, x, y, w, h)
            self.assertTrue(np.array_equal(a, b), (n, x, y, w, h))

    def test_no_mic_is_one_rule(self):
        shim, InputPage = self.page(0.1)
        a, b = fb(), fb()
        InputPage._draw_meter(shim, a, None, 10, 100, 64, 20)
        REF.draw_meter(shim, b, None, 10, 100, 64, 20)
        self.assertTrue(np.array_equal(a, b))

    def test_a_silent_mic_is_the_flat_line(self):
        shim, InputPage = self.page(0.25)
        mic = Mic([0.0] * 32)
        a, b = fb(), fb()
        InputPage._draw_meter(shim, a, mic, 10, 100, 64, 20)
        REF.draw_meter(shim, b, mic, 10, 100, 64, 20)
        self.assertTrue(np.array_equal(a, b))

    def test_the_threshold_dots_land_identically_when_clipped(self):
        shim, InputPage = self.page(1.0)
        mic = Mic([0.9] * 16, armed=True)
        for y, h in ((0, 4), (230, 40), (-2, 6)):
            a, b = fb(), fb()
            InputPage._draw_meter(shim, a, mic, -8, y, 340, h)
            REF.draw_meter(shim, b, mic, -8, y, 340, h)
            self.assertTrue(np.array_equal(a, b), (y, h))


class TheComparisonCanFail(unittest.TestCase):
    """Negative control (S15, #56): a primitive that is wrong by one pixel is
    caught by the same assertion every case above makes. Without this, "the
    frames are equal" is a test nobody has watched fail."""

    def test_an_off_by_one_fill_is_caught(self):
        a, b = fb(), fb()
        draw.fill_rect(a, 10, 10, 20, 20, palette.RED)
        REF.fill_rect(b, 10, 10, 20, 21, palette.RED)
        self.assertFalse(np.array_equal(a, b))

    def test_a_dropped_glyph_is_caught(self):
        a, b = fb(), fb()
        draw.text(a, 4, 20, "SYNTH", palette.INK)
        REF.draw_text(b, 4, 20, "SYNT", palette.INK)
        self.assertFalse(np.array_equal(a, b))

    def test_a_meter_drawn_without_the_curve_is_caught(self):
        shim, InputPage = self.pagey()
        mic = Mic([0.25] * 32)
        a, b = fb(), fb()
        InputPage._draw_meter(shim, a, mic, 10, 100, 64, 30)
        REF.draw_meter(shim, b, Mic([0.0625] * 32), 10, 100, 64, 30)
        self.assertFalse(np.array_equal(a, b),
                         "the sqrt curve is not being applied")

    def pagey(self):
        from pages.input import InputPage
        import types
        return types.SimpleNamespace(
            _meter_scale=InputPage._meter_scale,
            p_thresh=types.SimpleNamespace(value=0.2)), InputPage


if __name__ == "__main__":
    unittest.main()
