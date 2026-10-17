"""Stage I1 — the four synth boxes as the UI sees them.

The boxes' own goldens (`ctest -R va|string|wavetable|dbox`) prove the DSP and
`host_synthboxes.py` proves the protocol. What is left is the emulator's half:
that the pages build from the descriptors without being told anything about
these boxes, that a fresh instrument does not change engine because a box was
added, and that DRUM's per-pad pages work on `arbutus/dbox`, whose pads are
**parameters with no file behind them**.

No page code knows the name of any box here. That is the test.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tests.test_native_smoke import NativeCase              # noqa: E402

VA_PARAMS = ["WAVE", "CUTOFF", "RES", "ENV-F", "SUB", "NOISE", "SPREAD", "DRIVE"]
STRING_PARAMS = ["EXCITE", "DECAY", "DAMP", "POS", "TONE", "FORCE", "BODY", "SPREAD"]
WT_PARAMS = ["POS", "SWEEP", "WARP", "FOLD", "SPREAD", "SUB", "DRIFT", None]
DBOX_PARAMS = ["MODE", "TUNE", "DECAY", "SNAP", "TONE", "DRIVE", "LEVEL", "PAN"]


class SynthBoxCase(NativeCase):
    name = "i1"

    def synth(self):
        self.key("SYNTH")
        self.key("1")                             # SYNTH › ENGINE
        self.run_blocks(1)
        return self.app.pages["SYNTH"]

    def load(self, folder: str, item: int = 0):
        page = self.synth()
        names = [f.folder for f in self.app.library.list("synth")]
        page.load_engine(names.index(folder), item)
        self.run_blocks(1)
        return page

    def slot_names(self, page):
        return [s.name if s else None for s in page.slots()]


class TestDefaults(SynthBoxCase):
    """A new box must not change which engine an instrument arrives on."""

    def test_synth_and_drum_arrive_on_the_named_box(self):
        # `arbutus/dbox` sorts before `arbutus/drum`, and folder 0 used to be
        # the default — the same trap H1 hit on SEQ with `arp`.
        self.assertEqual(self.app.pages["SYNTH"].DEFAULT_BOX, "fm")
        self.assertEqual(self.app.pages["DRUM"].DEFAULT_BOX, "drum")
        self.key("DRUM")
        self.run_blocks(1)
        self.assertEqual(self.native.slot("DRUM", "engine").get("id"),
                         "arbutus/drum")
        self.key("SYNTH")
        self.run_blocks(1)
        self.assertEqual(self.native.slot("SYNTH", "engine").get("id"),
                         "arbutus/fm")

    def test_every_new_box_is_offered(self):
        folders = [f.folder for f in self.app.library.list("synth")]
        for name in ("va", "string", "wavetable"):
            self.assertIn(name, folders)
        self.assertIn("dbox", [f.folder for f in self.app.library.list("drum")])


class TestSynthPages(SynthBoxCase):
    """SYNTH › ENGINE is built from the descriptor and nothing else."""

    def test_va_slots(self):
        page = self.load("va")
        self.assertEqual(self.native.slot("SYNTH", "engine")["id"], "arbutus/va")
        self.assertEqual(self.slot_names(page), VA_PARAMS)
        # WAVE is an ENUM: a value label, never a bar (§3.5)
        self.assertTrue(page.slots()[0].is_discrete())
        self.assertEqual(page.slots()[0].choices, ["SAW", "PULSE", "SQR", "TRI"])

    def test_string_slots(self):
        page = self.load("string")
        self.assertEqual(self.slot_names(page), STRING_PARAMS)
        self.assertEqual(page.slots()[0].choices, ["PLUCK", "BOW", "STRIKE"])

    def test_wavetable_slots_and_the_pos_label(self):
        page = self.load("wavetable")
        self.assertEqual(self.slot_names(page), WT_PARAMS)
        # format_param puts the frame on the slot as `display` — the UI needs
        # no table of its own (`native_engine._labelled`).
        self.assertRegex(page.slots()[0].format_value(), r"^\d+\.\d/\d+$")

    def test_a_turn_reaches_the_host(self):
        page = self.load("va")
        self.enc(1, 10)                           # ENC2 = CUTOFF
        self.run_blocks(1)
        self.assertGreater(
            self.native.slot("SYNTH", "engine")["params"]["cutoff"], 0.6)
        self.assertGreater(self.play(idx=7, blocks=24), 1e-3)
        self.assertEqual(page.instrument.engine_name, "VA")

    def test_each_box_sounds(self):
        for folder in ("va", "string", "wavetable"):
            self.load(folder)
            peak = self.play(idx=7, blocks=48)
            self.assertGreater(peak, 1e-3, "%s played nothing" % folder)


class TestDboxPages(SynthBoxCase):
    """DRUM's per-pad pages on a kit that has no samples in it."""

    def dbox(self):
        self.key("DRUM")
        self.key("1")
        self.run_blocks(1)
        page = self.app.pages["DRUM"]
        folders = self.app.library.list("drum")
        idx = [f.folder for f in folders].index("dbox")
        page.load_engine(idx, 0)
        self.run_blocks(1)
        return page

    def hit(self, pad: int):
        self.note(pad, True)
        self.run_blocks(2)
        self.note(pad, False)
        self.run_blocks(1)

    def pad_params(self, pad: int) -> dict:
        pads = (self.native.instrument("DRUM").get("kit") or {}).get("pads") or {}
        rec = pads.get(str(pad)) or {}
        return (rec.get("params") or {}) if isinstance(rec, dict) else {}

    def test_the_eight_slots_are_the_pads(self):
        page = self.dbox()
        self.assertEqual(self.native.slot("DRUM", "engine")["id"], "arbutus/dbox")
        self.assertEqual([s.name for s in page.slots()], DBOX_PARAMS)

    def test_a_turn_writes_into_that_pads_entry(self):
        page = self.dbox()
        self.hit(5)
        self.assertEqual(page.pad, 5)
        before = self.pad_params(5).get("decay")
        self.enc(2, 9)                            # ENC3 = DECAY
        self.run_blocks(1)
        self.assertGreater(self.pad_params(5).get("decay"), before)
        # ...and pad 5 only
        self.assertNotEqual(self.pad_params(5).get("decay"),
                            self.pad_params(6).get("decay"))

    def test_a_pad_has_no_file_and_the_page_still_draws(self):
        page = self.dbox()
        self.hit(2)
        self.assertEqual(page.instrument.engine.pad_file(2), "")
        self.assertTrue(page.pad_name().startswith("KEY 3 · "), page.pad_name())
        self.shot("i1-dbox-engine")               # "NO SAMPLE" — there is none

    def test_pads_sound(self):
        self.dbox()
        for pad in (0, 2, 6):
            self.assertGreater(self.play(idx=pad, blocks=24), 1e-3,
                               "dbox pad %d played nothing" % pad)

    # -- the ENV page a dbox ignores (S11, #53) ---------------------------
    def test_env_is_dim_and_inert_on_a_box_that_ignores_it(self):
        """`arbutus/dbox` declares `honours_env: false` (DDR-053).

        Before this, DRUM › ENV drew four live-looking knobs and sent an
        envelope the engine throws away. ATK and DEC now join SUS and REL,
        which have always been locked here.
        """
        page = self.dbox()
        self.assertFalse(page.honours_env())
        self.key("2")                             # DRUM › ENV
        self.run_blocks(1)
        cells = page.slots()[:4]
        self.assertEqual([c.name for c in cells], ["ATK", "DEC", "SUS", "REL"])
        for c in cells:
            self.assertTrue(c.locked and c.dimmed, "%s still turns" % c.name)
        # the voice-policy row is untouched: MODE/GLIDE/DETUNE/BEND mean the
        # same thing on a kit whose envelope is per pad
        for c in page.slots()[4:]:
            self.assertFalse(c.locked, "%s should still turn" % c.name)
        self.shot("i1-dbox-env")                  # the grey AD and `NO ENV`

    def test_a_turn_on_a_dead_env_cell_moves_nothing_and_says_why(self):
        page = self.dbox()
        self.key("2")
        self.run_blocks(1)
        before = page.p_dec.value
        self.enc(1, 12)                           # ENC2 = DEC
        self.run_blocks(1)
        self.assertEqual(page.p_dec.value, before)
        self.assertIn("NO ENV", " ".join(t[1] for t in self.app.tickers()))

    def test_the_same_page_is_alive_on_a_kit_that_honours_it(self):
        """`arbutus/drum` says true, so the AD half turns (§15.11)."""
        self.key("DRUM")
        self.key("2")
        self.run_blocks(1)
        page = self.app.pages["DRUM"]
        self.assertTrue(page.honours_env())
        atk, dec, sus, rel = page.slots()[:4]
        self.assertFalse(atk.locked or dec.locked)
        self.assertTrue(sus.locked and rel.locked)   # DDR-053, always
        before = dec.value
        self.enc(1, 6)
        self.run_blocks(1)
        self.assertNotEqual(page.p_dec.value, before)


if __name__ == "__main__":
    unittest.main()
