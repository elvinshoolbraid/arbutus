"""Engines on the card, the instrument's half (#1122; engine-spec § 3.9, D125).

1. A patch folder whose engine is not installed stays in the browser, marked
   `NEEDS <ENGINE>`, and a pick says the host's own `NEEDS ENGINE <id>`.
2. A box on the card with no Arbutus signature asks before it first runs
   (desk 49): CANCEL leaves it unloaded, RUN ANYWAY loads it and is kept for
   those exact contents. A stock box as copied loads silently; edited, it asks.
3. The same question when no browser pick names the box (#1161): a project
   opened from FILE › LOAD, a slot letter's record, a whole rack from RACKS.
   CANCEL keeps what was open and playing; the record asked about is kept.

The host runs with a card of its own (`<card>/engines/<author>/<name>/`, the
device's `--library /data`), holding copies of the build's boxes: `fm` and
`drum` recorded as stock, `va` and the `chorus` effect not recorded at all.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import box_trust                                   # noqa: E402
from app import project_file as PF                          # noqa: E402
from app.keys import ENC_PUSH_KEYS                          # noqa: E402
from app.app import App, SAMPLE_RATE                        # noqa: E402
from app.native_library import NativeLibrary                # noqa: E402
from testing.headless_display import HeadlessDisplay        # noqa: E402

from tests.native_support import (BOXES, EVIDENCE, native_or_skip,  # noqa: E402
                                  scratch_library, state_dir)

STOCK = ("fm", "drum")
UNSIGNED = "va"
UNSIGNED_FX = "chorus"           # `fx/racks/ensemble.rack`'s one box


class EngineTrust(unittest.TestCase):
    def setUp(self):
        self.card = tempfile.mkdtemp(prefix="arb-card-")
        self.addCleanup(shutil.rmtree, self.card, True)
        engines = os.path.join(self.card, "engines")
        installed = {}
        for name in STOCK + (UNSIGNED, UNSIGNED_FX):
            src = os.path.join(BOXES, "arbutus", name)
            if not os.path.isdir(src):
                self.skipTest("no built box %s" % src)
            dst = os.path.join(engines, "arbutus", name)
            shutil.copytree(src, dst)
            if name in STOCK:
                installed["arbutus/" + name] = box_trust.tree_hash(dst)
        with open(os.path.join(engines, box_trust.STOCK), "w") as f:
            json.dump({"format": 1, "installed": installed, "removed": []}, f)
        self.native = native_or_skip(
            self, offline=True, libraries=[self.card, scratch_library()])
        self.addCleanup(self.native.close)
        self.app = App(sample_rate=SAMPLE_RATE, fresh=True,
                       state_dir=state_dir("trust"), native=self.native,
                       library=NativeLibrary(self.native,
                                             root=scratch_library()))
        self.page = self.app.pages["SYNTH"]

    # -- the seam ----------------------------------------------------------
    def browse(self):
        """SHIFT+1: the engine browser, opened on what is loaded."""
        self.page._open_menu(0)
        return self.app.menu

    def turn_to(self, name: str):
        menu = self.app.menu
        names = [f.folder for f in self.app.library.list("synth")]
        self.app.on_encoder(0, names.index(name) - menu.state.folder_idx)

    def tap(self, key: str):
        self.app.on_key(key, True)
        self.app.on_key(key, False)

    def run_unsigned(self):
        """Load `va` through the browser's question, then forget the answer
        (a fresh card): the next load of it owes the question again."""
        self.browse()
        self.turn_to(UNSIGNED)
        self.tap("1")
        self.app.close_menu()
        self.assertEqual(self.loaded(), "arbutus/va")
        os.remove(os.path.join(self.card, "engines", box_trust.ANSWERS))

    def loaded(self) -> str:
        return self.native.slot("SYNTH", "engine").get("id", "")

    def shot(self, name):
        fb = np.zeros((240, 320, 3), dtype=np.uint8)
        self.app.render(fb)
        display = HeadlessDisplay()
        display.initialize()
        display.update(fb)
        return display.save_png(os.path.join(EVIDENCE, name + ".png"))

    # -- 1. the mark -------------------------------------------------------
    def test_a_folder_whose_engine_is_missing_is_marked_not_hidden(self):
        menu = self.browse()
        rows = menu.source.folders()
        self.assertIn("NEEDS STRING", rows)       # `synth/string/`, no box
        self.assertIn("FM", rows)                 # installed: no mark
        n = rows.index("NEEDS STRING")
        self.assertTrue(menu.source.marked(n))
        self.assertTrue(menu.source.items(n))     # its patches still listed
        self.turn_to("string")
        self.assertEqual(self.loaded(), "arbutus/fm")
        long, _short, _ = self.app._flash
        self.assertEqual(long, "NEEDS ENGINE arbutus/string")
        self.shot("engine-trust-needs-mark")

    def test_the_default_engine_skips_a_marked_folder(self):
        self.assertEqual(self.loaded(), "arbutus/fm")

    # -- 2. the question ---------------------------------------------------
    def test_an_unsigned_box_asks_and_cancel_leaves_it_unloaded(self):
        self.browse()
        self.turn_to(UNSIGNED)
        self.assertIsNotNone(self.app.prompt)
        self.assertEqual(self.app.prompt.choices, ["RUN", "CANCEL"])
        self.shot("engine-trust-unsigned-question")
        self.app.on_key("2", True)
        self.app.on_key("2", False)
        self.assertIsNone(self.app.prompt)
        self.assertEqual(self.loaded(), "arbutus/fm")

    def test_run_anyway_loads_it_and_is_kept_for_those_contents(self):
        self.browse()
        self.turn_to(UNSIGNED)
        self.app.on_key("1", True)
        self.app.on_key("1", False)
        self.assertEqual(self.loaded(), "arbutus/va")
        self.turn_to("fm")                        # stock: no question
        self.assertIsNone(self.app.prompt)
        self.assertEqual(self.loaded(), "arbutus/fm")
        self.turn_to(UNSIGNED)                    # answered: no question
        self.assertIsNone(self.app.prompt)
        self.assertEqual(self.loaded(), "arbutus/va")
        # its contents change: it asks again
        self.turn_to("fm")
        with open(os.path.join(self.card, "engines", "arbutus", UNSIGNED,
                               "engine.json"), "a") as f:
            f.write("\n")
        self.turn_to(UNSIGNED)
        self.assertIsNotNone(self.app.prompt)

    def test_a_stock_box_edited_on_the_card_asks(self):
        """D125: trust follows the signature, not the folder."""
        self.browse()
        self.turn_to(UNSIGNED)
        self.app.on_key("1", True)               # off fm, so fm is a pick
        self.app.on_key("1", False)
        with open(os.path.join(self.card, "engines", "arbutus", "fm",
                               "engine.json"), "a") as f:
            f.write("\n")
        self.turn_to("fm")
        self.assertIsNotNone(self.app.prompt)
        self.assertEqual(self.loaded(), "arbutus/va")

    def test_another_key_cancels_and_plays(self):
        """D107: the question never costs a move; a turn is CANCEL, then turns."""
        self.browse()
        self.turn_to(UNSIGNED)
        self.app.on_encoder(0, -1)
        self.assertIsNone(self.app.prompt)
        self.assertNotEqual(self.loaded(), "arbutus/va")


    # -- 3. a document naming the box (#1161) -------------------------------
    def open_project(self, saved: str):
        """FILE › LOAD on `saved`, ENC2's push (D98)."""
        self.app.goto("FILE")
        page = self.app.pages["FILE"]
        self.app.on_encoder(1, 1)                 # LOAD, a turn right
        date, _, name = saved.partition("/")
        self.app.on_encoder(0, list(page._listing).index(date))
        self.app.on_encoder(1, page._listing[date].index(name))
        self.app.on_key(ENC_PUSH_KEYS[1], True)
        self.app.on_key(ENC_PUSH_KEYS[1], False)

    def saved_with_unsigned(self) -> str:
        self.run_unsigned()
        PF.save(self.app)
        self.app.flush()
        saved = self.app.open_project
        PF.new(self.app)
        self.assertEqual(self.loaded(), "arbutus/fm")
        return saved

    def test_a_project_naming_an_unsigned_box_asks_and_cancel_keeps_the_open_one(self):
        saved = self.saved_with_unsigned()
        opened = self.app.open_project
        self.open_project(saved)
        self.assertIsNotNone(self.app.prompt)
        self.assertEqual(self.app.prompt.choices, ["RUN", "CANCEL"])
        self.assertIn("VA", self.app.prompt.strip)
        self.shot("engine-trust-project-question")
        self.tap("2")
        self.assertIsNone(self.app.prompt)
        self.assertEqual(self.loaded(), "arbutus/fm")
        self.assertEqual(self.app.open_project, opened)

    def test_a_project_run_anyway_opens_it(self):
        saved = self.saved_with_unsigned()
        self.open_project(saved)
        self.tap("1")
        self.assertEqual(self.app.open_project, saved)
        self.assertEqual(self.loaded(), "arbutus/va")

    def test_a_slot_recall_asks_and_cancel_stays_on_the_slot_playing(self):
        store = self.page.slot_store
        self.tap("B")
        self.run_unsigned()                       # B is `va`
        self.tap("A")                             # A is `fm`: no question
        self.assertIsNone(self.app.prompt)
        self.assertEqual(self.loaded(), "arbutus/fm")
        self.tap("B")
        self.assertIsNotNone(self.app.prompt)
        self.tap("2")
        self.assertEqual(self.loaded(), "arbutus/fm")
        self.assertEqual((store.active, self.page.state.slot_letter), ("A", "A"))
        self.app.flush()
        self.assertEqual(store.get("B")["native_patch"]["engine"]["id"],
                         "arbutus/va")            # the record asked about
        self.tap("B")
        self.tap("1")
        self.assertEqual(self.loaded(), "arbutus/va")
        self.assertEqual(store.active, "B")

    def test_a_rack_naming_an_unsigned_box_asks_before_it_loads(self):
        folders = self.app.library.list("fx")
        f = next(i for i, lf in enumerate(folders) if lf.folder == "racks")
        i = folders[f].items.index("ensemble.rack")
        rack = lambda: [t.get("engine") for t in
                        self.native.slot("SYNTH", "fx").get("tabs") or []]
        self.page._pick_fx(f, i)
        self.assertIsNotNone(self.app.prompt)
        self.assertIn("CHORUS", self.app.prompt.strip)
        self.tap("2")
        self.assertNotIn("arbutus/chorus", rack())
        self.page._pick_fx(f, i)
        self.tap("1")
        self.assertIn("arbutus/chorus", rack())

    def test_doc_ids_reads_every_place_a_box_is_named(self):
        doc = {"engine": {"id": "a/e"}, "seq": {"id": "a/s"},
               "fx": {"tabs": [{"engine": "a/f"}], "requires": ["a/r"]},
               "mod": {"tabs": [{"engine": "a/m"}, {"instance": "x"}]}}
        self.assertEqual(box_trust.doc_ids(doc),
                         ["a/e", "a/s", "a/f", "a/r", "a/m"])
        self.assertEqual(box_trust.doc_ids({"tabs": [{"engine": "a/t"}]}),
                         ["a/t"])
        self.assertEqual(box_trust.doc_ids(None), [])


if __name__ == "__main__":
    unittest.main()
