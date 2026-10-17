"""#334 — the screen's two palettes, and the phone choosing between them.

ui-aesthetic.md § 3 is the spec: every role in both modes, the knob colours
fixed by Elfy, the contrast floors in § 3.4. PROTOCOL.md § The phone's
appearance is the message: the phone sends `appearance {mode}` to the audio
process, which pushes it to the UI, which draws in that mode from the next
frame and remembers it in the device's settings (light the first time).

The end-to-end cases run the real audio process with a second client on its
socket playing the phone, the way `arbutus_os/tests/test_ui_feed.py` does for
the D17 feed.
"""

import os
import shutil
import tempfile
import unittest

import numpy as np

from tests import native_support as NS                     # noqa: F401  (pins light)
from ui import palette, zones                              # noqa: E402
from app.app import APPEARANCE_ENV                         # noqa: E402
from app.settings import Settings                          # noqa: E402


def _lum(c):
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class PaletteCase(unittest.TestCase):
    def tearDown(self):
        palette.set_mode("light")


class TestTheTables(PaletteCase):
    def test_every_role_in_both_modes(self):
        self.assertEqual(set(palette.LIGHT), set(palette.EDITABLE))
        self.assertEqual(set(palette.DARK), set(palette.EDITABLE))

    def test_set_mode_rebinds_every_name(self):
        for mode in palette.MODES:
            palette.set_mode(mode)
            self.assertEqual(palette.mode(), mode)
            for name, rgb in palette.TABLES[mode].items():
                self.assertEqual(getattr(palette, name), rgb, (mode, name))
            self.assertEqual(palette.encoder_color(5), palette.TABLES[mode]["ENC2"])

    def test_the_two_modes_differ_where_it_matters(self):
        for name in ("GROUND", "INK", "PANEL", "ENC1", "ENC2", "RED"):
            self.assertNotEqual(palette.LIGHT[name], palette.DARK[name], name)

    def test_an_unknown_mode_is_refused_and_changes_nothing(self):
        palette.set_mode("dark")
        with self.assertRaises(ValueError):
            palette.set_mode("sepia")
        self.assertEqual(palette.mode(), "dark")
        self.assertEqual(palette.GROUND, palette.DARK["GROUND"])

    def test_the_ticket_s_fixed_values(self):
        """The colours #334 names, as hex, so a tidy-up cannot drift them."""
        hx = lambda c: "#%02x%02x%02x" % c                  # noqa: E731
        L, D = palette.LIGHT, palette.DARK
        self.assertEqual(hx(L["GROUND"]), "#fdeccb")
        self.assertEqual(hx(L["INK"]), "#8c3c03")
        self.assertEqual(hx(L["SUBTLE"]), "#7a4a20")
        self.assertEqual(hx(L["GREEN"]), "#1b5c22")
        self.assertEqual(hx(D["GROUND"]), "#8c3c03")
        self.assertEqual(hx(D["INK"]), "#fce0a7")
        self.assertEqual(hx(D["PANEL"]), "#6e2f02")          # darkCard
        self.assertEqual(hx(D["SUBTLE"]), "#eccb93")         # darkSoft
        self.assertEqual(hx(D["GREEN"]), "#a2ab72")          # darkGreenInk
        self.assertEqual([hx(L["ENC%d" % n]) for n in (1, 2, 3, 4)],
                         ["#0a4580", "#1b5c22", "#c72c2c", "#ad5f00"])
        # the record red is brighter than ENC3's, which shares its hue
        self.assertGreater(L["RED"][0], L["ENC3"][0])
        self.assertGreater(_lum(L["RED"]), _lum(L["ENC3"]))


class TestContrast(PaletteCase):
    """ui-aesthetic.md § 3.4's floors. A number below its floor is either a
    palette edit that broke legibility or a spec table that needs the
    exception written into it — both are worth a red test."""

    #: (role, floor) — the ground a role is measured on is both grounds
    TEXT = ("INK", "SUBTLE", "INVERT_FG")
    #: roles § 3.4 lists as logged exceptions, with the floor they do meet
    FLOORS = {
        "light": {"RED": 4.0, "ENC4": 4.0, "ENC3": 4.5, "GREEN": 4.5,
                  "ACCENT_BROWN": 4.5, "ENC1": 4.5, "ENC2": 4.5,
                  "DIM": 2.4, "RULE": 2.2},
        "dark": {"RED": 3.5, "GREEN": 3.0, "ACCENT_BROWN": 3.7,
                 "ENC1": 4.5, "ENC2": 4.5, "ENC3": 4.5, "ENC4": 4.5,
                 "DIM": 2.5, "RULE": 2.2},
    }

    def test_text_is_at_least_4_5_on_every_ground(self):
        for mode in palette.MODES:
            t = palette.TABLES[mode]
            for role in ("INK", "SUBTLE"):
                for ground in ("GROUND", "PANEL", "OVERLAY_BG"):
                    self.assertGreaterEqual(contrast(t[role], t[ground]), 4.5,
                                            (mode, role, ground))
            self.assertGreaterEqual(contrast(t["INVERT_FG"], t["INVERT_BG"]), 4.5, mode)

    def test_every_other_role_meets_its_floor(self):
        for mode, floors in self.FLOORS.items():
            t = palette.TABLES[mode]
            for role, floor in floors.items():
                for ground in ("GROUND", "PANEL"):
                    self.assertGreaterEqual(round(contrast(t[role], t[ground]), 2),
                                            floor, (mode, role, ground))

    #: #693: the SHIFT row and the clip blocks, each at § 3.4's figure:
    #: (role, ground) -> {mode: floor}
    ON = {
        ("ACCENT_BROWN", "SHIFT_HIGHLIGHT"): {"light": 5.09, "dark": 3.76},
        ("INK", "SHIFT_HIGHLIGHT"): {"light": 5.21, "dark": 5.92},
        ("INK", "CLIP_FILL"): {"light": 5.92, "dark": 6.81},
        ("INK", "CLIP_FILL_SEL"): {"light": 5.21, "dark": 7.90},
    }

    def test_the_shift_row_and_the_clips_meet_their_floors(self):
        for (role, ground), floors in self.ON.items():
            for mode, floor in floors.items():
                t = palette.TABLES[mode]
                self.assertGreaterEqual(round(contrast(t[role], t[ground]), 2),
                                        floor, (mode, role, ground))

    def test_the_floors_catch_a_bad_colour(self):
        """Negative control (#56): the same check, on a colour that fails."""
        t = dict(palette.DARK, ENC1=(150, 90, 40))
        self.assertLess(contrast(t["ENC1"], t["GROUND"]), self.FLOORS["dark"]["ENC1"])


class TestLiveEditing(PaletteCase):
    def test_an_edit_lands_in_the_mode_on_screen_and_survives_a_switch(self):
        before = palette.LIGHT["INK"]
        palette.set_mode("dark")
        dark_ink = palette.INK
        try:
            palette.set_color("INK", (1, 2, 3))
            self.assertEqual(palette.INK, (1, 2, 3))
            self.assertEqual(palette.dirty(), ("INK",))
            self.assertEqual(palette.dirty_entries(), (("dark", "INK"),))
            palette.set_mode("light")
            self.assertEqual(palette.INK, before)
            self.assertEqual(palette.dirty(), ())
            palette.set_mode("dark")
            self.assertEqual(palette.INK, (1, 2, 3))
        finally:
            palette.set_mode("dark")
            palette.set_color("INK", dark_ink)
        self.assertEqual(palette.dirty_entries(), ())

    def test_writeback_writes_each_mode_s_table(self):
        import tools.edit_writeback as W

        tmp = tempfile.mkdtemp(prefix="arbutus-palette-")
        self.addCleanup(shutil.rmtree, tmp, True)
        copy = os.path.join(tmp, "palette.py")
        shutil.copyfile(W.PALETTE_PY, copy)
        orig_path, orig = W.PALETTE_PY, {m: dict(t) for m, t in palette.TABLES.items()}
        W.PALETTE_PY = copy

        def restore():
            W.PALETTE_PY = orig_path
            for m, t in orig.items():
                palette.TABLES[m].update(t)
            palette.mark_clean()
            palette.set_mode("light")
        self.addCleanup(restore)

        palette.set_mode("light")
        palette.set_color("RULE", (10, 20, 30))
        palette.set_mode("dark")
        palette.set_color("RULE", (40, 50, 60))
        done = W.write_palette()
        self.assertEqual(set(done), {("light", "RULE"), ("dark", "RULE")})
        self.assertEqual(palette.dirty_entries(), ())

        with open(copy) as f:
            src = f.read()
        ns = {}
        exec(compile(src, copy, "exec"), ns)                 # noqa: S102
        self.assertEqual(ns["LIGHT"]["RULE"], (10, 20, 30))
        self.assertEqual(ns["DARK"]["RULE"], (40, 50, 60))
        # every other entry is untouched, comments and all
        self.assertEqual(ns["LIGHT"]["INK"], orig["light"]["INK"])
        line = next(ln for ln in src.splitlines() if '"RULE": (10, 20, 30)' in ln)
        self.assertIn("# rules / separators", line)


class TestSettings(unittest.TestCase):
    def test_light_the_first_time_and_for_anything_unknown(self):
        tmp = tempfile.mkdtemp(prefix="arbutus-settings-")
        self.addCleanup(shutil.rmtree, tmp, True)
        s = Settings(tmp)
        self.assertEqual(s.appearance, "light")
        s.set("system", "appearance", "dark")
        self.assertEqual(Settings(tmp).appearance, "dark")
        s.set("system", "appearance", "sepia")
        self.assertEqual(Settings(tmp).appearance, "light")

    def test_reset_keeps_the_appearance(self):
        """#693: the screen follows the phone, not the box (ui-aesthetic
        § 3.1), so RESET leaves the mode it last had."""
        tmp = tempfile.mkdtemp(prefix="arbutus-settings-")
        self.addCleanup(shutil.rmtree, tmp, True)
        s = Settings(tmp)
        s.set("system", "appearance", "dark")
        s.set("system", "vel_curve", "hard")
        s.reset()
        self.assertEqual(s.appearance, "dark")
        self.assertEqual(Settings(tmp).appearance, "dark")
        self.assertNotEqual(Settings(tmp).vel_curve, "HARD")    # the rest did reset


class AppCase(PaletteCase):
    """The App against the real audio process, on a throwaway library."""

    def setUp(self):
        # offline (#681): nothing here needs real time, and a real-time host
        # on a Mac whose CoreAudio never answers never opens its socket
        self.native = NS.native_or_skip(self, offline=True)
        self.addCleanup(self.native.close)
        self.lib = tempfile.mkdtemp(prefix="arbutus-lib-")
        self.addCleanup(shutil.rmtree, self.lib, True)
        NS.library_copy(self.lib)
        # the pin is what every other test relies on; these are about the mode
        self.pin = os.environ.pop(APPEARANCE_ENV, None)
        self.addCleanup(self._repin)

    def _repin(self):
        if self.pin is not None:
            os.environ[APPEARANCE_ENV] = self.pin

    def boot(self, name="palette"):
        app, _ = NS.app_for(self.native, NS.state_dir(name), library_root=self.lib)
        return app

    def phone(self, mode):
        import arbutus_ctl
        c = arbutus_ctl.connect(self.native.sock)
        try:
            return c.request({"type": "appearance", "mode": mode})
        finally:
            c.close()

    def ground_of(self, app):
        fb = app.render()
        x, y, w, h = zones.MAIN
        return tuple(int(v) for v in fb[y + h - 2, x + 1])


class TestTheAppFollowsThePhone(AppCase):
    def test_light_on_a_first_boot(self):
        app = self.boot()
        self.assertEqual(app.appearance, "light")
        self.assertEqual(self.ground_of(app), palette.LIGHT["GROUND"])

    def test_the_phone_s_dark_reaches_the_screen_and_is_remembered(self):
        app = self.boot()
        self.assertEqual(self.phone("dark")["mode"], "dark")
        app.tick()
        self.assertEqual(app.appearance, "dark")
        self.assertEqual(self.ground_of(app), palette.DARK["GROUND"])
        self.assertEqual(Settings(self.lib).appearance, "dark")
        # and back
        self.phone("light")
        app.tick()
        self.assertEqual(self.ground_of(app), palette.LIGHT["GROUND"])
        self.assertEqual(Settings(self.lib).appearance, "light")

    def test_a_push_heard_mid_request_waits_for_the_tick(self):
        """#693: a push can be absorbed inside any request, a frame's draw
        included; the mode changes (and the setting is written) only in
        `App.tick`, never under a page that is drawing."""
        app = self.boot()
        self.phone("dark")
        app.native.appearance()                   # any request takes the push in
        self.assertEqual(app.appearance, "light")
        self.assertEqual(Settings(self.lib).appearance, "light")
        app.tick()
        self.assertEqual(app.appearance, "dark")
        self.assertEqual(Settings(self.lib).appearance, "dark")

    def test_without_a_phone_a_boot_keeps_the_last_mode(self):
        app = self.boot()
        app.set_appearance("dark")
        palette.set_mode("light")                 # a different process, in effect
        app2 = self.boot("palette-2")
        self.assertEqual(app2.appearance, "dark")

    def test_a_ui_that_starts_late_asks_the_host(self):
        self.phone("dark")                        # the phone spoke first
        app = self.boot()
        self.assertEqual(app.appearance, "dark")

    def test_a_mode_the_build_does_not_know_is_ignored(self):
        app = self.boot()
        self.assertFalse(app.set_appearance("sepia"))
        self.assertEqual(app.appearance, "light")
        self.assertEqual(Settings(self.lib).appearance, "light")

    def test_a_pinned_run_follows_but_writes_nothing(self):
        os.environ[APPEARANCE_ENV] = "light"
        app = self.boot()
        self.phone("dark")
        app.tick()
        self.assertEqual(app.appearance, "dark")
        self.assertEqual(Settings(self.lib).appearance, "light")

    def test_every_page_draws_its_ground_in_both_modes(self):
        """The category tints are gone (§ 3.1): one ground per mode, every page."""
        app = self.boot()
        for mode in palette.MODES:
            app.set_appearance(mode, persist=False)
            for page in ("SYNTH", "DRUM", "TRACK", "MIXER", "SEQ", "INPUT",
                         "HELP", "FILE", "TEMPO"):
                app.goto(page)
                fb = app.render()
                colours, counts = np.unique(fb.reshape(-1, 3), axis=0,
                                            return_counts=True)
                top = tuple(int(c) for c in colours[counts.argmax()])
                self.assertIn(top, (palette.TABLES[mode]["GROUND"],
                                    palette.TABLES[mode]["PANEL"]), (mode, page))
                self.assertNotIn((0, 0, 0), {tuple(int(v) for v in c) for c in colours},
                                 (mode, page, "black is not in either palette"))

    def test_the_header_is_the_panel_not_the_ground(self):
        """#1137 (t681 row 1): the check above takes either colour as the
        frame's commonest, so a header filled with the ground passed. § 3.1:
        the header is panel, one step darker than the ground in dark mode.
        The pixel is the header's top-right corner: no text, no rule."""
        app = self.boot()
        for mode in palette.MODES:
            app.set_appearance(mode, persist=False)
            fb = app.render()
            self.assertEqual(tuple(int(v) for v in fb[0, zones.WIDTH - 2]),
                             palette.TABLES[mode]["PANEL"], mode)


if __name__ == "__main__":
    unittest.main()
