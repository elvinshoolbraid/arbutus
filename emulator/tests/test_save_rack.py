"""#820 — SAVE THIS RACK: up from the racks saves, down loads (D98).

`control-inventory.md` § Patches, *Saving an effect rack*: the FX browser's
top level has a green row above the racks folder; the cursor still opens on
the racks; scrolled up onto the row the right pane is the name menu (tool
names) under `PICK A NAME TO SAVE RACK`; the commit writes the rack into
SAVED RACKS under the stock racks, and a saved rack loads like a stock one.

The sound check: a crusher's bit depth sets the step between the levels its
output can take, so the smallest step in a rendered note is the rack's
fingerprint, and it does not care where the oscillator's phase starts (two
slots never start a note at the same phase).
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tests import test_native_smoke as smoke                # noqa: E402
from tests.native_support import library_copy               # noqa: E402
from app import name_menu as NM                              # noqa: E402
from app import save_row as SR                               # noqa: E402


def level_step(block) -> float:
    """The smallest gap between two levels the block takes."""
    levels = np.unique(np.round(np.asarray(block, dtype=np.float64), 6))
    gaps = np.diff(levels)
    gaps = gaps[gaps > 1e-6]
    return float(gaps.min()) if len(gaps) else 0.0


class SaveRackCase(smoke.NativeCase):
    """A host and an App over a library of its own: these tests write
    into it, and the per-process scratch copy is shared (#851)."""

    name = "save-rack"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arbutus-rack-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = library_copy(os.path.join(self.tmp, "library"))
        with mock.patch.object(smoke, "scratch_library", lambda: self.lib):
            super().setUp()

    @property
    def saved_dir(self):
        return os.path.join(self.lib, "fx", "racks", "saved")

    def saved(self):
        d = self.saved_dir
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def fx(self):
        """SYNTH › FX, showing the rack."""
        self.key("SYNTH")
        self.key("3")
        self.run_blocks(1)

    def browse(self):
        """SHIFT+3: the effect browser over FX."""
        self.key("3", shift=True)
        menu = self.app.menu
        self.assertIsInstance(menu, SR.SaveRowMenu)
        return menu

    def crush(self, bits_delta=-10):
        """A crusher on the rack, its BITS turned off the default."""
        self.fx()
        self.browse()
        folders = [f.name for f in self.app.library.list("fx")]
        self.enc(0, folders.index("CRUSHER"))
        self.key("3")
        self.run_blocks(1)
        self.enc(0, bits_delta)                      # ENC1 = BITS
        self.run_blocks(1)
        tabs = self.native.rack("SYNTH")["tabs"]
        self.assertEqual([t["engine"] for t in tabs], ["arbutus/crusher"])

    def render(self, idx=7, blocks=24):
        """One note from silence (the crusher keeps no tail to hear)."""
        self.run_blocks(40)
        self.note(idx, True)
        block = self.run_blocks(blocks)
        self.note(idx, False)
        self.run_blocks(4)
        return block

    def to_row(self, menu):
        self.enc(0, -1)
        self.assertTrue(menu.on_row)


class TestTheRow(SaveRackCase):
    def test_the_row_sits_above_the_racks_and_the_cursor_opens_on_them(self):
        self.fx()
        menu = self.browse()
        self.assertEqual(menu.source.folders()[:2], [SR.ROW, "RACKS"])
        self.assertEqual(menu.state.folder_idx, 1)
        self.assertFalse(menu.on_row)
        self.assertEqual(menu.red_strip_cell(), None)
        # the library's own folders are unchanged: the row is the browser's
        self.assertEqual(self.app.library.list("fx")[0].name, "RACKS")

    def test_up_shows_tool_names_and_loads_nothing(self):
        self.crush()
        before = self.native.rack("SYNTH")
        menu = self.browse()
        self.to_row(menu)
        self.run_blocks(1)
        self.assertEqual(self.native.rack("SYNTH"), before, "up loaded")
        names = menu.source.items(0)
        self.assertEqual(len(names), 3)
        for n in names:
            self.assertIn(n.partition("-")[2], NM.TOOLS)
            self.assertNotIn(n, self.app.library.rack_names())
        self.assertEqual(menu.strip_labels()[0], SR.SAVE)
        self.assertEqual(menu.red_strip_cell(), 0)
        self.assertEqual(self.saved(), [], "saved before the commit")
        # down again loads, as the racks folder always has
        self.enc(0, 1)
        self.assertFalse(menu.on_row)
        self.assertEqual(self.native.rack("SYNTH")["name"],
                         os.path.splitext(
                             self.app.library.list("fx")[0].items[0])[0]
                         .upper())

    def test_the_push_of_enc1_saves_the_first_name(self):
        self.crush()
        menu = self.browse()
        self.to_row(menu)
        first = menu.source.items(0)[0]
        self.key("ENC1_PUSH")
        self.assertIsNone(self.app.menu)
        self.assertEqual(self.saved(), [first.lower() + ".rack"])
        folders = [f.name for f in self.app.library.list("fx")]
        self.assertEqual(folders[:2], ["RACKS", "SAVED RACKS"])

    def test_enc2_picks_another_name_and_its_push_commits(self):
        self.crush()
        menu = self.browse()
        self.to_row(menu)
        self.enc(1, 2)
        picked = menu.source.items(0)[2]
        self.key("ENC1_PUSH")                        # not the one just turned
        self.assertIs(self.app.menu, menu)
        self.assertEqual(self.saved(), [])
        self.key("ENC2_PUSH")
        self.assertIsNone(self.app.menu)
        self.assertEqual(self.saved(), [picked.lower() + ".rack"])

    def test_the_red_key_saves_and_stays_on_fx(self):
        self.crush()
        page = self.app.pages["SYNTH"]
        menu = self.browse()
        self.to_row(menu)
        first = menu.source.items(0)[0]
        self.key("1")
        self.assertIsNone(self.app.menu)
        self.assertEqual(self.saved(), [first.lower() + ".rack"])
        self.assertEqual(page.state.subpage, 2, "the red key left FX")

    def test_another_number_key_or_a_page_key_saves_nothing(self):
        self.crush()
        for key in ("2", "SYNTH"):
            menu = self.browse()
            self.to_row(menu)
            self.key(key)
            self.assertIsNone(self.app.menu)
            self.assertEqual(self.saved(), [], key)
            self.fx()

    def test_a_second_save_never_takes_the_first_ones_name(self):
        self.crush()
        menu = self.browse()
        self.to_row(menu)
        self.key("ENC1_PUSH")
        menu = self.browse()
        self.to_row(menu)
        self.assertNotIn(self.saved()[0][:-5].upper(), menu.source.items(0))
        self.key("ENC1_PUSH")
        self.assertEqual(len(self.saved()), 2)

    def test_shots(self):
        self.crush()
        menu = self.browse()
        self.shot("820-01-browser-opens-on-racks")
        self.to_row(menu)
        self.shot("820-02-save-this-rack")
        self.enc(1, 1)
        self.shot("820-03-second-name")
        self.key("ENC2_PUSH")
        menu = self.browse()
        self.enc(0, 1)
        self.shot("820-04-saved-racks")


class TestSavedRackSounds(SaveRackCase):
    def test_a_saved_rack_loads_into_another_slot_with_the_same_sound(self):
        self.crush()
        step_a = level_step(self.render())
        self.assertGreater(step_a, 0.0)
        menu = self.browse()
        self.to_row(menu)
        self.key("ENC1_PUSH")
        self.assertEqual(len(self.saved()), 1)

        # slot B starts as a copy of A: a stock rack makes it another sound
        self.key("B")
        self.run_blocks(1)
        menu = self.browse()
        self.enc(1, self.app.library.list("fx")[0].items.index(
            "low-pass.rack"))
        self.key("3")
        self.run_blocks(1)
        step_b = level_step(self.render())
        self.assertNotAlmostEqual(step_b, step_a, places=4,
                                  msg="slot B already sounds like A's rack")

        # down to SAVED RACKS, its one rack
        menu = self.browse()
        self.enc(0, 1)
        self.assertEqual(menu.source.folders()[menu.state.folder_idx],
                         "SAVED RACKS")
        self.key("3")
        self.run_blocks(1)
        self.assertAlmostEqual(level_step(self.render()), step_a, places=5,
                               msg="the saved rack does not sound like A's")


if __name__ == "__main__":
    unittest.main()
