"""N3a regression tests against the real offline host."""
import json
import os
import tempfile

from tests.test_native_smoke import NativeCase
from app.keys import note_key
from app.native_library import NativeFolder, patch_from_state


class TestModRack(NativeCase):
    name = "mod-rack"

    def mod_page(self):
        self.key("4")
        return self.app.pages["SYNTH"]

    def pick(self, page, engine="lfo"):
        folders = self.app.library.list("mod")
        page._pick_mod(next(i for i, f in enumerate(folders)
                            if f.folder == engine), 0)

    def test_browser_from_master_creates_then_replaces_first_tab(self):
        page = self.mod_page()
        self.pick(page)
        self.assertEqual(self.native.mod_tabs("SYNTH")[0]["id"], "arbutus/lfo")
        self.key("LEFT", shift=True)
        self.pick(page, "random")
        self.assertEqual(len(self.native.mod_tabs("SYNTH")), 1)
        self.assertEqual(self.native.mod_tabs("SYNTH")[0]["id"], "arbutus/random")

    def test_rate_uses_engine_display_and_encoder_updates_host(self):
        page = self.mod_page()
        self.pick(page)
        self.native.set_param("SYNTH", "mod", 0, 0.5, tab=0)
        self.assertEqual(page.slots()[0].format_value(), "STOP")
        self.enc(0, 8)
        rec = self.native.mod_tabs("SYNTH")[0]
        self.assertGreater(rec["params"]["rate"], 0.5)
        self.assertEqual(page.slots()[0].format_value(), rec["display"]["rate"])

    def test_four_tabs_have_no_fifth_empty_tab(self):
        page = self.mod_page()
        view = page.mod_rack_view
        for _ in range(4):
            self.native.mod_edit("SYNTH", {"type": "add_mod_tab", "engine": "arbutus/lfo"})
        for _ in range(4):
            self.key("RIGHT", shift=True)
        self.assertEqual(view.tab, 4)
        self.assertFalse(view.on_empty_tab)
        # #68: a full rack still has K past the end, and nothing past that
        for _ in range(4):
            self.key("RIGHT", shift=True)
        self.assertTrue(view.on_keys_tab)
        self.assertFalse(view.on_empty_tab)
        self.key("LEFT", shift=True)
        self.assertEqual(view.tab, 4)

    def test_bypass_and_route_encoders_reach_host(self):
        page = self.mod_page()
        self.pick(page)
        self.key("4")
        self.assertTrue(self.native.mod_rack("SYNTH")["tabs"][0]["bypass"])
        source = self.native.mod_rack("SYNTH")["tabs"][0]["instance"]
        self.native.mod_edit("SYNTH", {"type": "route", "source": source,
                            "output": "sine", "target": "host.pan"})
        self.key("LEFT", shift=True)
        self.enc(0, 5)
        self.key("SHIFT", down=True)
        self.enc(0, 1)
        self.key("SHIFT", down=False)
        route = self.native.mod_rack("SYNTH")["routes"][0]
        self.assertAlmostEqual(route["depth"], 0.35)
        self.assertEqual(route["curve"], "exp")
        self.key("4")
        self.assertTrue(self.native.mod_rack("SYNTH")["bypass"])

    def test_snapshot_roundtrip_keeps_modulation(self):
        page = self.mod_page()
        self.pick(page)
        self.enc(0, 3)
        saved = page.snapshot()
        expected = self.native.mod_rack("SYNTH")
        self.native.mod_edit("SYNTH", {"type": "remove_mod_tab", "index": 0})
        page.restore(saved)
        self.assertEqual(self.native.mod_rack("SYNTH"), expected)

    def test_mod_rack_file_preserves_rest_of_sound(self):
        before = patch_from_state(self.native, "SYNTH")
        with tempfile.TemporaryDirectory() as path:
            doc = {"format": 1, "kind": "modrack", "tabs": [
                {"instance": "lfo", "engine": "arbutus/lfo", "params": {}}], "routes": []}
            with open(os.path.join(path, "test.rack"), "w") as f:
                json.dump(doc, f)
            folder = NativeFolder(category="mod", folder="racks", name="RACKS", path=path)
            self.assertTrue(self.app.library.load_mod_rack_item("SYNTH", folder, "test.rack"))
        after = patch_from_state(self.native, "SYNTH")
        for key in ("engine", "env", "fx", "seq", "octave"):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertEqual(after["mod"]["tabs"][0]["engine"], "arbutus/lfo")

    def test_saved_modulator_item_applies_its_parameters(self):
        page = self.mod_page()
        with tempfile.TemporaryDirectory() as path:
            with open(os.path.join(path, "stopped.patch"), "w") as f:
                json.dump({"format": 1, "kind": "patch", "engine": {"id": "arbutus/lfo", "params": {"rate": 0.5}}}, f)
            folder = NativeFolder(category="mod", folder="lfo", name="LFO", path=path,
                                  manifest={"id": "arbutus/lfo"})
            folder.items = ["stopped.patch"]
            self.app.library.folders["mod"] = [folder]
            page._pick_mod(0, 0)
        self.assertEqual(page.slots()[0].format_value(), "STOP")

    def test_drum_mod_edits_do_not_change_synth(self):
        self.key("DRUM")
        self.key("4")
        self.pick(self.app.pages["DRUM"])
        self.assertEqual(len(self.native.mod_tabs("DRUM")), 1)
        self.assertEqual(len(self.native.mod_tabs("SYNTH")), 0)

    # -- § 6.5 / modulation-spec § 3.2: the one box that reads hardware ----
    def test_motion_box_says_no_sensor_and_only_that_box_does(self):
        page = self.mod_page()
        self.pick(page, "motion")
        view = page.mod_rack_view
        self.assertEqual(self.native.mod_tabs("SYNTH")[0]["id"],
                         "arbutus/motion")
        self.assertFalse(view.motion_sensor)        # no LSM6DSO on any bench
        self.assertEqual(view.outputs_heading("arbutus/motion"),
                         ("NO SENSOR", True))
        # every other modulator's outputs are its own business
        self.assertEqual(view.outputs_heading("arbutus/lfo"),
                         ("OUTPUTS", False))
        # and the page still draws on the box's own tab
        view.tab = 1
        self._fb[:] = 0
        self.app.render(self._fb)
        self.assertTrue(self._fb.any())

    def test_motion_box_with_a_sensor_shows_its_outputs(self):
        page = self.mod_page()
        self.pick(page, "motion")
        view = page.mod_rack_view
        self.native.motion_sensor = True            # what `hello` would say
        self.assertEqual(view.outputs_heading("arbutus/motion"),
                         ("OUTPUTS", False))
        view.tab = 1
        self._fb[:] = 0
        self.app.render(self._fb)
        self.assertTrue(self._fb.any())

    def test_lift_output_drop_on_engine_creates_route_and_ghost(self):
        page = self.mod_page()
        self.pick(page)
        view = page.mod_rack_view

        self.key("LIFT")
        self.assertEqual(view.mode, "copy_source")
        self.assertEqual([p.name if p else None for p in page.slots()[:4]],
                         ["SINE", "TRI", "SQR", "S&H"])
        self.enc(0, 1)
        self.assertEqual(view.lifted_source[1], "sine")
        self.key("1")
        self.key("DROP")
        self.assertEqual(view.mode, "paste_target")
        self.assertTrue(page.slots()[1].highlight)
        target = "engine." + page.instrument.engine.desc_params[1]["id"]
        self.enc(1, 1)

        self.assertEqual(page.state.subpage, 3)
        self.assertEqual(view.tab, 0)
        self.assertEqual(self.native.mod_rack("SYNTH")["routes"][0]["target"],
                         target)
        self.run_blocks(2)
        self.key("1")
        self.assertIsNotNone(page.slots()[1].ghost)

    # -- #68: the K tab (modulation-spec §3.2, §8) ------------------------
    def keys_tab(self, page):
        """SHIFT+› to the end of the row, which is where K is."""
        for _ in range(8):
            self.key("RIGHT", shift=True)
        self.assertTrue(page.mod_rack_view.on_keys_tab)
        return page.mod_rack_view

    def test_keys_tab_is_present_with_no_modulator_at_all(self):
        page = self.mod_page()
        view = self.keys_tab(page)
        self.assertEqual(view.tab_name(), "KEYS")
        self.assertEqual(self.native.mod_tabs("SYNTH"), [])
        # §3.2: no params of its own, so the eight cells stay empty
        self.assertEqual([p for p in page.slots() if p is not None], [])
        # and it draws
        self._fb[:] = 0
        self.app.render(self._fb)
        self.assertTrue(self._fb.any())

    def test_keys_tab_has_no_bypass_and_no_engine_of_its_own(self):
        page = self.mod_page()
        self.pick(page)
        view = self.keys_tab(page)
        self.key("4")                       # DDR-049's second press
        self.assertTrue(view.on_keys_tab)
        self.assertFalse(self.native.mod_rack("SYNTH")["bypass"])
        self.assertFalse(self.native.mod_rack("SYNTH")["tabs"][0]["bypass"])
        # SHIFT+4 on K holds no engine, so it acts on tab 1 as master does
        self.pick(page, "random")
        self.assertEqual(len(self.native.mod_tabs("SYNTH")), 1)
        self.assertEqual(self.native.mod_tabs("SYNTH")[0]["id"], "arbutus/random")
        self.assertEqual(view.tab, 1)

    def test_keys_vel_route_made_from_the_ui_reaches_the_host(self):
        page = self.mod_page()
        view = self.keys_tab(page)

        self.key("LIFT")
        self.assertEqual(view.mode, "copy_source")
        self.assertEqual([p.name if p else None for p in page.slots()[:4]],
                         ["VEL", "NOTE", None, None])
        self.enc(0, 1)
        self.assertEqual(view.lifted_source[:2], ("keys", "vel"))
        self.assertEqual(view.lifted_source[2], "KEYS.VEL")

        self.key("1")                       # the engine page
        self.key("DROP")
        self.assertEqual(view.mode, "paste_target")
        target = "engine." + page.instrument.engine.desc_params[1]["id"]
        self.enc(1, 1)

        self.assertEqual(view.tab, 0)       # back on master, depth under hand
        self.assertEqual([(r["source"], r["output"], r["target"])
                          for r in self.native.mod_rack("SYNTH")["routes"]],
                         [("keys", "vel", target)])

    def test_keys_note_is_the_second_output(self):
        page = self.mod_page()
        view = self.keys_tab(page)
        self.key("LIFT")
        self.enc(1, 1)
        self.assertEqual(view.lifted_source[:2], ("keys", "note"))

    def test_a_keys_vel_route_follows_the_velocity_of_the_note_played(self):
        """The capability #68 says was built and unreachable, now reached.

        The negative control is the first pair (#56): before the route exists
        the target has no live value at all, so a pass here cannot come from
        the meter moving on its own.
        """
        page = self.mod_page()
        self.keys_tab(page)
        target = "engine." + page.instrument.engine.desc_params[1]["id"]

        def played(velocity):
            # `App.on_key`'s third argument is the input seam's velocity: the
            # scripted driver has no way to say it, a MIDI note-on does.
            self.app.on_key(note_key(7), True, velocity)
            self.run_blocks(4)
            v = self.native.mod_route_values().get(target)
            self.app.on_key(note_key(7), False)
            self.run_blocks(2)
            return v

        self.assertIsNone(played(0.2))              # nothing routed yet
        self.assertIsNone(played(1.0))

        self.native.mod_edit("SYNTH", {"type": "route", "source": "keys",
                                       "output": "vel", "target": target})
        soft, loud = played(0.2), played(1.0)
        self.assertIsNotNone(soft)
        # \u00a76.2's default depth is 0.25 and \u00a78 applies the last note's
        # velocity, so the two readings differ by 0.25 x the velocity gap.
        depth = self.native.mod_rack("SYNTH")["routes"][0]["depth"]
        self.assertAlmostEqual(loud - soft, depth * (1.0 - 0.2), places=4)

    def test_route_range_popup_and_route_lift_move_delete(self):
        page = self.mod_page()
        self.pick(page)
        source = self.native.mod_rack("SYNTH")["tabs"][0]["instance"]
        for output, target in (("sine", "host.pan"),
                               ("tri", "engine.level")):
            self.native.mod_edit("SYNTH", {"type": "route", "source": source,
                                  "output": output, "target": target})
        view = page.mod_rack_view
        view.tab = 0

        self.key("DROP", shift=True)
        self.assertEqual(view.mode, "range")
        self.assertEqual([p.name if p else None for p in page.slots()[:4]],
                         ["LO", "HI", "CURVE", "DEPTH"])
        self.enc(0, 10)
        self.assertAlmostEqual(self.native.mod_rack("SYNTH")["routes"][0]["lo"],
                               0.10)
        self.key("DROP", shift=True)

        self.key("LIFT")
        self.enc(0, 1)
        self.assertEqual(len(self.native.mod_rack("SYNTH")["routes"]), 1)
        self.assertEqual(view.lifted_route["output"], "sine")
        self.key("DROP")
        self.enc(1, 1)
        self.assertEqual([r["output"] for r in self.native.mod_rack("SYNTH")["routes"]],
                         ["tri", "sine"])

        self.key("LIFT")
        self.enc(1, 1)                 # lift without a later DROP = delete
        self.assertEqual([r["output"] for r in self.native.mod_rack("SYNTH")["routes"]],
                         ["tri"])

    def test_fx_second_press_bypasses_tab_and_master(self):
        page = self.app.pages["SYNTH"]
        self.key("3")
        folders = self.app.library.list("fx")
        page._pick_fx(next(i for i, f in enumerate(folders)
                           if f.folder == "biquad"), 0)
        self.key("3")
        self.assertTrue(self.native.rack("SYNTH")["tabs"][0]["bypass"])
        self.key("LEFT", shift=True)
        self.key("3")
        self.assertTrue(self.native.rack("SYNTH")["bypass"])


    def test_the_strip_names_the_box_after_a_scroll_past_another(self):
        # #329: SHIFT+4 applies on every detent, so scrolling to lfo past
        # follower swaps the box on a tab follower named.
        page = self.mod_page()
        view = page.mod_rack_view
        self.key("4", shift=True)
        folders = [f.folder for f in self.app.library.list("mod")]
        self.assertLess(folders.index("follower"), folders.index("lfo"), folders)
        for _ in range(folders.index("lfo")):             # one detent at a time
            self.enc(0, 1)
        self.key("4")
        self.run_blocks(1)
        self.assertEqual(self.native.mod_tabs("SYNTH")[0]["id"], "arbutus/lfo")
        self.key("LIFT")
        self.enc(0, 1)
        lifted = [t[1] for t in view.tickers() if t[1].startswith("LIFTED:")]
        self.assertEqual(len(lifted), 1, view.tickers())
        self.assertTrue(lifted[0].startswith("LIFTED: LFO_1."), lifted)

    # #711: LIFT first, *then* browse: the lift follows the tab's new name
    # when the new box has the same output, and is cancelled when it has not.
    def browse_mod(self, *folders):
        """SHIFT+4, then one detent at a time through each of `folders`, close."""
        self.key("4", shift=True)
        names = [f.folder for f in self.app.library.list("mod")]
        for name in folders:
            at = self.app.menu.state.folder_idx
            step = 1 if names.index(name) > at else -1
            for _ in range(abs(names.index(name) - at)):
                self.enc(0, step)
        self.key("4")
        self.run_blocks(1)

    def test_a_lifted_source_follows_a_browse_and_drops(self):
        page = self.mod_page()
        view = page.mod_rack_view
        self.browse_mod("follower")
        self.key("LIFT")
        self.enc(0, 1)                                    # ENV
        self.assertEqual(view.lifted_source[:2], ("follower_1", "env"))
        self.browse_mod("lfo", "envelope")                # past a box with no ENV
        self.assertEqual(self.native.mod_rack("SYNTH")["tabs"][0]["instance"],
                         "envelope_1")
        self.assertEqual(view.lifted_source, ("envelope_1", "env", "ENVELOPE_1.ENV"))
        self.key("1")
        self.key("DROP")
        target = "engine." + page.instrument.engine.desc_params[0]["id"]
        self.enc(0, 1)
        self.assertEqual([(r["source"], r["output"], r["target"])
                          for r in self.native.mod_rack("SYNTH")["routes"]],
                         [("envelope_1", "env", target)])

    def test_a_lifted_source_the_new_box_lacks_is_cancelled_on_the_strip(self):
        page = self.mod_page()
        view = page.mod_rack_view
        self.browse_mod("follower")
        self.key("LIFT")
        self.enc(1, 1)                                    # FAST
        self.browse_mod("random")                         # lfo, motion, random
        self.assertIsNone(view.lifted_source)
        self.assertIn("LIFT CANCELLED: RANDOM_1 HAS NO FAST",
                      [t[1] for t in self.app.tickers()])

    def test_a_lifted_route_follows_its_fx_target_through_a_browse(self):
        page = self.app.pages["SYNTH"]
        view = page.mod_rack_view
        self.native.rack_edit("SYNTH", {"type": "add_tab", "engine": "arbutus/reverb"})
        self.native.mod_edit("SYNTH", {"type": "add_mod_tab", "engine": "arbutus/lfo"})
        self.native.mod_edit("SYNTH", {"type": "route", "source": "lfo_1",
                                       "output": "sine", "target": "fx.reverb_1.mix"})
        self.key("4")
        self.key("LEFT", shift=True)                      # MOD master
        self.key("LIFT")
        self.enc(0, 1)                                    # lifts the route off
        self.assertEqual(self.native.mod_rack("SYNTH")["routes"], [])
        self.key("3")                                     # FX, its one tab
        rack = page.rack_view
        rack.tab = 1
        self.key("3", shift=True)
        # the browser's own rows: SAVE THIS RACK sits above the library's (#820)
        names = self.app.menu.source.folders()
        at = self.app.menu.state.folder_idx
        for _ in range(abs(names.index("DELAY") - at)):
            self.enc(0, 1 if names.index("DELAY") > at else -1)
        self.key("3")
        self.assertEqual(view.lifted_route["target"], "fx.delay_1.mix")
        self.key("4")
        self.key("DROP")
        self.enc(0, 1)
        self.assertEqual([r["target"] for r in self.native.mod_rack("SYNTH")["routes"]],
                         ["fx.delay_1.mix"])

    # #1134 row 1: undo puts the old box back, and the lift goes back with it
    def test_undo_after_a_browse_carries_a_lifted_source_back(self):
        page = self.mod_page()
        view = page.mod_rack_view
        self.browse_mod("follower")
        self.key("LIFT")
        self.enc(0, 1)                                    # ENV
        self.browse_mod("lfo", "envelope")
        self.assertEqual(view.lifted_source[0], "envelope_1")
        self.key("STOP", shift=True)                      # undo the browse
        self.assertEqual(self.native.mod_rack("SYNTH")["tabs"][0]["instance"],
                         "follower_1")
        self.assertEqual(view.lifted_source, ("follower_1", "env", "FOLLOWER_1.ENV"))
        self.key("1")
        self.key("DROP")
        target = "engine." + page.instrument.engine.desc_params[0]["id"]
        self.enc(0, 1)
        self.assertEqual([(r["source"], r["output"], r["target"])
                          for r in self.native.mod_rack("SYNTH")["routes"]],
                         [("follower_1", "env", target)])

    def lift_fx_route(self, param):
        """An LFO route onto REVERB's `param`, lifted off the MOD master tab;
        then FX's one tab, showing."""
        page = self.app.pages["SYNTH"]
        self.native.rack_edit("SYNTH", {"type": "add_tab", "engine": "arbutus/reverb"})
        self.native.mod_edit("SYNTH", {"type": "add_mod_tab", "engine": "arbutus/lfo"})
        self.native.mod_edit("SYNTH", {"type": "route", "source": "lfo_1",
                                       "output": "sine",
                                       "target": "fx.reverb_1." + param})
        self.key("4")
        self.key("LEFT", shift=True)                      # MOD master
        self.key("LIFT")
        self.enc(0, 1)                                    # lifts the route off
        self.assertEqual(self.native.mod_rack("SYNTH")["routes"], [])
        self.key("3")
        page.rack_view.tab = 1
        return page.mod_rack_view

    def browse_fx(self, *names):
        self.key("3", shift=True)
        folders = self.app.menu.source.folders()
        for name in names:
            at = self.app.menu.state.folder_idx
            step = 1 if folders.index(name) > at else -1
            for _ in range(abs(folders.index(name) - at)):
                self.enc(0, step)
        self.key("3")
        self.run_blocks(1)

    # #1134 row 2: a route a browse cancelled stays cancelled once it closes
    def test_a_cancelled_lifted_route_stays_gone_in_a_later_browse(self):
        view = self.lift_fx_route("size")                 # DELAY has no SIZE
        self.browse_fx("DELAY", "REVERB")                 # same browse: back
        self.assertEqual(view.lifted_route["target"], "fx.reverb_1.size")
        self.browse_fx("DELAY")                           # cancelled, closed
        self.assertIsNone(view.lifted_route)
        self.browse_fx("REVERB")                          # a new browse
        self.assertIsNone(view.lifted_route)
        self.key("4")
        self.key("DROP")
        self.enc(0, 1)
        self.assertEqual(self.native.mod_rack("SYNTH")["routes"], [])

    def lift_route_from(self, box, output):
        """A route `box`.`output` -> host.pan, lifted off the MOD master tab."""
        page = self.mod_page()
        view = page.mod_rack_view
        self.browse_mod(box)
        source = self.native.mod_rack("SYNTH")["tabs"][0]["instance"]
        self.native.mod_edit("SYNTH", {"type": "route", "source": source,
                                       "output": output, "target": "host.pan"})
        view.tab = 0
        self.key("LIFT")
        self.enc(0, 1)
        self.assertEqual(self.native.mod_rack("SYNTH")["routes"], [])
        return view

    # #1134 row 1: undoing a route's lift puts it back, lifted no longer
    def test_undo_after_lifting_a_route_does_not_drop_it_twice(self):
        view = self.lift_route_from("follower", "env")
        self.key("STOP", shift=True)
        self.assertEqual(len(self.native.mod_rack("SYNTH")["routes"]), 1)
        self.assertIsNone(view.lifted_route)
        view.tab = 0
        self.key("DROP")
        self.enc(0, 1)
        self.assertEqual([(r["source"], r["output"], r["target"])
                          for r in self.native.mod_rack("SYNTH")["routes"]],
                         [("follower_1", "env", "host.pan")])

    def test_undo_during_a_route_paste_ends_the_paste(self):
        view = self.lift_route_from("follower", "env")
        self.key("DROP")
        self.assertEqual(view.mode, "paste_route")
        self.key("STOP", shift=True)
        self.assertIsNone(view.lifted_route)
        self.assertIsNone(view.mode)

    # #1134 row 3: a lifted route whose MOD source the browse replaces
    def test_a_lifted_route_follows_its_source_through_a_browse(self):
        view = self.lift_route_from("follower", "env")
        self.browse_mod("lfo", "envelope")                # past a box with no ENV
        self.assertIsNotNone(view.lifted_route)
        self.assertEqual((view.lifted_route["source"], view.lifted_route["output"]),
                         ("envelope_1", "env"))
        self.key("LEFT", shift=True)                      # back to the master tab
        self.key("DROP")
        self.assertEqual(view.mode, "paste_route")
        self.enc(0, 1)
        self.assertEqual([(r["source"], r["output"], r["target"])
                          for r in self.native.mod_rack("SYNTH")["routes"]],
                         [("envelope_1", "env", "host.pan")])

    def test_a_lifted_route_whose_new_source_lacks_its_output_is_cancelled(self):
        view = self.lift_route_from("follower", "fast")
        self.browse_mod("random")
        self.assertIsNone(view.lifted_route)
        self.assertIn("LIFT CANCELLED: RANDOM_1 HAS NO FAST",
                      [t[1] for t in self.app.tickers()])

    # #1134 row 4: a cancel ends the paste mode already under way
    def test_a_cancel_ends_an_active_source_paste(self):
        page = self.mod_page()
        view = page.mod_rack_view
        self.browse_mod("follower")
        self.key("LIFT")
        self.enc(1, 1)                                    # FAST
        self.key("1")
        self.key("DROP")
        self.assertEqual(view.mode, "paste_target")
        self.key("4")
        self.browse_mod("random")
        self.assertIsNone(view.lifted_source)
        self.assertIsNone(view.mode)

    def test_a_cancel_ends_an_active_route_paste(self):
        view = self.lift_route_from("follower", "fast")
        self.key("DROP")
        self.assertEqual(view.mode, "paste_route")
        self.browse_mod("random")
        self.assertIsNone(view.lifted_route)
        self.assertIsNone(view.mode)


class TestStepmodEntry(NativeCase):
    """#60 — the STEP/VALUE gesture on an `arbutus/stepmod` tab.

    `modulation-spec.md` §3.2.1. The two parameters are the UI's own, so
    everything here is about the *asset*: the file the box opens, what it
    holds, and that it is still there after a save.
    """

    name = "stepmod-entry"

    def stepmod_tab(self):
        self.key("4")
        page = self.app.pages["SYNTH"]
        folders = self.app.library.list("mod")
        page._pick_mod(next(i for i, f in enumerate(folders)
                            if f.folder == "stepmod"), 0)
        return page, page.mod_rack_view

    def asset(self, inst="SYNTH", tab=0):
        return (self.native.mod_rack(inst)["tabs"][tab].get("assets")
                or {}).get("pattern")

    def test_the_tab_gains_step_and_value_after_the_boxs_own_five(self):
        page, view = self.stepmod_tab()
        names = [p.name if p else None for p in page.slots()]
        self.assertEqual(names[:5], ["STEPS", "DIV", "SLEW", "DIR", "RANGE"])
        self.assertEqual(names[5:], ["STEP", "VALUE", None])
        # nothing has been entered, so nothing has been written
        self.assertIsNone(self.asset())

    def test_no_other_modulator_gets_the_gesture(self):
        self.key("4")
        page = self.app.pages["SYNTH"]
        folders = self.app.library.list("mod")
        page._pick_mod(next(i for i, f in enumerate(folders)
                            if f.folder == "lfo"), 0)
        names = [p.name if p else None for p in page.slots()]
        self.assertNotIn("VALUE", names)

    def test_step_latches_and_value_writes_the_asset_the_box_reads(self):
        from app import stepmod as SM

        page, view = self.stepmod_tab()
        # STEPS 4 (slot 0, an INTEGER in unit space; the default is 8)
        page.slots()[0].set_value(4)
        self.assertEqual(view.step_values(0), SM.default_values(4))

        # enter -1, 1, -1, 1 by turning STEP to each step and VALUE onto it
        for step, want in enumerate((-1.0, 1.0, -1.0, 1.0)):
            view.step_sel[view._tab_instance(0)] = step
            page.slots()[6].set_value(want)
            self.assertTrue(view.flush_pattern())
        self.assertEqual(view.step_values(0), [-1.0, 1.0, -1.0, 1.0])

        # the box is reading exactly that file
        path = self.asset()
        self.assertTrue(SM.is_scratch(path), path)
        self.assertEqual(SM.read_values(path), [-1.0, 1.0, -1.0, 1.0])
        self.assertEqual(self.native.mod_tabs("SYNTH")[0]["assets"]["pattern"],
                         path)

    def test_entry_keeps_the_tabs_own_knobs_and_its_routes(self):
        page, view = self.stepmod_tab()
        source = self.native.mod_rack("SYNTH")["tabs"][0]["instance"]
        self.native.mod_edit("SYNTH", {"type": "route", "source": source,
                                       "output": "step", "target": "host.pan"})
        page.slots()[2].set_normalized(0.5)          # SLEW
        before = dict(self.native.mod_rack("SYNTH")["tabs"][0]["params"])

        page.slots()[6].set_value(0.5)
        self.assertTrue(view.flush_pattern())
        after = self.native.mod_rack("SYNTH")["tabs"][0]["params"]
        self.assertEqual(after, before)
        self.assertEqual(len(self.native.mod_rack("SYNTH")["routes"]), 1)

    def test_one_push_a_frame_however_many_detents(self):
        page, view = self.stepmod_tab()
        slot = page.slots()[6]
        slot.turn(3)                                  # three detents, one frame
        self.assertIsNotNone(view._pattern_dirty)
        self.assertTrue(view.flush_pattern())
        self.assertFalse(view.flush_pattern())        # nothing left to send

    def test_a_wrapped_file_is_materialised_at_the_drawn_length(self):
        from app import stepmod as SM

        page, view = self.stepmod_tab()
        page.slots()[0].set_value(5)                  # STEPS 5
        view._entered[view._tab_instance(0)] = [1.0, -1.0]
        self.assertEqual(view.step_values(0), [1.0, -1.0, 1.0, -1.0, 1.0])
        view.step_sel[view._tab_instance(0)] = 0
        page.slots()[6].set_value(0.5)
        self.assertTrue(view.flush_pattern())
        self.assertEqual(SM.read_values(self.asset()),
                         [0.5, -1.0, 1.0, -1.0, 1.0])

    def test_saving_a_project_promotes_the_entered_pattern(self):
        from app import stepmod as SM
        from app.project_file import promote_step_patterns

        page, view = self.stepmod_tab()
        page.slots()[6].set_value(-0.75)
        self.assertTrue(view.flush_pattern())
        self.assertTrue(SM.is_scratch(self.asset()))
        entered = SM.read_values(self.asset())

        out = promote_step_patterns(self.app, "BIG CAT")
        self.assertEqual(list(out), ["SYNTH.%s" % view._tab_instance(0)])
        saved = self.asset()
        self.addCleanup(lambda p=saved: os.path.exists(p) and os.remove(p))
        self.assertFalse(SM.is_scratch(saved))
        self.assertTrue(saved.endswith(SM.SUFFIX), saved)
        self.assertEqual(SM.read_values(saved), entered)
        # the box is reading the library file now, and a second save is a no-op
        self.assertEqual(self.native.mod_tabs("SYNTH")[0]["assets"]["pattern"],
                         saved)
        self.assertEqual(promote_step_patterns(self.app, "BIG CAT"), {})

    def test_a_promoted_pattern_does_not_show_up_in_the_browser(self):
        from app import stepmod as SM
        from app.project_file import mod_pattern_folder, promote_step_patterns

        page, view = self.stepmod_tab()
        page.slots()[6].set_value(0.25)
        view.flush_pattern()
        promote_step_patterns(self.app, "BIG CAT")
        saved = self.asset()
        self.addCleanup(lambda p=saved: os.path.exists(p) and os.remove(p))
        folder = mod_pattern_folder(self.app, "arbutus/stepmod")
        folder.rescan_items()
        self.assertNotIn(os.path.basename(saved), folder.items)
        self.assertEqual(folder.items or ["use"], ["use"])
        self.assertTrue(SM.read_values(saved))

    def test_the_patch_a_promoted_pattern_rides_in_reloads(self):
        from app import stepmod as SM
        from app.native_library import patch_from_state
        from app.project_file import promote_step_patterns

        page, view = self.stepmod_tab()
        page.slots()[6].set_value(1.0)
        view.flush_pattern()
        promote_step_patterns(self.app, "BIG CAT")
        saved = self.asset()
        self.addCleanup(lambda p=saved: os.path.exists(p) and os.remove(p))

        doc = patch_from_state(self.native, "SYNTH", "BIG CAT")
        self.assertEqual(doc["mod"]["tabs"][0]["assets"]["pattern"], saved)
        self.native.mod_edit("SYNTH", {"type": "remove_mod_tab", "index": 0})
        self.assertNotEqual(self.native.load_patch("SYNTH", doc).get("type"),
                            "error")
        self.assertEqual(SM.read_values(self.asset()),
                         SM.read_values(saved))

    def test_the_step_bar_is_drawn_on_the_tab(self):
        page, view = self.stepmod_tab()
        view.tab = 1
        page.slots()[6].set_value(1.0)
        self._fb[:] = 0
        self.app.render(self._fb)
        self.assertTrue(self._fb.any())

    def test_entering_a_four_step_pattern_with_the_real_encoders(self):
        """The ticket's acceptance, driven the way a finger drives it."""
        from app import stepmod as SM

        page, view = self.stepmod_tab()
        view.tab = 1
        self.enc(0, -4)                               # STEPS 8 -> 4
        self.assertEqual(len(view.step_values(0)), 4)

        for step, detents in enumerate((20, -20, 10, -10)):
            self.key("SHIFT", down=True)
            self.enc(1, step - view.step_index(0))    # STEP -> this one
            self.assertEqual(view.step_index(0), step)
            self.enc(2, detents)                      # VALUE
            self.key("SHIFT", down=False)
            self.app.tick()                           # the frame that pushes it

        want = [round(v, 4) for v in view.step_values(0)]
        self.assertEqual(SM.read_values(self.asset()), want)
        self.assertEqual(len(want), 4)
        self.assertGreater(want[0], want[1])
        self.assertNotEqual(want, SM.default_values(4))
