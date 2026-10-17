"""Stage F2: the mixer UI — eight columns, mute/solo, EQ, the master rack.

Everything is driven through the input seam (`App.on_key` / `App.on_encoder`)
against the **real offline host**, because every question on this page is
"did the gesture reach the host, and does the page draw what came back".

Spec: DDR-061 (mute on SHIFT+1-4, solo on hold), DDR-062 (per-track EQ, the
master rack, MASTER's strip), `screen-sketches.md` § MIXER, `engine-spec.md`
§15.5 / §15.13.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import mixer as MX                                  # noqa: E402
from pages.mixer import EQ, FX, MASTER, MIX                  # noqa: E402

from tests.test_native_tape import TapeCase                  # noqa: E402


class MixerCase(TapeCase):
    """On MIXER, with the hold threshold out of the way where a test wants it."""

    def setUp(self):
        super().setUp()
        self.key("MIXER")
        self.page = self.app.pages["MIXER"]

    def names(self):
        return [s.name if s else None for s in self.page.slots()]

    def values(self):
        return [s.format_value() if s else None for s in self.page.slots()]

    def tracks(self):
        return (self.native.state.get("tape") or {}).get("tracks") or []

    def hold(self, key):
        """Hold a number key past HOLD_S — one tick is what engages a solo."""
        self.page.HOLD_S = 0.0
        self.key(key, down=True)
        self.app.tick()

    def release(self, key):
        self.key(key, down=False)

    def enc_shift(self, idx, delta):
        """The SHIFT row: `App.on_encoder` reads SHIFT off the app, not a flag."""
        self.input.feed(("key", "SHIFT", True), ("enc", idx, delta),
                        ("key", "SHIFT", False))
        self.input.poll_events()


# ---------------------------------------------------------------------------
# MIX: eight columns, and 1-4 doing two jobs (DDR-001, DDR-060, DDR-061)
# ---------------------------------------------------------------------------

class TestMixColumns(MixerCase):
    def test_the_page_opens_on_mix_with_the_banks_four_on_the_encoders(self):
        self.assertEqual(self.page.breadcrumb(), ["MIXER", "MIX"])
        self.assertEqual(self.names(),
                         ["TRK1", "TRK2", "TRK3", "TRK4",
                          "PAN1", "PAN2", "PAN3", "PAN4"])

    def test_the_bank_follows_track_and_shift_arrows_move_it(self):
        self.app.tape.selected = 5                 # TRACK stood in bank B
        self.assertEqual(self.page.bank_tracks(), [4, 5, 6, 7])
        self.assertEqual(self.names()[:4], ["TRK5", "TRK6", "TRK7", "TRK8"])
        self.key("LEFT", shift=True)
        self.assertEqual(self.page.bank_tracks(), [0, 1, 2, 3])
        # the selection came with the bank: one fact, and TRACK stays in step
        self.assertEqual(self.app.tape.selected, 1)
        self.key("RIGHT", shift=True)
        self.assertEqual(self.app.tape.selected, 5)

    def test_all_eight_levels_are_addressable_bank_by_bank(self):
        self.enc(0, -5)
        self.assertLess(self.app.mixer.levels[0], 0.8)
        self.app.tape.selected = 4
        self.enc(0, -5)
        self.assertLess(self.app.mixer.levels[4], 0.8)
        self.run_blocks(1)
        tracks = self.tracks()
        self.assertAlmostEqual(tracks[0]["gain"], self.app.mixer.levels[0], 3)
        self.assertAlmostEqual(tracks[4]["gain"], self.app.mixer.levels[4], 3)

    def test_a_tap_on_one_to_four_is_the_subpage(self):
        for key, sub, name in (("2", EQ, "EQ"), ("3", FX, "FX"),
                               ("4", MASTER, "MASTER"), ("1", MIX, "MIX")):
            self.key(key)
            self.assertEqual(self.page.state.subpage, sub)
            self.assertEqual(self.page.breadcrumb()[1], name)

    def test_a_tap_never_solos(self):
        self.key("2")
        self.assertFalse(any(t.get("solo") for t in self.tracks()))


class TestMuteAndSolo(MixerCase):
    def test_shift_one_to_four_mutes_the_banks_track_and_latches(self):
        self.key("2", shift=True)
        self.run_blocks(1)
        self.assertTrue(self.tracks()[1]["mute"])
        self.assertTrue(self.page.is_muted(1))
        self.assertEqual(self.page.state.subpage, MIX)      # not a navigation
        self.key("2", shift=True)
        self.run_blocks(1)
        self.assertFalse(self.tracks()[1]["mute"])

    def test_mute_addresses_the_shown_bank(self):
        self.app.tape.selected = 4
        self.key("1", shift=True)
        self.run_blocks(1)
        self.assertTrue(self.tracks()[4]["mute"])
        self.assertFalse(self.tracks()[0]["mute"])

    def test_a_held_key_solos_and_lets_go_on_release(self):
        self.hold("3")
        self.run_blocks(1)
        self.assertTrue(self.tracks()[2]["solo"])
        self.assertEqual(self.page.state.subpage, MIX)     # a hold is not a tap
        self.release("3")
        self.run_blocks(1)
        self.assertFalse(self.tracks()[2]["solo"])
        self.assertEqual(self.page.state.subpage, MIX)

    def test_two_held_keys_solo_two_tracks(self):
        self.hold("1")
        self.hold("2")
        self.run_blocks(1)
        solo = [bool(t.get("solo")) for t in self.tracks()]
        self.assertEqual(solo[:4], [True, True, False, False])
        self.release("1")
        self.release("2")
        self.run_blocks(1)
        self.assertFalse(any(t.get("solo") for t in self.tracks()))

    def test_the_page_draws_the_hosts_gate_and_does_not_compute_one(self):
        """H7 is open; the host's answer is solo wins, so the UI asks it."""
        self.native.mixer(track=0, mute=True)
        self.native.mixer(track=0, solo=True)
        self.run_blocks(1)
        self.assertTrue(self.page.is_muted(0))
        self.assertTrue(self.page.is_soloed(0))
        self.assertFalse(self.page.is_silent(0))   # solo wins over its own mute
        self.assertTrue(self.page.is_silent(1))    # and silences the rest
        self.native.mixer(track=0, solo=False)
        self.run_blocks(1)
        self.assertTrue(self.page.is_silent(0))    # the mute is still latched

    def test_panic_lets_every_held_solo_go(self):
        self.hold("1")
        self.run_blocks(1)
        self.app.panic()
        self.run_blocks(1)
        self.assertFalse(any(t.get("solo") for t in self.tracks()))

    def test_a_solo_released_on_another_page_ends(self):
        """#623: the release went to the page that was up, not to MIXER."""
        self.hold("1")
        self.key("TRACK")
        self.release("1")
        self.run_blocks(1)
        self.assertFalse(any(t.get("solo") for t in self.tracks()))
        self.assertFalse(self.page._down)

    def test_the_signal_path_released_on_another_page_goes_away(self):
        """#873 (t623 row 5): SHIFT+MIXER is held; its release on TRACK is
        MIXER's, so the view is already gone when you come back."""
        self.key("SHIFT", down=True)
        self.key("MIXER", down=True)
        self.assertTrue(self.page.signal_path)
        self.key("SHIFT", down=False)
        self.key("TRACK")
        self.key("MIXER", down=False)
        self.assertFalse(self.page.signal_path)

    def test_a_press_carried_off_mixer_never_becomes_a_solo(self):
        """#623: App ticks every page, so the hold timer ran on TRACK."""
        self.page.HOLD_S = 0.0
        self.key("1", down=True)
        self.key("TRACK")
        self.app.tick()
        self.run_blocks(1)
        self.assertFalse(any(t.get("solo") for t in self.tracks()))
        self.release("1")
        self.assertFalse(self.page._down)

    def test_the_strip_says_what_is_muted_or_soloed(self):
        self.key("2", shift=True)
        self.run_blocks(1)
        self.assertIn("MUTE 2", " ".join(t[1] for t in self.app.tickers()))
        self.hold("1")
        self.run_blocks(1)
        self.assertIn("SOLO 1", " ".join(t[1] for t in self.app.tickers()))
        self.release("1")


# ---------------------------------------------------------------------------
# EQ (DDR-062) — real units, and the host's clamp is the only clamp
# ---------------------------------------------------------------------------

class TestEqPage(MixerCase):
    def setUp(self):
        super().setUp()
        self.key("2")

    def test_the_two_rows_are_the_sketchs(self):
        self.assertEqual(self.names(),
                         ["TRACK", "LOW", "MID", "HIGH",
                          "HP", "LP", "MID-F", "MID-Q"])

    def test_a_band_goes_down_in_db_and_comes_back_clamped(self):
        self.enc(1, 6)                      # LOW, 0.5 dB a detent
        self.run_blocks(1)
        self.assertAlmostEqual(self.tracks()[0]["eq"]["low"], 3.0, places=3)
        self.assertEqual(self.values()[1], "+3.0dB")
        # far past the host's +/-12: the page shows the clamp, not the turn
        self.enc(1, 200)
        self.run_blocks(1)
        self.assertAlmostEqual(self.tracks()[0]["eq"]["low"],
                               MX.EQ_MAX_DB, places=3)
        self.assertEqual(self.values()[1], "+12.0dB")

    def test_enc1_picks_the_track_and_the_row_follows_it(self):
        self.enc(0, 4)
        self.assertEqual(self.page.eq_track, 4)
        self.assertEqual(self.values()[0], "TRK5")
        self.enc(1, -4)
        self.run_blocks(1)
        self.assertAlmostEqual(self.tracks()[4]["eq"]["low"], -2.0, places=3)
        self.assertEqual(self.tracks()[0]["eq"]["low"], 0.0)

    def test_hp_and_lp_read_off_at_their_ends(self):
        self.assertEqual(self.values()[4], "OFF")
        self.assertEqual(self.values()[5], "OFF")
        self.enc_shift(0, 8)                 # SHIFT row ENC1 = HP
        self.run_blocks(1)
        self.assertGreater(self.tracks()[0]["eq"]["hp"], MX.EQ_HP[0])
        self.assertNotEqual(self.values()[4], "OFF")

    def test_nothing_on_this_page_keeps_its_own_copy_of_the_block(self):
        """The host is asked every frame; a page-side value cannot drift."""
        self.native.eq(0, mid=-6.0, mid_f=2500.0, mid_q=3.0)
        self.run_blocks(1)
        self.assertEqual(self.values()[2], "-6.0dB")
        self.assertEqual(self.values()[6], "2.50k")
        self.assertEqual(self.values()[7], "3.00")

    def test_flat_is_flat_and_the_curve_says_so(self):
        eq = self.page.eq_of(0)
        self.assertTrue(MX.eq_is_flat(eq))
        self.assertEqual(MX.eq_response_db(eq, [100.0, 1000.0, 8000.0]),
                         [0.0, 0.0, 0.0])

    def test_the_drawn_curve_is_the_hosts_own_sections(self):
        self.native.eq(0, low=6.0, high=-6.0)
        self.run_blocks(1)
        eq = self.page.eq_of(0)
        low, mid, high = MX.eq_response_db(eq, [60.0, 800.0, 10000.0])
        self.assertGreater(low, 4.0)            # +6 shelf under 200 Hz…
        self.assertLess(abs(mid), 2.0)          # …and little in the middle
        self.assertLess(high, -4.0)

    def test_the_filters_are_real_corners_not_shelves_at_the_band_edge(self):
        self.native.eq(0, hp=400.0, lp=4000.0)
        self.run_blocks(1)
        eq = self.page.eq_of(0)
        below, band, above = MX.eq_response_db(eq, [100.0, 1200.0, 16000.0])
        self.assertLess(below, -20.0)           # 12 dB/oct, two octaves down
        self.assertLess(abs(band), 1.5)
        self.assertLess(above, -20.0)


# ---------------------------------------------------------------------------
# MIXER › FX — the master rack, on the rack page that already existed
# ---------------------------------------------------------------------------

class TestMasterRack(MixerCase):
    def test_the_fx_subpage_is_the_master_bus(self):
        self.key("3")
        self.assertEqual(self.page.instrument_id, "master")
        reply = self.native.rack_edit("master",
                                      {"type": "add_tab", "engine": "arbutus/biquad"})
        self.assertNotEqual(reply.get("type"), "error", reply)
        self.run_blocks(1)
        self.assertEqual(self.page.rack_view.n_tabs, 1)
        self.assertEqual(self.native.state["mixer"]["fx"]["tabs"][0]["engine"],
                         "arbutus/biquad")

    def test_a_master_rack_edit_never_lands_on_an_instrument(self):
        self.native.rack_edit("master",
                              {"type": "add_tab", "engine": "arbutus/biquad"})
        self.run_blocks(1)
        self.assertEqual(self.native.rack("SYNTH").get("tabs") or [], [])
        self.assertEqual(self.native.rack("DRUM").get("tabs") or [], [])
        self.assertEqual(len(self.native.rack("master").get("tabs") or []), 1)

    def test_the_rack_pages_slots_and_tabs_work_on_it(self):
        self.key("3")
        self.native.rack_edit("master",
                              {"type": "add_tab", "engine": "arbutus/biquad"})
        self.run_blocks(1)
        self.page.rack_view.tab = 1
        names = [s.name if s else None for s in self.page.slots()]
        self.assertTrue(any(names), names)
        self.key("RIGHT", shift=True)          # SHIFT+› walks the tabs
        self.assertEqual(self.page.rack_view.tab, 2)

    def test_shift_three_on_fx_opens_the_effect_browser(self):
        self.key("3")
        self.key("3", shift=True)
        self.assertIsNotNone(self.app.menu)
        self.assertEqual(self.app.menu.breadcrumb()[:2], ["MIXER", "FX"])

    def test_shift_one_two_four_still_mute_on_the_fx_subpage(self):
        self.key("3")
        self.key("2", shift=True)
        self.run_blocks(1)
        self.assertTrue(self.tracks()[1]["mute"])


# ---------------------------------------------------------------------------
# MASTER (§15.5) and the signal path
# ---------------------------------------------------------------------------

class TestMasterStrip(MixerCase):
    def setUp(self):
        super().setUp()
        self.key("4")

    def test_the_six_knobs_are_the_sketchs(self):
        self.assertEqual(self.names(),
                         ["LEVEL", "COMP", "DRIVE", "BAL",
                          "COMP REL", "DRIVE TONE", None, None])

    def test_every_knob_reaches_the_host(self):
        self.enc(2, 10)                              # DRIVE
        self.enc(3, -4)                              # BAL left
        self.enc_shift(0, 5)                         # COMP REL
        self.enc_shift(1, -10)                       # DRIVE TONE
        self.run_blocks(1)
        mixer = self.native.state["mixer"]
        self.assertAlmostEqual(mixer["drive"], self.app.mixer.drive, places=3)
        self.assertAlmostEqual(mixer["bal"], self.app.mixer.bal, places=3)
        self.assertAlmostEqual(mixer["comp_rel"], self.app.mixer.comp_rel,
                               places=3)
        self.assertAlmostEqual(mixer["drive_tone"], self.app.mixer.drive_tone,
                               places=3)
        self.assertLess(self.app.mixer.bal, 0.0)
        self.assertGreater(self.app.mixer.drive, 0.0)

    def test_the_defaults_are_the_hosts_and_are_bypass(self):
        mixer = self.native.state["mixer"]
        self.assertEqual(mixer["drive"], 0.0)
        self.assertEqual(mixer["bal"], 0.0)
        self.assertAlmostEqual(mixer["drive_tone"], 0.5, places=3)
        self.assertAlmostEqual(mixer["comp_rel"], 0.12, places=3)

    def test_the_master_strip_survives_a_restart(self):
        self.enc(2, 8)
        self.run_blocks(1)
        drive = self.app.mixer.drive
        self.app.flush()
        app = self.make_app(fresh=False)
        self.assertAlmostEqual(app.mixer.drive, drive, places=3)


class TestSignalPath(MixerCase):
    def test_shift_mixer_shows_it_while_held_and_not_after(self):
        self.key("MIXER", shift=True, down=True)
        self.assertTrue(self.page.signal_path)
        self.key("MIXER", down=False)
        self.assertFalse(self.page.signal_path)

    def test_the_chain_is_the_protocols_order(self):
        self.key("MIXER", shift=True, down=True)
        rows = self.page._signal_rows()
        chain = [label for row in rows for label, _ in row]
        for a, b in (("EQ", "MUTE/SOLO"), ("MUTE/SOLO", "GAIN/PAN"),
                     ("MASTER FX", "DRIVE"), ("DRIVE", "COMP"),
                     ("COMP", "LEVEL/BAL"), ("LEVEL/BAL", "OUT")):
            self.assertLess(chain.index(a), chain.index(b), chain)
        self.key("MIXER", down=False)

    def test_a_stage_doing_nothing_is_drawn_dim(self):
        self.key("MIXER", shift=True, down=True)
        live = {label: on for row in self.page._signal_rows() for label, on in row}
        self.assertFalse(live["EQ"])
        self.assertFalse(live["DRIVE"])
        self.assertFalse(live["MASTER FX"])
        self.native.eq(0, low=4.0)
        self.native.mixer(drive=0.4)
        self.native.rack_edit("master",
                              {"type": "add_tab", "engine": "arbutus/biquad"})
        self.run_blocks(1)
        self.app.mixer.drive = 0.4
        live = {label: on for row in self.page._signal_rows() for label, on in row}
        self.assertTrue(live["EQ"])
        self.assertTrue(live["DRIVE"])
        self.assertTrue(live["MASTER FX"])
        self.key("MIXER", down=False)


class TestItAllDraws(MixerCase):
    """Every subpage renders, at both banks, with the gestures live."""

    def test_every_screen_renders(self):
        self.native.eq(1, low=8.0, mid=-5.0, mid_f=900.0, hp=120.0, lp=6000.0)
        self.native.mixer(track=2, mute=True)
        self.native.rack_edit("master",
                              {"type": "add_tab", "engine": "arbutus/biquad"})
        self.run_blocks(1)
        for shot, key in (("f2-mix", "1"), ("f2-eq", "2"),
                          ("f2-fx", "3"), ("f2-master", "4")):
            self.key(key)
            self.assertTrue(os.path.exists(self.shot(shot)))
        self.app.tape.selected = 5
        self.key("1")
        self.assertTrue(os.path.exists(self.shot("f2-mix-bank-b")))
        self.key("MIXER", shift=True, down=True)
        self.assertTrue(os.path.exists(self.shot("f2-signal-path")))
        self.key("MIXER", down=False)


class TestCellsReadTheModel(MixerCase):
    """#632, review m-emu-pages row 3: a cell that kept its own copy went
    stale when another door moved the value, and the next detent jumped it
    back."""

    def test_master_moved_on_track_then_one_detent_on_master(self):
        self.key("TRACK")
        self.enc(3, -10)                         # TRACK's ENC4 is the master
        moved = self.app.mixer.master_level
        self.key("MIXER")
        self.key("4")                            # MASTER
        self.assertEqual(self.page.state.subpage, MASTER)
        self.assertAlmostEqual(self.page.slots()[0].value, moved)
        self.enc(0, 1)
        self.assertAlmostEqual(self.app.mixer.master_level, moved + 0.02)

    def test_a_level_reset_elsewhere_is_what_the_cell_steps_from(self):
        self.enc(0, -10)                         # TRK1 down
        self.app.mixer.reset()                   # what NEW does to the model
        fresh = self.app.mixer.levels[0]
        self.assertAlmostEqual(self.page.slots()[0].value, fresh)
        self.enc(0, 1)
        self.assertAlmostEqual(self.app.mixer.levels[0], min(1.0, fresh + 0.02))


if __name__ == "__main__":
    unittest.main()
