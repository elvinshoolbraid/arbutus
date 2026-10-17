"""Stage N2b — the per-pad DRUM pages and the sampler v2 page (§15.3, §15.11).

Driven through the input seam, like the rest of the native tests: the question
is whether a player's gestures land in the **kit document the host holds**,
because that document is the save (there is no `save_kit`). Every assertion
about a pad therefore reads `state.instruments.drum.kit` back, not the Param
the encoder happened to move.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tests.test_native_smoke import NativeCase              # noqa: E402

#: `arbutus/drum`'s eight, all `per_note` (§15.3)
PAD_PARAMS = ["START", "END", "PITCH", "LEVEL", "PLAY", "REV", "PAN", "CHOKE"]
#: `arbutus/sampler` 2.0's eight (§15.11) — LEVEL is gone, loop and fade in
SAMPLER_PARAMS = ["START", "END", "LOOP-IN", "LOOP-OUT",
                  "ROOT", "TUNE", "REV", "FADE"]


class DrumCase(NativeCase):
    """On DRUM, with a stock kit loaded and the ENGINE subpage showing."""

    name = "per-pad"

    def drum(self):
        self.key("DRUM")
        self.key("1")                            # DRUM › ENGINE
        self.run_blocks(1)
        return self.app.pages["DRUM"]

    def kit_pads(self) -> dict:
        return (self.native.instrument("DRUM").get("kit") or {}).get("pads") or {}

    def pad_params(self, pad: int) -> dict:
        rec = self.kit_pads().get(str(pad)) or {}
        return (rec.get("params") or {}) if isinstance(rec, dict) else {}

    def hit(self, pad: int):
        """Sound a pad, which is what makes it the one the pages edit."""
        self.note(pad, True)
        self.run_blocks(2)
        self.note(pad, False)
        self.run_blocks(1)

    def load_kit(self, name: str = "bark"):
        """A kit this test controls the shape of.

        The default kit is whichever sorts first, and how many pads it fills
        is content — a test that wants an *empty* pad has to say which kit it
        is talking about. BARK fills 0..11, and is ours, so the public export
        carries it where the op1.fun kits are held back (#1295).
        """
        page = self.app.pages["DRUM"]
        folders = self.app.library.list("drum")
        idx = next(i for i, f in enumerate(folders) if name in (f.items or []))
        page.load_engine(idx, folders[idx].items.index(name))
        self.run_blocks(1)
        return page

    def empty_pad(self) -> int:
        pads = self.kit_pads()
        return next(p for p in range(24) if str(p) not in pads)


class TestPerPadEngine(DrumCase):
    """DRUM › ENGINE is the last-hit key's own eight."""

    def test_the_eight_slots_are_the_drums_per_pad_params(self):
        page = self.drum()
        self.assertEqual([s.name for s in page.slots()], PAD_PARAMS)

    def test_the_page_follows_the_last_hit_key(self):
        page = self.drum()
        self.hit(7)
        self.assertEqual(page.pad, 7)
        self.hit(2)
        self.assertEqual(page.pad, 2)

    def test_the_main_area_names_the_pad_and_its_file(self):
        page = self.drum()
        self.hit(6)
        # `KEY 7 · 07.wav` — the key as a player counts it, one-based
        self.assertTrue(page.pad_name().startswith("KEY 7 · "), page.pad_name())
        self.assertIn(".wav", page.pad_name())

    def test_a_turn_writes_into_that_pads_entry_in_the_kit(self):
        page = self.drum()
        self.hit(3)
        before = self.pad_params(3).get("start", 0.0)
        self.enc(0, 8)                            # ENC1 = START
        self.run_blocks(1)
        after = self.pad_params(3).get("start")
        self.assertIsNotNone(after)
        self.assertGreater(after, before)
        # the v1 string shape migrated forward on the way through the host
        self.assertIsInstance(self.kit_pads()["3"], dict)

    def test_pads_hold_their_own_values(self):
        self.drum()
        self.hit(3)
        self.enc(0, 8)
        self.run_blocks(1)
        self.hit(4)
        self.enc(0, -4)
        self.run_blocks(1)
        self.assertNotEqual(self.pad_params(3).get("start"),
                            self.pad_params(4).get("start"))

    def test_the_slots_rebuild_when_the_pad_changes(self):
        """The knob under your finger must be the new pad's, not the old
        pad's value still sitting in a Param that was built once."""
        page = self.drum()
        self.hit(3)
        self.enc(0, 10)
        self.run_blocks(1)
        moved = page.slots()[0].value
        self.hit(9)
        self.assertNotAlmostEqual(page.slots()[0].value, moved, places=3)
        self.assertAlmostEqual(page.slots()[0].value,
                               self.pad_params(9).get("start", 0.0), places=5)

    def test_a_pad_has_a_waveform(self):
        page = self.drum()
        self.hit(1)
        peaks = self.app.instruments["DRUM"].engine.pad_waveform(1, 32)
        self.assertIsNotNone(peaks)
        self.assertEqual(len(peaks["min"]), 32)
        self.assertTrue(any(v != 0.0 for v in peaks["max"]))


class TestPadCopy(DrumCase):
    """LIFT on a pad, DROP on another (DDR-039's fourth payload)."""

    def lift(self, pad):
        self.key("LIFT", down=True)
        self.note(pad, True)
        self.note(pad, False)
        self.key("LIFT", down=False)
        self.run_blocks(1)

    def drop(self, pad):
        self.key("DROP", down=True)
        self.note(pad, True)
        self.note(pad, False)
        self.key("DROP", down=False)
        self.run_blocks(2)

    def test_a_pad_copies_file_and_params(self):
        self.drum()
        self.load_kit()
        dst = self.empty_pad()
        self.hit(2)
        self.enc(0, 9)                            # give pad 2 a START to carry
        self.run_blocks(1)
        src_file = self.kit_pads()["2"]["file"]
        src_start = self.pad_params(2)["start"]

        self.lift(2)
        self.drop(dst)

        self.assertEqual(self.kit_pads()[str(dst)]["file"], src_file)
        self.assertAlmostEqual(self.pad_params(dst)["start"], src_start,
                               places=5)

    def test_a_note_held_through_lift_stops(self):
        """#873 (t623 row 3): the gesture takes new presses only; a pad
        sounding before LIFT went down still gets its note-off."""
        self.drum()
        self.note(5, True)
        self.key("LIFT", down=True)
        self.note(5, False)
        self.key("LIFT", down=False)
        self.assertFalse(self.app.instruments["DRUM"]._held)
        self.assertEqual(self.app._note_owner, {})

    def test_the_gesture_does_not_sound_the_pad_it_names(self):
        """A pad you are *addressing* must not fire — naming it would
        otherwise play it twice at whatever level it is set to."""
        page = self.drum()
        self.hit(5)
        self.key("LIFT", down=True)
        self.note(11, True)
        self.note(11, False)
        self.key("LIFT", down=False)
        self.run_blocks(1)
        # the note went to the gesture, so the *edited* pad is still 5
        self.assertEqual(page.pad, 5)
        self.assertEqual(page._lifted_pad, 11)

    def test_lifting_an_empty_pad_lifts_nothing(self):
        page = self.drum()
        self.load_kit()
        self.lift(self.empty_pad())
        self.assertIsNone(page._lifted_pad)

    def test_dropping_with_nothing_lifted_changes_no_kit(self):
        self.drum()
        self.load_kit()
        before = dict(self.kit_pads())
        self.drop(self.empty_pad())
        self.assertEqual(set(self.kit_pads()), set(before))

    def test_panic_lets_go_of_the_gesture(self):
        page = self.drum()
        self.key("LIFT", down=True)
        self.app.panic()
        self.assertFalse(page._lift_held)
        self.assertIsNone(page._lifted_pad)


class TestDrumEnv(DrumCase):
    """DRUM › ENV is an AD, and SUS/REL are named but do not turn (DDR-053)."""

    def env(self) -> dict:
        return self.native.instrument("DRUM").get("env") or {}

    def open_env(self):
        self.key("DRUM")
        self.key("2")
        self.run_blocks(1)
        return self.app.pages["DRUM"]

    def test_the_row_is_atk_dec_then_two_locked(self):
        page = self.open_env()
        slots = page.slots()
        self.assertEqual([s.name for s in slots[:4]], ["ATK", "DEC", "SUS", "REL"])
        self.assertFalse(slots[0].locked)
        self.assertTrue(slots[2].locked and slots[3].locked)
        self.assertTrue(slots[2].dimmed and slots[3].dimmed)
        # the SHIFT row is still voice policy — a kit glides and bends too
        self.assertEqual([s.name for s in slots[4:]],
                         ["MODE", "GLIDE", "DETUNE", "BEND"])

    def test_the_drum_keeps_the_hosts_own_ad_defaults(self):
        """a 0 s / d 4 s — v1's sound, and *not* SYNTH's 0.012 / 0.2.

        The page hands its ADSR down when an engine loads, so a DRUM page
        built on SYNTH's defaults overwrote the host's before anything was
        played: every kit decayed in 200 ms. The page pulls before it pushes
        now, and its own AD slots reach 4 s so it can show what it was given.
        """
        page = self.open_env()
        self.assertAlmostEqual(self.env()["a"], 0.0, places=5)
        self.assertAlmostEqual(self.env()["d"], 4.0, places=5)
        self.assertAlmostEqual(page.p_dec.value, 4.0, places=5)
        synth = self.native.instrument("SYNTH").get("env") or {}
        self.assertAlmostEqual(synth["d"], 0.2, places=5)

    def test_sustain_and_release_do_not_move(self):
        page = self.open_env()
        before = self.env().get("s")
        self.enc(2, 10)                           # ENC3 = SUS
        self.run_blocks(1)
        self.assertEqual(page.slots()[2].value, 0.0)
        self.assertEqual(self.env().get("s"), before)

    def test_attack_and_decay_reach_the_host(self):
        self.open_env()
        before = self.env().get("d")
        self.enc(1, 6)                            # ENC2 = DEC
        self.run_blocks(1)
        self.assertNotEqual(self.env().get("d"), before)

    def test_the_header_names_the_pad_it_is_editing(self):
        """§15.11's deviation: the AD is the host's global `env`, latched per
        hit. The page has to say *whose* hit it is showing, or the knob looks
        like it belongs to the kit."""
        page = self.open_env()
        self.hit(4)
        self.assertIn("KEY 5", page.pad_name())


class TestSamplerPage(NativeCase):
    """SYNTH on `arbutus/sampler` 2.0 — loop, reverse, fade and the markers."""

    name = "sampler-v2"

    def sampler(self):
        """Load the sampler by name rather than by position in the list."""
        lib = self.app.library
        folders = [f.folder for f in lib.list("synth")]
        self.assertIn("sampler", folders)
        page = self.app.pages["SYNTH"]
        page.load_engine(folders.index("sampler"), 0)
        self.run_blocks(1)
        return page

    def test_the_eight_slots_are_sampler_two(self):
        page = self.sampler()
        self.assertEqual([s.name for s in page.slots()], SAMPLER_PARAMS)

    def test_root_is_drawn_as_a_note_name(self):
        """ABI 2's `format_param`: ROOT is a MIDI number in the descriptor and
        a note name on the screen (§15.11)."""
        page = self.sampler()
        root = page.slots()[4]
        self.assertEqual(root.name, "ROOT")
        shown = root.format_value()
        self.assertFalse(shown.strip().lstrip("-").isdigit(),
                         "ROOT showed %r — format_param did not reach it" % shown)

    def test_positions_are_drawn_in_seconds(self):
        page = self.sampler()
        self.assertIn("s", page.slots()[0].format_value())

    def test_no_loop_means_no_loop_markers(self):
        """LOOP-IN = LOOP-OUT = 1 is the default and means no loop, so the
        page draws two markers, not four stacked on the right edge."""
        engine = self.app.instruments["SYNTH"].engine
        self.sampler()
        self.assertFalse(engine.has_loop)
        self.assertEqual(len(engine.markers()), 2)

    def test_a_loop_adds_its_two_markers(self):
        page = self.sampler()
        engine = self.app.instruments["SYNTH"].engine
        page.slots()[2].set_value(0.25)           # LOOP-IN
        self.run_blocks(1)
        self.assertTrue(engine.has_loop)
        marks = engine.markers()
        self.assertEqual(len(marks), 4)
        self.assertEqual([m[1] for m in marks], [False, False, True, True])

    def test_the_waveform_comes_from_the_host(self):
        self.sampler()
        engine = self.app.instruments["SYNTH"].engine
        self.assertTrue(engine.has_asset)
        peaks = engine.asset_waveform(48)
        self.assertIsNotNone(peaks)
        self.assertEqual(len(peaks["max"]), 48)


if __name__ == "__main__":
    unittest.main()
