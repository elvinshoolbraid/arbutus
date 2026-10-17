"""#1123 (the tape half of #632's rest): undo, the scrub grain, LIFT ALL, refresh.

The findings are `docs/logs/findings/2026-10-06-m-emu-app.md` rows 5, 6, 11
and risk 9. Pure `Tape` geometry and a stub socket: no audio process needed.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import tape as T                                 # noqa: E402
from app.native_audio import NativeAudio                  # noqa: E402


def clip(start, n=1000):
    return T.Clip(start, n, 1.0)


class UndoIsSpentOnlyOnAChange(unittest.TestCase):
    """App row 5: the one undo level survives a gesture that changes nothing."""

    def setUp(self):
        self.tp = T.Tape()
        self.tp.tracks[0] = [clip(0), clip(5000)]
        self.tp.seek(5500)
        self.tp.do_lift()                       # the real undo record
        self.tp.seek(20000)                     # empty tape from here on

    def assert_lift_undoes(self):
        self.assertTrue(self.tp.undo())
        self.assertEqual([c.start for c in self.tp.tracks[0]], [0, 5000])

    def test_a_slice_on_empty_tape_keeps_the_lift_undoable(self):
        self.tp.do_slice()
        self.assert_lift_undoes()

    def test_a_slide_with_nothing_under_the_cursor_keeps_it(self):
        self.tp.do_slide(1000)
        self.assert_lift_undoes()

    def test_a_merge_with_nothing_to_join_keeps_it(self):
        self.tp.do_merge()
        self.assert_lift_undoes()

    def test_a_lift_on_empty_tape_keeps_it(self):
        self.tp.do_drop()                       # closes the lift group
        self.tp.seek(30000)
        self.tp.do_lift()
        self.assertTrue(self.tp.undo())         # undoes the DROP, not nothing
        self.assertEqual([c.start for c in self.tp.tracks[0]], [0])

    def test_a_lift_all_on_empty_tape_keeps_it(self):
        self.tp.do_drop()                       # closes the lift group
        self.tp.seek(30000)
        self.tp.do_lift_all()
        self.assertTrue(self.tp.undo())         # undoes the DROP, not nothing
        self.assertEqual([c.start for c in self.tp.tracks[0]], [0])


class ASlideIsOneUndo(unittest.TestCase):
    def test_undo_after_five_detents_puts_the_clip_back(self):
        tp = T.Tape()
        tp.tracks[0] = [clip(10000)]
        tp.seek(10500)
        for _ in range(5):
            tp.do_slide(1000)
            tp.seek(tp.playhead + 1000)         # the cursor rides the clip
        self.assertEqual(tp.tracks[0][0].start, 15000)
        self.assertTrue(tp.undo())
        self.assertEqual(tp.tracks[0][0].start, 10000)

    def test_another_op_closes_the_slide_group(self):
        tp = T.Tape()
        tp.tracks[0] = [clip(10000)]
        tp.seek(10500)
        tp.do_slide(1000)
        tp.seek(11200)
        tp.do_slice()
        self.assertTrue(tp.undo())              # the slice, not the slide
        self.assertEqual([c.start for c in tp.tracks[0]], [11000])


class TheScrubGrainExpires(unittest.TestCase):
    """App row 6: a nudge after the grain has run out reseeds at the cursor."""

    def setUp(self):
        self.calls = []
        calls = self.calls

        class Host:
            def scrub(self, position, frames, reseed):
                calls.append((position, frames, reseed))

        self.now = 100.0
        self.tp = T.Tape(host=Host())
        self.tp.clock = lambda: self.now
        self.tp.seek(48000)
        self.tp.scrub_preview(1)

    def test_a_nudge_inside_the_grain_extends_it(self):
        self.now += T.SCRUB_GRAIN_S / 2
        self.tp.seek(48100)
        self.tp.scrub_preview(1)
        self.assertFalse(self.calls[-1][2])

    def test_a_nudge_after_the_grain_reseeds_at_the_cursor(self):
        self.now += 2.0
        self.tp.seek(48100)
        self.tp.scrub_preview(1)
        self.assertEqual(self.calls[-1][0], 48100)
        self.assertTrue(self.calls[-1][2])


class ALoneLiftAllClipGoesHome(unittest.TestCase):
    """App row 11: LIFT ALL's buffer drops on its own lane, one clip or many."""

    def setUp(self):
        self.tp = T.Tape()
        self.tp.tracks[2] = [clip(1000)]
        self.tp.seek(1500)
        self.tp.do_lift_all()
        self.tp.selected = 0
        self.tp.seek(8000)

    def test_drop_puts_it_back_on_its_own_track(self):
        self.tp.do_drop()
        self.assertEqual(self.tp.tracks[0], [])
        self.assertEqual([c.start for c in self.tp.tracks[2]], [8000])

    def test_the_strip_counts_one_clip(self):
        self.assertTrue(self.tp.lift_buffer.description.startswith("1 CLIP "))

    def test_a_lift_still_drops_on_the_selected_track(self):
        tp = T.Tape()
        tp.tracks[2] = [clip(1000)]
        tp.selected = 2
        tp.seek(1500)
        tp.do_lift()
        tp.selected = 0
        tp.seek(8000)
        tp.do_drop()
        self.assertEqual([c.start for c in tp.tracks[0]], [8000])
        self.assertEqual(tp.tracks[2], [])

    def test_shift_drop_lays_it_on_the_selected_track(self):
        self.assertTrue(self.tp.do_merge_drop())
        self.assertEqual([c.start for c in self.tp.tracks[0]], [8000])
        self.assertEqual(self.tp.tracks[2], [])


class RefreshIsGuarded(unittest.TestCase):
    """App risk 9: a host that died this frame does not raise out of refresh."""

    def test_a_dead_socket_marks_the_link_down_and_keeps_the_state(self):
        class Dead:
            def state(self):
                raise BrokenPipeError(32, "Broken pipe")

        na = NativeAudio.__new__(NativeAudio)   # no process: only the guard
        na.conn = Dead()
        na.state = {"active": "drum"}
        na.errors = []
        na.link_down = False
        try:
            got = na.refresh()
        except OSError as exc:
            self.fail(f"refresh raised {exc!r} out to the key handler")
        self.assertEqual(got, {"active": "drum"})
        self.assertTrue(na.link_down)
        self.assertTrue(na.errors)


if __name__ == "__main__":
    unittest.main()
