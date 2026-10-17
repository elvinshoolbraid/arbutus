"""Live sampling: trim DSP, the capture state machine, and the INPUT page.

Everything runs through `testing/fake_mic.py`, the same seam the real
sounddevice driver uses, so none of this needs an audio device.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.app import App                                    # noqa: E402
from app.keys import note_key                              # noqa: E402
from app.mic import (ARMED, CAPTURING, IDLE, MicCapture,    # noqa: E402
                     next_sample_path, trim_normalize, write_wav)
from tests.native_support import (app_for, host,            # noqa: E402
                                  library_copy, load_wav)
from testing.fake_mic import FakeMic, bottle_ping          # noqa: E402

SR = 48000


class TrimTests(unittest.TestCase):
    def test_cuts_silence_off_both_ends(self):
        sig = np.concatenate([np.zeros(SR // 2, dtype=np.float32),
                              bottle_ping(SR, 0.4),
                              np.zeros(SR, dtype=np.float32)])
        out = trim_normalize(sig, SR)
        self.assertIsNotNone(out)
        # 0.4s of ping plus pre-roll and tail, nowhere near the 1.9s input
        self.assertLess(len(out) / SR, 0.6)
        self.assertGreater(len(out) / SR, 0.3)

    def test_normalises_to_target(self):
        quiet = bottle_ping(SR, 0.3, amp=0.02)
        out = trim_normalize(quiet, SR)
        self.assertAlmostEqual(float(np.max(np.abs(out))), 0.90, places=2)

    def test_fade_out_lands_on_zero(self):
        """The loop-click guarantee: a sample cut while still loud ends at 0."""
        block = np.full(SR // 2, 0.5, dtype=np.float32)
        out = trim_normalize(block, SR)
        self.assertLess(abs(float(out[-1])), 0.02)

    def test_fade_in_never_files_off_the_attack(self):
        """A struck sound peaks within ~2 ms; the fade must not reach it."""
        ping = bottle_ping(SR, 0.4)
        out = trim_normalize(ping, SR)
        self.assertAlmostEqual(float(np.max(np.abs(out))), 0.90, places=2)

    def test_fade_in_applies_when_there_is_quiet_to_fade_across(self):
        sig = np.concatenate([np.zeros(SR // 4, dtype=np.float32),
                              bottle_ping(SR, 0.4)])
        out = trim_normalize(sig, SR)
        self.assertLess(abs(float(out[0])), 0.02)

    def test_silence_returns_none(self):
        self.assertIsNone(trim_normalize(np.zeros(SR, dtype=np.float32), SR))
        self.assertIsNone(trim_normalize(np.zeros(0, dtype=np.float32), SR))

    def test_keeps_the_attack(self):
        """The point of the pre-roll: the strike survives the trim."""
        ping = bottle_ping(SR, 0.5)
        sig = np.concatenate([np.zeros(SR // 4, dtype=np.float32), ping])
        out = trim_normalize(sig, SR)
        # peak lands in the first 30 ms, i.e. we did not cut into the strike
        self.assertLess(int(np.argmax(np.abs(out))) / SR, 0.03)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mic = MicCapture(SR, self.dir)
        self.fake = FakeMic(self.mic)
        self.fake.initialize(SR, self.mic.feed)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_idle_mic_ignores_sound(self):
        self.fake.tap()
        self.assertEqual(self.mic.state, IDLE)
        self.assertFalse(self.mic.poll())
        self.assertIsNone(self.mic.captured)

    def test_arm_then_tap_captures_and_saves(self):
        self.mic.arm()
        self.assertEqual(self.mic.state, ARMED)
        self.fake.tap()
        self.assertTrue(self.mic.poll())
        self.assertIsNotNone(self.mic.captured)
        self.assertTrue(os.path.exists(self.mic.captured_path))
        data, sr = load_wav(self.mic.captured_path)
        self.assertEqual(sr, SR)
        self.assertGreater(float(np.max(np.abs(data))), 0.5)

    def test_capture_starts_before_the_transient(self):
        """Pre-roll: the sample's own peak is right at its start."""
        self.mic.arm()
        self.fake.tap()
        self.mic.poll()
        peak_at = int(np.argmax(np.abs(self.mic.captured))) / SR
        self.assertLess(peak_at, 0.04)

    def test_quiet_sound_below_threshold_never_triggers(self):
        self.mic.threshold = 0.5
        self.mic.arm()
        self.fake.tap(amp=0.05)
        self.assertEqual(self.mic.state, ARMED)
        self.assertFalse(self.mic.poll())

    def test_length_ceiling_stops_a_long_capture(self):
        self.mic.max_len = 0.25
        self.mic.arm()
        self.fake.silence(0.05)
        self.fake.play(np.ones(SR, dtype=np.float32) * 0.5)   # 1s of tone
        self.assertTrue(self.mic.poll())
        self.assertLessEqual(self.mic.length_s, 0.35)

    def test_gain_lifts_a_quiet_source_over_the_threshold(self):
        self.mic.threshold = 0.20
        self.mic.gain = 8.0
        self.mic.arm()
        self.fake.tap(amp=0.05)
        self.assertTrue(self.mic.poll())

    def test_retry_discards_the_take(self):
        self.mic.arm()
        self.fake.tap()
        self.mic.poll()
        self.assertIsNotNone(self.mic.captured)
        self.mic.clear()
        self.assertIsNone(self.mic.captured)
        self.assertEqual(self.mic.state, IDLE)

    def test_second_take_does_not_overwrite_the_first(self):
        paths = []
        for _ in range(2):
            self.mic.arm()
            self.fake.tap()
            self.mic.poll()
            paths.append(self.mic.captured_path)
        self.assertNotEqual(paths[0], paths[1])
        self.assertTrue(all(os.path.exists(p) for p in paths))

    def test_meter_follows_the_input(self):
        self.assertEqual(float(np.max(self.mic.meter)), 0.0)
        self.fake.play(bottle_ping(SR, 0.3))
        self.assertGreater(float(np.max(self.mic.meter)), 0.1)

    def test_ring_wraps_without_losing_the_tail(self):
        """Feed more than the ring holds; the newest samples must survive."""
        self.fake.play(np.zeros(3 * SR, dtype=np.float32))
        tail = np.linspace(0.0, 0.8, 1000, dtype=np.float32)
        self.fake.play(tail)
        got = self.mic._ring_tail(1000)
        self.assertAlmostEqual(float(got[-1]), 0.8, places=3)

    def test_monitor_is_off_by_default(self):
        """A laptop feeds back; monitoring is opt-in every time (DDR-036).

        The audio through it is the host's business now (`monitor {on, gain}`)
        — `MicCapture` carries the flag and never a second copy of the input.
        See `InputPageTests.test_key_4_reaches_the_host`.
        """
        self.assertFalse(self.mic.monitor)
        self.fake.play(bottle_ping(SR, 0.2))
        self.assertFalse(self.mic.monitor)
        self.assertFalse(hasattr(self.mic, "monitor_read"))

    def test_next_sample_path_increments(self):
        a = next_sample_path(self.dir)
        write_wav(a, np.zeros(10, dtype=np.float32), SR)
        b = next_sample_path(self.dir)
        self.assertNotEqual(a, b)
        self.assertTrue(b.endswith("mic-02.wav"))


class _Rig:
    """App with a scratch library + state dir and a fake mic attached.

    The library is a *copy*: a capture lands in `synth/sampler/` and must never
    leave a WAV in the repo's `audio/library/`.
    """

    def __init__(self, case):
        self.tmp = tempfile.mkdtemp()
        lib_root = library_copy(os.path.join(self.tmp, "library"))
        self.native = host(case, library_root=lib_root)
        self.app, self.audio = app_for(
            self.native, os.path.join(self.tmp, "state"),
            fresh=True, library_root=lib_root)
        self.page = self.app.pages["INPUT"]
        self.fake = FakeMic()
        self.app.mic_driver_factory = lambda: self.fake

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class InputPageTests(unittest.TestCase):
    def setUp(self):
        self.rig = _Rig(self)
        self.app = self.rig.app
        self.page = self.rig.page

    def tearDown(self):
        self.rig.close()

    def test_input_is_a_real_page_now(self):
        self.app.on_key("INPUT", True)
        self.assertEqual(self.app.page_id, "INPUT")
        self.assertEqual(self.app.current_page.breadcrumb(), ["INPUT"])
        self.assertEqual(self.page.strip_labels(),
                         ["ARM", "RETRY", "→ SYNTH", "MONITOR"])
        self.assertEqual([p.name for p in self.page.slots()[:4]],
                         ["THRESH", "LENGTH", "GAIN", "MONITOR"])

    def test_mic_opens_on_arrival_not_at_boot(self):
        self.assertIsNone(self.app.mic)
        self.assertFalse(self.rig.fake.running)
        self.app.on_key("INPUT", True)
        self.assertIsNotNone(self.app.mic)
        self.assertTrue(self.rig.fake.running)

    def test_arm_tap_lands_on_synth(self):
        self.app.on_key("INPUT", True)
        self.app.on_key("1", True)                  # ARM
        self.assertTrue(self.app.mic.armed)
        self.assertIn("LISTENING", " ".join(t[1] for t in self.app.tickers()))

        self.rig.fake.tap()
        self.app.tick()                             # poll: trim, save, load

        self.assertIsNotNone(self.app.mic.captured)
        synth = self.app.pages["SYNTH"]
        self.assertEqual(synth.instrument.engine_folder.folder, "sampler")
        self.assertTrue(synth.instrument.item.startswith("mic-"))
        self.assertIn("SAMPLED", " ".join(t[1] for t in self.app.tickers()))

    def test_the_sampled_bottle_actually_sounds(self):
        self.app.on_key("INPUT", True)
        self.app.on_key("1", True)
        self.rig.fake.tap()
        self.app.tick()

        self.app.on_key("SYNTH", True)
        self.app.on_key("NOTE_7", True)             # a chromatic key
        block = self.rig.audio.pull(40)
        self.assertGreater(float(np.max(np.abs(block))), 1e-3)

    def test_threshold_param_reaches_the_mic(self):
        self.app.on_key("INPUT", True)
        self.page.p_thresh.set_value(0.33)
        self.assertAlmostEqual(self.app.mic.threshold, 0.33, places=3)

    def test_key_4_toggles_monitor(self):
        self.app.on_key("INPUT", True)
        self.assertFalse(self.app.mic.monitor)
        self.app.on_key("4", True)
        self.assertTrue(self.app.mic.monitor)
        self.app.on_key("4", True)
        self.assertFalse(self.app.mic.monitor)

    def test_key_4_reaches_the_host(self):
        """MONITOR is the host's `monitor {on, gain}` — the input goes to the
        speakers in the audio process, never back through the UI."""
        self.app.on_key("INPUT", True)
        self.app.tick()
        self.assertFalse(self.rig.native.state["monitor"]["on"])
        self.app.on_key("4", True)
        self.app.tick()
        self.assertTrue(self.rig.native.state["monitor"]["on"])

    def test_key_3_jumps_to_synth(self):
        self.app.on_key("INPUT", True)
        self.app.on_key("3", True)
        self.assertEqual(self.app.page_id, "SYNTH")

    def test_no_driver_is_survivable(self):
        """No mic on the machine: the page still renders and says so."""
        self.app.mic_driver_factory = None
        self.app.on_key("INPUT", True)
        fb = self.app.render()
        self.assertEqual(fb.shape, (240, 320, 3))
        self.app.on_key("1", True)              # ARM with no device: no crash
        self.app.tick()

    def test_params_survive_a_restart(self):
        self.app.on_key("INPUT", True)
        self.page.p_thresh.set_value(0.21)
        data = self.page.snapshot()
        self.page.p_thresh.set_value(0.06)
        self.page.restore(data)
        self.assertAlmostEqual(self.page.p_thresh.value, 0.21, places=3)


class PatternItemTests(unittest.TestCase):
    """`.seq` library items carry a pattern (the pentatonic polymeter ones)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.native = host(self)
        self.app, self.audio = app_for(
            self.native, os.path.join(self.tmp, "state"), fresh=True)
        # first tap navigates to SEQ, second arms the slot with
        # `arbutus/endless` (DDR-035) — a pattern needs a box to enter it into
        for _ in range(2):
            self.app.on_key("SEQ", True)
            self.app.on_key("SEQ", False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, item):
        lf = self.app.library.find("seq", "endless")
        self.assertIn(item, lf.items)
        self.app.library.apply_item(self.app.sequencer, lf, item)

    def test_shipped_patterns_are_polymeric(self):
        """Neither length divides a bar — that is what makes it precess."""
        for item, length in (("penta-7.seq", 7), ("penta-5.seq", 5)):
            self._load(item)
            self.assertEqual(len(self.app.sequencer.pattern), length)
            self.assertNotEqual(16 % length, 0)

    def test_pattern_notes_are_pentatonic(self):
        self._load("penta-7.seq")
        degrees = {n % 12 for step in self.app.sequencer.pattern for n in step}
        # A minor pentatonic: A C D E G
        self.assertEqual(degrees, {9, 0, 2, 4, 7})

    def test_named_params_apply(self):
        self._load("penta-7.seq")
        self.assertEqual(self.app.sequencer.param("div").format_value(), "1/8")
        self._load("penta-5.seq")
        self.assertEqual(self.app.sequencer.param("div").format_value(), "1/16")

    def test_root_transposes_to_the_held_key(self):
        """The file names the root; the *box* does the transposing.

        `_shift()` was the Python sequencer's arithmetic. Natively the UI only
        says which key is held — `arbutus/endless` lands the pattern's root on
        it — so what is left to pin here is the root the file carried and the
        held note reaching the slot.
        """
        self._load("penta-7.seq")
        seq = self.app.sequencer
        self.assertEqual(seq.root, 57)
        self.app.on_key(note_key(7), True)    # hold a C (53 + 7 = 60)
        self.assertEqual(seq.trigger_note, 60)
        self.app.on_key(note_key(7), False)
        self.assertIsNone(seq.trigger_note)

    def test_pattern_plays_the_active_instrument(self):
        self._load("penta-7.seq")
        seq = self.app.sequencer
        self.app.on_key(note_key(4), True)   # hold the root: the box starts
        audio = self.audio.pull(40)     # `render` is the clock now
        self.app.tick()
        self.assertTrue(seq.running)
        self.assertGreater(float(np.max(np.abs(audio))), 1e-4)

    def test_a_plain_patch_file_still_loads(self):
        """The pattern hook must not disturb ordinary patch items."""
        lf = self.app.library.find("synth", "fm")
        engine = self.app.library.instantiate(lf, SR)
        self.assertTrue(self.app.library.apply_item(engine, lf, "soft-bell.patch"))

    def test_shipped_files_are_valid_json(self):
        lf = self.app.library.find("seq", "endless")
        for item in lf.items:
            with open(lf.item_path(item)) as f:
                json.load(f)


if __name__ == "__main__":
    unittest.main()
