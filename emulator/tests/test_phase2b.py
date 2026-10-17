"""Phase-2b trace: SEQ, TEMPO, HELP, stubs, persistence, north-star §5.1.

Driven through the same seam as phase 1/2a (ScriptedInput + HeadlessDisplay +
NullAudio); nothing here reaches into the renderer. PNGs land in
validation/ui/phase2b/.
"""

import os
import sys
import time
import unittest
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import app as app_module                         # noqa: E402
from app.app import App, SAMPLE_RATE                      # noqa: E402
from tests.native_support import app_for, host      # noqa: E402
from app.keys import Key, note_key                        # noqa: E402
from app import tape as T                                 # noqa: E402
from testing.headless_display import HeadlessDisplay      # noqa: E402
from testing.scripted_input import ScriptedInput          # noqa: E402

OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "validation", "ui", "phase2b"))
STATE_DIR = os.path.join(HERE, "state", "test2b")

BLOCK = 512


class Rig:
    def __init__(self, state_dir=STATE_DIR, fresh=True, block=BLOCK):
        self.native = host(owner=self, block=BLOCK)
        self.app, self.audio = app_for(self.native, state_dir, fresh=fresh, block=BLOCK)
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

    def play_note(self, n):
        self.send(("key", note_key(n), True), ("key", note_key(n), False))

    def arm_seq(self):
        """Tap SEQ on the SEQ page: the slot takes `arbutus/endless` (DDR-035).

        Entry is the box's own REC soft-key now, not SHIFT (DDR-041), and a
        soft-key only means anything once the engine is in the slot.
        """
        self.tap(Key.SEQ)

    def entry(self, *notes, chord=False):
        """Hold the REC soft-key and play: the box's entry gesture."""
        self.send(("key", Key.K1, True))
        if chord:
            for n in notes:
                self.send(("key", note_key(n), True))
            for n in notes:
                self.send(("key", note_key(n), False))
        else:
            for n in notes:
                self.play_note(n)
        self.send(("key", Key.K1, False))

    def run_audio(self, seconds: float) -> np.ndarray:
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
        from ui.frame import _strip_text
        return _strip_text(self.app)

    def bar_seconds(self) -> float:
        return 4.0 * 60.0 / self.app.bpm


def _clip_peak(app, track: int, index: int = 0) -> float:
    """A clip's loudest sample, read back out of the host's medium."""
    clips = app.tape.tracks[track]
    if index >= len(clips):
        return 0.0
    c = clips[index]
    got = app.native.clip_peaks(track, c.src, c.length, 32,
                                src_track=c.on(track))
    if not got:
        return 0.0
    return max(max(got["max"], default=0.0), -min(got["min"], default=0.0))


def setUpModule():
    os.makedirs(OUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# SEQ
# ---------------------------------------------------------------------------

class TestSeqPage(unittest.TestCase):
    def test_01_empty(self):
        rig = Rig()
        rig.tap(Key.SEQ)
        app = rig.app
        self.assertEqual(app.current_page.breadcrumb(), ["SEQ"])
        # 1-4 are the sequencer engine's own soft-keys (§3.6, DDR-041)
        self.assertEqual(app.current_page.strip_labels(),
                         ["REC", "REST", "UNDO", "CLEAR"])
        self.assertIsNone(app.current_page.active_strip_cell())
        # stage H2: the row is the box's descriptor, all eight cells of it —
        # `arbutus/endless` declares seven, so slot 7 is empty. ACTIVE lost its
        # cell and is the SEQ key plus the header badge.
        names = [s.name if s else None for s in app.current_page.slots()]
        self.assertEqual(names, ["DIV", "SWING", "HOLD", "DIR",
                                 "GATE", "PATTERN", "ROT", None])
        self.assertEqual(app.sequencer.pattern, [])
        self.assertTrue(os.path.exists(rig.frame("01-seq-empty")))

    def test_02_entry_mode(self):
        rig = Rig()
        rig.tap(Key.SEQ)
        rig.arm_seq()
        seq = rig.app.sequencer
        rig.send(("key", Key.K1, True))                   # hold REC
        self.assertTrue(seq.entry)
        self.assertEqual(seq.display_step, 0)
        for n in (0, 4, 7, 12, 5):
            rig.play_note(n)
        self.assertEqual(len(seq.pattern), 5)
        self.assertEqual(seq.root, 53)                    # NOTE_0 = F3
        self.assertEqual(seq.display_step, 5)
        # captured with REC still down: number inverted, brown row lit
        self.assertTrue(os.path.exists(rig.frame("02-seq-entry")))
        rig.send(("key", Key.K1, False))
        self.assertFalse(seq.entry)

    def test_03_rest_undo_and_chords(self):
        rig = Rig()
        rig.tap(Key.SEQ)
        rig.arm_seq()
        seq = rig.app.sequencer
        rig.send(("key", Key.K1, True))                   # hold REC
        rig.play_note(0)
        rig.tap(Key.K2)                                   # REST
        self.assertEqual(seq.pattern[-1], [])
        rig.tap(Key.K3)                                   # UNDO
        self.assertEqual(len(seq.pattern), 1)
        # chord = keys down together before the first release
        rig.send(("key", note_key(0), True), ("key", note_key(4), True),
                 ("key", note_key(7), True), ("key", note_key(0), False),
                 ("key", note_key(4), False), ("key", note_key(7), False))
        self.assertEqual(seq.pattern[-1], [53, 57, 60])
        self.assertEqual(len(seq.pattern), 2)
        rig.send(("key", Key.K1, False))
        # a second REC press starts over from 0 (overwrite)
        rig.send(("key", Key.K1, True))
        rig.play_note(9)
        self.assertEqual(len(seq.pattern), 1)
        rig.send(("key", Key.K1, False))

    def test_04_max_99_steps(self):
        rig = Rig()
        rig.tap(Key.SEQ)
        rig.arm_seq()
        seq = rig.app.sequencer
        rig.entry(*[i % 12 for i in range(120)])
        self.assertEqual(len(seq.pattern), 99)

    def test_05_playback_transposes_and_holds(self):
        rig = Rig()
        rig.tap(Key.SEQ)
        rig.arm_seq()                                     # DDR-035
        seq = rig.app.sequencer
        self.assertTrue(seq.active)
        rig.entry(0, 3, 7, 10)

        # held key transposes: the box lands the pattern's root on it
        rig.send(("key", note_key(2), True))
        self.assertTrue(seq.running)
        audio = rig.run_audio(0.5)
        self.assertGreater(float(np.max(np.abs(audio))), 1e-3)
        self.assertIn("SEQUENCER", rig.strip())
        rig.send(("key", note_key(2), False))
        self.assertFalse(seq.running)

        # HOLD is ENC3 on `arbutus/endless` (C is a slot key on SEQ): the row
        # is the descriptor's since H2, and the box puts HOLD third
        rig.send(("enc", 2, +1))
        self.assertTrue(seq.hold)
        self.assertTrue(seq.running)
        rig.run_audio(0.5)
        longs = [t[1] for t in rig.app.tickers()]
        self.assertIn("SEQUENCER HOLD", longs)
        self.assertIn("HOLD", rig.strip())
        self.assertTrue(os.path.exists(rig.frame("03-seq-hold")))

        # the clock is the box's now: it keeps sounding, sample-accurately
        # inside the block rather than once per block (DDR-041)
        self.assertGreater(float(np.max(np.abs(rig.run_audio(0.2)))), 1e-3)

        # it keeps running after leaving SEQ (workflow §3)
        rig.tap(Key.TRACK)
        self.assertTrue(seq.running)
        self.assertIn("HOLD", rig.strip())
        rig.tap(Key.SEQ)
        rig.send(("enc", 2, -1))
        self.assertFalse(seq.hold)

    def test_06_octave_and_div(self):
        rig = Rig()
        rig.tap(Key.SEQ)
        rig.arm_seq()
        seq = rig.app.sequencer
        rig.entry(0)
        rig.tap(Key.RIGHT)                                # playback: octave up
        self.assertEqual(seq.octave, 1)
        rig.tap(Key.LEFT)
        rig.tap(Key.LEFT)
        self.assertEqual(seq.octave, -1)
        rig.send(("enc", 0, -2))                          # ENC1 DIV
        self.assertEqual(seq.param("div").format_value(), "1/8")
        rig.send(("enc", 1, 10))                          # ENC2 SWING
        self.assertEqual(seq.param("swing").format_value(), "60%")
        rig.send(("enc", 3, 1))                           # ENC4 DIR
        self.assertEqual(seq.param("dir").format_value(), "REV")
        # the SHIFT row is the box's too: GATE, PATTERN, ROT
        rig.send(("key", Key.SHIFT, True))
        rig.send(("enc", 1, 2))                           # SHIFT+ENC2 PATTERN
        rig.send(("key", Key.SHIFT, False))
        self.assertEqual(seq.param("pattern").format_value(), "8THS")

    def test_07_engine_menu(self):
        rig = Rig()
        rig.tap(Key.SEQ)
        rig.shift_tap(Key.SEQ)
        app = rig.app
        self.assertEqual(app.current_page.breadcrumb(), ["SEQ", "ENGINE", "SELECT"])
        folders = [f.name for f in app.library.list("seq")]
        self.assertIn("ENDLESS", folders)
        self.assertTrue(os.path.exists(rig.frame("04-seq-menu")))
        rig.tap(Key.SEQ)                                  # dismiss
        self.assertEqual(app.current_page.breadcrumb(), ["SEQ"])

    def test_08_slots_hold_pattern(self):
        rig = Rig()
        rig.tap(Key.SEQ)
        rig.arm_seq()
        seq = rig.app.sequencer
        rig.entry(0, 4)
        pattern_a = [list(s) for s in seq.pattern]
        rig.tap(Key.B)                                    # slot B
        rig.entry(2, 5, 9)
        self.assertEqual(len(seq.pattern), 3)
        rig.tap(Key.A)                                    # back to slot A
        self.assertEqual(rig.app.sequencer.pattern, pattern_a)


# ---------------------------------------------------------------------------
# TEMPO
# ---------------------------------------------------------------------------

class TestTempoPage(unittest.TestCase):
    def test_01_page(self):
        rig = Rig()
        rig.tap(Key.TEMPO)
        app = rig.app
        self.assertEqual(app.current_page.breadcrumb(), ["TEMPO"])
        self.assertEqual(app.current_page.strip_labels(), [None] * 4)
        names = [s.name if s else None for s in app.current_page.slots()]
        # stage H2 built the rest of the page (screen-sketches § TEMPO)
        self.assertEqual(names, ["BPM", "CLICK LVL", "CLICK PITCH", "LINK",
                                 "BPM FINE", "COUNT-IN", "SYNC", None])
        self.assertTrue(os.path.exists(rig.frame("05-tempo")))

    def test_02_bpm_encoder_moves_the_grid_not_the_audio(self):
        rig = Rig()
        rig.tap(Key.TEMPO)
        rig.send(("enc", 0, 20))
        self.assertAlmostEqual(rig.app.bpm, 140.0, places=3)
        # DDR-016: the tape's own speed is untouched
        self.assertAlmostEqual(rig.app.tape.speed, 1.0, places=6)

    def test_03_tap_tempo(self):
        rig = Rig()
        interval = 0.30                                   # 200 BPM
        # The app times taps with its `tap_clock`. Hand it one that moves
        # exactly `interval` per gap: a real `time.sleep(0.3)` ran 0.35-0.45 s
        # in the Mac runner's background process (#242, #928).
        now = [time.monotonic()]
        rig.app.tap_clock = lambda: now[0]
        for i in range(4):
            rig.tap(Key.TEMPO)
            now[0] += interval
        self.assertEqual(rig.app.page_id, "TEMPO")
        self.assertAlmostEqual(rig.app.bpm, 60.0 / interval, delta=0.01)
        self.assertTrue(os.path.exists(rig.frame("06-tempo-tap")))

    def test_03b_tap_tempo_on_the_default_clock(self):
        """The instrument's own binding, untouched: test_03 replaces it, so a
        default clock that never advances passed there (#1181, t928's review).
        The App module's `time.monotonic` is swapped while the App is built."""
        interval = 0.30                                   # 200 BPM
        now = [1000.0]
        clock = lambda: now[0]                            # noqa: E731
        with mock.patch.object(app_module.time, "monotonic", clock):
            rig = Rig()
        self.assertIs(getattr(rig.app, "tap_clock", None), clock)
        for i in range(4):
            rig.tap(Key.TEMPO)
            now[0] += interval
        self.assertAlmostEqual(rig.app.bpm, 60.0 / interval, delta=0.01)

    def test_04_click_and_beat_indicator(self):
        rig = Rig()
        rig.tap(Key.TEMPO)
        silent = rig.run_audio(0.5)                       # click level 0 = off
        self.assertLess(float(np.max(np.abs(silent))), 1e-6)
        rig.send(("enc", 1, 10))                          # CLICK LVL up
        rig.tap(Key.PLAY)
        audio = rig.run_audio(1.0)
        self.assertGreater(float(np.max(np.abs(audio))), 1e-3)
        met = rig.app.metronome
        self.assertGreaterEqual(met.beat, 1)
        rig.tap(Key.STOP)
        after = rig.run_audio(0.5)
        self.assertLess(float(np.max(np.abs(after))), 1e-6)

    def test_05_bpm_drives_the_sequencer_clock(self):
        """BPM is the grid: the clock the box steps on is the host's."""
        rig = Rig()
        rig.tap(Key.SEQ)
        rig.arm_seq()
        rig.entry(0, 4, 7, 11)
        rig.tap(Key.TEMPO)
        rig.send(("enc", 0, 120))                         # BPM up
        self.assertGreater(rig.app.bpm, 120.0)
        self.assertAlmostEqual(rig.app.native.state["tempo"], rig.app.bpm,
                               places=3)
        rig.tap(Key.SEQ)
        rig.send(("key", note_key(0), True))
        self.assertGreater(float(np.max(np.abs(rig.run_audio(0.3)))), 1e-3)
        rig.send(("key", note_key(0), False))


# ---------------------------------------------------------------------------
# HELP
# ---------------------------------------------------------------------------

class TestHelp(unittest.TestCase):
    def test_01_help_page(self):
        rig = Rig()
        rig.tap(Key.HELP)
        self.assertEqual(rig.app.current_page.breadcrumb(), ["HELP"])
        self.assertTrue(os.path.exists(rig.frame("07-help-page")))

    def test_02_hold_overlay_describes_the_page(self):
        rig = Rig()
        rig.tap(Key.TRACK)
        rig.send(("key", Key.HELP, True))
        self.assertTrue(rig.app.help_held)
        lines = " ".join(rig.app.help_lines())
        self.assertIn("TRACK", lines)
        self.assertIn("tape", lines)
        self.assertTrue(os.path.exists(rig.frame("08-help-track")))
        rig.send(("key", Key.HELP, False))
        # nothing else was touched, so the bare tap lands on the HELP page
        self.assertEqual(rig.app.page_id, "HELP")
        self.assertFalse(rig.app.help_held)

    def test_03_hold_plus_control(self):
        rig = Rig()
        rig.tap(Key.TRACK)
        rig.send(("key", Key.HELP, True))
        before_zoom = rig.app.current_page.state.view_len
        rig.send(("enc", 1, 4))                           # HELP + ENC2
        self.assertEqual(rig.app.help_target, "ENC2")
        lines = " ".join(rig.app.help_lines())
        self.assertIn("ZOOM", lines)
        self.assertEqual(rig.app.current_page.state.view_len, before_zoom)
        self.assertTrue(os.path.exists(rig.frame("09-help-enc2")))
        rig.send(("key", Key.HELP, False))
        self.assertEqual(rig.app.page_id, "TRACK")
        self.assertIsNone(rig.app.help_target)

    def test_04_hold_plus_key_does_not_act(self):
        rig = Rig()
        rig.tap(Key.SEQ)
        rig.send(("key", Key.HELP, True))
        rig.send(("key", Key.C, True), ("key", Key.C, False))
        self.assertFalse(rig.app.sequencer.hold)           # nothing acted
        self.assertEqual(rig.app.current_page.state.slot_letter, "A")
        self.assertIn("slot C", " ".join(rig.app.help_lines()))
        rig.send(("key", Key.HELP, False))
        self.assertEqual(rig.app.page_id, "SEQ")


# ---------------------------------------------------------------------------
# stubs + page categories
# ---------------------------------------------------------------------------

class TestStubsAndCategories(unittest.TestCase):
    def test_01_input_page(self):
        """INPUT is a live sampling page now (DDR-036), not a stub."""
        rig = Rig()
        rig.tap(Key.INPUT)
        self.assertEqual(rig.app.current_page.category, "utility")
        self.assertEqual(rig.app.current_page.strip_labels(),
                         ["ARM", "RETRY", "→ SYNTH", "MONITOR"])
        self.assertTrue(os.path.exists(rig.frame("10-input-page")))

    def test_02_file_page(self):
        """USB/FILE is a real page since stage J1: four subpages on 1-4."""
        rig = Rig()
        rig.tap(Key.FILE)
        page = rig.app.current_page
        self.assertEqual(page.breadcrumb(), ["USB/FILE", "PROJECT"])
        self.assertEqual(page.strip_labels(),
                         ["PROJECT", "MIDI", "USB", "SYSTEM"])
        self.assertTrue(os.path.exists(rig.frame("11-file-page")))

    def test_03_bg_category_per_page(self):
        rig = Rig()
        expect = {"SYNTH": "performance", "DRUM": "performance",
                  "TRACK": "recording", "MIXER": "recording", "SEQ": "recording",
                  "INPUT": "utility", "HELP": "utility", "FILE": "utility",
                  "TEMPO": "utility"}
        for pid, cat in expect.items():
            self.assertEqual(rig.app.pages[pid].category, cat, pid)


# ---------------------------------------------------------------------------
# north-star trace (workflow §5.1)
# ---------------------------------------------------------------------------

class TestNorthStar(unittest.TestCase):
    """§5.1 steps 1-5, end to end, with a master WAV at the end."""

    def test_north_star(self):
        rig = Rig()
        app = rig.app
        bar = rig.bar_seconds()

        # --- 1. drum kit -> 8-step beat -> HOLD -> record TRK1 -------------
        rig.tap(Key.DRUM)
        self.assertEqual(app.active_instrument_id, "DRUM")
        rig.shift_tap(Key.K1)
        self.assertEqual(app.current_page.breadcrumb()[-1], "SELECT")
        rig.send(("enc", 1, 0))                       # live-apply the kit
        rig.tap(Key.DRUM)                             # dismiss the menu
        self.assertIsNotNone(app.instruments["DRUM"].engine)
        rig.frame("12-ns-drum-engine")

        rig.tap(Key.SEQ)
        rig.arm_seq()
        rig.entry(0, 3, 0, 3, 0, 3, 0, 5)
        self.assertEqual(len(app.sequencer.pattern), 8)
        rig.send(("enc", 2, +1))                      # ENC3 HOLD on
        self.assertTrue(app.sequencer.running)
        rig.frame("13-ns-seq-beat")

        rig.tap(Key.TRACK)
        rig.tap(Key.K1)
        self.assertEqual(app.tape.selected, 0)
        rig.tap(Key.REC)
        rig.tap(Key.PLAY)
        self.assertTrue(app.tape.recording)
        rig.run_audio(4 * bar)
        rig.tap(Key.STOP)
        self.assertEqual(len(app.tape.tracks[0]), 1)
        self.assertGreater(_clip_peak(app, 0), 1e-3)
        rig.frame("14-ns-trk1-drums")

        # --- 2. synth bass on TRK2 ----------------------------------------
        rig.tap(Key.SEQ)
        rig.send(("enc", 2, -1))                      # ENC3 HOLD off
        self.assertFalse(app.sequencer.running)
        rig.tap(Key.SYNTH)
        self.assertEqual(app.active_instrument_id, "SYNTH")
        rig.shift_tap(Key.K1)
        folders = [f.folder for f in app.library.list("synth")]
        rig.send(("enc", 0, folders.index("sampler")
                  - app.current_page.state.folder_idx))
        rig.tap(Key.SYNTH)
        self.assertEqual(app.instrument.engine_folder.folder, "sampler")

        rig.tap(Key.TRACK)
        rig.tap(Key.K2)
        rig.tap(Key.REC)
        rig.send(("key", note_key(3), True))
        rig.tap(Key.PLAY)
        rig.run_audio(2 * bar)
        rig.send(("key", note_key(3), False))
        rig.tap(Key.STOP)
        self.assertEqual(len(app.tape.tracks[1]), 1)
        self.assertGreater(_clip_peak(app, 1), 1e-3)

        # --- 3. lead on TRK3 ----------------------------------------------
        rig.tap(Key.SYNTH)
        rig.tap(Key.RIGHT)                            # octave up
        rig.tap(Key.TRACK)
        rig.tap(Key.K3)
        rig.tap(Key.REC)
        rig.send(("key", note_key(10), True))
        rig.tap(Key.PLAY)
        rig.run_audio(2 * bar)
        rig.send(("key", note_key(10), False))
        rig.tap(Key.STOP)
        self.assertEqual(len(app.tape.tracks[2]), 1)
        rig.frame("15-ns-three-tracks")

        # --- 4. mixer: levels, then pans on the SHIFT row ------------------
        rig.tap(Key.MIXER)
        rig.tap(Key.K1)
        before = app.mixer.levels[1]
        rig.send(("enc", 1, -4))
        self.assertLess(app.mixer.levels[1], before)
        rig.send(("key", Key.SHIFT, True))
        rig.send(("enc", 2, 4))
        rig.send(("key", Key.SHIFT, False))
        self.assertNotAlmostEqual(app.mixer.pans[2], 0.0)
        rig.frame("16-ns-mixer")

        # --- 5. loop bars 1-5, play, sweep FILTER --------------------------
        rig.tap(Key.TRACK)
        app.tape.seek(0)
        rig.tap(Key.A)                                # IN at bar 1
        app.tape.seek(4 * bar * SAMPLE_RATE)
        rig.tap(Key.B)                                # OUT at bar 5
        rig.tap(Key.C)                                # LOOP on
        self.assertTrue(app.tape.loop_on)
        app.tape.seek(0)
        rig.tap(Key.PLAY)
        rig.audio.buffers.clear()
        master = rig.run_audio(4 * bar)
        rig.send(("key", Key.Z, True))                # hold FILTER
        rig.send(("enc", 3, -6))
        self.assertEqual(app.current_page.slots()[3].name, "FILTER")
        self.assertIn("FILTER", " ".join(t[1] for t in app.tickers()))
        rig.frame("17-ns-loop-filter")
        rig.send(("key", Key.Z, False))
        rig.tap(Key.STOP)

        # master: non-silent for the whole 4 bars
        self.assertGreaterEqual(len(master), int(4 * bar * SAMPLE_RATE * 0.98))
        self.assertGreater(float(np.max(np.abs(master))), 1e-3)
        chunk = len(master) // 4
        for i in range(4):
            seg = master[i * chunk:(i + 1) * chunk]
            self.assertGreater(float(np.max(np.abs(seg))), 1e-4, f"bar {i + 1}")
        rig.audio.save_wav(os.path.join(OUT_DIR, "northstar-master.wav"))


# ---------------------------------------------------------------------------
# slot recall (workflow §5.3) + persistence (user story 18)
# ---------------------------------------------------------------------------

class TestSlotRecall(unittest.TestCase):
    def test_slot_recall_keeps_engine_and_params(self):
        rig = Rig()
        app = rig.app
        rig.tap(Key.SYNTH)
        rig.shift_tap(Key.K1)
        folders = [f.folder for f in app.library.list("synth")]
        rig.send(("enc", 0, folders.index("sampler")
                  - app.current_page.state.folder_idx))
        rig.tap(Key.SYNTH)
        rig.tap(Key.K2)                               # ENV
        rig.send(("enc", 0, 14))                      # tweak ATK on slot A
        atk_a = app.current_page.slots()[0].value
        engine_a = app.instrument.engine_folder.folder
        self.assertEqual(engine_a, "sampler")

        rig.tap(Key.B)                                # slot B: a different engine
        rig.shift_tap(Key.K1)
        rig.send(("enc", 0, folders.index("fm")
                  - app.current_page.state.folder_idx))
        rig.tap(Key.SYNTH)
        rig.send(("enc", 0, -10))
        self.assertEqual(app.instrument.engine_folder.folder, "fm")
        self.assertNotAlmostEqual(app.current_page.slots()[0].value, atk_a)

        rig.tap(Key.A)                                # back to A
        self.assertEqual(app.instrument.engine_folder.folder, engine_a)
        self.assertAlmostEqual(app.current_page.slots()[0].value, atk_a, places=5)


class TestPersistence(unittest.TestCase):
    def test_restores_tape_audio_page_and_tempo(self):
        state = os.path.join(HERE, "state", "test2b_persist")
        rig = Rig(state_dir=state, fresh=True, block=BLOCK)
        app = rig.app
        rig.tap(Key.TRACK)
        rig.tap(Key.K2)
        rig.tap(Key.REC)
        rig.send(("key", note_key(7), True))
        rig.tap(Key.PLAY)
        rig.run_audio(1.0)
        rig.send(("key", note_key(7), False))
        rig.tap(Key.STOP)
        rig.tap(Key.TEMPO)
        rig.send(("enc", 0, 12))
        bpm = app.bpm
        clip = app.tape.tracks[1][0]
        peaks = app.native.clip_peaks(1, clip.src, clip.length, 16)["max"]
        app.flush()
        self.assertTrue(os.path.exists(os.path.join(state, "project.json")))
        # the clip list is JSON; the audio is the medium the host wrote as the
        # take happened (#297) — track 1's is the host's `tape-2.wav`
        self.assertTrue(os.path.exists(os.path.join(state, "tape.json")))
        self.assertTrue(os.path.exists(os.path.join(state, "tape-2.wav")))

        # a second audio process, as a restart is
        rig2 = Rig(state_dir=state, fresh=False, block=BLOCK)
        again = rig2.app
        self.assertEqual(len(again.tape.tracks[1]), 1)
        back = again.tape.tracks[1][0]
        self.assertEqual((back.start, back.length, back.src),
                         (clip.start, clip.length, clip.src))
        self.assertEqual(again.native.clip_peaks(1, back.src, back.length,
                                                 16)["max"], peaks)
        self.assertEqual(again.tape.selected, 1)
        self.assertAlmostEqual(again.bpm, bpm, places=3)
        self.assertEqual(again.page_id, "TEMPO")

        fresh = Rig(state_dir=state, fresh=True, block=BLOCK).app
        self.assertEqual(len(fresh.tape.tracks[1]), 0)


if __name__ == "__main__":
    unittest.main()
