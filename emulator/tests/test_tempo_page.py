"""Stage H2: the whole TEMPO page (screen-sketches § TEMPO).

`BPM · CLICK LVL · CLICK PITCH · LINK` / `BPM FINE · COUNT-IN · SYNC · —`,
with `‹ ›` nudging ±1 BPM and ±0.1 under SHIFT.

Three of those eight are new kinds of thing rather than another knob:

* **LINK** is the host's (DDR-059, E1). The page sends `tempo {link}` and
  reads `state.tape.link.on` back — it keeps no copy, and it never touches
  `base_bpm`, which is a project load's business.
* **COUNT-IN** delays the punch by whole bars of click while the transport
  rolls. `Tape` owns the pending frame; `App._native_sync` polls it, so the
  host learns about the take through the ordinary transport fingerprint.
* **SYNC** picks the clock. There is no MIDI clock source in the instrument
  yet (FILE › MIDI is stage J2), so `midi` **locks** the BPM controls rather
  than pretending a number this page can still move came from outside.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.keys import Key                                  # noqa: E402
from tests.native_support import app_for, host, state_dir  # noqa: E402

STATE = state_dir("tempo-page")


class TempoCase(unittest.TestCase):
    def setUp(self):
        self.native = host(self)
        self.app, self.audio = app_for(self.native, STATE, fresh=True)
        self.addCleanup(self.audio.stop)
        self.key(Key.TEMPO.value)
        self.page = self.app.current_page

    def key(self, name, down=None, shift=False):
        if shift:
            self.app.on_key(Key.SHIFT.value, True)
        if down is None:
            self.app.on_key(name, True)
            self.app.on_key(name, False)
        else:
            self.app.on_key(name, down)
        if shift:
            self.app.on_key(Key.SHIFT.value, False)

    def names(self):
        return [s.name if s else None for s in self.page.slots()]

    def link_state(self):
        return (self.native.state.get("tape") or {}).get("link") or {}

    # -- the row -----------------------------------------------------------
    def test_the_page_is_the_sketchs_eight_cells(self):
        self.assertEqual(self.names(),
                         ["BPM", "CLICK LVL", "CLICK PITCH", "LINK",
                          "BPM FINE", "COUNT-IN", "SYNC", None])
        self.assertEqual(self.page.strip_labels(), [None] * 4)

    def test_bpm_fine_is_a_tenth_and_is_the_same_number(self):
        self.app.on_encoder(4, +3)                 # SHIFT row, ENC1
        self.assertAlmostEqual(self.app.bpm, 120.3, places=3)
        # the two cells are one number: BPM reads it back on the next frame
        self.assertEqual(self.page.slots()[0].format_value(), "120")
        self.assertAlmostEqual(self.page.p_bpm.value, 120.3, places=3)
        self.assertAlmostEqual(self.native.state["tempo"], 120.3, places=3)

    # -- the arrows (control-inventory § arrow keys) -----------------------
    def test_the_arrows_nudge(self):
        self.key(Key.RIGHT.value)
        self.assertAlmostEqual(self.app.bpm, 121.0, places=3)
        self.key(Key.LEFT.value)
        self.key(Key.LEFT.value)
        self.assertAlmostEqual(self.app.bpm, 119.0, places=3)

    def test_shift_nudges_by_a_tenth(self):
        self.key(Key.RIGHT.value, shift=True)
        self.assertAlmostEqual(self.app.bpm, 120.1, places=3)
        self.key(Key.LEFT.value, shift=True)
        self.assertAlmostEqual(self.app.bpm, 120.0, places=3)

    def test_the_nudge_reaches_the_host(self):
        self.key(Key.RIGHT.value)
        self.assertAlmostEqual(self.native.state["tempo"], 121.0, places=3)

    # -- LINK (DDR-059) ----------------------------------------------------
    def test_link_is_on_by_default_and_read_from_the_host(self):
        self.assertTrue(self.page.link)
        self.assertEqual(self.page.p_link.format_value(), "ON")

    def test_link_toggles_the_hosts_bit_and_nothing_else(self):
        base = self.link_state().get("base_bpm")
        self.app.on_encoder(3, -1)                 # ENC4 LINK -> off
        self.assertFalse(self.link_state()["on"])
        self.assertFalse(self.page.link)
        self.assertEqual(self.link_state()["factor"], 1.0)
        self.app.on_encoder(3, +1)
        self.assertTrue(self.link_state()["on"])
        # `base_bpm` is a project load's, never this page's (E1)
        self.assertEqual(self.link_state().get("base_bpm"), base)

    def test_link_on_makes_bpm_move_the_tape(self):
        self.app.on_encoder(0, +60)                # 180 BPM against base 120
        self.assertAlmostEqual(self.link_state()["factor"], 1.5, places=3)
        self.app.on_encoder(3, -1)                 # LINK off
        self.assertEqual(self.link_state()["factor"], 1.0)

    # -- COUNT-IN ----------------------------------------------------------
    def set_count_in(self, bars: int):
        self.app.on_encoder(5, bars)               # SHIFT row, ENC2
        self.assertEqual(self.page.count_in_bars, bars)

    def roll(self, seconds: float = 1.0):
        self.audio.pull(int(self.app.sample_rate * seconds))
        self.app.tick()

    def test_count_in_defaults_to_off(self):
        self.assertEqual(self.page.count_in_bars, 0)
        self.assertEqual(self.page.p_count.format_value(), "OFF")

    def test_a_take_waits_out_the_count_then_punches_in(self):
        self.set_count_in(1)
        tp = self.app.tape
        tp.selected = 0
        self.key(Key.REC.value)                    # arm
        self.assertTrue(tp.armed[0])
        self.key(Key.PLAY.value)
        self.assertTrue(tp.playing)
        self.assertFalse(tp.recording, "the take started during the count")
        self.assertTrue(tp.counting_in)
        self.assertIn("COUNT-IN", " ".join(t[1] for t in self.app.tickers()))

        bar = self.app.bar_samples() / self.app.sample_rate
        self.roll(bar * 1.2)
        self.assertFalse(tp.counting_in)
        self.assertTrue(tp.recording, "the punch never landed")
        self.key(Key.STOP.value)

    def test_count_in_off_punches_in_at_once(self):
        tp = self.app.tape
        self.key(Key.REC.value)
        self.key(Key.PLAY.value)
        self.assertTrue(tp.recording)
        self.assertFalse(tp.counting_in)
        self.key(Key.STOP.value)

    def test_rec_during_the_count_cancels_it(self):
        self.set_count_in(2)
        tp = self.app.tape
        self.key(Key.REC.value)
        self.key(Key.PLAY.value)
        self.assertTrue(tp.counting_in)
        self.key(Key.REC.value)
        self.assertFalse(tp.counting_in)
        self.assertFalse(tp.armed[0])
        self.assertFalse(tp.recording)
        self.key(Key.STOP.value)

    def test_stop_drops_a_pending_count(self):
        self.set_count_in(2)
        tp = self.app.tape
        self.key(Key.REC.value)
        self.key(Key.PLAY.value)
        self.assertTrue(tp.counting_in)
        self.key(Key.STOP.value)
        self.assertFalse(tp.counting_in)
        self.assertFalse(tp.recording)

    def test_rec_plus_note_ignores_the_count(self):
        """That gesture *is* "start here, now" (E2's grid-locked punch)."""
        self.set_count_in(2)
        tp = self.app.tape
        self.app.on_key(Key.REC.value, True)
        self.app.on_key("NOTE_0", True)
        self.app.on_key("NOTE_0", False)
        self.app.on_key(Key.REC.value, False)
        self.assertTrue(tp.recording)
        self.assertFalse(tp.counting_in)
        self.key(Key.STOP.value)

    # -- SYNC --------------------------------------------------------------
    def test_sync_defaults_to_free(self):
        self.assertEqual(self.page.p_sync.format_value(), "FREE")
        self.assertFalse(self.page.external_sync)

    def test_midi_sync_locks_the_bpm_controls(self):
        self.app.on_encoder(6, +1)                 # SHIFT row, ENC3
        self.assertTrue(self.page.external_sync)
        slots = self.page.slots()
        self.assertTrue(slots[0].locked and slots[0].dimmed)
        self.assertTrue(slots[4].locked)
        self.app.on_encoder(0, +10)
        self.assertEqual(self.app.bpm, 120.0)
        self.key(Key.RIGHT.value)
        self.assertEqual(self.app.bpm, 120.0)
        self.app.set_bpm(150.0)                    # tap tempo
        self.assertEqual(self.app.bpm, 120.0)

    # -- persistence -------------------------------------------------------
    def test_the_page_saves_what_it_owns(self):
        self.set_count_in(2)
        self.app.on_encoder(6, +1)
        self.app.on_encoder(3, -1)                 # LINK off
        snap = self.page.snapshot()
        self.assertEqual(snap["count_in"], 2)
        self.assertEqual(snap["sync"], "midi")
        self.assertFalse(snap["link"])

        self.page.p_count.set_value(0)
        self.page.p_sync.set_value(0)
        self.page.restore(snap)
        self.assertEqual(self.page.count_in_bars, 2)
        self.assertTrue(self.page.external_sync)
        self.assertFalse(self.page.link)


if __name__ == "__main__":
    unittest.main()
