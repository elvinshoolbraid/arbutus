"""SYNTH page — subpages ENGINE / ENV / FX / MOD on keys 1-4.

Layout and parameter sets follow screen-sketches.md § SYNTH.
"""

from typing import List, Optional

import numpy as np

from app import box_trust
from app import patch as P
from app.menu import LibrarySource, MenuPage, StaticSource
from app.name_menu import NameMenu, first_free
from app.native_library import is_rack_folder
from app.save_row import SaveRowMenu
from app.page import Page
from app.params import Param, StepType, pad_slots
from ui import draw, palette
from ui.font import LARGE, SMALL

ENGINE, ENV, FX, MOD = 0, 1, 2, 3
SUBPAGES = ("ENGINE", "ENV", "FX", "MOD")

ENV_MODES = ["poly", "mono", "legato", "unison"]

#: The LFO subpage is **superseded by the host's MOD rack** (modulation-spec):
#: the host has no LFO of its own, so these four knobs move nothing. The page
#: still draws — the shape it draws is the shape a MOD tab will get — and the
#: values ride along in a patch's `lfo` block, which the host carries and does
#: not apply. Stage N3 replaces this subpage with the MOD rack.
LIVE_INPUT = "arbutus/live-input"
#: DDR-063's four, in the host's order — the same tuple INPUT's cell uses.
SOURCES = ("mic", "line", "usb", "resample")
#: DDR-063 / screen-sketches § SYNTH › ENGINE: with the input in the ENGINE slot
#: the main area is the live waveform, which here is the same peak history the
#: scope already draws — the UI never sees samples (see `App.scope`).
LFO_SHAPES = ["SINE", "TRI", "SQR", "RAND"]
LFO_DESTS = ["OFF", "AMP", "FILT"]

#: What a turn on a dead ENV cell says in the strip (S11, #53). `[TBD-Elfy]`:
#: the ticket suggested `NO ENV · dbox`; this names the box the way the ENGINE
#: header does, so the sentence works for `live-input` as well as a drum box.
NO_ENV_TICKER = "NO ENV · %s"


def _without_missing_assets(doc: dict) -> Optional[dict]:
    """A copy of a §7.2 patch with every asset whose file is gone dropped from
    `engine` and `seq`, or None when nothing was missing (so a refusal for
    any other reason is not retried)."""
    import os
    from copy import deepcopy

    out = deepcopy(doc)
    dropped = False
    for slot in ("engine", "seq"):
        block = out.get(slot)
        assets = block.get("assets") if isinstance(block, dict) else None
        if not isinstance(assets, dict):
            continue
        keep = {k: v for k, v in assets.items()
                if not isinstance(v, str) or os.path.exists(v)}
        if len(keep) != len(assets):
            dropped = True
            block["assets"] = keep
    return out if dropped else None


def _adapt(engine) -> List[Param]:
    """The 8 slots of the loaded engine, built from the host's descriptor.

    `parameter-framework.md` says the UI knows nothing about synthesis; here
    it could not if it wanted to — the engine is in another process and these
    slots are id/name/type/curve/min/max/def/step/unit/labels (§3.5).
    """
    if engine is None:
        return []
    return engine.ui_slots()


class SynthPage(Page):
    key_id = "SYNTH"
    title = "SYNTH"
    category = "performance"
    subpage_names = SUBPAGES
    #: which Instrument this page drives, and which library subtree it browses.
    #: DRUM subclasses this page and only changes these two plus ENGINE.
    instrument_id = "SYNTH"
    library_category = "synth"
    #: the box a fresh instrument arrives on, named rather than "folder 0".
    #: Stage H1 learned this on SEQ and stage I1 hit it again: `arbutus/dbox`
    #: sorts before `arbutus/drum`, so adding a box would have changed which
    #: engine DRUM starts on. That is not a decision a new box gets to make.
    DEFAULT_BOX = "fm"

    def __init__(self, app):
        super().__init__(app)
        self.slot_store = None
        #: the save's name menu while it is up (SHIFT+SYNTH, #819)
        self._names: Optional[NameMenu] = None
        self.state.subpage = ENGINE
        self._engine_params: List[Param] = []
        #: DDR-054's one-step undo of the last edit *on this page*. Built
        #: first: everything below is allowed to push into it.
        from app.edit_undo import EditUndo
        self._undo = EditUndo(self.snapshot, self._restore_edit)
        #: -1 / 0 / +1 — which way SHIFT+‹ › is currently bending (DDR-052)
        self._bend_dir = 0
        #: the FX subpage is the §6 rack (DDR-038/039/040)
        from pages.rack import RackView
        self.rack_view = RackView(self)
        #: the MOD subpage is the modulation-spec §2 rack (N3a)
        from pages.rack import ModRackView
        self.mod_rack_view = ModRackView(self)
        self._build_static_params()
        # **Pull before the first push.** `set_engine` hands this page's ADSR
        # to the host, so a page that starts on SYNTH's defaults overwrites
        # whatever the host had for *this* instrument. DRUM's are a 0 s / d 4 s
        # (§15.11, `kDrumEnvD` — v1's sound, which the v1 golden holds), and
        # until this line every kit decayed in 200 ms instead.
        self._pull_env()
        self._load_default_engine()

    # -- engine -----------------------------------------------------------
    @property
    def instrument(self):
        return self.app.instruments[self.instrument_id]

    def _load_default_engine(self):
        folders = self.app.library.list(self.library_category)
        if not folders:
            return
        self.load_engine(self._default_index(folders), 0)

    def _default_index(self, folders) -> int:
        """DEFAULT_BOX, or the first installed engine: a marked folder
        (#1122) would only say NEEDS ENGINE."""
        ok = [n for n, f in enumerate(folders) if not f.manifest.get("needs")]
        names = [folders[n].folder for n in ok]
        if self.DEFAULT_BOX in names:
            return ok[names.index(self.DEFAULT_BOX)]
        return ok[0] if ok else 0

    def load_engine(self, folder_idx: int, item_idx: int):
        folders = self.app.library.list(self.library_category)
        if not folders:
            return
        folder_idx = max(0, min(len(folders) - 1, folder_idx))
        lf = folders[folder_idx]
        item = lf.items[max(0, min(len(lf.items) - 1, item_idx))] if lf.items else ""
        inst = self.instrument
        same = (inst.engine_folder is not None and inst.engine_folder.folder == lf.folder)
        engine = inst.engine if same else self.app.library.instantiate(
            lf, self.app.sample_rate)
        if engine is None:
            # a marked folder (#1122) says the host's own `NEEDS ENGINE <id>`;
            # any other refusal stays quiet, as it was (a restart's `BUSY`)
            said = getattr(self.app.library, "last_error", None)
            if said and lf.manifest.get("needs"):
                self.app.flash(said, said)
            return False
        self.app.library.apply_item(engine, lf, item)
        inst.set_engine(engine, lf, item)
        # the slots carry the host's values, so they are rebuilt after every
        # item too — the item is what changed them
        self._engine_params = _adapt(engine)
        return True

    # -- static param sets ------------------------------------------------
    def _build_static_params(self):
        inst_get = lambda: self.instrument

        def env_cb(attr):
            def cb(v):
                setattr(inst_get().env, attr, float(v))
            return cb

        self.p_atk = Param("ATK", 0.001, 2.0, 0.012, StepType.LINEAR, 0.005,
                           unit="s", display_format="{:.3f}")
        self.p_dec = Param("DEC", 0.001, 2.0, 0.200, StepType.LINEAR, 0.005,
                           unit="s", display_format="{:.3f}")
        self.p_sus = Param("SUS", 0.0, 1.0, 0.80, StepType.LINEAR, 0.01)
        self.p_rel = Param("REL", 0.005, 4.0, 0.340, StepType.LINEAR, 0.01,
                           unit="s", display_format="{:.3f}")
        self.p_atk.on_change = env_cb("attack")
        self.p_dec.on_change = env_cb("decay")
        self.p_sus.on_change = env_cb("sustain")
        self.p_rel.on_change = env_cb("release")

        self.p_mode = Param("MODE", 0, len(ENV_MODES) - 1, 0, StepType.DISCRETE,
                            choices=ENV_MODES)
        self.p_mode.on_change = lambda v: inst_get().set_env_mode(
            int(v), ENV_MODES[max(0, min(len(ENV_MODES) - 1, int(v)))])
        # the rest of the SHIFT row (DDR-052). Ranges and units are the host's
        # (`audio/host/PROTOCOL.md` § env), because these are fields of its
        # `env` block, not engine parameters: GLIDE is portamento in
        # mono/legato *and* the bend ramp, DETUNE is the unison spread
        # outermost to outermost, BEND is the bend range SHIFT+‹ › reaches.
        def policy_cb(attr):
            def cb(v):
                inst = inst_get()
                setattr(inst, attr, float(v))
                inst.push_voice_policy()
            return cb

        self.p_glide = Param("GLIDE", 0.0, 2.0, 0.0, StepType.LINEAR, 0.01,
                             unit="s", display_format="{:.2f}")
        self.p_detune = Param("DETUNE", 0.0, 50.0, 12.0, StepType.LINEAR, 0.5,
                              unit="ct", display_format="{:.0f}")
        self.p_bend = Param("BEND", 0, 24, 2, StepType.DISCRETE,
                            unit="st", display_format="{:.0f}")
        self.p_glide.on_change = policy_cb("env_glide")
        self.p_detune.on_change = policy_cb("env_detune")
        self.p_bend.on_change = policy_cb("env_bend_range")

        # LFO: inert until the MOD rack lands on this subpage (stage N3).
        # The values are kept and saved, so a patch written now still carries
        # them; nothing sounds different when they move.
        self.p_rate = Param("RATE", 0.05, 20.0, 4.0, StepType.LINEAR, 0.1,
                            unit="Hz", display_format="{:.2f}")
        self.p_depth = Param("DEPTH", 0.0, 1.0, 0.0, StepType.LINEAR, 0.02)
        self.p_dest = Param("DEST", 0, len(LFO_DESTS) - 1, 0, StepType.DISCRETE,
                            choices=LFO_DESTS)
        self.p_shape = Param("SHAPE", 0, len(LFO_SHAPES) - 1, 0, StepType.DISCRETE,
                             choices=LFO_SHAPES)

    # -- Page contract ----------------------------------------------------
    def slots(self) -> List[Optional[Param]]:
        sp = self.state.subpage
        if sp == ENGINE:
            self._sync_live_input()
            slots = pad_slots(self._engine_params)
            return self._decorate_mod_targets(slots)
        if sp == ENV:
            self.lock_env_cells([self.p_atk, self.p_dec, self.p_sus,
                                 self.p_rel])
            slots = pad_slots([self.p_atk, self.p_dec, self.p_sus, self.p_rel,
                               self.p_mode, self.p_glide, self.p_detune,
                               self.p_bend])
            return self._decorate_mod_targets(slots)
        if sp == FX:
            slots = self.rack_view.slots()
            return self._decorate_mod_targets(slots)
        if sp == MOD:
            return self.mod_rack_view.slots()
        return pad_slots([self.p_rate, self.p_depth, self.p_dest, self.p_shape])

    # -- the dead ENV page (S11, #53) --------------------------------------
    def honours_env(self) -> bool:
        """Does the loaded box apply the ENV page's ADSR at all?

        `arbutus/dbox` says no because its envelope is per pad (DDR-053) and
        `arbutus/live-input` says no because an input is not a note. The
        descriptor has carried the flag since ABI 2 and nothing drew it, so
        both boxes showed four live-looking knobs the engine throws away.
        Read every frame like SOURCE above: a box arrives from a patch, a
        project load or the browser without this page being asked.
        """
        return bool(getattr(self.instrument.engine, "honours_env", True))

    def lock_env_cells(self, cells: List[Param]):
        """Dim and freeze the ADSR when the box ignores it.

        Same pair TEMPO's BPM uses under MIDI clock and DRUM's SUS/REL use
        for an AD: `dimmed` draws it grey, `locked` makes `Param.turn` a
        no-op. The values are *not* blanked — a patch has to round-trip, and
        they come alive the moment a box that honours them is loaded.
        """
        dead = not self.honours_env()
        for p in cells:
            p.locked = p.dimmed = dead

    def _sync_live_input(self):
        """SOURCE is the host's, so this cell is read and not remembered.

        DDR-063: `input_source` can move from INPUT's SHIFT row or from a patch
        while this page is not looking, and the eight ENGINE cells are built
        once per engine load. Nothing else in a descriptor is host-owned, so
        this is the one cell that needs re-reading rather than a general
        rebuild that would drop the copy rings and ghost markers.
        """
        engine = self.instrument.engine
        if getattr(engine, "engine_id", "") != LIVE_INPUT or not self._engine_params:
            return
        cell = self._engine_params[0]
        if cell is None or cell.name != "SOURCE":
            return
        want = float(SOURCES.index(self.app.native.input_source())
                     if self.app.native.input_source() in SOURCES else 0)
        if cell.value != want:
            cell.value = want                  # not set_value: that would echo it back

    def _mod_target_addresses(self) -> List[Optional[str]]:
        """String ids under the eight visible cells (modulation-spec §5)."""
        sp = self.state.subpage
        if sp == ENGINE:
            desc = getattr(self.instrument.engine, "desc_params", []) or []
            return pad_slots([("engine." + d.get("id")) if d.get("id") else None
                              for d in desc[:8]])
        if sp == ENV:
            return ["env.a", "env.d", "env.s", "env.r", None,
                    "env.glide", "env.detune", None]
        if sp == FX:
            return [self.rack_view.target_address(i) for i in range(8)]
        return [None] * 8

    def _decorate_mod_targets(self, slots: List[Optional[Param]]):
        self.mod_rack_view.decorate_targets(slots, self._mod_target_addresses())
        return slots

    def tick(self):
        """Per frame, and not in `render`: an edited step pattern goes down
        the socket here (#60).

        `set_mod_tab_engine` rebuilds the tab, so this is not something to do
        per detent — but it is also not a drawing concern, and a page that is
        not the one on screen still has to finish what the player started on
        it. `App.tick` runs every frame whether anything is drawn or not.
        """
        self.mod_rack_view.flush_pattern()

    def tickers(self):
        out = []
        if self._bend_dir:
            # a momentary gesture, so it reads before the octave. What it shows
            # is the *range* it is bending to, which is the thing SHIFT+‹ ›
            # cannot otherwise tell you (`[TBD-Elfy]`).
            sign = "+" if self._bend_dir > 0 else "-"
            st = f"{sign}{int(self.p_bend.value)}ST"
            out.append((4, f"BEND {st}", f"BND{st}"))
        if self.state.octave:
            sign = "+" if self.state.octave > 0 else ""
            out.append((5, f"OCTAVE {sign}{self.state.octave}",
                        f"OCT{sign}{self.state.octave}"))
        if self.state.subpage == FX:
            out.extend(self.rack_view.tickers())
        if self.state.subpage == MOD or self.mod_rack_view.routing_active:
            out.extend(self.mod_rack_view.tickers())
        return out

    def help_for(self, target) -> str:
        if self.state.subpage == FX:
            return self.rack_view.help_for(target)
        if self.state.subpage == MOD:
            return self.mod_rack_view.help_for(target)
        if target in ("LEFT", "RIGHT"):
            return "‹ › octave · SHIFT+‹ › held = bend, snaps back on release"
        return ""

    # -- input ------------------------------------------------------------
    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        if not pressed:
            # the arrows are the only keys this page cares about letting go of:
            # a bend ends when the arrow does, not when SHIFT does. Holding
            # SHIFT through the whole gesture is nobody's idea of playing.
            if key in ("LEFT", "RIGHT") and self._bend_dir:
                self._end_bend()
                return True
            return False
        if key == "LIFT":
            # One physical lift buffer, even though FX bindings and MOD
            # routing keep their protocol-shaped payloads in separate views.
            if self.state.subpage == FX:
                self.mod_rack_view.clear_payload()
            elif self.state.subpage == MOD:
                self.rack_view.mode = None
                self.rack_view.lifted = None
        if (key == "DROP" and self.state.subpage == ENGINE
                and not self.mod_rack_view.lifted_source
                and self.app.tape.lift_buffer is not None):
            # DDR-030 / workflow §2.1: what TRACK lifted becomes this
            # instrument's sample. The MOD payload wins if there is one — that
            # is a route being pasted, not tape.
            self.drop_tape_buffer()
            return True
        if self.state.subpage != MOD and self.mod_rack_view.target_key(
                key, pressed, shift):
            return True
        if self.state.subpage == FX and self.rack_view.key(key, pressed, shift):
            return True
        if self.state.subpage == MOD and self.mod_rack_view.key(key, pressed, shift):
            return True
        if key in ("1", "2", "3", "4"):
            idx = int(key) - 1
            if shift:
                self._open_menu(idx)
            else:
                if idx == MOD and self.state.subpage == MOD:
                    # DDR-049: second press of 4 = bypass on the current tab
                    self.mod_rack_view.toggle_bypass()
                    return True
                if idx == FX and self.state.subpage == FX:
                    # DDR-049's twin gesture: master = whole rack, engine tab
                    # = that node. The host keeps the DSP state alive.
                    self.rack_view.toggle_bypass()
                    return True
                self.state.subpage = idx
                if idx == FX:
                    # arriving at the rack picks the tab DDR-038 means
                    self.rack_view.settle()
                elif idx == MOD:
                    self.mod_rack_view.settle()
            return True
        if key in ("LEFT", "RIGHT"):
            direction = -1 if key == "LEFT" else 1
            if shift:
                self._start_bend(direction)      # DDR-052
            else:
                # the octave is a performance gesture, like the bend, and not
                # an edit (#152, Elfy 2026-09-23): it records no step of its
                # own, so SHIFT+STOP after a move undoes whatever was edited
                # before it. It rides in the page's snapshot all the same, so
                # the entry already open is moved on with it — otherwise that
                # undo would snap the octave back with the sound.
                self._set_octave(self.state.octave + direction)
                self._undo.amend(octave=self.state.octave)
            return True
        return False

    # -- tape -> instrument (DDR-030, workflow §6) -------------------------
    def drop_tape_buffer(self):
        """DROP on ENGINE: the lifted tape becomes this instrument's sample.

        The render is the host's (`clip_to_asset` — E1), and what comes back is
        a path in the sampler's own library folder, so the browser lists it and
        a patch saved afterwards names a real file. The lift buffer is **not**
        consumed: lift-without-drop is the tape's delete (DDR-013), and taking
        a copy to an instrument must not be what deletes a take.
        """
        if self.app.library_refused("DROP"):     # #876: before the path
            return False
        token = self._undo.push()                # its own edit, once
        file = self.app.sampler_file()           # None: the card refused (#1123)
        path = file and self.app.tape.buffer_to_asset(file)
        if not path or not self.load_sample_file(path):
            self.app.flash("DROP REFUSED", "REFUSED")
            self._undo.rollback(token)           # nothing landed
            return False
        self.app.flash("TAPE → %s" % self.title, "→%s" % self.title[:4])
        return True

    def load_sample_file(self, path: str) -> bool:
        """Put a WAV on the sampler and leave the browser pointing at it."""
        import os

        self.app.library.rescan()
        folders = [f.folder for f in self.app.library.list(self.library_category)]
        if "sampler" not in folders:
            return False
        lf = self.app.library.find(self.library_category, "sampler")
        item = os.path.basename(path)
        if lf is None or item not in lf.items:
            return False
        self.load_engine(folders.index("sampler"), lf.items.index(item))
        self.app.notify_param_changed(self)
        return True

    def on_menu_number(self, key: str):
        """A number that closes a browser is navigation, not a re-press.

        Without this seam, closing the FX/MOD browser with 3/4 immediately
        toggles the node DDR-049 just loaded.
        """
        idx = max(0, min(3, int(key) - 1))
        # the FX browser's save row: its red key saves and stays on FX
        commit = getattr(self.app.closed_menu, "commit_by_key", None)
        if commit is not None and commit(idx):
            return
        names = self._names
        if names is not None and self.app.closed_menu is names:
            # the save's name menu: the red key commits, the other three
            # only cancel, as on FILE (D98). The identity check keeps a name
            # menu a page key dismissed from committing under a later browser.
            self._names = None
            if idx == names.red_key:
                names.commit()
            return
        self.state.subpage = idx
        if idx == FX:
            self.rack_view.settle()
        elif idx == MOD:
            self.mod_rack_view.settle()

    # -- bend (DDR-052) ----------------------------------------------------
    def _start_bend(self, direction: int):
        """Full bend in that direction. The host ramps there over GLIDE and
        multiplies by the BEND range, so the UI sends ±1 and nothing else."""
        if self._bend_dir == direction:
            return
        self._bend_dir = direction
        self.instrument.set_bend(float(direction))

    def _end_bend(self):
        self._bend_dir = 0
        self.instrument.set_bend(0.0)

    def on_panic(self):
        """`Instrument.all_notes_off` already released the bend on the host;
        this is the ticker catching up, so it cannot claim a bend that ended."""
        self._bend_dir = 0
        self.mod_rack_view.on_panic()

    def on_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        """The rack owns its own encoders: a turn may lift or paste (§6.3)."""
        if self.state.subpage != MOD and self.mod_rack_view.mode == "paste_target":
            addresses = self._mod_target_addresses()
            slot = idx + 4 if shift else idx
            if shift and (slot >= len(addresses) or addresses[slot] is None):
                slot = idx                 # the ordinary fine-row fallback
            target = addresses[slot] if 0 <= slot < len(addresses) else None
            visible = self.slots()[slot] if 0 <= slot < 8 else None
            if visible is not None and visible.locked:
                return True
            token = self._undo.push()            # a route is its own edit
            if self.mod_rack_view.route_to(target):
                return True
            self._undo.rollback(token)
        if (self.state.subpage == ENV and not shift and 0 <= idx < 4
                and not self.honours_env()):
            # the four cells are already `locked`, so the turn moves nothing
            # either way; this is the page saying *why* rather than a knob
            # that looks broken (S11, #53)
            if self.app is not None:
                self.app.flash(NO_ENV_TICKER % self.instrument.engine_name,
                               "NO ENV")
            return True
        # DDR-054: the first turn of a run on one cell opens an undo entry and
        # the rest of the run joins it. The group names the cell, so moving to
        # the next one starts a new entry (and drops the old — one level).
        if self.state.subpage == FX:
            token = self._undo.push(("fx", self.rack_view.tab, idx, shift))
            if self.rack_view.encoder(idx, delta, shift):
                return True
            self._undo.rollback(token)           # no cell under that encoder
            return False
        if self.state.subpage == MOD:
            token = self._undo.push(("mod", self.mod_rack_view.tab, idx, shift))
            if self.mod_rack_view.encoder(idx, delta, shift):
                return True
            self._undo.rollback(token)
            return False
        slots = self.slots()
        target = slots[idx + 4] if shift else slots[idx]
        if target is None and shift:
            target = slots[idx]
        if target is not None and not target.locked:
            self._undo.push(("param", self.state.subpage, idx, shift,
                             target.name))
        return super().on_encoder(idx, delta, shift)

    # -- patches -----------------------------------------------------------
    def on_shift_page_key(self) -> bool:
        """SHIFT + SYNTH / DRUM — promote this slot to a named patch.

        The slot itself autosaves and gets overwritten as you keep playing;
        this drops a permanent copy into the current engine's folder, where
        both browsers will find it. It opens the name menu (plant and
        landmark names, D98, #819) and saves on the commit: SHIFT+SYNTH,
        then a push, is the whole gesture.
        """
        lf = self.instrument.engine_folder
        if lf is None:
            return False
        # ENC1 is the opener (run's call, 2026-10-14): no turn opened this
        # menu, so the push that commits the first name is the first
        # encoder's, and the red key sits under the first cell
        self._names = NameMenu(
            self.app, self, "patch", P.existing_names(lf),
            lambda name: self.save_patch(lf, name), opener=0,
            crumb=[self.title, "SAVE PATCH"])
        self.app.open_menu(self._names)
        return True

    def save_rack(self, name: str):
        """SAVE THIS RACK's commit: the FX rack as it is now, into SAVED
        RACKS as `name` (D98, #820). The host's rack is already a §6.7
        document, the stock racks' format, so it is written as it stands."""
        self.app.library.write_rack(name, self.app.native.rack(
            self.instrument_id))
        self.app.flash(f"SAVED RACK {name} → SAVED RACKS", f"SAVED {name}")

    def menu_closed(self, menu):
        """A name menu closed itself (its push, or a key it cancels on)."""
        if menu is self._names:
            self._names = None

    def save_patch(self, lf, name: str):
        """The name menu's commit: write the slot as `name` in `lf`."""
        inst = self.instrument
        # A patch carries the sequencer with it (control-inventory § slot keys),
        # and since H2 a pattern is a **file** (DDR-037) — so the pattern has to
        # become a library item before the patch can name one. The scratch file
        # an entered pattern rides on would not survive the session.
        # **this** instrument's sequencer, not the active one (S13): a patch
        # carries the sound it was promoted from, and SHIFT+DRUM on the DRUM
        # page must never name SYNTH's riff. The two are the same object
        # whenever this page is the one you are standing on, which is every
        # real call — being explicit is what stops the next one being wrong.
        seq = self.app.sequencers.get(self.instrument_id)
        if (seq is not None and seq.active and seq.pattern
                and getattr(seq, "takes_pattern", False)
                and seq.library_folder is not None):
            # the pattern is named for what it is, an action noun (#819),
            # not after the patch it rides in
            seq.promote_pattern(seq.library_folder, first_free(
                "pattern", P.existing_names(seq.library_folder)) or name)
        # A stepmod tab's entered step values are content in exactly the same
        # sense (#60), and a patch carries the MOD rack by value — so they
        # have to become a library file before the patch names one, or the
        # patch would name a scratch file that dies with the session.
        from app.project_file import promote_step_patterns
        promote_step_patterns(self.app, name, only=self.instrument_id)
        # the file is a §7.2 document, its picked name and all
        from app.native_library import patch_from_state, write_patch
        fname = write_patch(lf, name, patch_from_state(
            self.app.native, self.instrument_id, name, self.state.octave))
        # what you are playing *is* that patch now: the header says so, and
        # the browser opens on it
        inst.item = fname
        self.app.flash(*P.saved_message(name, lf.name))
        return True

    def load_patch(self, folder_idx: int, item_idx: int):
        """Whole-sound recall: engine *and* everything hanging off it."""
        if self.load_engine(folder_idx, item_idx) is False:
            return
        folders = self.app.library.list(self.library_category)
        if not folders:
            return
        lf = folders[max(0, min(len(folders) - 1, folder_idx))]
        items = lf.items or []
        if not items:
            return
        item = items[max(0, min(len(items) - 1, item_idx))]
        self.app.library.load_full_patch(lf, item)
        self._engine_params = _adapt(self.instrument.engine)
        self.rack_view.tab = min(self.rack_view.tab, self.rack_view.n_tabs)
        # the patch carried an octave and an envelope; both are this side's to
        # show. The host already has them — it was handed the whole document —
        # so this reads them back rather than deciding anything, and without it
        # the ENV page keeps the *previous* slot's ADSR on screen and pushes it
        # back down on the next encoder turn.
        state = self.app.native.instrument(self.instrument_id)
        oct_ = state.get("octave")
        if oct_ is not None:
            self._set_octave(int(oct_))
        self._pull_env(state)
        self._sync_sequencer()

    def _sync_sequencer(self):
        """A whole-sound load carries the sequencer with it (control-inventory
        § Patches): the host's SEQ slot just changed, so the SEQ page's mirror
        has to follow it rather than keep the picture it had."""
        page = self.app.pages.get("SEQ")
        sync = getattr(page, "sync_from_host", None)
        if sync is not None:
            sync(self.instrument_id)

    def _pull_env(self, state: Optional[dict] = None):
        """Set the ENV slots from the host's `env` block (§7.2)."""
        if state is None:
            state = self.app.native.instrument(self.instrument_id)
        env = state.get("env") or {}
        for key, p in (("a", self.p_atk), ("d", self.p_dec),
                       ("s", self.p_sus), ("r", self.p_rel)):
            if key in env:
                p.set_value(float(env[key]))
        mode = env.get("mode")
        if mode in ENV_MODES:
            self.p_mode.set_value(ENV_MODES.index(mode))
        # the SHIFT row travels in the same block (DDR-052), so it is read back
        # the same way — a patch that predates it simply has no keys here and
        # the page keeps what it had
        for key, p in (("glide", self.p_glide), ("detune", self.p_detune),
                       ("bend_range", self.p_bend)):
            if key in env:
                p.set_value(float(env[key]))

    def open_patch_menu(self):
        """SHIFT + slot key — browse whole sounds, applied live."""
        crumb = [self.title, f"SLOT {self.state.slot_letter}"]
        src = LibrarySource(self.app.library, self.library_category)
        group = ("menu", "patch", object())       # this browse, no other
        menu = MenuPage(self.app, self, src,
                        lambda f, i: self._edit(group, self.load_patch, f, i),
                        crumb)
        self._point_menu_at_current(menu)
        self.app.open_menu(menu)

    def _point_menu_at_current(self, menu):
        """Open a browser on what is loaded, not at the top of the list."""
        inst = self.instrument
        lf = inst.engine_folder
        if lf is None:
            return
        names = [x.folder for x in self.app.library.list(self.library_category)]
        if lf.folder not in names:
            return
        menu.state.folder_idx = names.index(lf.folder)
        if inst.item in (lf.items or []):
            menu.state.item_idx = lf.items.index(inst.item)

    def _pick_fx(self, folder_idx: int, item_idx: int):
        """SHIFT+3 on the FX subpage.

        RACKS and SAVED RACKS load a whole §6.7 rack; any other folder is an
        effect box and goes onto the current tab (`set_tab_engine`), or onto a
        new one when the empty next tab is showing (DDR-038).
        """
        folders = self.app.library.list("fx")
        if not folders:
            return
        lf = folders[max(0, min(len(folders) - 1, folder_idx))]
        if is_rack_folder(lf):
            items = lf.items or []
            if items:
                item = items[max(0, min(len(items) - 1, item_idx))]
                self._ask_rack(lf, item, lambda: self._load_fx_rack(lf, item))
            return
        engine_id = lf.manifest.get("id")
        if engine_id:
            self.rack_view.set_tab_engine(engine_id)

    def _ask_rack(self, lf, item: str, load):
        """A whole rack names its boxes in its tabs, which the browser's own
        question never sees: ask for those, then `load` (#1161)."""
        from app.native_library import read_doc
        doc = read_doc(lf.item_path(item)) or {}
        if not box_trust.ask_ids(self.app, box_trust.doc_ids(doc.get("mod", doc)),
                                 load):
            load()

    def _load_fx_rack(self, lf, item: str):
        self.app.library.load_rack_item(self.instrument_id, lf, item)
        self.rack_view.tab = min(self.rack_view.tab, self.rack_view.n_tabs)
        self.app.notify_param_changed(self)

    def _load_mod_rack(self, lf, item: str):
        self.app.library.load_mod_rack_item(self.instrument_id, lf, item)
        self.mod_rack_view.tab = min(self.mod_rack_view.tab,
                                     self.mod_rack_view.n_tabs)
        self.app.notify_param_changed(self)

    def _pick_mod(self, folder_idx: int, item_idx: int):
        """SHIFT+4 on the MOD subpage.

        The `racks` folder loads a whole §11 MOD rack; any other folder is a
        modulator box and goes onto the current tab (`set_tab_engine`), or onto
        a new one when the empty next tab is showing (DDR-038).
        """
        folders = self.app.library.list("mod")
        if not folders:
            return
        lf = folders[max(0, min(len(folders) - 1, folder_idx))]
        if lf.folder == "racks":
            items = lf.items or []
            if items:
                item = items[max(0, min(len(items) - 1, item_idx))]
                self._ask_rack(lf, item, lambda: self._load_mod_rack(lf, item))
            return
        engine_id = lf.manifest.get("id")
        if engine_id and self.mod_rack_view.set_tab_engine(engine_id):
            items = lf.items or []
            if items:
                from app.native_library import read_doc, patch_from_state
                item = items[max(0, min(len(items) - 1, item_idx))]
                doc = read_doc(lf.item_path(item))
                if doc:
                    # A saved item changes only this tab, never the whole sound.
                    patch = patch_from_state(self.app.native, self.instrument_id)
                    tab = patch["mod"]["tabs"][self.mod_rack_view.tab - 1]
                    block = doc.get("engine", doc)
                    for field in ("params", "assets", "sidechain"):
                        if field in block:
                            tab[field] = block[field]
                    patch["dir"] = lf.path
                    self.app.native.load_patch(self.instrument_id, patch)

    def _set_octave(self, value: int):
        self.state.octave = max(-3, min(3, value))
        self.instrument.octave = self.state.octave
        self.app.notify_param_changed(self)

    def _open_menu(self, subpage_idx: int):
        crumb = [self.title, self.subpage_names[subpage_idx]]
        #: one undo entry per browser opened, however many picks it applies
        group = ("menu", subpage_idx, object())
        if subpage_idx == ENGINE:
            src = LibrarySource(self.app.library, self.library_category)
            menu = MenuPage(self.app, self, src,
                            lambda f, i: self._edit(group, self.load_engine,
                                                    f, i), crumb)
            self._point_menu_at_current(menu)
        elif subpage_idx == FX:
            # up from the racks saves the rack you hear (D98, #820)
            src = LibrarySource(self.app.library, "fx")
            menu = SaveRowMenu(self.app, self, src,
                               lambda f, i: self._edit(group, self._pick_fx,
                                                       f, i),
                               self.app.library.rack_names, self.save_rack,
                               crumb)
        elif subpage_idx == MOD:
            src = LibrarySource(self.app.library, "mod")
            menu = MenuPage(self.app, self, src,
                            lambda f, i: self._edit(group, self._pick_mod,
                                                    f, i), crumb)
        else:
            return          # SHIFT+2 unused (control-inventory)
        self.app.open_menu(menu)

    # -- render -----------------------------------------------------------
    def render(self, fb, rect, app):
        sp = self.state.subpage
        if sp == ENGINE:
            self._render_engine(fb, rect)
        elif sp == ENV:
            self._render_env(fb, rect)
        elif sp == FX:
            self._render_fx(fb, rect)
        elif sp == MOD:
            self._render_mod(fb, rect)
        else:
            self._render_lfo(fb, rect)    # kept for the old LFO params that ride in patches

    def _header_line(self, fb, rect, left: str, right: str):
        x, y, w, h = rect
        draw.text(fb, x + 4, y + 3, draw.clip_text(left, w // 2, SMALL),
                  palette.INK, SMALL)
        rw = draw.text_width(right, SMALL)
        draw.text(fb, x + w - 4 - rw, y + 3, right, palette.GREEN, SMALL)
        draw.hline(fb, x + 4, y + 13, w - 8, palette.RULE)

    def _render_engine(self, fb, rect):
        x, y, w, h = rect
        inst = self.instrument
        self._header_line(fb, rect, f"{inst.engine_name}  {inst.item_name}",
                          f"SLOT {self.state.slot_letter}")
        gx, gy, gw, gh = x + 6, y + 18, w - 12, h - 26
        draw.rect_outline(fb, gx, gy, gw, gh, palette.RULE)
        mid = gy + gh // 2
        engine = inst.engine
        # a sampler draws its sample, anything else draws what it is making.
        # The peaks come from the host (§15.6) — the samples are in its
        # address space now, and nothing here reads the file.
        peaks = engine.asset_waveform(gw - 2) \
            if getattr(engine, "has_asset", False) else None
        if peaks:
            self._draw_sample(fb, peaks, gx + 1, gy + 1, gw - 2, gh - 2, engine)
        else:
            self._draw_scope(fb, self.app.scope, gx + 1, mid, gw - 2, gh // 2 - 2)
        if getattr(engine, "engine_id", "") == LIVE_INPUT:
            self._draw_live_input(fb, gx + 1, gy + 1, gw - 2)

    def _draw_live_input(self, fb, x, y, w):
        """What the live waveform is *of*, and how hot it is (DDR-063).

        The scope above is the input; without this line it is an anonymous
        wiggle. SOURCE is the host's (`state.input.source`) and never the UI's,
        so it is read here rather than kept. There is no tuner readout: no
        message in §8.2 hands a pitch estimate up, and inventing one in the UI
        would be DSP in the wrong folder.
        """
        src = str(self.app.native.input_source()).upper()
        peak = float(self.app.scope[-1]) if len(self.app.scope) else 0.0
        db = "-INF" if peak <= 1e-5 else "%.0f" % (20.0 * np.log10(peak))
        draw.text(fb, x + 2, y + 2, src, palette.GREEN, SMALL)
        label = "%s dB" % db
        draw.text(fb, x + w - 2 - draw.text_width(label, SMALL), y + 2, label,
                  palette.SUBTLE, SMALL)

    @staticmethod
    def _draw_scope(fb, scope, x, mid, w, amp):
        n = len(scope)
        pts = []
        for i in range(w):
            v = float(scope[int(i * n / max(1, w))]) if n else 0.0
            pts.append((x + i, int(mid - max(-1.0, min(1.0, v)) * amp)))
        draw.polyline(fb, pts, palette.encoder_color(0))

    @staticmethod
    def _draw_sample(fb, peaks, x, y, w, h, engine):
        """The sampler's four markers over its waveform (§15.11).

        START and END are offwhite and always drawn; LOOP-IN and LOOP-OUT are
        green and drawn only when there *is* a loop — they default to 1/1,
        which means none, and two markers stacked on the right-hand edge of
        every sample would be noise.
        """
        draw.peaks(fb, peaks, x, y, w, h, palette.encoder_color(0))
        markers = engine.markers() if hasattr(engine, "markers") else \
            [(getattr(engine, "start", 0.0), False),
             (getattr(engine, "end", 1.0), False)]
        for pos, is_loop in markers:
            draw.vline(fb, x + int(pos * (w - 1)), y, h,
                       palette.GREEN if is_loop else palette.INK)

    def _render_env(self, fb, rect):
        x, y, w, h = rect
        self._header_line(fb, rect, "AMP ENVELOPE",
                          ENV_MODES[int(self.p_mode.value)])
        gx, gy, gw, gh = x + 8, y + 20, w - 16, h - 28
        draw.rect_outline(fb, gx, gy, gw, gh, palette.RULE)
        base = gy + gh - 2
        top = gy + 2
        total = (self.p_atk.value + self.p_dec.value + 0.5 + self.p_rel.value)
        span = gw - 4
        ax = int(span * self.p_atk.value / total)
        dx = int(span * self.p_dec.value / total)
        sx = int(span * 0.5 / total)
        rx = span - ax - dx - sx
        sus_y = int(base - (base - top) * self.p_sus.value)
        pts = [(gx + 2, base), (gx + 2 + ax, top), (gx + 2 + ax + dx, sus_y),
               (gx + 2 + ax + dx + sx, sus_y),
               (gx + 2 + ax + dx + sx + max(0, rx), base)]
        # a box that ignores the ADSR draws the shape it is ignoring in grey,
        # the same weight the four cells go (S11, #53)
        live = self.honours_env()
        draw.polyline(fb, pts, palette.encoder_color(0) if live else palette.DIM)
        if not live:
            draw.text(fb, gx + 4, gy + 4, "NO ENV", palette.DIM, SMALL)

    def _render_fx(self, fb, rect):
        self.rack_view.render(fb, rect, self._header_line)

    def _render_mod(self, fb, rect):
        self.mod_rack_view.render(fb, rect, self._header_line)

    def _render_lfo(self, fb, rect):
        x, y, w, h = rect
        shape = LFO_SHAPES[int(self.p_shape.value)]
        dest = LFO_DESTS[int(self.p_dest.value)]
        self._header_line(fb, rect, f"LFO  {shape}", f"→ {dest}")
        gx, gy, gw, gh = x + 8, y + 20, w - 16, h - 28
        draw.rect_outline(fb, gx, gy, gw, gh, palette.RULE)
        mid = gy + gh // 2
        amp = (gh // 2 - 3) * max(0.08, self.p_depth.value)
        cycles = max(0.5, self.p_rate.value / 4.0)
        pts = []
        for i in range(gw - 4):
            ph = (i / max(1, gw - 5)) * cycles
            ph -= int(ph)
            if shape == "SINE":
                v = np.sin(2 * np.pi * ph)
            elif shape == "TRI":
                v = 4 * abs(ph - 0.5) - 1
            elif shape == "SQR":
                v = 1.0 if ph < 0.5 else -1.0
            else:
                v = ((np.sin(int(ph * 8) * 12.9898) * 43758.5453) % 1.0) * 2 - 1
            pts.append((gx + 2 + i, int(mid - v * amp)))
        draw.polyline(fb, pts, palette.encoder_color(1))

    def reset(self):
        """FILE › NEW: this instrument as it arrives at boot (stage J1).

        The static params are rebuilt rather than set — a `Param` does not
        remember its own default — and the default box is loaded as a **whole
        sound**, not as an engine: `_load_default_engine` goes through
        `apply_item`, which since DDR-030 takes only the engine block and
        leaves the previous rack, MOD rack and envelope standing. That is the
        right answer for SHIFT+1 and the wrong one for NEW.
        """
        self.state.subpage = ENGINE
        self._undo.clear()             # the sound the entry names is gone
        self._set_octave(0)
        self.instrument.set_engine(None)
        self._build_static_params()
        self.rack_view.tab = 0
        self.mod_rack_view.tab = 0
        folders = self.app.library.list(self.library_category)
        if folders:
            self.load_patch(self._default_index(folders), 0)

    # -- one-step undo (DDR-054) -------------------------------------------
    def _edit(self, group, fn, *args):
        """Run a live-applied browser pick as one undoable edit.

        A browser applies as you scroll (`open_patch_menu`), so the whole
        browse is one entry: `group` is unique per menu, so every pick inside
        it coalesces and the snapshot is the sound from before the browser
        opened. Nothing pushes when no browser was opened.
        """
        self._undo.push(group)
        return fn(*args)

    def undo_edit(self) -> bool:
        """SHIFT+STOP on this page — put the last edit back (DDR-054).

        The page owns the gesture whether or not it has anything: on
        SYNTH/DRUM SHIFT+STOP is *never* the tape's undo
        (`tape-sampling-workflow.md` § Gestures).
        """
        if not self._undo.undo():
            # the tape's undo says nothing when it has nothing, but there the
            # screen is the clip list and you can see it; here the sound would
            # simply not change. `[TBD-Elfy]`
            self.app.flash("NOTHING TO UNDO", "NO UNDO")
            return False
        self.app.flash("UNDO · %s" % self.instrument.engine_name, "UNDO")
        return True

    def _restore_edit(self, data: dict):
        """Undo's restore: the *sound* goes back, the view stays put.

        `restore` is a slot recall and carries the subpage with it, which is
        right when a whole slot arrives and wrong here — you undo a turn while
        looking at the page you turned it on, and being thrown to ENGINE
        because that is where the edit began is nobody's idea of one step.
        """
        subpage = self.state.subpage
        fx_was = [str(t.get("instance", "")) for t in self.rack_view.engine_tabs]
        mod_was = [str(t.get("instance", ""))
                   for t in self.mod_rack_view.engine_tabs]
        self.restore(data)
        self.state.subpage = subpage
        # #1134: undoing a browse puts the old box back under a lift not yet
        # dropped; carry it back as the browse carried it away (or cancel it,
        # said on the strip, when the restored box lacks it)
        for view, was in ((self.rack_view, fx_was),
                          (self.mod_rack_view, mod_was)):
            for i, name in enumerate(was[:len(view.engine_tabs)]):
                view._renamed(i, name)
        # undoing a route's lift puts the route back: it is no longer lifted,
        # or a DROP would install it twice
        mod = self.mod_rack_view
        if mod.lifted_route is not None and mod.lifted_route in mod.routes:
            mod.lifted_route = None
            if mod.mode == "paste_route":
                mod.mode = None
        self.app.notify_param_changed(self)      # the slot record follows

    # -- persistence (DDR-029: full snapshot) ------------------------------
    def snapshot(self) -> dict:
        # DDR-029 unchanged: a slot is still the page's whole snapshot. What it
        # *carries* is a format-1 patch, so the autosaved slot and a promoted
        # patch are the same document (§7.2).
        from app.native_library import patch_from_state

        inst = self.instrument
        return {
            "subpage": self.state.subpage,
            "octave": self.state.octave,
            "engine_folder": inst.engine_folder.folder if inst.engine_folder else None,
            "item": inst.item,
            "env": [self.p_atk.value, self.p_dec.value, self.p_sus.value,
                    self.p_rel.value, self.p_mode.value, self.p_glide.value,
                    self.p_detune.value, self.p_bend.value],
            "lfo": [self.p_rate.value, self.p_depth.value,
                    self.p_dest.value, self.p_shape.value],
            "native_patch": patch_from_state(self.app.native,
                                             self.instrument_id),
        }

    def restore(self, data: dict):
        # a whole snapshot arriving (a slot recall, a project load) replaces
        # the sound the undo entry was taken from, so the entry goes with it.
        # Undo's own restore has already taken its entry by the time it lands
        # here, so this never eats the gesture that called it.
        self._undo.clear()
        self.state.subpage = int(data.get("subpage", self.state.subpage))
        self.state.octave = int(data.get("octave", 0))
        self.instrument.octave = self.state.octave
        folder = data.get("engine_folder")
        if folder:
            names = [x.folder for x in self.app.library.list(self.library_category)]
            if folder in names:
                lf = self.app.library.find(self.library_category, folder)
                items = lf.items or [""]
                item = data.get("item") or (items[0] if items else "")
                idx = items.index(item) if item in items else 0
                self.instrument.set_engine(None)
                self.load_engine(names.index(folder), idx)
        env = data.get("env") or []
        for p, v in zip([self.p_atk, self.p_dec, self.p_sus, self.p_rel,
                         self.p_mode, self.p_glide, self.p_detune,
                         self.p_bend], env):
            p.set_value(float(v))
        for p, v in zip([self.p_rate, self.p_depth, self.p_dest, self.p_shape],
                        data.get("lfo") or []):
            p.set_value(float(v))
        doc = data.get("native_patch")
        if doc:
            reply = self.app.native.load_patch(self.instrument_id, doc)
            if reply.get("type") == "error":
                # §7.4 refuses the whole document for one asset it cannot
                # open, and a slot record can name one that is gone: an
                # entered pattern rides on a `$TMPDIR` scratch file (H2) and
                # a lifted sample is in `.trash/` (DDR-055). Losing the
                # envelope, the rack and the MOD rack over it is the wrong
                # trade — load the sound without that content. The SEQ page
                # restores its own mirror right after this and pushes it
                # back down on a fresh scratch file (inspection 2026-09-18).
                trimmed = _without_missing_assets(doc)
                if trimmed is not None:
                    self.app.native.load_patch(self.instrument_id, trimmed)
            self._engine_params = _adapt(self.instrument.engine)
            self.rack_view.tab = min(self.rack_view.tab, self.rack_view.n_tabs)
            # the document is the authority on the envelope, both rows. This
            # also heals a slot written before the SHIFT row changed, whose
            # `env` list still holds LEGATO/CURVE in those two places.
            self._pull_env()
            self._sync_sequencer()
