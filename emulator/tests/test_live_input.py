"""Stage G: the live input in the UI (DDR-063).

SOURCE is one value in three places — the host's `state.input.source`, the
SOURCE knob of `arbutus/live-input` on SYNTH › ENGINE, and the SHIFT-row cell on
INPUT. The tests that matter are the ones that would catch a second copy of it
appearing, so every assertion here reads the host back rather than the page's
own memory.

Spec: `engine-spec.md` §15.2 / §15.6, `screen-sketches.md` § SYNTH › ENGINE and
§ INPUT, `tape-sampling-workflow.md` §5.4.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pages.input import SOURCE_LABELS, SOURCES                # noqa: E402
from pages.mixer import LIVE_INPUT                            # noqa: E402

from tests.test_native_tape import TapeCase                   # noqa: E402

BOX_FOLDER = "live-input"


class LiveInputCase(TapeCase):
    def source(self) -> str:
        return self.native.input_source()

    def folder(self):
        """(index, the folder itself) of the box in the SYNTH library."""
        folders = self.app.library.list("synth")
        names = [f.folder for f in folders]
        self.assertIn(BOX_FOLDER, names, "the box needs a library folder")
        i = names.index(BOX_FOLDER)
        return i, folders[i]

    def load_live_input(self, item=0):
        """Pick the box out of the library the way SHIFT+1 does."""
        self.key("SYNTH")
        page = self.app.pages["SYNTH"]
        page.load_engine(self.folder()[0], item)
        self.run_blocks(1)
        return page

    def names(self, page):
        return [s.name if s else None for s in page.slots()]


# ---------------------------------------------------------------------------
class TestTheBox(LiveInputCase):
    def test_the_box_is_in_the_synth_library_with_source_and_gain(self):
        page = self.load_live_input()
        self.assertEqual(self.names(page)[:2], ["SOURCE", "GAIN"])
        self.assertIsNone(self.names(page)[2], "SOURCE · GAIN · — · —")
        self.assertEqual(page.instrument.engine.engine_id, LIVE_INPUT)

    def test_turning_source_moves_the_hosts_routing(self):
        page = self.load_live_input()
        self.assertEqual(self.source(), "mic")
        self.enc(0, 40)                       # ENC1 to the top of the ENUM
        self.run_blocks(1)
        self.assertEqual(self.source(), "resample")
        self.enc(0, -1)
        self.run_blocks(1)
        self.assertEqual(self.source(), "usb")

    def test_the_resample_patch_selects_resample_as_it_loads(self):
        page = self.load_live_input()
        idx, lf = self.folder()
        self.assertIn("resample.patch", lf.items)
        page.load_engine(idx, lf.items.index("resample.patch"))
        self.run_blocks(1)
        self.assertEqual(self.source(), "resample")

    def test_gain_reads_in_db_from_the_box(self):
        page = self.load_live_input()
        self.assertEqual(page.slots()[1].format_value(), "+0.0 dB")
        self.enc(1, 10)
        self.run_blocks(1)
        self.assertNotEqual(page.slots()[1].format_value(), "+0.0 dB")
        self.assertTrue(page.slots()[1].format_value().endswith("dB"))

    def test_the_engine_page_draws_the_live_waveform_and_says_what_of(self):
        self.load_live_input()
        self.native.input_source("line")
        self.run_blocks(2)
        self.shot("30-live-input-engine")     # evidence; the assertion is that it draws
        self.app.render(self._fb)


# ---------------------------------------------------------------------------
class TestTheEnvPage(LiveInputCase):
    """S11 (#53): a live input is not a note, so it has no envelope.

    The box has declared `honours_env: false` since stage G and nothing drew
    it — SYNTH › ENV showed a working ADSR that the host threw away.
    """

    def env(self):
        page = self.load_live_input()
        self.key("2")                             # SYNTH › ENV
        self.run_blocks(1)
        return page

    def test_the_adsr_is_dim_and_does_not_turn(self):
        page = self.env()
        self.assertFalse(page.honours_env())
        cells = page.slots()[:4]
        self.assertEqual([c.name for c in cells], ["ATK", "DEC", "SUS", "REL"])
        for c in cells:
            self.assertTrue(c.locked and c.dimmed, "%s still turns" % c.name)
        self.shot("32-live-input-env")

    def test_a_turn_moves_nothing_and_the_strip_says_why(self):
        page = self.env()
        before = page.p_atk.value
        self.enc(0, 20)
        self.run_blocks(1)
        self.assertEqual(page.p_atk.value, before)
        self.assertIn("NO ENV", " ".join(t[1] for t in self.app.tickers()))

    def test_the_values_are_kept_so_a_patch_round_trips(self):
        """Dimming is not blanking: the block is still sent and saved, and it
        comes alive the moment a box that honours it is loaded."""
        page = self.env()
        kept = (page.p_atk.value, page.p_dec.value,
                page.p_sus.value, page.p_rel.value)
        self.assertNotEqual(page.slots()[0].format_value(), "—")
        names = [f.folder for f in self.app.library.list("synth")]
        page.load_engine(names.index("fm"), 0)
        self.run_blocks(1)
        self.assertTrue(page.honours_env())
        cells = page.slots()[:4]
        for c in cells:
            self.assertFalse(c.locked or c.dimmed, "%s stayed dead" % c.name)
        self.assertEqual((cells[0].value, cells[1].value,
                          cells[2].value, cells[3].value), kept)
        self.enc(0, 4)
        self.run_blocks(1)
        self.assertNotEqual(page.p_atk.value, kept[0])


# ---------------------------------------------------------------------------
class TestInputPage(LiveInputCase):
    def setUp(self):
        super().setUp()
        self.key("INPUT")
        self.page = self.app.pages["INPUT"]

    def test_source_is_the_shift_row_and_sampling_keeps_the_top(self):
        names = self.names(self.page)
        self.assertEqual(names[:4], ["THRESH", "LENGTH", "GAIN", "MONITOR"])
        self.assertEqual(names[4], "SOURCE")
        self.assertEqual(names[5:], [None, None, None])

    def test_the_cell_sends_and_reads_the_host(self):
        self.page.p_source.set_value(3)
        self.run_blocks(1)
        self.assertEqual(self.source(), "resample")
        self.assertEqual(self.page.slots()[4].format_value(), "RSMP")

        # Moved from the other door: the cell must follow, not remember.
        self.native.input_source("line")
        self.run_blocks(1)
        self.assertEqual(self.page.slots()[4].format_value(), "LINE")

    def test_the_header_names_the_source(self):
        self.native.input_source("usb")
        self.run_blocks(1)
        self.assertEqual(self.page.source_index, SOURCES.index("usb"))
        self.assertEqual(SOURCE_LABELS[self.page.source_index], "USB")
        self.shot("31-input-source")

    def test_source_is_not_in_the_pages_snapshot(self):
        # One fact, one home: `state.input.source` is the save.
        self.native.input_source("usb")
        self.assertEqual(len(self.page.snapshot()["params"]), 4)


# ---------------------------------------------------------------------------
class TestSignalPath(LiveInputCase):
    def rows(self):
        self.key("MIXER")
        return self.app.pages["MIXER"]._signal_rows()

    def test_the_input_row_is_first_and_names_the_source(self):
        self.native.input_source("resample")
        self.run_blocks(1)
        row = self.rows()[0]
        self.assertEqual([label for label, _ in row],
                         ["INPUT", "RESAMPLE", "LIVE INPUT", "MONITOR"])

    def test_live_input_lights_only_when_the_box_is_loaded(self):
        self.assertFalse(dict(self.rows()[0])["LIVE INPUT"])
        self.load_live_input()
        self.assertTrue(dict(self.rows()[0])["LIVE INPUT"])

    def test_monitor_lights_with_the_hosts_monitor(self):
        self.assertFalse(dict(self.rows()[0])["MONITOR"])
        self.native.monitor(True, 1.0)
        self.run_blocks(1)
        self.assertTrue(dict(self.rows()[0])["MONITOR"])


if __name__ == "__main__":
    unittest.main()
