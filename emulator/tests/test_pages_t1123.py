"""#1123 (the pages half of #632's rest): a refusing card, the 1-2-3-4
confirm, HELP's copy, a value past its range.

The findings are `docs/logs/findings/2026-10-06-m-emu-pages.md` risk 11,
spec 9 and spec 10, and `2026-10-06-m-emu-ui-drivers.md` row 6.
"""

import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import mic                                           # noqa: E402
from app import project_file as PF                            # noqa: E402
from app import tape as T                                     # noqa: E402
from app.keys import note_key                                 # noqa: E402
from pages import file as FP                                  # noqa: E402
from pages import help as H                                   # noqa: E402
from pages import synth as S                                  # noqa: E402
from tests.test_project_file import ProjectCase               # noqa: E402
from ui import frame, zones                                   # noqa: E402


def refuse(*_a, **_k):
    raise OSError(30, "Read-only file system")


class RefusingCard(ProjectCase):
    """Risk 11: a full or read-only card never takes the UI down, and the
    strip never says SAVED for a write the card refused."""

    def setUp(self):
        super().setUp()
        PF.ensure_open(self.app)
        self.record_a_clip(track=0)

    def refuse_sampler(self):
        real = mic.next_sample_path
        self.addCleanup(setattr, mic, "next_sample_path", real)
        mic.next_sample_path = refuse

    def lift(self):
        tp = self.app.tape
        tp.selected = 0
        tp.seek(tp.tracks[0][0].start + 10)
        tp.do_lift()
        self.assertIsNotNone(tp.lift_buffer)

    def test_sampler_file_is_none_on_a_refusing_card(self):
        self.refuse_sampler()
        try:
            file = self.app.sampler_file("bounce")
        except OSError:
            self.fail("the card's refusal reached the key handler")
        self.assertIsNone(file)

    def test_bounce_says_refused(self):
        self.refuse_sampler()
        self.page._run("BOUNCE")
        self.assertEqual(self.app._flash[1], "REFUSED")
        self.assertIsNone(self.app.bounce_job)

    def test_drop_on_synth_says_refused(self):
        self.lift()
        self.refuse_sampler()
        page = self.app.pages["SYNTH"]
        page.state.subpage = S.SUBPAGES.index("ENGINE")
        self.assertFalse(page.drop_tape_buffer())
        self.assertEqual(self.app._flash[1], "REFUSED")

    def test_drop_on_drum_says_refused(self):
        self.lift()
        self.refuse_sampler()
        self.assertFalse(self.app.pages["DRUM"].drop_tape_buffer())
        self.assertEqual(self.app._flash[1], "REFUSED")

    def test_storage_does_not_say_saved_when_the_flush_was_refused(self):
        real = PF._write_text
        self.addCleanup(setattr, PF, "_write_text", real)
        PF._write_text = refuse
        self.app.pages["TEMPO"].p_bpm.set_value(97.0)
        self.app.tick()
        self.page._set_usb_mode(float(FP.USB_MODES.index("STORAGE")))
        self.assertTrue(PF.not_saved(self.app))
        self.assertEqual(self.app._flash[1], "NOT SAVED")

    def test_storage_still_says_saved_when_it_was(self):
        self.page._set_usb_mode(float(FP.USB_MODES.index("STORAGE")))
        self.assertNotEqual(self.app._flash[1], "NOT SAVED")
        self.assertTrue(self.app._flash[0].startswith("SAVED "))


class AnyOtherKeyCancels(ProjectCase):
    """Spec 10: `1 2 PLAY 3 4` or `1 2 <note> 3 4` no longer runs RESET."""

    def setUp(self):
        super().setUp()
        self.page.state.subpage = FP.SYSTEM
        self.page._arm(FP.RESET)
        self.app.on_key("1", True)
        self.app.on_key("1", False)
        self.app.on_key("2", True)
        self.app.on_key("2", False)
        self.assertEqual(self.page._confirm, 2)

    def test_a_transport_key_cancels(self):
        self.app.on_key("PLAY", True)
        self.app.on_key("PLAY", False)
        self.assertIsNone(self.page._armed)

    def test_a_note_cancels(self):
        self.app.on_key(note_key(0), True, 0.9)
        self.app.on_key(note_key(0), False)
        self.assertIsNone(self.page._armed)

    def test_a_note_leaves_loads_browser_open(self):
        """Only an armed run is put down: `disarm` also closes menus."""
        self.page.disarm()
        self.page._open_browser()
        self.app.on_key(note_key(0), True, 0.9)
        self.app.on_key(note_key(0), False)
        self.assertIs(self.app.menu, self.page._browser)
        self.assertIsNotNone(self.page._browser)

    def test_the_run_still_completes_without_one(self):
        for k in ("3", "4"):
            self.app.on_key(k, True)
            self.app.on_key(k, False)
        self.assertEqual(self.page._armed, FP.RESET)   # round two of two
        self.assertEqual(self.page._confirm, 0)


class HelpSaysWhatIsBuilt(unittest.TestCase):
    """Spec 9: HELP's copy describes the instrument, not the demo."""

    def test_synth_names_its_subpages(self):
        for name in S.SUBPAGES:
            self.assertIn(name, H.PAGE_HELP["SYNTH"])

    def test_track_and_lift_count_every_track(self):
        self.assertIn("%d-track" % T.NUM_TRACKS, H.PAGE_HELP["TRACK"])
        self.assertIn("all %d" % T.NUM_TRACKS, H.GLOBAL_HELP["LIFT"])

    def test_seq_entry_is_rec_not_shift(self):
        self.assertNotIn("SHIFT", H.PAGE_HELP["SEQ"])
        self.assertIn("REC", H.PAGE_HELP["SEQ"])

    def test_no_line_says_demo(self):
        for text in list(H.PAGE_HELP.values()) + list(H.GLOBAL_HELP.values()):
            self.assertNotIn("demo", text.lower())


class AValuePastItsRange(ProjectCase):
    """ui-drivers row 6: a value past its range stays in its own cell."""

    def cells(self, level):
        """The param row's bar line (its value text differs: 300%),
        all four cells wide; CLICK LVL is slot 1, ENC2's cell."""
        tempo = self.app.pages["TEMPO"]
        self.app.goto("TEMPO")
        self.assertIs(tempo.slots()[1], tempo.p_level)
        tempo.p_level.value = level
        _, y, _, _ = zones.param_cell(1)
        return frame.render_frame(self.app)[y + 24:y + 29].copy()

    def test_the_bar_is_clamped(self):
        self.assertTrue(np.array_equal(self.cells(1.0), self.cells(3.0)))

    def test_a_hand_edited_document_is_clamped(self):
        doc = PF.capture(self.app, "HAND EDITED")
        doc["metronome"] = {"level": 3.0, "pitch": 1600.0}
        PF.apply(self.app, doc, open_tape=False)
        self.assertEqual(self.app.pages["TEMPO"].p_level.value, 1.0)
        self.assertEqual(self.app.metronome.level, 1.0)


if __name__ == "__main__":
    unittest.main()
