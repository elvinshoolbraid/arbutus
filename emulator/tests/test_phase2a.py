"""Phase-2a trace: DRUM, tape, TRACK, MIXER — driven through the test seam.

Same rig as phase 1 (ScriptedInput + HeadlessDisplay + NullAudio). Nothing
below reaches into the renderer; screens are produced by pressing keys.
PNGs land in validation/ui/phase2a/.
"""

import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.app import App, SAMPLE_RATE                      # noqa: E402
from tests.native_support import app_for, host      # noqa: E402
from app.keys import Key, note_key                        # noqa: E402
from app import tape as T                                 # noqa: E402
from testing.headless_display import HeadlessDisplay      # noqa: E402
from testing.scripted_input import ScriptedInput          # noqa: E402

OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "validation", "ui", "phase2a"))
STATE_DIR = os.path.join(HERE, "state", "test2a")

BLOCK = 512


class Rig:
    def __init__(self):
        self.native = host(owner=self, block=BLOCK)
        self.app, self.audio = app_for(self.native, STATE_DIR, fresh=True, block=BLOCK)
        self.display = HeadlessDisplay()
        self.display.initialize()
        self.input = ScriptedInput()
        self.input.on_control = self.app.on_key
        self.input.on_encoder_turn = self.app.on_encoder
        self.input.initialize()
        self._fb = np.zeros((240, 320, 3), dtype=np.uint8)

    def send(self, *events):
        self.input.feed(*events)
        self.input.poll_events()

    def tap(self, key):
        self.send(("key", key, True), ("key", key, False))

    def shift_tap(self, key):
        self.send(("key", Key.SHIFT, True))
        self.tap(key)
        self.send(("key", Key.SHIFT, False))

    def run_audio(self, seconds: float):
        out = self.audio.pull(max(1, int(seconds * SAMPLE_RATE / BLOCK)))
        self.app.tick()        # the playhead and the meters come from the host
        return out

    def frame(self, name=None):
        self._fb[:] = 0
        self.app.render(self._fb)
        self.display.update(self._fb)
        if name:
            return self.display.save_png(os.path.join(OUT_DIR, f"{name}.png"))
        return None

    def strip(self) -> str:
        """The dynamic strip's text, via the same path the renderer uses."""
        from ui.frame import _strip_text
        return _strip_text(self.app)


def peak(app, track: int = 0) -> float:
    """The loudest sample on a track, read back out of the host's medium.

    The clip list is geometry; `clip_peaks` is how the UI (and a test) sees
    what a clip actually holds.
    """
    out = 0.0
    for c in app.tape.tracks[track]:
        got = app.native.clip_peaks(track, c.src, c.length, 32,
                                    src_track=c.on(track))
        if got:
            out = max(out, max(got["max"], default=0.0),
                      -min(got["min"], default=0.0))
    return out


class TestDrumPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(OUT_DIR, exist_ok=True)

    @staticmethod
    def app_engine(app):
        return app.native.slot(app.active_instrument_id, "engine").get("id")

    def test_01_drum_engine(self):
        rig = Rig()
        rig.tap(Key.DRUM)
        app = rig.app
        self.assertEqual(app.current_page.breadcrumb(), ["DRUM", "ENGINE"])
        # DDR-034: visiting DRUM makes it the active instrument
        self.assertEqual(app.active_instrument_id, "DRUM")
        # the eight slots are the drum box's own (engine-spec §15.3); the
        # per-pad editor is stage N2
        names = [s.name if s else None for s in app.current_page.slots()]
        self.assertEqual(names[:4], ["START", "END", "PITCH", "LEVEL"])
        self.assertEqual(self.app_engine(app), "arbutus/drum")

        rig.send(("key", note_key(4), True))
        audio = rig.audio.pull(6)
        self.assertGreater(float(np.max(np.abs(audio))), 1e-3)
        rig.send(("key", note_key(4), False))
        p = rig.frame("01-drum-engine")
        self.assertTrue(os.path.exists(p))

        rig.tap(Key.SYNTH)
        self.assertEqual(app.active_instrument_id, "SYNTH")


class TestTapeTrace(unittest.TestCase):
    """workflow §5.2: record → slice → lift → drop → undo."""

    @classmethod
    def setUpClass(cls):
        os.makedirs(OUT_DIR, exist_ok=True)
        rig = cls.rig = Rig()
        rig.tap(Key.TRACK)
        rig.frame("02-track-empty")

    def test_01_empty(self):
        app = self.rig.app
        self.assertEqual(app.current_page.breadcrumb(), ["TRACK"])
        self.assertFalse(app.tape.has_audio())

    def test_02_record_two_bars(self):
        rig = self.rig
        app = rig.app
        rig.tap(Key.K1)                       # select TRK1
        self.assertEqual(app.tape.selected, 0)
        rig.tap(Key.REC)                      # arm
        self.assertTrue(app.tape.armed[0])
        rig.send(("key", note_key(7), True))  # hold a note
        rig.tap(Key.PLAY)
        self.assertTrue(app.tape.recording)
        self.assertIn("RECORDING", rig.strip())
        bars = 2 * 4 * 60.0 / app.bpm         # 2 bars at 120 BPM = 4 s
        rig.run_audio(bars)
        rig.send(("key", note_key(7), False))
        rig.tap(Key.STOP)

        clips = app.tape.tracks[0]
        self.assertEqual(len(clips), 1)
        self.assertGreater(peak(app), 1e-3)
        self.assertGreater(clips[0].length, int(3.5 * SAMPLE_RATE))
        rig.frame("03-track-recorded")

    def test_03_slice_and_lift(self):
        rig = self.rig
        app = rig.app
        rig.tap(Key.LEFT)                     # rewind a bar, into the clip
        self.assertLess(app.tape.playhead, 4.0 * SAMPLE_RATE)
        rig.tap(Key.SLICE)
        self.assertEqual(len(app.tape.tracks[0]), 2)
        self._clips_before_drop = None
        rig.tap(Key.LIFT)
        self.assertEqual(len(app.tape.tracks[0]), 1)
        self.assertIsNotNone(app.tape.lift_buffer)
        self.assertIn("LIFTED", rig.strip())
        self.assertIn("REMOVED", rig.strip())
        rig.frame("04-track-slice-lift")

    def test_04_drop(self):
        rig = self.rig
        app = rig.app
        rig.tap(Key.DROP)
        self.assertEqual(len(app.tape.tracks[0]), 2)
        self.assertGreater(peak(app), 1e-3)
        rig.frame("05-track-drop")

    def test_05_undo(self):
        rig = self.rig
        app = rig.app
        rig.shift_tap(Key.STOP)               # SHIFT+STOP = 1-level undo
        self.assertEqual(len(app.tape.tracks[0]), 1)
        rig.frame("06-track-undo")

    def test_06_zoom(self):
        rig = self.rig
        app = rig.app
        before = app.current_page.state.view_len
        rig.send(("enc", 1, 6))               # ENC2 = zoom in from the playhead
        self.assertLess(app.current_page.state.view_len, before)
        rig.frame("07-track-zoomed")

    def test_06b_zoom_reaches_sample_detail(self):
        """ZOOM must go deep enough to place a trim on the waveform."""
        from pages.track import MIN_VIEW_SECONDS
        rig = self.rig
        page = rig.app.current_page
        rig.send(("enc", 1, 40))              # keep turning in
        self.assertAlmostEqual(page.state.view_len,
                               MIN_VIEW_SECONDS * SAMPLE_RATE, places=3)
        rig.frame("07b-track-sample-zoom")
        rig.send(("enc", 1, -40))             # back out for the loop trace

    def test_07_loop(self):
        rig = self.rig
        app = rig.app
        tp = app.tape
        tp.seek(1.0 * SAMPLE_RATE)
        rig.tap(Key.A)                        # IN
        tp.seek(9.0 * SAMPLE_RATE)
        rig.tap(Key.B)                        # OUT
        rig.tap(Key.C)                        # LOOP on
        self.assertTrue(tp.loop_on)
        self.assertGreater(tp.loop_out, tp.loop_in)
        # a loop longer than 4 bars forces a zoom-out
        self.assertGreaterEqual(app.current_page.state.view_len,
                                (tp.loop_out - tp.loop_in))
        rig.frame("08-track-loop")

    def test_08_filter_hold_relabels_enc4(self):
        rig = self.rig
        app = rig.app
        rig.send(("key", Key.Z, True))        # hold FILTER
        rig.send(("enc", 3, 4))               # ENC4 sweeps the cutoff
        slot = app.current_page.slots()[3]
        self.assertEqual(slot.name, "FILTER")
        # long form alone, short form when concatenated with LIFTED (DDR-031)
        longs = [t[1] for t in app.tickers()]
        self.assertTrue(any(l.startswith("FILTER LP") for l in longs), longs)
        self.assertIn("LP", rig.strip())
        rig.frame("09-track-filter-hold")
        rig.send(("key", Key.Z, False))
        self.assertEqual(app.current_page.slots()[3].name, "LEVEL")

    def test_09_speed_hold_relabels_enc4(self):
        rig = self.rig
        app = rig.app
        rig.send(("key", Key.Y, True))        # hold SPEED
        rig.send(("enc", 3, -4))
        slot = app.current_page.slots()[3]
        self.assertEqual(slot.name, "SPEED")
        longs = [t[1] for t in app.tickers()]
        self.assertTrue(any(l.startswith("SPEED ×") for l in longs), longs)
        self.assertIn("×", rig.strip())
        rig.send(("key", Key.Y, False))

    def test_10_mixer_meters_during_playback(self):
        rig = self.rig
        app = rig.app
        rig.tap(Key.C)                        # loop off, play the whole tape
        app.tape.seek(0)
        rig.tap(Key.PLAY)
        self.assertTrue(app.tape.playing)
        rig.run_audio(0.5)
        rig.tap(Key.MIXER)
        self.assertEqual(app.current_page.breadcrumb(), ["MIXER", "MIX"])
        self.assertGreater(app.mixer.track_peaks[0], 1e-3)
        self.assertGreater(app.mixer.master_peak, 1e-3)
        rig.frame("10-mixer-mix")

        rig.tap(Key.K4)
        self.assertEqual(app.current_page.breadcrumb(), ["MIXER", "MASTER"])
        names = [s.name if s else None for s in app.current_page.slots()]
        # v1's MASTER strip (DDR-062, stage F2): the demo's two knobs plus
        # DRIVE, BAL and the SHIFT row
        self.assertEqual(names, ["LEVEL", "COMP", "DRIVE", "BAL",
                                 "COMP REL", "DRIVE TONE", None, None])
        rig.frame("11-mixer-master")
        rig.tap(Key.STOP)

    def test_11_mix_shift_row_is_pans(self):
        rig = self.rig
        app = rig.app
        rig.tap(Key.MIXER)
        rig.tap(Key.K1)
        before = app.mixer.pans[0]
        rig.send(("key", Key.SHIFT, True))
        rig.send(("enc", 0, 3))
        rig.send(("key", Key.SHIFT, False))
        self.assertNotAlmostEqual(app.mixer.pans[0], before)


class TestTransportEverywhere(unittest.TestCase):
    def test_play_from_synth_page(self):
        rig = Rig()
        rig.tap(Key.SYNTH)
        self.assertEqual(rig.app.tape.state, T.STOPPED)
        rig.tap(Key.PLAY)
        self.assertTrue(rig.app.tape.playing)
        rig.tap(Key.STOP)
        self.assertEqual(rig.app.tape.state, T.STOPPED)


class TestMenuEmptySlots(unittest.TestCase):
    """A menu's empty slots 4-7 render '—', never the fine-tune fallback."""

    def test_menu_slots_are_discrete_so_no_fine_fallback(self):
        rig = Rig()
        rig.tap(Key.SYNTH)
        rig.shift_tap(Key.K1)
        slots = rig.app.current_page.slots()
        self.assertEqual(slots[0].name, "FOLDER")
        self.assertTrue(slots[0].is_discrete())
        self.assertTrue(slots[1].is_discrete())
        self.assertEqual(slots[4:], [None] * 4)


class TestClipOps(unittest.TestCase):
    """The pure functions, exercised without the UI.

    They are **geometry** now: a clip says where it plays and where its audio
    sits in the host's medium (`src_offset` / `src_track`), and that is what
    every op has to get right. The DSP these used to check — reverse, speed,
    the filter bake — is `tape_edit` in the audio process and is pinned by
    `audio/tests/host_clips.py`.
    """

    def _clip(self, start=0, n=1000, src=None):
        return T.Clip(start, n, 1.0, src)

    def test_slice_splits(self):
        out = T.slice_at([self._clip()], 400)
        self.assertEqual([(c.start, c.length) for c in out], [(0, 400), (400, 600)])

    def test_slice_keeps_each_half_reading_its_own_audio(self):
        out = T.slice_at([self._clip(1000, 1000, src=5000)], 1400)
        self.assertEqual([(c.start, c.src) for c in out],
                         [(1000, 5000), (1400, 5400)])

    def test_merge_groups_join_a_small_gap(self):
        gap = int(0.005 * T.SAMPLE_RATE)
        clips = [self._clip(0, 1000), self._clip(1000 + gap, 1000)]
        groups = T.merge_groups(clips)
        self.assertEqual([len(g) for g in groups], [2])
        # not one run in the medium, so TAPE has to bounce it
        self.assertFalse(T.contiguous(groups[0]))

    def test_merge_groups_keep_a_big_gap(self):
        clips = [self._clip(0, 1000), self._clip(60000, 1000)]
        self.assertEqual([len(g) for g in T.merge_groups(clips)], [1, 1])

    def test_a_take_and_its_slices_are_one_run_of_the_medium(self):
        out = T.slice_at([self._clip(0, 1000, src=0)], 400)
        self.assertTrue(T.contiguous(out))

    def test_drop_replaces_overlap(self):
        out = T.drop([self._clip(0, 1000)], 400, 200)
        self.assertEqual([(c.start, c.length) for c in out],
                         [(0, 200), (200, 400), (600, 400)])

    def test_drop_carries_the_address_of_what_was_lifted(self):
        out = T.drop([], 400, 9000, src_offset=120, src_track=2)
        self.assertEqual((out[0].src, out[0].src_track), (120, 2))

    def test_slide_stops_at_neighbour(self):
        clips = [self._clip(0, 1000), self._clip(2000, 1000)]
        out = T.slide(clips, 2500, -5000)
        self.assertEqual(out[1].start, 1000)

    def test_slide_does_not_move_the_audio(self):
        out = T.slide([self._clip(2000, 1000, src=2000)], 2500, -500)
        self.assertEqual((out[0].start, out[0].src), (1500, 2000))

    def test_cut_range_trims_both_survivors_addresses(self):
        out = T.cut_range([self._clip(0, 1000, src=4000)], 400, 600)
        self.assertEqual([(c.start, c.length, c.src) for c in out],
                         [(0, 400, 4000), (600, 400, 4600)])

    def test_undo_is_one_level(self):
        tp = T.Tape()
        tp.tracks[0] = [self._clip(0, 1000)]
        tp.seek(400)                    # inside the clip: a slice that cuts
        tp.do_slice()
        self.assertTrue(tp.undo())
        self.assertEqual(len(tp.tracks[0]), 1)
        self.assertFalse(tp.undo())

    def test_stop_twice_returns_to_zero(self):
        tp = T.Tape()
        tp.play()
        tp.seek(12000)                  # the host moves the playhead now
        tp.stop()
        self.assertGreater(tp.playhead, 0)
        tp.stop()
        self.assertEqual(tp.playhead, 0)

    def test_scrub_asks_the_host_for_a_grain(self):
        """Moving the cursor auditions the audio under it (trim by ear)."""
        calls = []

        class FakeHost:
            def scrub(self, position, frames, reseed):
                calls.append((position, frames, reseed))

        tp = T.Tape(host=FakeHost())
        tp.tracks[0] = [self._clip(0, T.SAMPLE_RATE)]
        tp.nudge(1000)
        tp.scrub_preview(1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 1000)
        self.assertTrue(calls[0][2])                  # a fresh grain
        tp.nudge(10)
        tp.scrub_preview(1)
        self.assertFalse(calls[1][2])                 # the running one extends

    def test_scrub_back_auditions_before_the_cursor(self):
        tp = T.Tape()
        tp.seek(T.SAMPLE_RATE)
        tp.scrub_preview(-1)
        self.assertLess(tp._scrub_pos, tp.playhead)

    def test_playing_never_scrubs(self):
        tp = T.Tape()
        tp.play()
        tp.scrub_preview(1)
        self.assertEqual(tp._scrub_until, 0.0)


class TestRecFromNote(unittest.TestCase):
    """REC held + a note key starts the take on the grid (workflow §2.2)."""

    def setUp(self):
        self.rig = Rig()
        self.rig.tap(Key.TRACK)
        self.tape = self.rig.app.tape

    def _rec_note(self, n=7):
        self.rig.send(("key", Key.REC, True), ("key", note_key(n), True))

    def test_snaps_the_take_to_the_nearest_bar(self):
        bar = 2.0 * SAMPLE_RATE                    # 120 BPM
        self.tape.seek(1.3 * SAMPLE_RATE)
        self._rec_note()
        self.assertEqual(self.tape.playhead, bar)
        self.assertEqual(self.tape._rec_start, bar)

    def test_snaps_backwards_when_that_bar_is_nearer(self):
        self.tape.seek(2.4 * SAMPLE_RATE)
        self._rec_note()
        self.assertEqual(self.tape.playhead, 2.0 * SAMPLE_RATE)

    def test_arms_the_selected_track_and_rolls(self):
        self.rig.tap(Key.K2)                       # TRK2
        self._rec_note()
        self.assertTrue(self.tape.armed[1])
        self.assertTrue(self.tape.recording)
        self.assertIn("RECORDING", self.rig.strip())

    def test_the_note_lands_in_the_clip(self):
        self.tape.seek(1.3 * SAMPLE_RATE)
        self._rec_note()
        self.rig.run_audio(1.0)
        self.rig.send(("key", note_key(7), False), ("key", Key.REC, False))
        self.rig.tap(Key.STOP)
        self.assertEqual(len(self.tape.tracks[0]), 1)
        clip = self.tape.tracks[0][0]
        self.assertEqual(clip.start, int(2.0 * SAMPLE_RATE))
        self.assertGreater(peak(self.rig.app), 1e-3)

    def test_rec_alone_still_only_arms(self):
        self.rig.tap(Key.REC)
        self.assertTrue(self.tape.armed[0])
        self.assertFalse(self.tape.recording)

    def test_a_note_without_rec_held_does_not_roll(self):
        self.rig.send(("key", note_key(7), True))
        self.assertFalse(self.tape.playing)

    def test_while_rolling_it_leaves_the_take_alone(self):
        self._rec_note()
        start = self.tape._rec_start
        self.rig.run_audio(0.2)
        self.rig.send(("key", note_key(9), True))   # REC still held
        self.assertEqual(self.tape._rec_start, start)

    def test_panic_drops_the_modifier(self):
        self.rig.send(("key", Key.REC, True))
        self.rig.app.panic()
        self.assertFalse(self.rig.app.rec_held)


if __name__ == "__main__":
    unittest.main()
