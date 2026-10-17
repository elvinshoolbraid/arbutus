"""C1 step 7: what the player sees when the card is behind.

The host's half is `audio/tests/host_tapefull.py`; this is the UI's — the
strip's words and the FILE › SYSTEM read-out, which is all step 7 puts on a
screen (`tape-streaming-design.md` § 3.7, and Elfy's § 7 answers of
2026-09-23: `HARD DRIVE SLOW`, and two words for `TAPE FULL`).

`_absorb_pushes` is driven on a stand-in rather than on a live `NativeAudio`,
because the thing under test is the dispatch and every other attribute it
touches belongs to a host that would have to be started to have one. It is the
**real method**, bound to an object with the fields it reads.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import native_audio as NA                            # noqa: E402
from pages import file as FILEPAGE                            # noqa: E402
from tests.native_support import (app_for, host,               # noqa: E402
                                  library_copy, state_dir)

STATE = state_dir("tape-flags")
#: A throwaway library, because `_system_change` on an editable row writes
#: `settings.json` and the repo's own library is not a scratch directory.
LIB = os.path.join(HERE, "state", "tape-flags-library")


class _Conn:
    def __init__(self, pushes):
        self._p = list(pushes)

    def pushes(self):
        return list(self._p)

    def clear_pushes(self):
        self._p = []


class _Stand:
    """Enough of `NativeAudio` for `_absorb_pushes` to run for real."""

    def __init__(self, pushes):
        self.conn = _Conn(pushes)
        self.said = []
        self.on_xrun = lambda long, short: self.said.append((long, short))
        self.on_meters = None
        self.state = {}
        self.meters = {}
        self.takes = []
        self.song_frame = 0
        self.tape_pos = 0
        self.xrun_flag = False
        self.late_flag = False
        self._wave_cache = {}
        self._wave_gen = 0
        self.changed = 0               # #678's frame gate counter

    absorb = NA.NativeAudio._absorb_pushes


def said_for(*pushes):
    s = _Stand(pushes)
    s.absorb()
    return s


class TestTapeLateFlag(unittest.TestCase):
    def test_the_flag_says_hard_drive_slow(self):
        """Elfy, § 7 q2: the words are `HARD DRIVE SLOW`, not `TAPE LATE`."""
        s = said_for({"type": "tape_late", "count": 4, "flag": True,
                      "total": 4, "tracks": 1, "window_s": 2.0})
        self.assertEqual(s.said, [(NA.LATE_LONG, NA.LATE_SHORT)])
        self.assertEqual(NA.LATE_SHORT, "HARD DRIVE SLOW")
        self.assertNotIn("TAPE LATE", NA.LATE_LONG)

    def test_a_count_without_the_flag_says_nothing(self):
        """The negative control: late blocks under the 3-in-2-s rule are
        counted by the host and are **not** an interruption."""
        s = said_for({"type": "tape_late", "count": 1, "flag": False,
                      "total": 1, "tracks": 1, "window_s": 2.0})
        self.assertEqual(s.said, [])
        self.assertFalse(s.late_flag)

    def test_it_is_said_once_per_rise(self):
        """A flag held across pushes is one interruption, like `xrun`'s."""
        s = said_for({"type": "tape_late", "flag": True, "count": 3},
                     {"type": "tape_late", "flag": True, "count": 5},
                     {"type": "tape_late", "flag": False, "count": 0},
                     {"type": "tape_late", "flag": True, "count": 3})
        self.assertEqual(len(s.said), 2)

    def test_it_does_not_borrow_the_xrun_flag(self):
        """Two alarms, one slot: a late tape must not make `CPU UNHAPPY`
        stop being sayable, and the other way round."""
        s = said_for({"type": "tape_late", "flag": True, "count": 3},
                     {"type": "xrun", "flag": True, "count": 3})
        self.assertEqual(s.said, [(NA.LATE_LONG, NA.LATE_SHORT),
                                  (NA.XRUN_LONG, NA.XRUN_SHORT)])


class TestXrunWords(unittest.TestCase):
    def test_the_strip_says_cpu_unhappy_overload(self):
        """D103 (Play date 1, #843): the instrument says what the app's Status
        says, `CPU UNHAPPY: OVERLOAD`, and the old `CPU NO HAPPY` is gone."""
        s = said_for({"type": "xrun", "flag": True, "count": 3})
        self.assertEqual(s.said, [("CPU UNHAPPY: OVERLOAD", "CPU UNHAPPY")])
        self.assertFalse(any("NO HAPPY" in w for w in s.said[0]))


class TestXrunReachesTheStrip(unittest.TestCase):
    """#1180 (t843 row 1): the stand-in above proves the words; this proves
    the wire. A real App on a real host, an `xrun` push through the real
    dispatcher, and the strip the renderer draws."""

    def setUp(self):
        library_copy(LIB)
        self.native = host(self, library_root=LIB)
        self.app, self.audio = app_for(self.native, STATE, fresh=True,
                                       library_root=LIB)
        self.addCleanup(self.audio.stop)

    def test_the_drawn_strip_says_it(self):
        from ui import frame
        self.app._flash = None
        self.native.conn._pushes.append({"type": "xrun", "flag": True,
                                         "count": 3})
        self.native._absorb_pushes()
        self.assertEqual(frame._strip_text(self.app), NA.XRUN_LONG)


class TestTapeFullWords(unittest.TestCase):
    def test_two_words_not_one(self):
        """Elfy, § 7 q3: `CARD STALLED` when the card stopped answering,
        `TAPE FULL` when there is genuinely no more room."""
        self.assertEqual(said_for({"type": "tape_full", "reason": "stall"}).said,
                         [(NA.CARD_STALLED_LONG, NA.CARD_STALLED_SHORT)])
        for reason in ("disk", "format"):
            self.assertEqual(said_for({"type": "tape_full", "reason": reason}).said,
                             [(NA.TAPE_FULL_LONG, NA.TAPE_FULL_SHORT)],
                             reason)

    def test_an_unknown_reason_is_the_safe_half(self):
        """A newer host with a fourth reason must not tell a player to try
        again — `CARD STALLED` is the one that says the take may work next
        time."""
        s = said_for({"type": "tape_full", "reason": "something-new"})
        self.assertEqual(s.said, [(NA.TAPE_FULL_LONG, NA.TAPE_FULL_SHORT)])

    def test_every_one_is_said(self):
        """Not a flag: each is a take that ended, which is not a rate."""
        s = said_for({"type": "tape_full", "reason": "disk"},
                     {"type": "tape_full", "reason": "disk"})
        self.assertEqual(len(s.said), 2)


class TestSystemTapeRow(unittest.TestCase):
    """FILE › SYSTEM's read-out of `state.tape.pool` (design § 3.7)."""

    def setUp(self):
        library_copy(LIB)
        self.native = host(self, library_root=LIB)
        self.app, self.audio = app_for(self.native, STATE, fresh=True,
                                       library_root=LIB)
        self.addCleanup(self.audio.stop)
        self.app.goto("FILE")
        self.page = self.app.pages["FILE"]

    def cells(self, pool):
        st = self.app.native.state
        st.setdefault("tape", {})
        if pool is None:
            st["tape"].pop("pool", None)
        else:
            st["tape"]["pool"] = pool
        return self.page._tape_cells()

    def test_no_window_says_so(self):
        """Every host until the medium moves onto storage: `ranges` is 0 and
        the whole tape is in memory, which is not a number worth printing."""
        value, editable, note = self.cells({"ranges": 0, "resident": 2,
                                            "queued": 0, "late_total": 0})
        self.assertEqual(value, FILEPAGE.TAPE_ALL_RAM_VALUE)
        self.assertEqual(note, FILEPAGE.TAPE_ALL_RAM)
        self.assertFalse(editable)

    def test_an_older_host_reads_the_same(self):
        """No `pool` in the document at all — the row must not disappear or
        raise; it says what a host with no window says."""
        self.assertEqual(self.cells(None)[0], FILEPAGE.TAPE_ALL_RAM_VALUE)

    def test_a_window_shows_residency(self):
        value, _, note = self.cells({"ranges": 117, "resident": 40,
                                     "queued": 0, "late_total": 0})
        self.assertEqual(value, "40/117")
        self.assertEqual(note, FILEPAGE.TAPE_OK)

    def test_late_reads_are_kept_where_they_can_be_read(self):
        """The flag is three seconds in the strip; the count stays here."""
        _, _, note = self.cells({"ranges": 117, "resident": 40,
                                 "queued": 0, "late_total": 198})
        self.assertEqual(note, FILEPAGE.TAPE_LATE % 198)

    def test_a_backlog_wins_over_the_late_count(self):
        """Ranges waiting for the card is what is happening *now*."""
        _, _, note = self.cells({"ranges": 117, "resident": 40,
                                 "queued": 7, "late_total": 198})
        self.assertEqual(note, FILEPAGE.TAPE_WRITING % 7)

    def test_the_row_is_read_only(self):
        """It is a read-out, and ENC2 on it must change nothing."""
        self.cells({"ranges": 117, "resident": 40, "queued": 0, "late_total": 0})
        rows = {r[0]: r for r in self.page._system_rows()}
        self.assertIn(FILEPAGE.SYS_TAPE, rows)
        self.assertFalse(rows[FILEPAGE.SYS_TAPE][3])
        # A deep copy, because `data` is a dict of dicts and a shallow one
        # would compare a section against itself.
        before = json.dumps(self.app.settings.data, sort_keys=True)
        self.page.system_row = FILEPAGE.SYS_TAPE
        self.page._system_change(1)
        self.page._system_change(-1)
        self.assertEqual(before, json.dumps(self.app.settings.data, sort_keys=True))
        # and the control: a row that *is* editable does move
        self.page.system_row = FILEPAGE.SYS_BRIGHT
        self.page._system_change(-1)
        self.assertNotEqual(before, json.dumps(self.app.settings.data, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
