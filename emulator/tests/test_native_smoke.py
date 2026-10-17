"""`--audio=native --offline`: does the instrument still work?

Everything here is driven through the **input seam** (`ScriptedInput` →
`App.on_key` / `App.on_encoder`), not by calling shim internals, because the
question is whether a player's gestures reach the audio process — not whether
the methods behind them can be called.

Screenshots land in `validation/ui/native/`. They are evidence, **not
baselines**: only Elfy promotes (`emulator/notes/testing.md` § Verification harness).
"""

import glob
import json
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.app import App, SAMPLE_RATE                       # noqa: E402
from app.keys import note_key                              # noqa: E402
from testing.headless_display import HeadlessDisplay       # noqa: E402
from testing.scripted_input import ScriptedInput           # noqa: E402

from tests.native_support import (EVIDENCE, native_or_skip,  # noqa: E402
                                  scratch_library, state_dir)

BLOCK = 256


class NativeCase(unittest.TestCase):
    """One host, one App, one scripted input driver."""

    name = "smoke"

    def setUp(self):
        from drivers.native_backend import NativeOfflineAudio

        self.native = native_or_skip(self, offline=True, block=BLOCK)
        self.addCleanup(self.native.close)
        from app.native_library import NativeLibrary
        # never the checkout's library, which a player may have written in (#851)
        self.app = App(sample_rate=SAMPLE_RATE, fresh=True,
                       state_dir=state_dir(self.name), native=self.native,
                       library=NativeLibrary(self.native, root=scratch_library()))
        self.audio = NativeOfflineAudio(self.native, blocksize=BLOCK)
        self.audio.initialize(SAMPLE_RATE, 2, None)
        self.audio.start()
        self.input = ScriptedInput()
        self.input.initialize()
        self.input.on_control = self.app.on_key
        self.input.on_encoder_turn = self.app.on_encoder
        self.display = HeadlessDisplay()
        self.display.initialize()
        self._fb = np.zeros((240, 320, 3), dtype=np.uint8)

    # -- the seam ----------------------------------------------------------
    def key(self, name, shift=False, down=None):
        name = str(getattr(name, "value", name))
        evs = []
        if shift:
            evs.append(("key", "SHIFT", True))
        if down is None:
            evs += [("key", name, True), ("key", name, False)]
        else:
            evs.append(("key", name, bool(down)))
        if shift:
            evs.append(("key", "SHIFT", False))
        self.input.feed(*evs)
        self.input.poll_events()

    def note(self, idx, down):
        self.key(note_key(idx), down=down)

    def enc(self, idx, delta):
        self.input.feed(("enc", idx, delta))
        self.input.poll_events()

    def run_blocks(self, n):
        block = self.audio.pull(n)
        self.app.tick()
        return block

    def play(self, idx=7, blocks=24):
        self.note(idx, True)
        block = self.run_blocks(blocks)
        self.note(idx, False)
        self.run_blocks(4)
        return float(np.max(np.abs(block))) if len(block) else 0.0

    def shot(self, name):
        self._fb[:] = 0
        self.app.render(self._fb)
        self.display.update(self._fb)
        return self.display.save_png(os.path.join(EVIDENCE, name + ".png"))


class TestStockSynths(NativeCase):
    name = "synths"

    #: `arbutus/live-input` is a synth-role box that makes no sound of its own
    #: (DDR-063): it passes the host's input, and offline there is no input. It
    #: still has to draw its slots, so it stays in the loop with the peak
    #: assertion lifted rather than being skipped.
    SILENT = {"live-input"}

    def test_every_stock_synth_makes_a_sound(self):
        page = self.app.pages["SYNTH"]
        folders = self.app.library.list("synth")
        self.assertTrue(folders, "no synth boxes in audio/library")
        for i, lf in enumerate(folders):
            page.load_engine(i, 0)
            self.run_blocks(1)
            peak = self.play()
            print("  %-10s %-16s peak %.4f" % (lf.folder, lf.items[0], peak))
            if lf.folder not in self.SILENT:
                self.assertGreater(peak, 1e-3, "%s rendered silence" % lf.folder)
            self.assertTrue(page.slots()[0], "%s drew no slots" % lf.folder)
        self.shot("01-synth-engine")

    def test_slots_come_from_the_descriptor(self):
        page = self.app.pages["SYNTH"]
        folders = [f.folder for f in self.app.library.list("synth")]
        page.load_engine(folders.index("fm"), 0)
        names = [s.name if s else None for s in page.slots()]
        self.assertEqual(names[:3], ["RATIO", "INDEX", "LEVEL"])
        # RATIO is an ENUM in the descriptor: a value label, never a bar (§3.5)
        self.assertTrue(page.slots()[0].is_discrete())
        self.assertEqual(page.slots()[0].choices[:2], ["0.5", "1"])


class TestDrumKit(NativeCase):
    name = "drum"

    def test_a_kit_loads_and_a_pad_sounds(self):
        self.key("DRUM")
        self.run_blocks(1)
        inst = self.app.instruments["DRUM"]
        self.assertTrue(inst.item, "no kit loaded onto DRUM")
        self.assertEqual(self.native.slot("DRUM", "engine").get("id"),
                         "arbutus/drum")
        peak = self.play(idx=0, blocks=16)
        print("  kit %s pad 0 peak %.4f" % (inst.item, peak))
        self.assertGreater(peak, 1e-3)
        self.shot("04-drum-kit")


class TestSeqSoftkeys(NativeCase):
    name = "seq"

    def test_softkeys_reach_the_endless_engine(self):
        self.key("SEQ")
        self.run_blocks(1)
        seq = self.app.sequencer
        page = self.app.pages["SEQ"]
        self.assertEqual(page.strip_labels(), ["REC", "REST", "UNDO", "CLEAR"])
        self.assertFalse(seq.active)

        self.key("SEQ")                       # DDR-035: arms the pattern
        self.assertTrue(seq.active)
        self.assertEqual(self.native.slot("SYNTH", "seq").get("id"),
                         "arbutus/endless")

        # hold the REC soft-key and play three notes: that is entry now
        self.key("1", down=True)
        self.run_blocks(1)
        for n in (0, 4, 7):
            self.note(n, True)
            self.run_blocks(1)
            self.note(n, False)
            self.run_blocks(1)
        self.key("1", down=False)
        self.run_blocks(1)
        self.assertEqual(seq.pattern, [[53], [57], [60]])
        self.shot("05-seq-softkeys")

        # hold the root: the box plays the pattern, the emulator does not
        self.note(0, True)
        block = self.run_blocks(180)
        self.note(0, False)
        peak = float(np.max(np.abs(block)))
        print("  endless peak %.4f over %d frames" % (peak, len(block)))
        self.assertGreater(peak, 1e-2)


class TestRack(NativeCase):
    name = "rack"

    def _open_fx(self):
        self.key("3")
        self.run_blocks(1)
        return self.app.pages["SYNTH"].rack_view

    def test_lift_drop_binds_a_macro_and_the_macro_is_audible(self):
        rack = self._open_fx()
        self.assertEqual(rack.n_tabs, 0)
        self.assertTrue(rack.on_empty_tab, "an empty next tab must be reachable")
        self.shot("02-rack-empty-tab")

        # SHIFT+3 on the empty tab: the effect selector picks the engine
        self.key("3", shift=True)
        folders = [f.name for f in self.app.library.list("fx")]
        self.assertIn("BIQUAD", folders)
        self.enc(0, folders.index("BIQUAD"))       # applied live as you scroll
        self.key("3")                              # 1-4 closes the menu
        self.run_blocks(1)
        self.assertEqual(rack.n_tabs, 1)
        self.assertEqual(rack.engine_tabs[0]["engine"], "arbutus/biquad")
        self.assertFalse(rack.on_empty_tab, "one engine: the page is that tab")
        self.shot("03-rack-one-engine")

        # LIFT, turn a param: the address goes in the lift buffer (DDR-039)
        self.key("LIFT")
        self.assertEqual(rack.mode, "copy")
        self.assertTrue(all(s.highlight for s in self.app.pages["SYNTH"].slots()
                            if s is not None))
        self.enc(0, 1)
        self.assertIsNotNone(rack.lifted)
        self.assertEqual(rack.lifted[1], "cutoff")
        strip = " ".join(t[1] for t in self.app.tickers())
        self.assertIn("LIFTED", strip)

        # master tab, DROP, turn a knob: bound
        self.key("LEFT", shift=True)
        self.assertEqual(rack.tab, 0)
        self.key("DROP")
        self.assertEqual(rack.mode, "paste")
        self.enc(0, 1)
        macros = self.native.rack("SYNTH").get("macros") or []
        self.assertEqual(len(macros), 1, "bind did not reach the host")
        target = macros[0]["targets"][0]
        self.assertEqual((target["instance"], target["param"]),
                         (rack.engine_tabs[0]["instance"], "cutoff"))
        self.shot("06-rack-master-bound")

        # turn the macro: the host writes the target, and it is audible
        self.enc(0, -100)                       # macro to 0 -> cutoff at 20 Hz
        self.run_blocks(1)
        dark = self.play(blocks=24)
        self.enc(0, 100)                        # macro to 1 -> cutoff wide open
        self.run_blocks(1)
        bright = self.play(blocks=24)
        print("  macro 0 peak %.5f, macro 1 peak %.5f" % (dark, bright))
        self.assertGreater(bright, dark * 4.0,
                           "turning the macro changed nothing audible")

    def test_range_popup_edits_the_binding(self):
        rack = self._open_fx()
        self.key("3", shift=True)
        folders = [f.name for f in self.app.library.list("fx")]
        self.enc(0, folders.index("REVERB"))
        self.key("3")
        self.key("LIFT")
        self.enc(0, 1)
        self.key("LEFT", shift=True)
        self.key("DROP")
        self.enc(0, 1)
        self.key("DROP", shift=True)            # [TBD-Elfy] the range gesture
        self.assertEqual(rack.mode, "range")
        names = [s.name if s else None for s in self.app.pages["SYNTH"].slots()]
        self.assertEqual(names[:4], ["LO", "HI", "CURVE", "DEPTH"])
        self.shot("07-rack-range")
        self.enc(0, 12)                          # LO up
        target = self.native.rack("SYNTH")["macros"][0]["targets"][0]
        self.assertAlmostEqual(target["lo"], 0.12, places=3)
        self.enc(2, 1)                           # CURVE lin -> exp
        target = self.native.rack("SYNTH")["macros"][0]["targets"][0]
        self.assertEqual(target["curve"], "exp")

    def test_a_stock_rack_loads_whole(self):
        rack = self._open_fx()
        self.key("3", shift=True)
        folders = [f.name for f in self.app.library.list("fx")]
        self.assertEqual(folders[0], "RACKS")
        items = self.app.library.list("fx")[0].items
        self.enc(1, items.index("colourbass.rack"))
        self.key("3")
        self.run_blocks(1)
        doc = self.native.rack("SYNTH")
        self.assertEqual(doc.get("name"), "COLOURBASS")
        self.assertEqual(len(doc.get("tabs") or []), 3)
        self.assertEqual(len((doc.get("macros") or [])[0]["targets"]), 4,
                         "GRIT is one macro on four targets (DDR-039)")
        self.assertNotIn("parallel", doc, "parallel groups were cut from v1 (engine-spec §6.5)")
        self.assertEqual(rack.tab, 0, "a multi-engine rack opens on master")
        self.shot("08-rack-colourbass")
        self.assertGreater(self.play(), 1e-3)


class TestLibraryDocuments(NativeCase):
    """Part C: every document in audio/library loads, or says why (§7.4)."""

    name = "library"

    def test_every_document_loads(self):
        root = scratch_library()       # the checkout's stock, not a player's (#851)
        checked = 0
        for path in sorted(glob.glob(os.path.join(root, "**", "*.patch"),
                                     recursive=True)):
            with open(path) as f:
                doc = json.load(f)
            reply = self.native.load_patch("SYNTH", doc)
            self.assertNotEqual(reply.get("type"), "error",
                                "%s: %s" % (path, reply.get("message")))
            checked += 1
        for path in sorted(glob.glob(os.path.join(root, "**", "*.rack"),
                                     recursive=True)):
            with open(path) as f:
                doc = json.load(f)
            reply = self.native.load_rack("SYNTH", doc)
            self.assertNotEqual(reply.get("type"), "error",
                                "%s: %s" % (path, reply.get("message")))
            checked += 1
        for path in sorted(glob.glob(os.path.join(root, "drum", "**", "kit.json"),
                                     recursive=True)):
            # `drum/<box>/<kit>/kit.json`: the folder under the category *is*
            # the box (app/native_library.py). Since stage I1 that matters —
            # `arbutus/dbox`'s kits are pad params with no files in them, and
            # `arbutus/drum` refuses a kit it cannot open a sample from.
            box = os.path.basename(os.path.dirname(os.path.dirname(path)))
            self.native.load_engine("DRUM", "engine", "arbutus/%s" % box)
            ok = self.app.library.load_kit_folder("DRUM", os.path.dirname(path))
            self.assertTrue(ok, "%s did not load" % path)
            checked += 1
        print("  %d format-1 documents loaded" % checked)
        self.assertGreaterEqual(checked, 18)

    def test_a_missing_engine_is_refused_by_name(self):
        """DDR-043: it says which engine, and loads nothing."""
        before = json.dumps(self.native.rack("SYNTH"))
        reply = self.native.load_rack("SYNTH", {
            "format": 1, "kind": "rack", "name": "NOPE",
            "requires": ["arbutus/formant"],
            "tabs": [{"instance": "f", "engine": "arbutus/formant",
                      "params": {}}], "macros": []})
        self.assertEqual(reply.get("type"), "error")
        self.assertEqual(reply.get("message"), "NEEDS ENGINE arbutus/formant")
        self.assertEqual(json.dumps(self.native.rack("SYNTH")), before)


class TestSlotsAndPatches(NativeCase):
    name = "patches"

    def test_a_slot_records_a_format_1_patch(self):
        page = self.app.pages["SYNTH"]
        snap = page.snapshot()
        doc = snap.get("native_patch")
        self.assertIsNotNone(doc, "the slot record carries no patch document")
        self.assertEqual((doc["format"], doc["kind"]), (1, "patch"))
        self.assertIn("engine", doc)
        self.assertIn("env", doc)

    def test_slot_recall_round_trips_through_the_host(self):
        page = self.app.pages["SYNTH"]
        folders = [f.folder for f in self.app.library.list("synth")]
        page.load_engine(folders.index("noise"), 0)
        self.enc(2, -20)                       # LEVEL down on arbutus/noise
        snap = page.snapshot()
        was = self.native.slot("SYNTH", "engine")["params"]["level"]

        page.load_engine(folders.index("osc"), 0)
        self.assertEqual(self.native.slot("SYNTH", "engine")["id"],
                         "arbutus/osc")
        page.restore(snap)
        self.assertEqual(self.native.slot("SYNTH", "engine")["id"],
                         "arbutus/noise")
        self.assertAlmostEqual(
            self.native.slot("SYNTH", "engine")["params"]["level"], was,
            places=5)


if __name__ == "__main__":
    unittest.main()
