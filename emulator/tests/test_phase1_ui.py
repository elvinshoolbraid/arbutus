"""Phase-1 UI trace: drive the app through the scripted seam, capture PNGs.

Seam: ScriptedInput + HeadlessDisplay (PNG) + NullAudio (WAV), per
emulator-spec.md §7. No UI internals are poked to make a screen appear.
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
from testing.headless_display import HeadlessDisplay      # noqa: E402
from testing.scripted_input import ScriptedInput          # noqa: E402

OUT_DIR = os.path.abspath(os.path.join(HERE, "..", "validation", "ui", "phase1"))
STATE_DIR = os.path.join(HERE, "state", "test")


class Rig:
    """Wires the three test drivers to an App exactly as main.py wires real ones."""

    def __init__(self):
        self.native = host(owner=self)
        self.app, self.audio = app_for(self.native, STATE_DIR, fresh=True)
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

    def frame(self, name=None):
        self._fb[:] = 0
        self.app.render(self._fb)
        self.display.update(self._fb)
        if name:
            return self.display.save_png(os.path.join(OUT_DIR, f"{name}.png"))
        return None


class TestPhase1Trace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(OUT_DIR, exist_ok=True)
        cls.rig = Rig()

    def test_01_trace(self):
        rig = self.rig
        app = rig.app

        # boot -> SYNTH > ENGINE
        self.assertEqual(app.current_page.breadcrumb(), ["SYNTH", "ENGINE"])
        p = rig.frame("01-synth-engine")
        self.assertTrue(os.path.exists(p))

        # key 2 -> ENV
        rig.tap(Key.K2)
        self.assertEqual(app.current_page.breadcrumb(), ["SYNTH", "ENV"])
        rig.frame("02-synth-env")

        # hold SHIFT -> top param row highlighted
        rig.send(("key", Key.SHIFT, True))
        self.assertTrue(app.shift_held)
        rig.frame("03-synth-env-shift")

        # SHIFT + 1 -> engine menu
        rig.tap(Key.K1)
        rig.send(("key", Key.SHIFT, False))
        self.assertEqual(app.current_page.breadcrumb(),
                         ["SYNTH", "ENGINE", "SELECT"])
        rig.frame("04-engine-menu")

        # scroll ENC2 (item pane)
        before = app.current_page.state.item_idx
        rig.send(("enc", 1, 1))
        self.assertNotEqual(app.current_page.state.item_idx, before)
        rig.frame("05-engine-menu-enc2")

        # page key DRUM -> dismisses the menu and navigates to the DRUM page
        rig.tap(Key.DRUM)
        self.assertEqual(app.current_page.breadcrumb(), ["DRUM", "ENGINE"])
        rig.frame("06-drum-engine")

        # back to SYNTH -> subpage is still ENV (DDR-028)
        rig.tap(Key.SYNTH)
        self.assertEqual(app.current_page.breadcrumb(), ["SYNTH", "ENV"])
        self.assertEqual(app.pages["SYNTH"].state.subpage, 1)
        rig.frame("07-synth-env-restored")

    def test_02_zone_geometry(self):
        """Fixed zones: every page renders the same 320x240 frame (DDR-032)."""
        rig = Rig()
        sizes = set()
        for key in (Key.SYNTH, Key.DRUM, Key.TRACK, Key.MIXER, Key.SEQ,
                    Key.INPUT, Key.FILE, Key.TEMPO):
            rig.tap(key)
            rig.frame()
            sizes.add(rig.display.last_frame.shape)
        self.assertEqual(sizes, {(240, 320, 3)})

    def test_03_audio_non_silent_after_note_on(self):
        rig = Rig()
        silent = rig.audio.pull(4)
        self.assertLess(float(np.max(np.abs(silent))), 1e-6)
        rig.send(("key", note_key(7), True))       # a chromatic key
        audio = rig.audio.pull(16)
        self.assertGreater(float(np.max(np.abs(audio))), 1e-3)
        rig.send(("key", note_key(7), False))
        rig.audio.save_wav(os.path.join(OUT_DIR, "note-on.wav"))

    def test_04_help_hold_overlays(self):
        rig = Rig()
        rig.send(("key", Key.HELP, True))
        self.assertTrue(rig.app.help_held)
        rig.frame("08-help-overlay")
        rig.send(("key", Key.HELP, False))
        # a bare tap navigates to the HELP page instead
        self.assertEqual(rig.app.current_page.breadcrumb(), ["HELP"])

    def test_05_slot_recall_roundtrip(self):
        """Slot letters recall a full snapshot; autosave means no save step."""
        rig = Rig()
        app = rig.app
        rig.tap(Key.K2)                       # ENV
        rig.send(("enc", 0, 12))              # move ATK on slot A
        atk_a = app.current_page.slots()[0].value
        rig.tap(Key.B)                        # slot B inherits current state
        rig.send(("enc", 0, -8))
        atk_b = app.current_page.slots()[0].value
        self.assertNotAlmostEqual(atk_a, atk_b)
        rig.tap(Key.A)
        self.assertAlmostEqual(app.current_page.slots()[0].value, atk_a, places=5)
        app.flush()
        self.assertTrue(os.path.exists(os.path.join(STATE_DIR, "synth.json")))


    def test_06_sampler_engine_from_menu(self):
        """Engine swap is live from the two-pane menu, and it makes sound."""
        rig = Rig()
        app = rig.app
        rig.send(("key", Key.SHIFT, True))
        rig.tap(Key.K1)
        rig.send(("key", Key.SHIFT, False))
        folders = [f.folder for f in app.library.list("synth")]
        target = folders.index("sampler")
        rig.send(("enc", 0, target - app.current_page.state.folder_idx))
        rig.tap(Key.SYNTH)                       # dismiss the menu
        self.assertEqual(app.instrument.engine_folder.folder, "sampler")
        rig.frame("09-synth-engine-sampler")
        rig.send(("key", note_key(7), True))
        audio = rig.audio.pull(24)
        self.assertGreater(float(np.max(np.abs(audio))), 1e-3)


if __name__ == "__main__":
    unittest.main()
