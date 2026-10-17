"""DRUM page — SYNTH's structure, the kit's sound.

Subpages ENGINE / ENV / FX / LFO on 1-4, slots A B C X Y Z, SHIFT+1/3/4 menus.

Two of those subpages are **per pad**, which is the whole difference from
SYNTH (`engine-spec.md` §15.3, DDR-053):

* **ENGINE** edits the last-hit key. Its eight slots are the descriptor's
  `per_note` ones — `arbutus/drum` flags all eight — and they write with
  `set_note_param`, which lands in the kit document as well as in the engine.
  The main area names the pad and draws that pad's sample.
* **ENV** is an **AD**: ATK and DEC, with SUS and REL named but locked. The AD
  belongs to the last-hit pad the way the eight slots do, but it is *not* a
  ninth per-pad slot — §15.11's deviation from DDR-053 is that the host's
  `env_a`/`env_d` are global and latched per hit, so this page edits the same
  `env` block SYNTH's ENV edits, for whichever pad you hit last.

**LIFT then DROP copies a pad.** Hold LIFT and hit a pad to take it, hold DROP
and hit another to paste file *and* params (`copy_pad`, DDR-039's fourth
payload). Neither key sounds the pad it names — the gesture eats the note.
"""

from typing import List, Optional

from app.params import Param, StepType, pad_slots
from ui import draw, palette
from ui.font import SMALL

from .synth import ENGINE, ENV, SUBPAGES, SynthPage

#: pad 0 is F3, the lowest chromatic key — the note the engine receives
#: (`audio/host/PROTOCOL.md` § Per-pad values, `kKitBaseNote`).
PAD_BASE_NOTE = 53
NUM_PADS = 24


class DrumPage(SynthPage):
    key_id = "DRUM"
    title = "DRUM"
    category = "performance"
    subpage_names = SUBPAGES
    instrument_id = "DRUM"
    library_category = "drum"
    #: `arbutus/dbox` (stage I1) sorts before `arbutus/drum`, and folder 0 used
    #: to be the default — see SynthPage.DEFAULT_BOX.
    DEFAULT_BOX = "drum"

    def __init__(self, app):
        super().__init__(app)
        #: which pad the per-pad pages are showing, and the slots built for it
        self._pad = 0
        self._pad_slots: List[Param] = []
        self._pad_built_for: Optional[tuple] = None
        #: LIFT/DROP held, and what LIFT is holding (a pad index)
        self._lift_held = False
        self._drop_held = False
        self._lifted_pad: Optional[int] = None

    # -- the pad the pages are about ---------------------------------------
    @property
    def pad(self) -> int:
        """The last-hit key as a pad index, clamped into the kit's range.

        The engine hears the transposed note, so that is the one that names
        the pad — `‹ ›` moves which pads the keys reach, like every other
        engine.
        """
        note = self.instrument.last_note
        if note is None:
            return self._pad
        self._pad = max(0, min(NUM_PADS - 1, int(note) - PAD_BASE_NOTE))
        return self._pad

    def pad_name(self) -> str:
        """`KEY 7 · snare.wav` — the pad, and what is on it."""
        pad = self.pad
        engine = self.instrument.engine
        file = engine.pad_file(pad) if hasattr(engine, "pad_file") else ""
        return "KEY %d · %s" % (pad + 1, file or "—")

    # -- ENV as an AD (DDR-053) -------------------------------------------
    def _build_static_params(self):
        """The AD's own range, and the two slots that do not turn.

        **ATK and DEC are re-ranged here**, not reused from SYNTH. The host
        starts DRUM at **a 0 s / d 4 s** — v1's instant attack and long decay,
        the sound the v1 golden holds (`PROTOCOL.md` § env) — and SYNTH's
        slots stop at 2 s, so the page could not have shown the default it was
        given, let alone set it. The host clamps neither, so the ceiling is a
        UI choice: 8 s of decay puts the default at mid-bar. `[TBD-Elfy]`

        SUS and REL exist and say so; they just do not turn. A blank cell
        would read as "not implemented yet"; named and locked, it reads as
        "a drum voice has no sustain", which is the fact DDR-053 is about.
        `[TBD-Elfy]`: the greyed pair may want to be `—` instead.
        """
        super()._build_static_params()
        self.p_atk = Param("ATK", 0.0, 2.0, 0.0, StepType.LINEAR, 0.005,
                           unit="s", display_format="{:.3f}")
        self.p_dec = Param("DEC", 0.0, 8.0, 4.0, StepType.LINEAR, 0.02,
                           unit="s", display_format="{:.3f}")

        def env_cb(attr):
            def cb(v):
                setattr(self.instrument.env, attr, float(v))
            return cb

        self.p_atk.on_change = env_cb("attack")
        self.p_dec.on_change = env_cb("decay")
        self.p_sus_locked = Param("SUS", 0.0, 1.0, 0.0, StepType.LINEAR, 0.01)
        self.p_rel_locked = Param("REL", 0.0, 1.0, 0.0, StepType.LINEAR, 0.01)
        for p in (self.p_sus_locked, self.p_rel_locked):
            p.locked = True
            p.dimmed = True
            p.formatter = lambda _v: "—"

    # -- Page contract -----------------------------------------------------
    def slots(self):
        sp = self.state.subpage
        if sp == ENGINE:
            return self._decorate_mod_targets(pad_slots(self._pad_params()))
        if sp == ENV:
            # the AD, then SYNTH's SHIFT row — MODE / GLIDE / DETUNE / BEND
            # are voice policy and mean the same thing on a kit.
            # SUS and REL are locked for every kit (DDR-053); ATK and DEC go
            # with them only on a box that ignores the block (S11, #53) —
            # `arbutus/dbox`, whose envelope is per pad
            self.lock_env_cells([self.p_atk, self.p_dec])
            return self._decorate_mod_targets(pad_slots([
                self.p_atk, self.p_dec, self.p_sus_locked,
                self.p_rel_locked, self.p_mode, self.p_glide,
                self.p_detune, self.p_bend]))
        return super().slots()

    def _pad_params(self) -> List[Param]:
        """Rebuilt when the pad changes, not every frame.

        The slots carry their own value once built (an encoder turn writes to
        the Param and the Param writes down the socket), so they are only
        stale when the pad or the engine underneath them changed.
        """
        engine = self.instrument.engine
        if engine is None or not hasattr(engine, "pad_slots"):
            return []
        key = (self.pad, id(engine), engine.pad_file(self.pad))
        if key != self._pad_built_for:
            self._pad_slots = engine.pad_slots(self.pad)
            self._pad_built_for = key
        return self._pad_slots

    def tickers(self):
        out = list(super().tickers())
        if self._lifted_pad is not None:
            # the app's own LIFTED ticker is the tape's; this one names a pad
            out.append((1, "LIFTED: PAD %d" % (self._lifted_pad + 1),
                        "LIFT P%d" % (self._lifted_pad + 1)))
        return out

    def help_for(self, target) -> str:
        if self.state.subpage == ENGINE:
            if target in ("LIFT", "DROP"):
                return "LIFT a pad, DROP on another — copies sample and params"
            return "DRUM ENGINE: the last-hit key's own eight (§15.3)"
        if self.state.subpage == ENV:
            return "DRUM ENV: attack and decay per hit — a drum has no sustain"
        return super().help_for(target)

    # -- the pad copy gesture (DDR-039) ------------------------------------
    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        if (key == "DROP" and pressed and self.state.subpage == ENGINE
                and not self.mod_rack_view.lifted_source
                and not self._lift_held and self._lifted_pad is None
                and self.app.tape.lift_buffer is not None):
            # a pad copy in progress owns this key (LIFT+pad came first);
            # otherwise a held tape buffer makes DROP the tape → pad gesture
            self.drop_tape_buffer()
            return True
        if (self.state.subpage == ENGINE and key in ("LIFT", "DROP")
                and not self.mod_rack_view.lifted_source):
            # held, not latched: the pad that completes the gesture is the
            # next key you hit, and letting go cancels nothing you already did
            if key == "LIFT":
                self._lift_held = pressed
            else:
                self._drop_held = pressed
            return True
        return super().on_key(key, pressed, shift)

    def claims_note_keys(self) -> bool:
        """The first line of `on_note_key`, as a question (Page, #125)."""
        return bool(self._lift_held or self._drop_held)

    def on_note_key(self, note: int, pressed: bool) -> bool:
        """LIFT/DROP + a chromatic key. True = the gesture ate the note."""
        if not self.claims_note_keys():
            return False
        if not pressed:
            return True                      # its press was eaten; so is this
        pad = max(0, min(NUM_PADS - 1, int(note) - PAD_BASE_NOTE))
        if self._lift_held:
            engine = self.instrument.engine
            if engine is not None and not engine.pad_file(pad):
                self.app.flash("PAD %d IS EMPTY" % (pad + 1), "EMPTY")
                return True
            self._lifted_pad = pad
            return True
        if self._lifted_pad is None:
            return True                      # DROP with nothing lifted
        self._paste_pad(pad)
        return True

    # -- tape -> pad (DDR-030) ---------------------------------------------
    def drop_tape_buffer(self):
        """DROP on DRUM › ENGINE: the lifted tape lands on the last-hit pad.

        Which pad is not a choice made here — it is the key you last played,
        the same pad every other per-pad page is about (§15.3).
        """
        if self.app.library_refused("DROP"):    # #876: before the path
            return False
        engine = self.instrument.engine
        pad = self.pad
        token = self._undo.push()               # DDR-054: its own edit
        file = self.app.sampler_file()          # None: the card refused (#1123)
        path = file and self.app.tape.buffer_to_asset(file)
        if not path or engine is None or not hasattr(engine, "set_pad_file") \
                or not engine.set_pad_file(pad, path):
            self.app.flash("DROP REFUSED", "REFUSED")
            self._undo.rollback(token)          # the kit did not change
            return False
        self.app.flash("TAPE → KEY %d" % (pad + 1), "→KEY%d" % (pad + 1))
        self._pad_built_for = None          # the pad's params are new
        self.app.notify_param_changed(self)
        return True

    def _paste_pad(self, dst: int):
        src = self._lifted_pad
        engine = self.instrument.engine
        if engine is None or src is None or src == dst:
            self._lifted_pad = None
            return
        token = self._undo.push()               # DDR-054: its own edit
        reply = engine.copy_pad(src, dst)
        if (reply or {}).get("type") == "error":
            # §7.4: a refusal is for the player, and the kit did not change
            self.app.flash("COPY REFUSED", "REFUSED")
            self._undo.rollback(token)
        else:
            self.app.flash("PAD %d → PAD %d" % (src + 1, dst + 1),
                           "P%d→P%d" % (src + 1, dst + 1))
            self._pad_built_for = None       # the target's params are new
            self.app.notify_param_changed(self)
        self._lifted_pad = None

    def on_panic(self):
        super().on_panic()
        self._lift_held = self._drop_held = False
        self._lifted_pad = None

    # -- render ------------------------------------------------------------
    def render(self, fb, rect, app):
        if self.state.subpage == ENV:
            self._render_ad(fb, rect)
            return
        super().render(fb, rect, app)

    def _render_engine(self, fb, rect):
        """The pad, its sample, and where START/END sit in it."""
        x, y, w, h = rect
        inst = self.instrument
        self._header_line(fb, rect, f"{inst.engine_name}  {inst.item_name}",
                          f"SLOT {self.state.slot_letter}")
        draw.text(fb, x + 6, y + 17,
                  draw.clip_text(self.pad_name(), w - 12, SMALL),
                  palette.INK, SMALL)
        gx, gy, gw, gh = x + 6, y + 29, w - 12, h - 37
        draw.rect_outline(fb, gx, gy, gw, gh, palette.RULE)
        engine = inst.engine
        peaks = engine.pad_waveform(self.pad, gw - 2) \
            if hasattr(engine, "pad_waveform") else None
        if not peaks:
            draw.text_centered(fb, gx + gw // 2, gy + gh // 2 - 3,
                               "NO SAMPLE", palette.DIM, SMALL)
            return
        draw.peaks(fb, peaks, gx + 1, gy + 1, gw - 2, gh - 2,
                   palette.encoder_color(0))
        for pid, col in (("start", palette.INK), ("end", palette.INK)):
            pos = self._pad_value(pid)
            if pos is not None:
                draw.vline(fb, gx + 1 + int(pos * (gw - 3)), gy + 1, gh - 2, col)

    def _pad_value(self, pid: str) -> Optional[float]:
        engine = self.instrument.engine
        if engine is None or not hasattr(engine, "pad_record"):
            return None
        params = engine.pad_record(self.pad).get("params") or {}
        v = params.get(pid)
        return None if v is None else float(v)

    def _render_ad(self, fb, rect):
        """An AD, drawn unlike the synth's ADSR: it falls to zero and stops.

        The synth envelope is a four-segment line across the whole box; this
        is a filled decay that ends where DEC ends, so the two are not the
        same picture with different numbers. `[TBD-Elfy]`
        """
        x, y, w, h = rect
        self._header_line(fb, rect, "PAD ENVELOPE  A·D", self.pad_name())
        gx, gy, gw, gh = x + 8, y + 22, w - 16, h - 30
        draw.rect_outline(fb, gx, gy, gw, gh, palette.RULE)
        base, top = gy + gh - 2, gy + 2
        span = gw - 4
        total = max(0.05, self.p_atk.value + self.p_dec.value)
        # the envelope keeps its shape rather than filling the box: a short
        # hit stays visibly short next to a long one, so the box is the AD's
        # whole range and not whatever this hit happens to be
        full = self.p_atk.max_val + self.p_dec.max_val
        used = min(1.0, total / full)
        ax = int(span * used * (self.p_atk.value / total))
        dx = int(span * used) - ax
        peak = gx + 2 + ax
        live = self.honours_env()
        col = palette.encoder_color(0) if live else palette.DIM
        draw.polyline(fb, [(gx + 2, base), (peak, top),
                           (peak + max(1, dx), base)], col)
        draw.hline(fb, gx + 2, base, span, palette.RULE)
        if not live:
            # the shape is still drawn — it is the block the page would send —
            # but nothing on a `dbox` hears it (S11, #53)
            draw.text(fb, gx + 4, gy + 4, "NO ENV", palette.DIM, SMALL)

    def reset(self):
        """FILE › NEW, plus the per-pad view (stage J1)."""
        super().reset()
        self._pad = 0
        self._pad_built_for = None
        self._lift_held = self._drop_held = False
        self._lifted_pad = None

    # -- persistence -------------------------------------------------------
    def snapshot(self) -> dict:
        data = super().snapshot()
        data["pad"] = self._pad
        return data

    def restore(self, data: dict):
        super().restore(data)
        self._pad = int(data.get("pad", 0))
        self._pad_built_for = None
