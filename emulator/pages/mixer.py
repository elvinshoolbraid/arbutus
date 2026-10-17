"""MIXER page — MIX / EQ / FX / MASTER (screen-sketches § MIXER, stage F2).

Every number on this page is the **host's**. MIX moves `mixer {track, gain|pan}`
and reads the meters back; mute and solo are `mixer {track, mute|solo}` and are
*drawn from* `state.tape.tracks[i]`, never computed here (solo wins over mute in
the host — DDR-061 — and a second gate up here could only disagree); EQ is
`eq {track, ...}` in real dB and Hz with the host's clamped block coming back
(DDR-062); FX is the §6 rack on `instrument: "master"`, so `pages/rack.py` draws
it unchanged; MASTER is the six-knob strip of §15.5.

Gestures (DDR-061, DDR-001):

| Gesture | What happens |
|---|---|
| `1`-`4` tapped | the subpage — MIX / EQ / FX / MASTER |
| `1`-`4` **held** | solo that column of the shown bank, while held |
| SHIFT + `1`-`4` | mute it (latching) |
| SHIFT + `‹ ›` | the bank, on MIX |
| SHIFT + MIXER, held | the signal-path screen |

`[TBD, Elvin's call]` choices are listed in `emulator/notes/as-built.md` § F2.
"""

import time
from typing import Dict, List, Optional

from app import mixer as MX
from app.menu import LibrarySource, MenuPage
from app.mixer import db_text
from app.native_library import is_rack_folder
from app.page import Page
from app.params import Param, StepType, pad_slots
from app.tape import BANK_SIZE, NUM_TRACKS
from ui import draw, palette
from ui.font import LARGE, SMALL

MIX, EQ, FX, MASTER = 0, 1, 2, 3
SUBPAGES = ("MIX", "EQ", "FX", "MASTER")

#: DDR-001: tap commits (the subpage), hold performs (solo). A key held past
#: this is a solo and never a navigation, so the two can share 1-4 without a
#: subpage change ever flicking the other seven tracks silent.
SOLO_HOLD_S = 0.28

#: DDR-063: the box that puts the live input in the ENGINE slot. The signal
#: path draws its row bright only when it is actually loaded.
LIVE_INPUT = "arbutus/live-input"

#: how many points the EQ curve is drawn from — enough for a 12 dB peak at
#: Q 8 to keep its shape at 308 px wide.
CURVE_POINTS = 76


class MixerPage(Page):
    key_id = "MIXER"
    title = "MIXER"
    category = "recording"
    subpage_names = SUBPAGES

    #: MIXER › FX is the §6 rack on the master bus (F1). `pages/rack.py` asks
    #: its page for this and nothing else, so the same view serves both.
    instrument_id = "master"

    HOLD_S = SOLO_HOLD_S

    def __init__(self, app):
        super().__init__(app)
        self.state.subpage = MIX
        self.state.eq_track = 0
        #: key index -> monotonic time it went down (a candidate solo)
        self._down: Dict[int, float] = {}
        #: key index -> the track it is currently soloing
        self._solo: Dict[int, int] = {}
        #: SHIFT+MIXER held
        self.signal_path = False
        from pages.rack import RackView
        self.rack_view = RackView(self)
        self._build_params()

    @property
    def mixer(self):
        return self.app.mixer

    @property
    def native(self):
        return self.app.native

    # -- params ------------------------------------------------------------
    def _build_params(self):
        mx = self.mixer

        def level(i):
            p = Param(f"TRK{i + 1}", 0.0, 1.0, mx.levels[i], StepType.LINEAR, 0.02)
            p.on_change = lambda v, i=i: mx.levels.__setitem__(i, float(v))
            return p

        def pan(i):
            p = Param(f"PAN{i + 1}", -1.0, 1.0, mx.pans[i], StepType.LINEAR, 0.05,
                      display_format="{:+.2f}")
            p.on_change = lambda v, i=i: mx.pans.__setitem__(i, float(v))
            return p

        self.p_levels = [level(i) for i in range(NUM_TRACKS)]
        self.p_pans = [pan(i) for i in range(NUM_TRACKS)]

        self.p_master = Param("LEVEL", 0.0, 1.0, mx.master_level,
                              StepType.LINEAR, 0.02)
        self.p_master.on_change = lambda v: setattr(mx, "master_level", float(v))
        self.p_comp = Param("COMP", 0.0, 1.0, mx.comp.amount, StepType.LINEAR, 0.02)
        self.p_comp.on_change = lambda v: setattr(mx.comp, "amount", float(v))
        self.p_drive = Param("DRIVE", 0.0, 1.0, mx.drive, StepType.LINEAR, 0.02)
        self.p_drive.on_change = lambda v: setattr(mx, "drive", float(v))
        self.p_bal = Param("BAL", -1.0, 1.0, mx.bal, StepType.LINEAR, 0.05,
                           display_format="{:+.2f}")
        self.p_bal.on_change = lambda v: setattr(mx, "bal", float(v))
        # COMP REL prints the knob, not a promise: the host's release
        # coefficient runs once per block, so the labelled seconds are
        # approximate (audio/CLAUDE.md, stage F1).
        self.p_comp_rel = Param("COMP REL", 0.02, 1.0, mx.comp_rel,
                                StepType.LINEAR, 0.02, unit="s",
                                display_format="{:.2f}")
        self.p_comp_rel.on_change = lambda v: setattr(mx, "comp_rel", float(v))
        self.p_drive_tone = Param("DRIVE TONE", 0.0, 1.0, mx.drive_tone,
                                  StepType.LINEAR, 0.02)
        self.p_drive_tone.on_change = lambda v: setattr(mx, "drive_tone",
                                                        float(v))

    # -- the host's tracks -------------------------------------------------
    def bank_tracks(self) -> List[int]:
        """The four columns `1`-`4` address — TRACK's bank (DDR-060).

        The bank is derived from `tape.selected`, so MIXER and TRACK cannot
        disagree and the sketch's "bank follows TRACK's on arrival" needs no
        code at all.
        """
        return self.app.tape.bank_tracks()

    def track_state(self, i: int) -> dict:
        return self.native.track(i)

    def is_muted(self, i: int) -> bool:
        return bool(self.track_state(i).get("mute", False))

    def is_soloed(self, i: int) -> bool:
        return bool(self.track_state(i).get("solo", False))

    def any_solo(self) -> bool:
        return any(self.is_soloed(t) for t in range(NUM_TRACKS))

    def is_silent(self, i: int) -> bool:
        """What the host is doing to this track's contribution to the mix."""
        return (not self.is_soloed(i)) if self.any_solo() else self.is_muted(i)

    def eq_of(self, i: int) -> dict:
        eq = dict(MX.EQ_FLAT)
        eq.update(self.native.track_eq(i))
        return eq

    # -- slots -------------------------------------------------------------
    def _sync_params(self):
        """Re-read every cell from `app.mixer` (#632, review m-emu-pages row 3).

        The model has other doors (TRACK's ENC4 sets the master, NEW resets
        it), so a `Param` that kept its own copy went stale and the next
        detent jumped the value back. Reading it here each frame, as TRACK's
        `_sync_params` does, leaves the model the one home.
        """
        mx = self.mixer
        for i in range(NUM_TRACKS):
            self.p_levels[i].value = mx.levels[i]
            self.p_pans[i].value = mx.pans[i]
        self.p_master.value = mx.master_level
        self.p_comp.value = mx.comp.amount
        self.p_drive.value = mx.drive
        self.p_bal.value = mx.bal
        self.p_comp_rel.value = mx.comp_rel
        self.p_drive_tone.value = mx.drive_tone

    def slots(self) -> List[Optional[Param]]:
        self._sync_params()
        sp = self.state.subpage
        if sp == MIX:
            shown = self.bank_tracks()
            return pad_slots([self.p_levels[t] for t in shown]
                             + [self.p_pans[t] for t in shown])
        if sp == EQ:
            return pad_slots(self._eq_slots())
        if sp == FX:
            return self.rack_view.slots()
        return pad_slots([self.p_master, self.p_comp, self.p_drive, self.p_bal,
                          self.p_comp_rel, self.p_drive_tone])

    def _eq_slots(self) -> List[Optional[Param]]:
        """Built from the host's block every frame (DDR-062).

        The reply carries the whole clamped block, so there is nothing to
        remember up here: one encoder is one field of one `eq` message and
        what comes back is what the cell reads.
        """
        t = self.eq_track
        eq = self.eq_of(t)

        sel = Param("TRACK", 0, NUM_TRACKS - 1, t, StepType.DISCRETE,
                    choices=[f"TRK{n + 1}" for n in range(NUM_TRACKS)])
        sel.on_change = lambda v: setattr(self.state, "eq_track",
                                          max(0, min(NUM_TRACKS - 1, int(v))))

        def band(name, field):
            p = Param(name, -MX.EQ_MAX_DB, MX.EQ_MAX_DB, float(eq[field]),
                      StepType.LINEAR, 0.5, unit="dB", display_format="{:+.1f}")
            p.on_change = lambda v, f=field: self.native.eq(t, **{f: float(v)})
            return p

        def spell(v: float) -> str:
            return f"{v:.0f}Hz" if v < 1000 else f"{v / 1000:.2f}k"

        def hz(name, field, lo, hi, off=None):
            """HP at 20 and LP at 20 kHz are *off*, not a filter at the band
            edge (DDR-062), so the cell says so rather than printing a number
            that would read as a filter doing nothing."""
            p = Param(name, lo, hi, float(eq[field]), StepType.LOGARITHMIC, 0.06)
            if off is not None:
                p.formatter = (lambda v, o=off:
                               "OFF" if (v <= o if o <= lo else v >= o)
                               else spell(v))
            else:
                p.formatter = spell
            p.on_change = lambda v, f=field: self.native.eq(t, **{f: float(v)})
            return p

        q = Param("MID-Q", MX.EQ_MID_Q[0], MX.EQ_MID_Q[1], float(eq["mid_q"]),
                  StepType.LINEAR, 0.1, display_format="{:.2f}")
        q.on_change = lambda v: self.native.eq(t, mid_q=float(v))

        return [sel, band("LOW", "low"), band("MID", "mid"), band("HIGH", "high"),
                hz("HP", "hp", MX.EQ_HP[0], MX.EQ_HP[1], MX.EQ_HP[0]),
                hz("LP", "lp", MX.EQ_LP[0], MX.EQ_LP[1], MX.EQ_LP[1]),
                hz("MID-F", "mid_f", MX.EQ_MID_F[0], MX.EQ_MID_F[1]),
                q]

    @property
    def eq_track(self) -> int:
        return max(0, min(NUM_TRACKS - 1, int(getattr(self.state, "eq_track", 0))))

    # -- input -------------------------------------------------------------
    def tick(self):
        """A `1`-`4` held past HOLD_S becomes a solo (DDR-001, DDR-061)."""
        if not self._down or self.app.page_id != "MIXER":
            return              # App ticks every page: a solo starts on MIXER only (#623)
        now = time.monotonic()
        for idx, at in list(self._down.items()):
            if idx not in self._solo and now - at >= self.HOLD_S:
                self._begin_solo(idx)

    def _begin_solo(self, idx: int):
        shown = self.bank_tracks()
        if idx >= len(shown):
            return
        track = shown[idx]
        self._solo[idx] = track
        self.native.mixer(track=track, solo=True)

    def _end_solo(self, idx: int):
        track = self._solo.pop(idx, None)
        if track is not None:
            self.native.mixer(track=int(track), solo=False)

    def _toggle_mute(self, idx: int):
        shown = self.bank_tracks()
        if idx >= len(shown):
            return
        track = shown[idx]
        self.native.mixer(track=track, mute=not self.is_muted(track))

    def _step_bank(self, delta: int):
        """SHIFT+‹ › on MIX. The bank is `selected // 4`, so moving the bank
        moves the selection with it — one fact, and TRACK stays in step."""
        tape = self.app.tape
        bank = max(0, min(NUM_TRACKS // BANK_SIZE - 1, tape.bank + delta))
        tape.selected = bank * BANK_SIZE + (tape.selected % BANK_SIZE)

    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        if key == "MIXER":
            # SHIFT+MIXER is held (screen-sketches § MIXER): the release of the
            # page key is what puts the signal path away.
            if not pressed:
                self.signal_path = False
                return True
            return False

        if key in ("1", "2", "3", "4"):
            idx = int(key) - 1
            if pressed:
                if shift and self.state.subpage == FX and idx == FX:
                    # the master rack's only door (see emulator/notes/as-built.md § F2)
                    self._open_fx_menu()
                    return True
                if shift:
                    self._toggle_mute(idx)
                    return True
                self._down[idx] = time.monotonic()
                return True
            held = idx in self._solo
            pressed_here = self._down.pop(idx, None) is not None
            if held:
                self._end_solo(idx)
                return True
            if not pressed_here:
                # the press was a mute or a browser (SHIFT was down); the
                # release of that same key is not a navigation
                return True
            self.state.subpage = idx
            if idx == FX:
                self.rack_view.settle()
            return True

        if self.state.subpage == FX and self.rack_view.key(key, pressed, shift):
            return True

        if (pressed and shift and key in ("LEFT", "RIGHT")
                and self.state.subpage in (MIX, EQ)):
            self._step_bank(1 if key == "RIGHT" else -1)
            return True
        return False

    def on_shift_page_key(self):
        """SHIFT + MIXER: the signal path, while it is held."""
        self.signal_path = True

    def on_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        if self.state.subpage == FX:
            return self.rack_view.encoder(idx, delta, shift)
        return super().on_encoder(idx, delta, shift)

    def on_menu_number(self, key: str):
        idx = max(0, min(3, int(key) - 1))
        self.state.subpage = idx
        if idx == FX:
            self.rack_view.settle()

    def on_panic(self):
        for idx in list(self._solo):
            self._end_solo(idx)
        self._down.clear()
        self.signal_path = False

    def _open_fx_menu(self):
        src = LibrarySource(self.app.library, "fx")
        menu = MenuPage(self.app, self, src, self._pick_fx,
                        [self.title, SUBPAGES[FX]])
        self.app.open_menu(menu)

    def _pick_fx(self, folder_idx: int, item_idx: int):
        """SHIFT+3 on MIXER › FX — the same picker SYNTH › FX has."""
        folders = self.app.library.list("fx")
        if not folders:
            return
        lf = folders[max(0, min(len(folders) - 1, folder_idx))]
        if is_rack_folder(lf):
            items = lf.items or []
            if items:
                self.app.library.load_rack_item(
                    self.instrument_id, lf,
                    items[max(0, min(len(items) - 1, item_idx))])
                self.rack_view.tab = min(self.rack_view.tab,
                                         self.rack_view.n_tabs)
            return
        engine_id = lf.manifest.get("id")
        if engine_id:
            self.rack_view.set_tab_engine(engine_id)

    # -- strip / help ------------------------------------------------------
    def tickers(self):
        out = []
        soloed = [t for t in range(NUM_TRACKS) if self.is_soloed(t)]
        if soloed:
            names = " ".join("%d" % (t + 1) for t in soloed)
            out.append((2, "SOLO " + names, "SOLO " + names))
        elif any(self.is_muted(t) for t in range(NUM_TRACKS)):
            muted = " ".join("%d" % (t + 1) for t in range(NUM_TRACKS)
                             if self.is_muted(t))
            out.append((3, "MUTE " + muted, "MUTE " + muted))
        if self.state.subpage == FX:
            out.extend(self.rack_view.tickers())
        return out

    def help_for(self, target) -> str:
        if self.state.subpage == FX and self.rack_view.help_for(target):
            return self.rack_view.help_for(target)
        table = {
            "1": "tap: MIX · hold: solo track 1 of the bank · SHIFT: mute it",
            "2": "tap: EQ · hold: solo track 2 of the bank · SHIFT: mute it",
            "3": "tap: FX (the master rack) · hold: solo · SHIFT here: effects",
            "4": "tap: MASTER · hold: solo track 4 of the bank · SHIFT: mute it",
            "LEFT": "SHIFT+‹ : the other bank of four",
            "RIGHT": "SHIFT+› : the other bank of four",
            "MIXER": "SHIFT+MIXER held: the signal path, end to end",
        }
        return table.get(str(target), "")

    # -- render ------------------------------------------------------------
    def render(self, fb, rect, app):
        if self.signal_path:
            self._render_signal_path(fb, rect)
            return
        sp = self.state.subpage
        if sp == MIX:
            self._render_mix(fb, rect)
        elif sp == EQ:
            self._render_eq(fb, rect)
        elif sp == FX:
            self.rack_view.render(fb, rect, self._header_line)
        else:
            self._render_master(fb, rect)

    def _header_line(self, fb, rect, left: str, right: str):
        x, y, w, h = rect
        draw.text(fb, x + 4, y + 3, draw.clip_text(left, w // 2, SMALL),
                  palette.INK, SMALL)
        rw = draw.text_width(right, SMALL)
        draw.text(fb, x + w - 4 - rw, y + 3, right, palette.GREEN, SMALL)
        draw.hline(fb, x + 4, y + 13, w - 8, palette.RULE)

    def _col_color(self, i: int, shown: List[int]):
        """The bank's four in their encoder colours; the other four dim, and a
        track the host is silencing dim whichever bank it is in (DDR-061)."""
        if i not in shown or self.is_silent(i):
            return palette.DIM
        return palette.encoder_color(shown.index(i))

    def _render_mix(self, fb, rect):
        x, y, w, h = rect
        mx = self.mixer
        shown = self.bank_tracks()
        col_w = w // NUM_TRACKS                      # 8 columns of 40 (DDR-060)
        top, bot = y + 14, y + h - 22
        span = bot - top
        for i in range(NUM_TRACKS):
            cx = x + i * col_w
            col = self._col_color(i, shown)
            bx = cx + col_w // 2 - 7
            # the number, and what the host is doing to this track
            draw.text_centered(fb, cx + col_w // 2, y + 2, str(i + 1), col, SMALL)
            if self.is_soloed(i):
                draw.text(fb, cx + 2, y + 2, "S", palette.GREEN, SMALL)
            elif self.is_muted(i):
                draw.text(fb, cx + 2, y + 2, "M", palette.ACCENT_BROWN, SMALL)
            draw.rect_outline(fb, bx, top, 15, span, palette.RULE)
            # the meter is pre-gate (stage F1), so a muted column still moves
            peak = min(1.0, mx.track_peaks[i])
            ph = int(peak * (span - 2))
            if ph > 0:
                draw.fill_rect(fb, bx + 1, top + span - 1 - ph, 13, ph, palette.METER)
            lv = int(mx.levels[i] * (span - 2))
            draw.fill_rect(fb, bx + 6, top + span - 1 - lv, 3, max(1, lv), col)
            draw.hline(fb, bx + 1, top + span - 1 - lv, 13, col)
            py = bot + 4
            draw.hline(fb, bx, py, 15, palette.RULE)
            pm = bx + 7 + int(mx.pans[i] * 7)
            draw.fill_rect(fb, pm - 1, py - 2, 3, 5, palette.ACCENT_BROWN)
            draw.text_centered(fb, cx + col_w // 2, y + h - 10,
                               draw.clip_text(db_text(mx.levels[i]), col_w - 2,
                                              SMALL), col, SMALL)
            if i and i % BANK_SIZE == 0:
                draw.vline(fb, cx - 1, y + 2, h - 4, palette.RULE)
        # which four `1`-`4` address (DDR-060), out of the columns' way
        bank = self.app.tape.bank
        draw.text(fb, x + w - 7, y + 2, "AB"[bank],
                  palette.encoder_color(bank), SMALL)

    def _render_eq(self, fb, rect):
        x, y, w, h = rect
        t = self.eq_track
        eq = self.eq_of(t)
        flat = MX.eq_is_flat(eq)
        self._header_line(fb, rect, "EQ  TRK%d" % (t + 1),
                          "FLAT" if flat else "ON")
        # the curve — the host hands up no magnitude response, so this is the
        # same five sections evaluated for drawing (app/mixer.eq_response_db)
        gx, gy, gw, gh = x + 6, y + 18, w - 12, h - 42
        draw.rect_outline(fb, gx, gy, gw, gh, palette.RULE)
        mid = gy + gh // 2
        draw.hline(fb, gx + 1, mid, gw - 2, palette.RULE)
        freqs = MX.log_freqs(CURVE_POINTS)
        db = MX.eq_response_db(eq, freqs, float(self.app.sample_rate))
        scale = (gh // 2 - 2) / MX.EQ_MAX_DB
        pts = []
        for n, value in enumerate(db):
            px = gx + 1 + int(n * (gw - 3) / max(1, CURVE_POINTS - 1))
            py = mid - int(max(-MX.EQ_MAX_DB, min(MX.EQ_MAX_DB, value)) * scale)
            pts.append((px, max(gy + 1, min(gy + gh - 2, py))))
        draw.polyline(fb, pts, palette.DIM if flat
                      else palette.encoder_color(t))
        draw.text(fb, gx + 3, gy + 2, "+12", palette.SUBTLE, SMALL)
        draw.text(fb, gx + 3, gy + gh - 9, "-12", palette.SUBTLE, SMALL)
        # the eight columns, aligned with MIX
        col_w = w // NUM_TRACKS
        by = y + h - 20
        for i in range(NUM_TRACKS):
            cx = x + i * col_w
            on = not MX.eq_is_flat(self.eq_of(i))
            if i == t:
                draw.fill_rect(fb, cx + 2, by, col_w - 4, 11, palette.INVERT_BG)
                fg = palette.INVERT_FG
            else:
                draw.rect_outline(fb, cx + 2, by, col_w - 4, 11, palette.RULE)
                fg = palette.INK if on else palette.DIM
            draw.text_centered(fb, cx + col_w // 2, by + 2, str(i + 1), fg, SMALL)
            if on and i != t:
                draw.hline(fb, cx + 4, by + 10, col_w - 8,
                           palette.encoder_color(i))

    def _render_master(self, fb, rect):
        x, y, w, h = rect
        mx = self.mixer
        self._header_line(fb, rect, "MASTER", "OUT")
        draw.text(fb, x + 6, y + 20, db_text(mx.master_level) + " dB",
                  palette.INK, LARGE)
        bx, by, bw, bh = x + 6, y + 40, w - 12, 14
        draw.rect_outline(fb, bx, by, bw, bh, palette.RULE)
        pk = int(min(1.0, mx.master_peak) * (bw - 2))
        if pk > 0:
            draw.fill_rect(fb, bx + 1, by + 1, pk, bh - 2, palette.METER)
            draw.vline(fb, bx + pk, by + 1, bh - 2, palette.METER_PEAK)
        draw.text(fb, x + 6, y + 60, "COMP", palette.ACCENT_BROWN, SMALL)
        gr = 1.0 - min(1.0, max(0.0, mx.comp.gain_reduction))
        cx, cy, cw = x + 42, y + 60, w - 52
        draw.hline(fb, cx, cy + 4, cw, palette.RULE)
        draw.fill_rect(fb, cx, cy + 2, max(1, int(gr * cw)), 5,
                       palette.encoder_color(1))
        draw.text(fb, x + 6, y + 74,
                  "GR %.0f%%   REL %.2fs" % (gr * 100, mx.comp_rel),
                  palette.SUBTLE, SMALL)
        drive = ("DRIVE OFF" if mx.drive <= 0.0
                 else "DRIVE %.2f  TONE %.2f" % (mx.drive, mx.drive_tone))
        draw.text(fb, x + 6, y + 86, drive,
                  palette.DIM if mx.drive <= 0.0 else palette.encoder_color(2),
                  SMALL)
        side = ("CENTRE" if abs(mx.bal) < 1e-3
                else ("L%.0f%%" % (-mx.bal * 100) if mx.bal < 0
                      else "R%.0f%%" % (mx.bal * 100)))
        draw.text(fb, x + 6, y + 98, "BAL " + side, palette.encoder_color(3),
                  SMALL)

    # -- the signal path (SHIFT+MIXER, screen-sketches § MIXER) ------------
    def _signal_rows(self):
        """(label, live) per stage, in `PROTOCOL.md` § The mixer strip order.

        `live` is "this stage is doing something to the sound" — a dim stage is
        one that is currently bit-exact bypass, which is the one thing a chain
        diagram can usefully say that the pages cannot.
        """
        mx = self.mixer
        inst = self.app.active_instrument_id
        mod = bool(self.native.mod_tabs(inst))
        fx = bool((self.native.rack(inst).get("tabs") or []))
        eq_on = any(not MX.eq_is_flat(self.eq_of(t)) for t in range(NUM_TRACKS))
        gate = any(self.is_muted(t) or self.is_soloed(t)
                   for t in range(NUM_TRACKS))
        master_fx = bool((self.native.rack("master").get("tabs") or []))
        clips = any(self.app.tape.tracks)
        # DDR-063: where the live input goes. It is an ENGINE, so it only
        # reaches the instrument when `arbutus/live-input` is loaded; MONITOR
        # is the other door and goes straight to the master.
        src = str(self.native.input_source()).upper()
        live_in = (self.native.slot(inst, "engine") or {}).get("id") == LIVE_INPUT
        mon = bool((self.native.state.get("monitor") or {}).get("on"))
        return [
            [("INPUT", True), (src, True), ("LIVE INPUT", live_in),
             ("MONITOR", mon)],
            [(inst, True), ("MOD", mod), ("FX", fx), ("MIX", True)],
            [("TAPE 1-8", clips), ("EQ", eq_on), ("MUTE/SOLO", gate),
             ("GAIN/PAN", True), ("MIX", True)],
            [("MIX", True), ("MASTER FX", master_fx),
             ("DRIVE", mx.drive > 0.0), ("COMP", mx.comp.amount > 0.0),
             ("LEVEL/BAL", True), ("OUT", True)],
        ]

    def _render_signal_path(self, fb, rect):
        x, y, w, h = rect
        self._header_line(fb, rect, "SIGNAL PATH", "SHIFT+MIXER")
        ty = y + 20
        for row in self._signal_rows():
            tx = x + 6
            for n, (label, live) in enumerate(row):
                if n:
                    draw.text(fb, tx, ty, "›", palette.RULE, SMALL)
                    tx += draw.text_width("› ", SMALL)
                colour = palette.INK if live else palette.DIM
                draw.text(fb, tx, ty, label, colour, SMALL)
                tx += draw.text_width(label + " ", SMALL)
            ty += 12
        draw.hline(fb, x + 6, ty + 2, w - 12, palette.RULE)
        draw.text(fb, x + 6, ty + 8, "DIM = PASSING THROUGH UNCHANGED",
                  palette.SUBTLE, SMALL)
        draw.text(fb, x + 6, ty + 20,
                  "SIDECHAIN: TAPE 1-8 · INPUT · SELF · MASTER",
                  palette.SUBTLE, SMALL)

    # -- persistence -------------------------------------------------------
    def snapshot(self) -> dict:
        return {"subpage": self.state.subpage, "mixer": self.mixer.snapshot(),
                "eq_track": self.eq_track}

    def restore(self, data: dict):
        self.state.subpage = int(data.get("subpage", self.state.subpage))
        self.state.eq_track = int(data.get("eq_track", self.eq_track))
        self.mixer.restore(data.get("mixer") or {})
        self._sync_params()
