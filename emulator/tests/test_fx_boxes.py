"""Stage I2 — the five effect boxes as the UI sees them.

The boxes' own goldens (`ctest -R delay|chorus|crusher|drive|eq3`) prove the DSP
and `host_fxboxes.py` proves the protocol — instrument rack, master rack,
macros, bypass, tempo. What is left is the emulator's half: that the FX browser
offers them, that the rack page builds its eight cells from each descriptor
without being told anything about these boxes, that a box's own `format_param`
readout reaches the cell, and that the stage-I2 stock racks load whole.

No page code knows the name of any box here. That is the test.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tests.test_native_smoke import NativeCase              # noqa: E402

DELAY_PARAMS = ["TIME", "FDBK", "TONE", "MIX", "PING", "WOW", "DRIVE", "DUCK"]
CHORUS_PARAMS = ["RATE", "DEPTH", "DELAY", "MIX", "FDBK", "SPREAD", "VOICES", "TONE"]
CRUSHER_PARAMS = ["BITS", "RATE", "DRIVE", "MIX", "TONE", "JITTER", None, None]
DRIVE_PARAMS = ["MODE", "DRIVE", "TONE", "MIX", "BIAS", "HP", "SAG", "OUT"]
EQ3_PARAMS = ["LOW", "MID", "HIGH", "MID-F", "LOW-F", "HIGH-F", "MID-Q", "OUT"]

I2_RACKS = {"tape-echo.rack": ("TAPE-ECHO", 1, 2),
            "ensemble.rack": ("ENSEMBLE", 1, 1),
            "handheld.rack": ("HANDHELD", 2, 1),
            "stack.rack": ("STACK", 2, 2),
            "voice-eq.rack": ("VOICE-EQ", 1, 1)}


class FxBoxCase(NativeCase):
    name = "i2"

    def fx(self):
        """SYNTH › FX, showing the rack."""
        self.key("SYNTH")
        self.key("3")
        self.run_blocks(1)
        return self.app.pages["SYNTH"].rack_view

    def put(self, folder_name: str):
        """SHIFT+3 on the empty tab, pick `folder_name`, close the menu."""
        rack = self.fx()
        self.key("3", shift=True)
        folders = [f.name for f in self.app.library.list("fx")]
        self.assertIn(folder_name, folders, folders)
        self.enc(0, folders.index(folder_name))
        self.key("3")
        self.run_blocks(1)
        return rack

    def slot_names(self):
        return [s.name if s else None for s in self.app.pages["SYNTH"].slots()]


class TestBrowser(FxBoxCase):
    def test_every_new_box_is_offered(self):
        folders = [f.name for f in self.app.library.list("fx")]
        for name in ("DELAY", "CHORUS", "CRUSHER", "DRIVE", "EQ3"):
            self.assertIn(name, folders)
        # RACKS stays the first folder: the browser's own order is
        # documents-then-boxes, and five more boxes must not move it.
        self.assertEqual(folders[0], "RACKS")

    def test_the_stock_racks_are_listed(self):
        items = self.app.library.list("fx")[0].items
        for f in I2_RACKS:
            self.assertIn(f, items)


class TestRackPages(FxBoxCase):
    """The eight cells are the descriptor and nothing else."""

    def check(self, folder, expect):
        rack = self.put(folder)
        self.assertEqual(rack.n_tabs, 1)
        self.assertEqual(self.slot_names(), expect)
        return rack

    def test_delay_slots(self):
        self.check("DELAY", DELAY_PARAMS)
        # TIME is the two-sided knob: its readout is the box's, through
        # format_param, and the UI has no table of note values of its own.
        self.assertEqual(self.app.pages["SYNTH"].slots()[0].format_value(), "1/8")

    def test_delay_fdbk_top_reads_hold(self):
        # FDBK's top detent is the hold (#779), and the box says so rather
        # than "0.95"; one detent down is an ordinary feedback (#1027 row 2).
        self.check("DELAY", DELAY_PARAMS)
        self.enc(1, 200)                             # ENC2 = FDBK, to its top
        self.run_blocks(1)
        fdbk = self.app.pages["SYNTH"].slots()[1]
        self.assertEqual(fdbk.format_value(), "HOLD")
        self.enc(1, -1)
        self.run_blocks(1)
        self.assertNotEqual(fdbk.format_value(), "HOLD")

    def test_chorus_slots(self):
        self.check("CHORUS", CHORUS_PARAMS)

    def test_crusher_leaves_two_cells_empty(self):
        self.check("CRUSHER", CRUSHER_PARAMS)
        # TONE's top detent is off, and the box says so rather than "20000 Hz"
        self.assertEqual(self.app.pages["SYNTH"].slots()[4].format_value(), "OFF")

    def test_drive_slots_and_its_mode_enum(self):
        self.check("DRIVE", DRIVE_PARAMS)
        mode = self.app.pages["SYNTH"].slots()[0]
        self.assertTrue(mode.is_discrete())
        self.assertEqual(mode.choices, ["SOFT", "HARD", "FOLD", "FUZZ"])

    def test_eq3_slots(self):
        self.check("EQ3", EQ3_PARAMS)

    def test_a_turn_reaches_the_host(self):
        self.put("EQ3")
        self.enc(0, 20)                              # ENC1 = LOW
        self.run_blocks(1)
        tab = self.native.rack("SYNTH")["tabs"][0]
        self.assertGreater(tab["params"]["low"], 0.6)
        self.assertEqual(tab["engine"], "arbutus/eq3")


class TestAudible(FxBoxCase):
    """Each box on a tab, with a note through it."""

    def test_each_box_passes_sound(self):
        for folder in ("DELAY", "CHORUS", "CRUSHER", "DRIVE", "EQ3"):
            self.put(folder)
            peak = self.play(idx=7, blocks=48)
            self.assertGreater(peak, 1e-4, "%s rendered silence" % folder)


class TestStockRacks(FxBoxCase):
    """SHIFT+3 › RACKS: the whole §6.7 document, macros and all."""

    def test_each_i2_rack_loads_whole(self):
        for fname, (name, tabs, macros) in I2_RACKS.items():
            rack = self.fx()
            self.key("3", shift=True)
            self.enc(0, 0)                           # folder 0 = RACKS
            items = self.app.library.list("fx")[0].items
            self.enc(1, items.index(fname))
            self.key("3")
            self.run_blocks(1)
            doc = self.native.rack("SYNTH")
            self.assertEqual(doc.get("name"), name, fname)
            self.assertEqual(len(doc.get("tabs") or []), tabs, fname)
            self.assertEqual(len(doc.get("macros") or []), macros, fname)
            self.assertGreater(self.play(idx=7, blocks=32), 1e-4, fname)
            self.assertEqual(rack.tab, 0 if tabs > 1 else 1,
                             "%s opened on the wrong tab" % fname)

    def test_a_macro_on_an_i2_box_moves_its_target(self):
        rack = self.fx()
        self.key("3", shift=True)
        self.enc(0, 0)
        items = self.app.library.list("fx")[0].items
        self.enc(1, items.index("voice-eq.rack"))
        self.key("3")
        self.run_blocks(1)
        self.assertEqual(rack.tab, 1, "one engine: the page is that tab")
        # to the master tab, where PRESENCE lives
        self.key("LEFT", shift=True)
        self.assertEqual(rack.tab, 0)
        self.assertEqual(self.app.pages["SYNTH"].slots()[0].name, "PRESENCE")
        # A macro *owns* its target: the §6.7 document keeps the binding and
        # the host writes the target every block, so what moves is the macro's
        # position, never the tab's stored value (PROTOCOL.md § macros).
        self.assertEqual(self.native.macros("SYNTH")[0], 0.0)
        self.enc(0, 100)
        self.run_blocks(2)
        self.assertGreater(self.native.macros("SYNTH")[0], 0.9)
        self.assertGreater(self.play(idx=7, blocks=32), 1e-4)
        self.shot("i2-voice-eq-macro")


class TestScrolledName(FxBoxCase):
    """#329: the browser applies on every detent, so the box a scroll passes
    is on the tab for a moment. The tab, and the strip's LIFTED line, must
    name the box the scroll stopped on."""

    def test_the_strip_names_the_box_after_a_scroll_past_another(self):
        rack = self.fx()
        self.key("3", shift=True)
        folders = [f.name for f in self.app.library.list("fx")]
        self.assertLess(folders.index("BIQUAD"), folders.index("REVERB"), folders)
        for _ in range(folders.index("REVERB")):          # one detent at a time
            self.enc(0, 1)
        self.key("3")
        self.run_blocks(1)
        self.assertEqual(rack.engine_tabs[0]["engine"], "arbutus/reverb")
        self.key("LIFT")
        self.enc(0, 1)
        lifted = [t[1] for t in rack.tickers() if t[1].startswith("LIFTED:")]
        self.assertEqual(len(lifted), 1, rack.tickers())
        self.assertTrue(lifted[0].startswith("LIFTED: REVERB_1."), lifted)

    # #711: LIFT first, *then* browse. The lift names the tab, so it follows
    # the tab's new name when the new box has the same parameter, and is
    # cancelled, said on the strip, when it has not.
    def browse(self, *names):
        """SHIFT+3, then one detent at a time through each of `names`, close."""
        self.key("3", shift=True)
        # the browser's own rows: SAVE THIS RACK sits above the library's (#820)
        folders = self.app.menu.source.folders()
        for name in names:
            at = self.app.menu.state.folder_idx
            step = 1 if folders.index(name) > at else -1
            for _ in range(abs(folders.index(name) - at)):
                self.enc(0, step)
        self.key("3")
        self.run_blocks(1)

    def lifted(self, rack):
        return [t[1] for t in rack.tickers() if t[1].startswith("LIFTED:")]

    def test_a_lift_follows_a_browse_to_a_box_with_the_same_param(self):
        rack = self.put("REVERB")
        self.key("LIFT")
        self.enc(1, 1)                                    # MIX
        self.assertEqual(self.lifted(rack), ["LIFTED: REVERB_1.MIX — PARAM"])
        self.browse("EQ3", "DELAY")                       # past a box with no MIX
        self.assertEqual(rack.engine_tabs[0]["instance"], "delay_1")
        self.assertEqual(self.lifted(rack), ["LIFTED: DELAY_1.MIX — PARAM"])
        self.key("LEFT", shift=True)                      # master
        self.key("DROP")
        self.enc(0, 1)
        self.assertEqual([(t["instance"], t["param"])
                          for t in rack.macro(0)["targets"]],
                         [("delay_1", "mix")])
        self.assertEqual(self.lifted(rack), [])

    def test_a_lift_the_new_box_lacks_is_cancelled_on_the_strip(self):
        rack = self.put("REVERB")
        self.key("LIFT")
        self.enc(0, 1)                                    # SIZE
        self.browse("DELAY")
        self.assertEqual(self.lifted(rack), [])
        strip = [t[1] for t in self.app.tickers()]
        self.assertIn("LIFT CANCELLED: DELAY_1 HAS NO SIZE", strip)
        self.key("LEFT", shift=True)
        self.key("DROP")
        self.enc(0, 1)
        self.assertIsNone(rack.macro(0))

    # #1134: undo puts the old box back, and the lift goes back with it
    def test_undo_after_a_browse_carries_the_lift_back_and_drops(self):
        rack = self.put("REVERB")
        self.key("LIFT")
        self.enc(1, 1)                                    # MIX
        self.browse("EQ3", "DELAY")
        self.assertEqual(self.lifted(rack), ["LIFTED: DELAY_1.MIX — PARAM"])
        self.key("STOP", shift=True)                      # undo the browse
        self.assertEqual(rack.engine_tabs[0]["instance"], "reverb_1")
        self.assertEqual(self.lifted(rack), ["LIFTED: REVERB_1.MIX — PARAM"])
        self.key("LEFT", shift=True)                      # master
        self.key("DROP")
        self.enc(0, 1)
        self.assertEqual([(t["instance"], t["param"])
                          for t in rack.macro(0)["targets"]],
                         [("reverb_1", "mix")])

    # #1134 row 2: a lift a browse cancelled stays cancelled once it closes
    def test_a_cancelled_lift_stays_gone_in_a_later_browse(self):
        rack = self.put("REVERB")
        self.key("LIFT")
        self.enc(0, 1)                                    # SIZE
        self.browse("DELAY", "REVERB")                    # same browse: back
        self.assertEqual(self.lifted(rack), ["LIFTED: REVERB_1.SIZE — PARAM"])
        self.browse("DELAY")                              # cancelled, closed
        self.assertEqual(self.lifted(rack), [])
        self.browse("REVERB")                             # a new browse
        self.assertEqual(self.lifted(rack), [])
        self.key("LEFT", shift=True)
        self.key("DROP")
        self.enc(0, 1)
        self.assertIsNone(rack.macro(0))


if __name__ == "__main__":
    unittest.main()
