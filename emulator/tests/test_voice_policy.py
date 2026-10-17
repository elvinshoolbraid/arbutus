"""Stage N2a — the ENV SHIFT row and the bend gesture (DDR-052).

Driven through the input seam like `test_native_smoke.py`: the question is
whether a player's gestures reach the audio process, not whether the methods
behind them can be called. GLIDE / DETUNE / BEND are fields of the host's `env`
block — not engine parameters — so every assertion reads them back out of the
host's own document.
"""

import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tests.test_native_smoke import NativeCase              # noqa: E402

from app.native_library import patch_from_state             # noqa: E402

SAMPLE_RATE = 48000


def peak_hz(block: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    """The loudest bin of a rendered block — enough to hear a bend move."""
    mono = block.mean(axis=1) if block.ndim > 1 else block
    mono = mono * np.hanning(len(mono))
    spec = np.abs(np.fft.rfft(mono))
    return float(np.fft.rfftfreq(len(mono), 1.0 / sample_rate)[int(np.argmax(spec))])


class TestEnvShiftRow(NativeCase):
    """`MODE · GLIDE · DETUNE · BEND` replaces MODE/LEGATO/DETUNE/CURVE."""

    name = "env-row"

    def env(self, inst_id="SYNTH"):
        return self.native.instrument(inst_id).get("env") or {}

    def open_env(self):
        self.key("2")                            # SYNTH › ENV
        self.run_blocks(1)
        return self.app.pages["SYNTH"]

    def test_the_shift_row_is_mode_glide_detune_bend(self):
        page = self.open_env()
        names = [s.name if s else None for s in page.slots()]
        self.assertEqual(names[:4], ["ATK", "DEC", "SUS", "REL"])
        self.assertEqual(names[4:], ["MODE", "GLIDE", "DETUNE", "BEND"])

    def test_the_defaults_are_the_hosts(self):
        """0 s / 12 ct / 2 st — how every patch sounded before they existed."""
        page = self.open_env()
        glide, detune, bend = page.slots()[5:8]
        self.assertAlmostEqual(glide.value, 0.0, places=6)
        self.assertAlmostEqual(detune.value, 12.0, places=6)
        self.assertAlmostEqual(bend.value, 2.0, places=6)
        env = self.env()
        self.assertAlmostEqual(env.get("glide", -1), 0.0, places=4)
        self.assertAlmostEqual(env.get("detune", -1), 12.0, places=3)
        self.assertAlmostEqual(env.get("bend_range", -1), 2.0, places=4)

    def test_turning_them_reaches_the_host(self):
        page = self.open_env()
        # SHIFT slots are encoders 1-3 of the top row: GLIDE, DETUNE, BEND
        self.input.feed(("key", "SHIFT", True))
        self.input.poll_events()
        self.enc(1, 25)                          # GLIDE + 0.25 s
        self.enc(2, 10)                          # DETUNE + 5 ct
        self.enc(3, 3)                           # BEND + 3 st (discrete)
        self.input.feed(("key", "SHIFT", False))
        self.input.poll_events()
        self.run_blocks(1)

        glide, detune, bend = page.slots()[5:8]
        self.assertAlmostEqual(glide.value, 0.25, places=4)
        self.assertAlmostEqual(detune.value, 17.0, places=4)
        self.assertAlmostEqual(bend.value, 5.0, places=4)

        env = self.env()
        self.assertAlmostEqual(env["glide"], 0.25, places=3)
        self.assertAlmostEqual(env["detune"], 17.0, places=2)
        self.assertAlmostEqual(env["bend_range"], 5.0, places=3)

    def test_they_are_clamped_to_the_hosts_ranges(self):
        page = self.open_env()
        self.input.feed(("key", "SHIFT", True))
        self.input.poll_events()
        self.enc(1, 999)
        self.enc(2, 999)
        self.enc(3, 999)
        self.input.feed(("key", "SHIFT", False))
        self.input.poll_events()
        self.run_blocks(1)
        glide, detune, bend = page.slots()[5:8]
        self.assertAlmostEqual(glide.value, 2.0, places=4)      # 0-2 s
        self.assertAlmostEqual(detune.value, 50.0, places=4)    # 0-50 ct
        self.assertAlmostEqual(bend.value, 24.0, places=4)      # 0-24 st

    def test_drum_has_the_same_row_and_its_own_values(self):
        """DRUM subclasses SYNTH, but the two instruments are separate."""
        self.key("DRUM")
        self.key("2")
        self.run_blocks(1)
        drum = self.app.pages["DRUM"]
        self.assertEqual([s.name for s in drum.slots()[4:]],
                         ["MODE", "GLIDE", "DETUNE", "BEND"])
        self.input.feed(("key", "SHIFT", True))
        self.input.poll_events()
        self.enc(1, 50)                          # DRUM GLIDE = 0.5 s
        self.input.feed(("key", "SHIFT", False))
        self.input.poll_events()
        self.run_blocks(1)
        self.assertAlmostEqual(self.env("DRUM")["glide"], 0.5, places=3)
        self.assertAlmostEqual(self.env("SYNTH")["glide"], 0.0, places=4)


class TestBendGesture(NativeCase):
    """SHIFT+‹ › held = bend, snapping back on release."""

    name = "bend"

    def test_unshifted_arrows_are_still_the_octave(self):
        page = self.app.pages["SYNTH"]
        self.key("RIGHT")
        self.assertEqual(page.state.octave, 1)
        self.key("LEFT")
        self.key("LEFT")
        self.assertEqual(page.state.octave, -1)
        self.assertEqual(page._bend_dir, 0)

    def test_shifted_arrows_bend_and_the_octave_stays_put(self):
        page = self.app.pages["SYNTH"]
        self.key("RIGHT", shift=True, down=True)
        self.assertEqual(page._bend_dir, 1)
        self.assertEqual(page.state.octave, 0)
        self.key("RIGHT", down=False)
        self.assertEqual(page._bend_dir, 0)
        self.assertEqual(page.state.octave, 0)

    def test_the_bend_ends_with_the_arrow_not_with_shift(self):
        """Holding SHIFT through the whole gesture is nobody's idea of playing."""
        page = self.app.pages["SYNTH"]
        self.input.feed(("key", "SHIFT", True), ("key", "LEFT", True),
                        ("key", "SHIFT", False))
        self.input.poll_events()
        self.assertEqual(page._bend_dir, -1, "letting SHIFT go ended the bend")
        self.input.feed(("key", "LEFT", False))
        self.input.poll_events()
        self.assertEqual(page._bend_dir, 0)

    def test_the_bend_ticker_names_the_range(self):
        page = self.app.pages["SYNTH"]
        self.key("RIGHT", shift=True, down=True)
        self.assertIn("BEND +2ST", [t[1] for t in page.tickers()])
        self.key("RIGHT", down=False)
        self.assertEqual([t for t in page.tickers() if "BEND" in t[1]], [])

    def test_a_bend_moves_the_pitch_and_release_brings_it_back(self):
        page = self.app.pages["SYNTH"]
        folders = [f.folder for f in self.app.library.list("synth")]
        page.load_engine(folders.index("osc"), 0)
        self.run_blocks(2)

        self.note(7, True)
        flat = peak_hz(self.run_blocks(48))

        self.key("RIGHT", shift=True, down=True)   # GLIDE is 0: one block
        self.run_blocks(4)
        bent = peak_hz(self.run_blocks(48))

        self.key("RIGHT", down=False)
        self.run_blocks(4)
        back = peak_hz(self.run_blocks(48))
        self.note(7, False)
        self.run_blocks(4)

        # +2 st is +12.2 %; the FFT bin spacing at 48 kHz / 48 blocks is small
        # enough to see it, and this is the whole point of the gesture
        print("  bend  %.1f Hz -> %.1f Hz -> %.1f Hz" % (flat, bent, back))
        self.assertGreater(bent, flat * 1.08)
        self.assertLess(bent, flat * 1.16)
        self.assertAlmostEqual(back, flat, delta=max(4.0, flat * 0.02))

    def test_panic_lets_the_bend_go(self):
        """An alt-tab mid-bend must not leave the instrument a tone sharp."""
        page = self.app.pages["SYNTH"]
        folders = [f.folder for f in self.app.library.list("synth")]
        page.load_engine(folders.index("osc"), 0)
        self.run_blocks(2)

        self.note(7, True)
        flat = peak_hz(self.run_blocks(48))
        self.key("RIGHT", shift=True, down=True)   # and the release never comes
        self.run_blocks(4)
        self.assertGreater(peak_hz(self.run_blocks(48)), flat * 1.08)

        self.app.panic()
        self.run_blocks(4)
        self.assertEqual(page._bend_dir, 0, "the ticker still claims a bend")
        self.assertEqual([t for t in page.tickers() if "BEND" in t[1]], [])

        self.note(7, True)                        # a fresh note, after panic
        self.assertAlmostEqual(peak_hz(self.run_blocks(48)), flat,
                               delta=max(4.0, flat * 0.02))
        self.note(7, False)
        self.run_blocks(4)


class TestPatchCarriesTheRow(NativeCase):
    """A patch's `env` carries `glide` / `detune` / `bend_range` (§7.2)."""

    name = "voice-patch"

    def test_a_patch_written_now_carries_all_three(self):
        self.key("2")
        self.input.feed(("key", "SHIFT", True))
        self.input.poll_events()
        self.enc(1, 40)                          # GLIDE 0.40 s
        self.enc(3, 5)                           # BEND 7 st
        self.input.feed(("key", "SHIFT", False))
        self.input.poll_events()
        self.run_blocks(1)

        doc = patch_from_state(self.native, "SYNTH", "TEST")
        self.assertAlmostEqual(doc["env"]["glide"], 0.40, places=3)
        self.assertAlmostEqual(doc["env"]["bend_range"], 7.0, places=3)
        self.assertAlmostEqual(doc["env"]["detune"], 12.0, places=2)

    def test_loading_it_back_shows_it_on_the_page(self):
        page = self.app.pages["SYNTH"]
        self.key("2")
        doc = patch_from_state(self.native, "SYNTH", "LOUD")
        doc["env"].update({"glide": 1.25, "detune": 40.0, "bend_range": 12.0})
        self.native.load_patch("SYNTH", doc)
        page._pull_env()
        glide, detune, bend = page.slots()[5:8]
        self.assertAlmostEqual(glide.value, 1.25, places=3)
        self.assertAlmostEqual(detune.value, 40.0, places=2)
        self.assertAlmostEqual(bend.value, 12.0, places=3)

    def test_a_patch_without_them_loads_the_defaults(self):
        """Every patch written before DDR-052 is one of these."""
        page = self.app.pages["SYNTH"]
        self.key("2")
        doc = patch_from_state(self.native, "SYNTH", "OLD")
        for key in ("glide", "detune", "bend_range"):
            doc["env"].pop(key)
        self.native.load_patch("SYNTH", doc)
        page._pull_env()
        self.assertAlmostEqual(page.slots()[5].value, 0.0, places=4)
        self.assertAlmostEqual(page.slots()[6].value, 12.0, places=3)
        self.assertAlmostEqual(page.slots()[7].value, 2.0, places=4)

    def test_a_slot_round_trip_keeps_the_row(self):
        page = self.app.pages["SYNTH"]
        self.key("2")
        self.input.feed(("key", "SHIFT", True))
        self.input.poll_events()
        self.enc(2, 20)                          # DETUNE 22 ct
        self.input.feed(("key", "SHIFT", False))
        self.input.poll_events()
        self.run_blocks(1)

        snap = page.snapshot()
        page.p_detune.set_value(0.0)
        self.run_blocks(1)
        page.restore(snap)
        self.run_blocks(1)
        self.assertAlmostEqual(page.slots()[6].value, 22.0, places=2)
        self.assertAlmostEqual(
            (self.native.instrument("SYNTH").get("env") or {})["detune"],
            22.0, places=2)


if __name__ == "__main__":
    unittest.main()
