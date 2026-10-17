"""Phase-3 checks: photo chrome, hotspot keymap, MIDI config, SEQ slot fix.

Geometry and config are tested as pure data (no window needed); the window
itself is exercised by running `main.py --headless --exit-after` in a
subprocess and inspecting the screenshot it writes.
PNGs land in validation/ui/phase3/.
"""


import os
import subprocess
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import chrome as C                                        # noqa: E402
from app.keys import ALL_KEYS, Key                          # noqa: E402
from drivers.desktop import midi_in as M                   # noqa: E402
from tests.test_phase2b import Rig                         # noqa: E402

OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "validation", "ui", "phase3"))
STATE_DIR = os.path.join(HERE, "state", "test3")
ENCODERS = ("ENC1", "ENC2", "ENC3", "ENC4")


def setUpModule():
    os.makedirs(OUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# keymap.json
# ---------------------------------------------------------------------------
class TestKeymap(unittest.TestCase):
    def setUp(self):
        self.km = C.load_keymap()

    def test_covers_every_key_exactly_once(self):
        names = list(self.km)
        self.assertEqual(len(names), len(set(names)), "duplicate hotspot name")
        for key in ALL_KEYS:
            self.assertIn(key, self.km, f"{key} has no hotspot")
        for enc in ENCODERS:
            self.assertIn(enc, self.km)
        self.assertEqual(len(names), len(ALL_KEYS) + 4)
        self.assertEqual(len(ALL_KEYS), 52)

    def test_polygons_are_sane(self):
        w, h = C.load_photo_size()
        for name, poly in self.km.items():
            self.assertGreaterEqual(len(poly), 4, name)
            x0, y0, x1, y1 = C.poly_bbox(poly)
            self.assertGreater(x1 - x0, 40, name)
            self.assertGreater(y1 - y0, 40, name)
            self.assertTrue(0 <= x0 and x1 <= w, name)
            self.assertTrue(0 <= y0 and y1 <= h, name)

    def test_no_two_hotspots_overlap(self):
        items = sorted(self.km.items())
        for i, (na, pa) in enumerate(items):
            for nb, pb in items[i + 1:]:
                self.assertFalse(C.polys_overlap(pa, pb),
                                 f"{na} overlaps {nb}")

    def test_hit_test_inside_synth_yields_synth(self):
        poly = self.km["SYNTH"]
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        self.assertEqual(C.hit_test((cx, cy), self.km), "SYNTH")
        # a point in the gap below the row hits nothing
        gap = C.poly_bbox(poly)[3] + 0.5 * (C.poly_bbox(self.km["1"])[1]
                                            - C.poly_bbox(poly)[3])
        self.assertIsNone(C.hit_test((cx, gap), self.km))
        # and the neighbours are distinct
        self.assertEqual(C.hit_test((self.km["DRUM"][0][0] + 20,
                                     cy), self.km), "DRUM")

    def test_keycaps_sit_on_the_16_mm_grid(self):
        import json as _json
        with open(C.SCREEN_RECT_PATH) as f:
            pxmm = _json.load(f)["px_per_mm"]
        row = ["SHIFT", "DROP", "1", "2", "3", "4", "LEFT", "RIGHT",
               "A", "B", "C", "X", "Y", "Z"]
        cx = []
        for n in row:
            x0, _, x1, _ = C.poly_bbox(self.km[n])
            cx.append((x0 + x1) / 2)
        steps = [b - a for a, b in zip(cx, cx[1:])]
        for s in steps:
            self.assertAlmostEqual(s / pxmm, 16.0, delta=0.3)
        # and a keycap is 15 mm across
        x0, y0, x1, y1 = C.poly_bbox(self.km["SYNTH"])
        self.assertAlmostEqual((x1 - x0) / pxmm, 15.0, delta=0.6)

    def test_circle_shapes_are_recorded_not_inferred(self):
        shapes = C.load_shapes()
        self.assertEqual(set(shapes), set(ENCODERS))
        self.assertTrue(all(v == "circle" for v in shapes.values()))

        # why it is recorded: a square cap's rounded-rect polygon carries only
        # corner-arc vertices, so no radius-spread test can tell it from a
        # circle. SYNTH lands inside any threshold loose enough to admit ENC1.
        def spread(poly):
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            d = [((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5 for p in poly]
            return max(d) / min(d)
        self.assertLess(spread(self.km["SYNTH"]) - spread(self.km["ENC1"]), 0.08)

    def test_encoder_hotspots_are_round_and_ordered(self):
        xs = []
        for name in ENCODERS:
            x0, y0, x1, y1 = C.poly_bbox(self.km[name])
            self.assertAlmostEqual((x1 - x0) / (y1 - y0), 1.0, delta=0.15)
            xs.append(x0)
        self.assertEqual(xs, sorted(xs))


# ---------------------------------------------------------------------------
# screen cutout / framebuffer placement
# ---------------------------------------------------------------------------
class TestScreenRect(unittest.TestCase):
    def test_screen_rect_loads(self):
        x, y, w, h = C.load_screen_rect()
        self.assertGreater(w, 300)
        self.assertGreater(h, 200)
        # the LCD sits left of centre, clear of every hotspot
        km = C.load_keymap()
        rect_poly = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        for name, poly in km.items():
            self.assertFalse(C.polys_overlap(rect_poly, poly),
                             f"{name} overlaps the screen cutout")

    def test_lcd_is_an_exact_4_3_rect(self):
        x, y, w, h = C.load_screen_rect()
        self.assertAlmostEqual(w / h, 4 / 3, delta=0.002)
        self.assertAlmostEqual(w % C.FB_W, 0, delta=0.5)
        self.assertAlmostEqual(h % C.FB_H, 0, delta=0.5)

    def test_fit_scale_lands_the_lcd_on_a_whole_multiple(self):
        rect = C.load_screen_rect()
        pw, ph = C.load_photo_size()
        for width in (1200, 2560, 3000, 5120):
            s = C.fit_scale(rect, pw, width)
            k = rect[2] * s / C.FB_W
            self.assertAlmostEqual(k, round(k), delta=1e-6,
                                   msg="--width %d gives a fractional LCD" % width)
            self.assertGreaterEqual(round(k), 1)
            if width >= pw * C.FB_W / rect[2]:
                self.assertLessEqual(pw * s, width + 1)

    def test_corner_radius_is_3_5_mm(self):
        import json as _json
        with open(C.SCREEN_RECT_PATH) as f:
            d = _json.load(f)
        self.assertAlmostEqual(d["corner_radius"] / d["px_per_mm"], 3.5, delta=0.05)

    def test_fb_rect_is_integer_scaled_and_inside(self):
        rect = C.load_screen_rect()
        for scale in (0.6, 0.75, 1.0):
            sr = tuple(v * scale for v in rect)
            fx, fy, fw, fh = C.fb_rect(sr)
            s = C.fb_scale(sr)
            self.assertGreaterEqual(s, 1)
            self.assertEqual(fw, 320 * s)
            self.assertEqual(fh, 240 * s)
            self.assertEqual(fw % 320, 0)
            self.assertEqual(fh % 240, 0)
            # inside the cutout, centred (letterboxed by the black fill)
            self.assertGreaterEqual(fx, sr[0] - 1)
            self.assertGreaterEqual(fy, sr[1] - 1)
            self.assertLessEqual(fx + fw, sr[0] + sr[2] + 1)
            self.assertLessEqual(fy + fh, sr[1] + sr[3] + 1)
            self.assertAlmostEqual(fx - sr[0], sr[0] + sr[2] - (fx + fw), delta=1)

    def test_fb_scale_never_drops_below_one(self):
        # a tiny window still shows a 1x framebuffer (documented behaviour)
        self.assertEqual(C.fb_scale((0, 0, 100, 80)), 1)
        self.assertEqual(C.fb_rect((0, 0, 100, 80))[2:], (320, 240))


# ---------------------------------------------------------------------------
# MIDI
# ---------------------------------------------------------------------------
class TestMidi(unittest.TestCase):
    def test_config_parses(self):
        cfg = M.load_config()
        self.assertEqual(sorted(M.cc_to_encoder(cfg).values()), [0, 1, 2, 3])
        self.assertEqual(cfg["note_offset"], 53)
        self.assertTrue(cfg["velocity"])
        self.assertIn(cfg["cc"]["encoding"], M.ENCODINGS)

    def test_relative_encodings(self):
        cases = {
            "twos-complement": {1: +1, 3: +3, 127: -1, 125: -3, 0: 0},
            "offset-64": {65: +1, 67: +3, 63: -1, 61: -3, 64: 0},
            "signmag": {0x41: +1, 0x43: +3, 0x01: -1, 0x03: -3, 0x00: 0},
        }
        for style, table in cases.items():
            for value, want in table.items():
                self.assertEqual(M.decode_relative(value, style), want,
                                 f"{style} {value}")
        with self.assertRaises(ValueError):
            M.decode_relative(1, "nope")

    def test_cc_message_reaches_the_right_encoder(self):
        seen = []

        class FakeApp:
            def on_encoder(self, idx, delta):
                seen.append((idx, delta))

            def on_key(self, key, pressed, velocity=1.0):
                seen.append((key, pressed, velocity))

        class Msg:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        for style in M.ENCODINGS:
            seen.clear()
            cfg = M.load_config()
            cfg["cc"]["encoding"] = style
            midi = M.MidiIn.__new__(M.MidiIn)
            midi.app = FakeApp()
            midi.cfg = cfg
            midi.cc_map = M.cc_to_encoder(cfg)
            midi.encoding = style
            midi.channel = -1
            midi.note_offset = 53
            midi.use_velocity = True
            midi.port = None
            up = {"twos-complement": 1, "offset-64": 65, "signmag": 0x41}[style]
            down = {"twos-complement": 127, "offset-64": 63, "signmag": 0x01}[style]
            for cc, idx in sorted(midi.cc_map.items()):
                midi.handle(Msg(type="control_change", control=cc,
                                value=up, channel=0))
                midi.handle(Msg(type="control_change", control=cc,
                                value=down, channel=0))
                self.assertEqual(seen[-2:], [(idx, +1), (idx, -1)], style)

    def test_note_maps_to_chromatic_key_with_velocity(self):
        rig = Rig(state_dir=STATE_DIR)

        class Msg:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        midi = M.MidiIn.__new__(M.MidiIn)
        midi.app = rig.app
        midi.cfg = M.load_config()
        midi.cc_map = M.cc_to_encoder(midi.cfg)
        midi.encoding = "twos-complement"
        midi.channel = -1
        midi.note_offset = 53
        midi.use_velocity = True
        midi.port = None

        seen = []
        inst = rig.app.instrument
        inst.note_on = lambda n, v=1.0: seen.append((n, v))
        inst.note_off = lambda n: seen.append((n, None))

        midi.handle(Msg(type="note_on", note=53, velocity=64, channel=0))
        self.assertEqual(seen[-1][0], 53)                  # key 0 = low F
        self.assertAlmostEqual(seen[-1][1], 64 / 127.0, places=3)
        midi.handle(Msg(type="note_off", note=53, velocity=0, channel=0))
        self.assertEqual(seen[-1], (53, None))

        # a note below the 24 keys still plays the instrument
        midi.handle(Msg(type="note_on", note=41, velocity=127, channel=0))
        self.assertEqual(seen[-1], (41, 1.0))
        midi.handle(Msg(type="note_off", note=41, velocity=0, channel=0))

        # ... and the ‹ › octave buttons transpose it (Instrument.octave)
        self.assertEqual(rig.app.instrument.octave, 0)


# ---------------------------------------------------------------------------
# SEQ: C is a slot key, HOLD is the ENC4 param
# ---------------------------------------------------------------------------
class TestSeqSlotC(unittest.TestCase):
    def test_c_recalls_a_slot_and_never_toggles_hold(self):
        rig = Rig(state_dir=STATE_DIR)
        rig.tap(Key.SEQ)
        rig.arm_seq()                     # the slot takes arbutus/endless
        seq = rig.app.sequencer
        self.assertIsNotNone(seq)

        rig.entry(0, 4, 7)                # hold the box's REC soft-key (DDR-041)
        self.assertEqual(len(seq.pattern), 3)
        self.assertFalse(seq.hold)

        for letter in ("A", "B", "C", "X", "Y", "Z"):
            rig.tap(getattr(Key, letter))
            self.assertEqual(rig.app.current_page.state.slot_letter, letter)
            self.assertFalse(rig.app.sequencer.hold,
                             f"{letter} must not touch HOLD")

        # HOLD is an encoder only — ENC3 on `arbutus/endless`, whose row is
        # the descriptor's since H2
        rig.tap(Key.A)
        rig.send(("enc", 2, +1))
        self.assertTrue(rig.app.sequencer.hold)
        rig.send(("enc", 2, -1))
        self.assertFalse(rig.app.sequencer.hold)

    def test_seq_slot_c_hotspot_exists(self):
        self.assertIn("C", C.load_keymap())


# ---------------------------------------------------------------------------
# the real window, driven from a subprocess
# ---------------------------------------------------------------------------
class TestChromeWindow(unittest.TestCase):
    @staticmethod
    def _run(*extra):
        # light, whatever a player left in the library's settings (#334)
        env = dict(os.environ, SDL_VIDEODRIVER="dummy", ARB_APPEARANCE="light")
        # and never the checkout's library: `main` opens a project in it
        # (D97) and reads a player's settings there (#851)
        from tests.native_support import scratch_library
        cmd = [sys.executable, os.path.join(HERE, "main.py"),
               "--headless", "--no-midi", "--exit-after", "1",
               "--library", scratch_library()] + list(extra)
        return subprocess.run(cmd, env=env, cwd=HERE, timeout=120,
                              capture_output=True, text=True)

    def _png(self, path):
        import pygame
        surf = pygame.image.load(path)
        return np.transpose(pygame.surfarray.array3d(surf), (1, 0, 2))

    def test_scripted_quit_writes_a_chrome_screenshot(self):
        png = os.path.join(OUT_DIR, "chrome.png")
        if os.path.exists(png):
            os.remove(png)
        res = self._run("--screenshot", png)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(png), res.stderr)

        img = self._png(png)
        view = C.ChromeView.__new__(C.ChromeView)     # geometry only
        rect = C.load_screen_rect()
        scale = img.shape[1] / C.load_photo_size()[0]
        sr = tuple(v * scale for v in rect)
        fx, fy, fw, fh = C.fb_rect(sr)
        fx, fy = fx + C.OFFSET[0], fy + C.OFFSET[1]     # whole chrome is shifted

        # the framebuffer region carries the SYNTH page, not photo pixels
        fbz = img[max(0, fy) + 4:fy + fh - 4, max(0, fx) + 4:fx + fw - 4]
        # #334: the UI is light now, and so is much of the photo, so darkness
        # no longer tells them apart. The UI's ground is one exact RGB over
        # most of the LCD; a JPEG never is.
        from ui import palette
        colours, counts = np.unique(fbz.reshape(-1, 3), axis=0, return_counts=True)
        self.assertEqual(tuple(int(c) for c in colours[counts.argmax()]),
                         palette.LIGHT["GROUND"], "LCD area is not the UI")
        self.assertGreater(counts.max() / counts.sum(), 0.3, "LCD area is not the UI")
        # the photo is still there: the keycaps are bright cream. (The photo
        # is now cropped to grass-around-the-synth, so half the frame is lawn —
        # sample the key field instead of a blind half-image.)
        km = C.load_keymap()
        xs = [p[0] for poly in km.values() for p in poly]
        ys = [p[1] for poly in km.values() for p in poly]
        kx0, kx1 = int(min(xs) * scale), int(max(xs) * scale)
        ky0, ky1 = int(min(ys) * scale), int(max(ys) * scale)
        keys = img[ky0:ky1, kx0:kx1]
        self.assertGreater(float(keys.mean()), 110, "photo missing")
        # and it is the photo-sized window, not the plain 3x one
        self.assertGreater(img.shape[1], 1000)

    def test_zoom_pane_variant(self):
        png = os.path.join(OUT_DIR, "chrome-zoom.png")
        if os.path.exists(png):
            os.remove(png)
        res = self._run("--zoom", "--screenshot", png)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(os.path.exists(png))
        img = self._png(png)
        # against the same run without --zoom (the photo alone has black
        # pixels on the right, so a darkness check passed with no pane, #1123)
        plain = os.path.join(OUT_DIR, "chrome-nozoom.png")
        res = self._run("--screenshot", plain)
        self.assertEqual(res.returncode, 0, res.stderr)
        base = self._png(plain)
        self.assertEqual(img.shape, base.shape)
        h, w, _ = img.shape
        changed = (img != base).any(axis=2)
        self.assertFalse(changed[:, :w // 2].any(), "zoom touched the left half")
        right = changed[:, w // 2:]
        self.assertGreater(right.mean(), 0.15, "no zoom pane drawn")
        # and the pane is the framebuffer: mostly the UI's ground colour
        from ui import palette
        px = img[changed]
        colours, counts = np.unique(px, axis=0, return_counts=True)
        self.assertEqual(tuple(int(c) for c in colours[counts.argmax()]),
                         palette.LIGHT["GROUND"], "the pane is not the UI")

    def test_outlines_follow_a_cropped_window(self):
        """Regression: the press outline used chrome.OFFSET while the photo used
        the centring origin, so every outline slid by the crop amount as soon as
        the window was capped to a smaller display."""
        import pygame
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        pygame.init()
        pygame.display.set_mode((64, 64))
        view = C.ChromeView(max_width=2560, max_size=(1512, 900))
        self.assertLess(view.origin[0], 0, "window should be cropping")
        self.assertNotEqual(view.origin, (0, 0))

        for name in ("SYNTH", "NOTE_7", "ENC4"):
            poly = view.keymap[name]
            ox, oy = view.photo_at
            pts = [(p[0] * view.scale + ox, p[1] * view.scale + oy) for p in poly]
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            self.assertEqual(view.hit((cx, cy)), name,
                             "%s outline and hotspot disagree" % name)

    def test_m_toggle_scales_the_whole_chrome(self):
        """M must double photo, hotspots, outlines and framebuffer together."""
        import pygame
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        pygame.init()
        pygame.display.set_mode((64, 64))
        view = C.ChromeView(max_width=2560, max_size=(1710, 1112))
        self.assertEqual(view.fb_multiple, 1)

        one = (view.scale, view.fb_rect, view.photo_px)
        self.assertTrue(view.set_fb_multiple(2))
        self.assertEqual(view.fb_multiple, 2)
        self.assertAlmostEqual(view.scale, one[0] * 2, places=9)
        self.assertEqual(view.photo_px, (one[2][0] * 2, one[2][1] * 2))
        self.assertEqual(view.fb_rect[2:], (640, 480))
        self.assertFalse(view.set_fb_multiple(2))

        # and a click still lands on the key you see, at either multiple
        for k in (2, 1):
            view.set_fb_multiple(k)
            ox, oy = view.photo_at
            for name in ("SYNTH", "NOTE_7", "ENC4"):
                x0, y0, x1, y1 = C.poly_bbox(view.keymap[name])
                cx = (x0 + x1) / 2 * view.scale + ox
                cy = (y0 + y1) / 2 * view.scale + oy
                self.assertEqual(view.hit((cx, cy)), name,
                                 "%s misses at %dx" % (name, k))

    def test_pressed_key_draws_an_outline(self):
        import pygame
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        pygame.init()
        view = C.ChromeView(max_width=1400)
        screen = pygame.display.set_mode(view.size)
        view.optimize()

        # the chrome needs a rendered framebuffer, and an App needs the audio
        # process to build one (stage N1 — there is no Python graph to fall
        # back on). What is under test is the outline, not the page.
        from tests.native_support import app_for, host
        native = host(self)
        app, _audio = app_for(native, STATE_DIR, fresh=True)
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        app.render(fb)

        view.press("SYNTH")
        view.press("ENC4")
        view.draw(screen, fb)
        pygame.display.flip()
        png = os.path.join(OUT_DIR, "chrome-press.png")
        pygame.image.save(screen, png)

        pressed = np.transpose(pygame.surfarray.array3d(screen), (1, 0, 2))

        # compare against the same frame with nothing held: "these pixels
        # changed when the key went down" is the real assertion; the colours
        # are then checked on the pixels that did change
        view._held.clear()
        view._outline_cache.clear()
        view.draw(screen, fb)
        idle = np.transpose(pygame.surfarray.array3d(screen), (1, 0, 2))

        s = view.scale
        ox, oy = view.photo_at
        t = view._t + 2
        for name in ("SYNTH", "ENC4"):
            x0, y0, x1, y1 = C.poly_bbox(view.keymap[name])
            sl = (slice(max(0, int(y0 * s) + oy - t), int(y1 * s) + oy + t),
                  slice(max(0, int(x0 * s) + ox - t), int(x1 * s) + ox + t))
            changed = np.any(pressed[sl].astype(int) != idle[sl].astype(int),
                             axis=-1)
            self.assertGreater(int(changed.sum()), 100,
                               "no outline drawn for %s" % name)
            # a red ring round a cap washed brighter than it was
            lit = pressed[sl][changed].astype(int)
            red = ((lit[:, 0] > lit[:, 1] + 60) & (lit[:, 0] > lit[:, 2] + 60))
            self.assertGreater(int(red.sum()), 50,
                               "%s: no red ring pixels" % name)
            was = idle[sl][changed].astype(int)
            self.assertGreater(float(lit[~red].mean()), float(was[~red].mean()),
                               "%s: interior is not brightened" % name)

        view.press("SYNTH")
        view.draw(screen, fb)

        # the outline survives at least one frame after release
        view.release("SYNTH")
        view.draw(screen, fb)
        self.assertIn("SYNTH", view._held)
        pygame.display.quit()

    def test_zoom_only_window_is_the_phase1_view(self):
        png = os.path.join(OUT_DIR, "zoom-only.png")
        res = self._run("--zoom-only", "--scale", "3", "--screenshot", png)
        self.assertEqual(res.returncode, 0, res.stderr)
        img = self._png(png)
        self.assertEqual(img.shape[:2], (720, 960))


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
