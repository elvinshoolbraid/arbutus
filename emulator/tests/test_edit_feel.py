"""Editing-feel fixes from Elfy's first play session (2026-09-09).

Marker tolerance, SHIFT+arrow snapping, and hearing what you enter on SEQ.
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.app import App
from app.keys import Key, note_key
from app.tape import Clip, SAMPLE_RATE, Tape
from tests.native_support import app_for, host


def one_second(level: float = 1.0) -> np.ndarray:
    return np.full((SAMPLE_RATE, 2), level, dtype=np.float32)


STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "state", "test-feel")


class HostCase(unittest.TestCase):
    """An App on its own offline audio process, for the tests that build one."""

    def app(self, **kw):
        native = host(self)
        app, self.audio = app_for(native, STATE_DIR, fresh=True)
        return app


class TestMarkerTolerance(HostCase):
    """Parking on a clip edge counts as being on the clip (within 10 ms)."""

    def setUp(self):
        self.tp = Tape()
        self.tp.tracks[0] = [Clip(SAMPLE_RATE, SAMPLE_RATE)]

    def test_cursor_on_end_marker_resolves_into_the_clip(self):
        self.tp.playhead = 2 * SAMPLE_RATE                  # == clip.end
        self.assertEqual(self.tp._edit_cursor(), 2 * SAMPLE_RATE - 1)

    def test_cursor_on_start_marker_resolves_into_the_clip(self):
        self.tp.playhead = SAMPLE_RATE - 50                 # just before start
        self.assertEqual(self.tp._edit_cursor(), SAMPLE_RATE)

    def test_cursor_beyond_tolerance_is_left_alone(self):
        far = 2 * SAMPLE_RATE + self.tp.edge_tol + 100
        self.tp.playhead = far
        self.assertEqual(self.tp._edit_cursor(), far)

    def test_slide_from_a_marker_moves_the_clip(self):
        self.tp.playhead = 2 * SAMPLE_RATE
        self.tp.do_slide(-SAMPLE_RATE // 2)
        self.assertNotEqual(self.tp.tracks[0][0].start, SAMPLE_RATE)

    def test_lift_from_a_marker_takes_the_clip(self):
        self.tp.playhead = 2 * SAMPLE_RATE
        self.tp.do_lift()
        self.assertEqual(self.tp.tracks[0], [])
        self.assertIsNotNone(self.tp.lift_buffer)

    def test_slice_still_needs_the_cursor_strictly_inside(self):
        self.tp.playhead = 2 * SAMPLE_RATE
        self.tp.do_slice()
        self.assertEqual(len(self.tp.tracks[0]), 1)


class TestTickNavigation(HostCase):
    """SHIFT + ‹ › steps one tick of the ladder (DDR-057/058).

    It replaced marker-to-marker stepping on 2026-09-17: clip edges are drawn
    but are not snap targets, because "one tick" is the one sentence that does
    not change when a loop is on or a clip happens to be nearby.
    """

    def setUp(self):
        self.tp = Tape()
        self.tp.tracks[0] = [Clip(SAMPLE_RATE, SAMPLE_RATE),
                             Clip(3 * SAMPLE_RATE, SAMPLE_RATE)]
        self.tick = float(SAMPLE_RATE)               # one second a tick

    def test_markers_are_clip_edges_plus_zero(self):
        secs = [m / SAMPLE_RATE for m in self.tp.markers()]
        self.assertEqual(secs, [0.0, 1.0, 2.0, 3.0, 4.0])

    def test_a_step_off_the_grid_lands_on_the_adjacent_tick(self):
        self.tp.playhead = int(1.4 * SAMPLE_RATE)
        self.tp.nudge_tick(1, self.tick)
        self.assertEqual(self.tp.playhead, 2 * SAMPLE_RATE)

    def test_stepping_backward_off_the_grid_takes_the_tick_below(self):
        self.tp.playhead = int(2.9 * SAMPLE_RATE)
        self.tp.nudge_tick(-1, self.tick)
        self.assertEqual(self.tp.playhead, 2 * SAMPLE_RATE)

    def test_steps_stay_on_the_grid_and_round_trip(self):
        self.tp.playhead = int(1.4 * SAMPLE_RATE)
        self.tp.nudge_tick(1, self.tick)
        self.tp.nudge_tick(1, self.tick)
        self.assertEqual(self.tp.playhead, 3 * SAMPLE_RATE)
        self.tp.nudge_tick(-1, self.tick)
        self.assertEqual(self.tp.playhead, 2 * SAMPLE_RATE)

    def test_a_clip_edge_is_not_a_snap_target(self):
        # the edges here sit on whole seconds; move the grid off them and the
        # cursor follows the grid, not the clips
        self.tp.playhead = 0
        self.tp.nudge_tick(1, self.tick * 1.5)
        self.assertEqual(self.tp.playhead, 1.5 * SAMPLE_RATE)

    def test_stepping_back_from_zero_stays_put(self):
        self.tp.playhead = 0
        self.tp.nudge_tick(-1, self.tick)
        self.assertEqual(self.tp.playhead, 0)


class TestTrackPageSnapKey(HostCase):
    def test_shift_arrow_steps_a_tick_and_bare_arrow_nudges(self):
        app = self.app()
        app.goto("TRACK")
        tp = app.tape
        tp.tracks[0] = [Clip(SAMPLE_RATE, SAMPLE_RATE)]
        bar = 4 * 60 / app.bpm * SAMPLE_RATE
        tp.playhead = int(1.4 * SAMPLE_RATE)

        app.on_key(Key.SHIFT.value, True)
        app.on_key(Key.RIGHT.value, True)
        app.on_key(Key.RIGHT.value, False)
        app.on_key(Key.SHIFT.value, False)
        self.assertAlmostEqual(tp.playhead, bar, places=3)

        before = tp.playhead
        app.on_key(Key.RIGHT.value, True)
        app.on_key(Key.RIGHT.value, False)
        self.assertGreater(tp.playhead, before)             # bar nudge, not snap


class TestSeqEntryIsAudible(HostCase):
    """Entering steps writes the pattern *and* sounds the note."""

    def test_entry_records_a_step_and_plays_it(self):
        app = self.app()
        app.goto("SEQ")
        seq = app.sequencer
        app.on_key(Key.SEQ.value, True)                 # arm the slot
        app.on_key(Key.SEQ.value, False)
        app.on_key("1", True)                           # hold REC (DDR-041)
        self.assertTrue(seq.entry)

        app.on_key(note_key(0), True)
        self.assertEqual(len(seq.pattern), 1)
        self.assertTrue(app.instrument._held,
                        "entry mode should still sound the key")

        app.on_key(note_key(0), False)
        app.on_key("1", False)
        self.assertFalse(seq.entry)
        self.assertFalse(app.instrument._held)



class TestNoNoteGetsStuck(HostCase):
    """A key always releases the voice it started (first play session, 2026-09-10:
    a held drone under everything, and every press sounded with it)."""

    def test_octave_change_while_held_still_releases(self):
        app = self.app()
        app.on_key(note_key(5), True)
        app.on_key(Key.RIGHT.value, True)        # octave up, mid-note
        app.on_key(Key.RIGHT.value, False)
        app.on_key(note_key(5), False)
        self.assertFalse(app.instruments["SYNTH"]._held)

    def test_page_change_while_held_still_releases(self):
        app = self.app()
        app.on_key(note_key(5), True)
        app.on_key(Key.DRUM.value, True)         # active instrument changes
        app.on_key(Key.DRUM.value, False)
        app.on_key(note_key(5), False)
        self.assertFalse(app.instruments["SYNTH"]._held,
                         "the note was released on the wrong instrument")
        self.assertFalse(app.instruments["DRUM"]._held)

    def test_a_note_released_under_help_still_stops(self):
        """#623: a release while HELP is held was the overlay's, so it never came."""
        app = self.app()
        app.on_key(note_key(5), True)
        app.on_key(Key.HELP.value, True)
        app.on_key(note_key(5), False)
        app.on_key(Key.HELP.value, False)
        self.assertFalse(app.instruments["SYNTH"]._held)
        self.assertEqual(app.page_id, "SYNTH")   # HELP was used: no HELP page

    def test_rec_released_under_help_is_no_longer_held(self):
        app = self.app()
        app.on_key(Key.REC.value, True)
        app.on_key(Key.HELP.value, True)
        app.on_key(Key.REC.value, False)
        app.on_key(Key.HELP.value, False)
        self.assertFalse(app.rec_held)

    def spy(self, obj, name):
        """Record every call to ``obj.name`` (still calling through)."""
        calls, real = [], getattr(obj, name)

        def wrapper(*a, **kw):
            calls.append(a)
            return real(*a, **kw)
        setattr(obj, name, wrapper)
        return calls

    def test_a_key_pressed_under_help_keeps_its_release_there(self):
        """The overlay took the press, so the page never sees the release.

        Spied at the handler boundary (#873, t623 row 6): an empty held map
        also holds when an orphan note_off leaks past the overlay."""
        app = self.app()
        synth = app.instruments["SYNTH"]
        ons, offs = self.spy(synth, "note_on"), self.spy(synth, "note_off")
        app.on_key(Key.HELP.value, True)
        app.on_key(note_key(5), True)
        app.on_key(Key.HELP.value, False)
        app.on_key(note_key(5), False)
        self.assertEqual((ons, offs), ([], []))
        self.assertFalse(synth._held)
        self.assertEqual(app.help_target, None)

    def test_a_key_after_panic_sounds_and_stops(self):
        """#873: panic forgets the keys HELP took, so the same key's next
        release is not swallowed and its note does not hang."""
        app = self.app()
        app.on_key(Key.HELP.value, True)
        app.on_key(note_key(5), True)            # the overlay's
        app.panic()                              # its release never comes
        app.on_key(note_key(5), True)
        self.assertTrue(app.instruments["SYNTH"]._held)
        app.on_key(note_key(5), False)
        self.assertFalse(app.instruments["SYNTH"]._held)

    def test_panic_clears_everything(self):
        app = self.app()
        app.on_key(Key.SHIFT.value, True)
        app.on_key(note_key(5), True)
        app.on_key(note_key(9), True)
        self.assertTrue(app.instrument._held)
        app.panic()
        self.assertFalse(app.instrument._held)
        self.assertFalse(app.shift_held)


class TestSequencerFeel(HostCase):
    """Round two: armed off-page, retranspose without restarting, DROP walks."""

    def _pattern_app(self):
        app = self.app()
        app.goto("SEQ")
        app.on_key(Key.SEQ.value, True)          # tap SEQ again = arm (DDR-035)
        app.on_key(Key.SEQ.value, False)
        app.on_key("1", True)                    # hold the REC soft-key
        for idx in (0, 4, 7):
            app.on_key(note_key(idx), True)
            app.on_key(note_key(idx), False)
        app.on_key("1", False)
        return app, app.sequencer

    def test_ordinary_notes_after_panic_leave_the_pattern(self):
        """#873: panic during REC entry ends it in the mirror as the host's
        reset ends it in the box, so playing afterwards writes no steps."""
        app, seq = self._pattern_app()
        entered = [list(s) for s in seq.pattern]
        app.on_key("1", True)                    # REC held again
        app.panic()
        self.audio.pull(1)                       # the host takes the reset
        self.assertFalse(seq.entry)
        app.on_key(note_key(7), True)
        app.on_key(note_key(7), False)
        app.on_key("1", False)                   # the stray release
        self.assertEqual(seq.pattern, entered)

    def test_a_key_on_another_page_starts_the_sequence(self):
        app, seq = self._pattern_app()
        app.goto("TRACK")
        app.on_key(note_key(2), True)
        self.assertTrue(seq.running)
        # the key reaches both: the box transposes the pattern to it and the
        # host routes the same note into the ENGINE slot, which is what makes
        # a sequenced note sound (§5) — the Python path consumed the key
        self.assertTrue(app.instrument._held)

    def test_active_off_hands_the_keys_back_to_the_instrument(self):
        app, seq = self._pattern_app()
        app.on_key(Key.SEQ.value, True)          # tap SEQ once more = disarm
        app.on_key(Key.SEQ.value, False)
        self.assertFalse(seq.active)
        app.goto("TRACK")
        app.on_key(note_key(2), True)
        self.assertFalse(seq.running)
        self.assertTrue(app.instrument._held)

    def test_a_held_key_keeps_the_pattern_running(self):
        """The clock and the steps are `arbutus/endless`'s now (DDR-041).

        What the UI still owns is the gesture: a held key runs the pattern, a
        second key re-transposes it without stopping, and letting go stops it.
        `audio/tests/host_seq.py` pins the box's own timing.
        """
        app, seq = self._pattern_app()
        app.on_key(note_key(0), True)
        self.assertTrue(seq.running)
        self.assertEqual(seq.trigger_note, 53)
        app.on_key(note_key(4), True)                # a second key, still on
        self.assertTrue(seq.running)
        app.on_key(note_key(4), False)
        app.on_key(note_key(0), False)
        self.assertFalse(seq.running)


class TestLiftDropWalk(HostCase):
    def test_lift_parks_at_the_clip_start_and_drops_walk_forward(self):
        app = self.app()
        app.goto("TRACK")
        tp = app.tape
        tp.tracks[0] = [Clip(2 * SAMPLE_RATE, SAMPLE_RATE)]
        tp.playhead = int(2.6 * SAMPLE_RATE)
        tp.do_lift()
        self.assertEqual(tp.playhead, 2 * SAMPLE_RATE)
        for _ in range(3):
            tp.do_drop()
        spans = [(c.start / SAMPLE_RATE, c.end / SAMPLE_RATE) for c in tp.tracks[0]]
        self.assertEqual(spans, [(2.0, 3.0), (3.0, 4.0), (4.0, 5.0)])


class TestBarSnap(HostCase):
    def test_the_default_zoom_ticks_in_whole_bars(self):
        app = self.app()
        app.goto("TRACK")
        tp = app.tape
        bar = 4 * 60 / app.bpm * SAMPLE_RATE
        tick = app.pages["TRACK"].tick()
        self.assertEqual(tick.kind, "bar")
        self.assertAlmostEqual(tick.samples, bar, places=3)
        tp.playhead = int(0.6 * bar)
        tp.nudge_tick(1, tick.samples)
        self.assertAlmostEqual(tp.playhead, bar, places=3)
        tp.nudge_tick(1, tick.samples)
        self.assertAlmostEqual(tp.playhead, 2 * bar, places=3)


class TestSeqArmGesture(HostCase):
    def test_seq_key_navigates_then_arms_then_disarms(self):
        app = self.app()
        seq = app.sequencer
        app.on_key(Key.SEQ.value, True)
        app.on_key(Key.SEQ.value, False)
        self.assertEqual(app._page_id, "SEQ")
        self.assertFalse(seq.active)

        app.on_key(Key.SEQ.value, True)
        app.on_key(Key.SEQ.value, False)
        self.assertTrue(seq.active)

        app.on_key(Key.SEQ.value, True)
        app.on_key(Key.SEQ.value, False)
        self.assertFalse(seq.active)
        self.assertEqual(app._page_id, "SEQ", "arming never navigates away")


class TestMenuExit(HostCase):
    def test_number_keys_dismiss_an_open_menu_and_pick_the_subpage(self):
        app = self.app()
        app.goto("SYNTH")
        for num, expect in ((Key.K1, 0), (Key.K3, 2)):
            app.on_key(Key.SHIFT.value, True)
            app.on_key(Key.K1.value, True)
            app.on_key(Key.K1.value, False)
            app.on_key(Key.SHIFT.value, False)
            self.assertIsNotNone(app.menu)
            app.on_key(num.value, True)
            app.on_key(num.value, False)
            self.assertIsNone(app.menu, f"{num.value} should close the menu")
            self.assertEqual(app.pages["SYNTH"].state.subpage, expect)

    def test_any_page_key_leaves_an_open_menu(self):
        for target in ("DRUM", "TRACK", "MIXER", "SEQ", "TEMPO"):
            app = self.app()
            app.goto("SYNTH")
            app.on_key(Key.SHIFT.value, True)
            app.on_key(Key.K1.value, True)
            app.on_key(Key.K1.value, False)
            app.on_key(Key.SHIFT.value, False)
            self.assertIsNotNone(app.menu, "SHIFT+1 should open the engine menu")
            app.on_key(target, True)
            app.on_key(target, False)
            self.assertIsNone(app.menu, f"{target} should close the menu")
            self.assertEqual(app._page_id, target)


class TestGotoRefusesAnUnknownPage(HostCase):
    """`goto` on an id that is not a page raises rather than doing nothing (#174).

    `page` is read by another process -- `arbutus-net` publishes it on
    `GET /status` off the autosave snapshot (`device-os-spec.md` 9.11) -- so a
    page renamed on one side has to go red here instead of navigating nowhere.
    """

    def test_an_unknown_id_raises_and_names_the_pages(self):
        app = self.app()
        app.goto("MIXER")
        with self.assertRaises(ValueError) as caught:
            app.goto("MIX")                  # the page is MIXER; this is #173's typo
        said = str(caught.exception)
        self.assertIn("MIX", said)
        self.assertIn("MIXER", said, "the message names the valid set")
        self.assertEqual(app._page_id, "MIXER", "a refused goto navigates nowhere")

    def test_every_real_page_still_goes(self):
        """The control: the guard rejects only ids that are not pages."""
        app = self.app()
        for target in sorted(app.pages):
            app.goto(target)
            self.assertEqual(app._page_id, target)

if __name__ == "__main__":
    unittest.main(verbosity=2)
