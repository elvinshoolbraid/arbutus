"""The FX subpage as a **rack** — engine-spec.md §6, DDR-038/039/040.

Native mode only. The Python path's FX page (one `EffectChain`, four knobs) is
untouched and is what `emulator/run.sh` with no flag still draws.

Everything here is the spec's *behaviour*; every drawing and gesture choice is
`[TBD-Elfy]` and is listed in `emulator/CLAUDE.md` § Native audio so Elfy can
find it. The rules this file may not break:

* the rack's state lives in the host. Every edit is a message and the page
  re-renders from the document that comes back — nothing below computes a
  macro, a binding or a tab.
* a one-engine rack shows that engine's page and **no tabs** (DDR-038), but
  something has to say the door is there.
* LIFT lifts an *address*, DROP pastes it onto a macro (DDR-039).
* ranges are set by ear, with the target sitting at the value being edited
  (DDR-040).

Chosen here, all `[TBD-Elfy]`:

| Thing | Spec | Choice |
|---|---|---|
| tab indicator | §6.1 "required, its form is not specified" | a row of boxes under the header, current one filled; the empty next tab is a box with `+` |
| one-engine hint | DDR-038 | a dim `›` at the right of the header rule — "more this way" |
| copy-mode highlight | §6.3 `[TBD-Elfy]` | a ring around every turnable param cell (`Param.highlight`) |
| range popup gesture | §6.4 `[TBD-Elfy]` | **SHIFT + DROP** on the master tab, on the macro last turned |
| range popup | §6.4 | a plain four-column overlay over the main area, with the live marker as a vertical rule in the LO..HI bar |
| target stepping | §6.4 "‹ › steps through targets" | ‹ › while the popup is open |
| the MOD rack's **K tab** | modulation-spec §3.2 "always present ... not counted in the four" (#68) | the same box in the same row, labelled `K`, one wider gap past the `+`; SHIFT+› reaches it from the last tab. Its page names the two outputs the way a modulator tab does and says `HOST SOURCE - NO PARAMS` |
"""

from typing import List, Optional

from app import stepmod as SM
from app.params import Param, StepType, pad_slots
from ui import draw, kali, palette
from ui.font import SMALL

MASTER = 0

#: the box whose tab draws op1r's own screen instead of the rack's (#1255)
KALI_BOX = "op1r/op1r-verb"

COPY_STRIP = "TURN A PARAM TO COPY ITS ADDRESS"
PASTE_STRIP = "TURN A KNOB TO PASTE"

CURVES = ("lin", "exp", "log", "s")

#: the four encoders in the range popup (§6.4)
RANGE_FIELDS = ("LO", "HI", "CURVE", "DEPTH")


def _lost_here(view):
    """`view._lost` while the browser that cancelled it is still open, else None.

    A cancelled lift comes back on a later detent of the *same* browse only
    (#711); closing that browser ends it, however it closed (#1134 row 2).
    """
    menu = view.page.app.menu
    if view._lost is not None and (menu is None or menu is not view._lost_in):
        view._lost = None
    return view._lost


class RackView:
    """One instrument's FX rack, drawn and edited through the protocol."""

    def __init__(self, page):
        self.page = page
        self.tab = MASTER
        self.mode = None            # None | "copy" | "paste" | "range"
        self.lifted = None          # (instance, param_id, "INST.PARAM")
        #: a lift the browse in progress cancelled (#711): a later detent onto
        #: a box that has the parameter brings it back; any key forgets it,
        #: and so does the browse closing (`_lost_here`, #1134)
        self._lost = None
        self._lost_in = None        # the browser `_lost` was cancelled in
        self.range_slot = 0
        self.range_target = 0
        self.last_macro = 0
        self._settled_for = None

    # -- the host's document ---------------------------------------------
    @property
    def native(self):
        return self.page.app.native

    @property
    def inst_id(self):
        return self.page.instrument_id

    @property
    def rack(self) -> dict:
        return self.native.rack(self.inst_id)

    @property
    def engine_tabs(self) -> List[dict]:
        return self.rack.get("tabs") or []

    @property
    def loaded_tabs(self) -> List[dict]:
        return self.native.tabs(self.inst_id)

    @property
    def n_tabs(self) -> int:
        return len(self.engine_tabs)

    @property
    def on_empty_tab(self) -> bool:
        return self.tab == self.n_tabs + 1

    def macro(self, slot: int) -> Optional[dict]:
        for m in self.rack.get("macros") or []:
            if int(m.get("slot", -1)) == slot:
                return m
        return None

    def tab_name(self) -> str:
        if self.tab == MASTER:
            return "MASTER"
        if self.on_empty_tab:
            return "EMPTY"
        tabs = self.engine_tabs
        i = self.tab - 1
        if i >= len(tabs):
            return "—"
        t = tabs[i]
        return str(t.get("instance") or t.get("engine", "")).upper()[:12]

    def _bypass(self) -> bool:
        if self.tab == MASTER:
            return bool(self.rack.get("bypass", False))
        i = self.tab - 1
        tabs = self.engine_tabs
        return bool(tabs[i].get("bypass", False)) if 0 <= i < len(tabs) else False

    def toggle_bypass(self):
        if self.tab == MASTER:
            msg = {"type": "set_bypass",
                   "bypass": not self.rack.get("bypass", False)}
        else:
            i = self.tab - 1
            tabs = self.engine_tabs
            if i < 0 or i >= len(tabs):
                return
            msg = {"type": "set_bypass", "index": i,
                   "bypass": not tabs[i].get("bypass", False)}
        self.native.rack_edit(self.inst_id, msg)

    def target_address(self, slot: int) -> Optional[str]:
        """The MOD route address under a visible FX slot (§5)."""
        if self.mode == "range" or slot < 0 or slot > 7:
            return None
        if self.tab == MASTER:
            return "fx.macro.%d" % slot if self.macro(slot) is not None else None
        i = self.tab - 1
        loaded = self.loaded_tabs
        tabs = self.engine_tabs
        if self.on_empty_tab or i < 0 or i >= len(loaded) or i >= len(tabs):
            return None
        desc = (loaded[i].get("desc") or {}).get("params") or []
        if slot >= len(desc):
            return None
        pid = desc[slot].get("id")
        inst = tabs[i].get("instance")
        return "fx.%s.%s" % (inst, pid) if inst and pid else None

    def settle(self):
        """Which tab the page opens on, and never past the end.

        DDR-038: a one-engine rack *is* that engine's page. More than one and
        the door the author meant you to use is the master page, so that is
        where it opens. Loading a different rack re-settles; walking the tabs
        yourself does not (DDR-028 keeps your place).
        """
        n = self.n_tabs
        if n != self._settled_for:
            self._settled_for = n
            self.tab = 1 if n <= 1 else MASTER
        self.tab = max(0, min(n + 1, self.tab))

    # -- slots -------------------------------------------------------------
    def slots(self) -> List[Optional[Param]]:
        self.settle()
        if self.mode == "range":
            return pad_slots(self._range_slots())
        if self.tab == MASTER:
            return pad_slots(self._macro_slots())
        return pad_slots(self._engine_slots())

    def _macro_slots(self) -> List[Optional[Param]]:
        from app.native_engine import slot_param            # noqa: PLC0415

        positions = self.native.macros(self.inst_id)
        out: List[Optional[Param]] = []
        for n in range(8):
            m = self.macro(n)
            if m is None:
                out.append(None)
                continue
            value = float(positions[n]) if n < len(positions) else 0.0
            p = Param((m.get("name") or "MACRO")[:8].upper(), 0.0, 1.0, value,
                      StepType.LINEAR, step_size=0.01)
            p.on_change = (lambda v, s=n:
                           self.native.set_macro(self.inst_id, s, float(v)))
            p.highlight = (self.mode == "paste")
            out.append(p)
        if self.mode == "paste":
            # an unbound slot has to be turnable: that is what DROP means
            for n in range(8):
                if out[n] is None:
                    p = Param("—", 0.0, 1.0, 0.0, StepType.LINEAR, 0.01)
                    p.highlight = True
                    out[n] = p
        _ = slot_param        # imported for symmetry with _engine_slots
        return out

    def _engine_slots(self) -> List[Optional[Param]]:
        from app.native_engine import slots_from            # noqa: PLC0415

        i = self.tab - 1
        loaded = self.loaded_tabs
        if self.on_empty_tab or i < 0 or i >= len(loaded):
            return []
        rec = loaded[i]
        desc = (rec.get("desc") or {}).get("params") or []
        params = rec.get("params") or {}

        def send(idx, value):
            if self.mode == "copy":
                self._lift(idx)
                return
            self.native.set_param(self.inst_id, "fx", idx, value, tab=i)

        # The engine's own readout (`format_param`) rides on the slot as
        # `display`, keyed by param id -- the same seam the ENGINE page and a
        # MOD tab already use. Without it a rack cell prints the raw number and
        # `arbutus/delay`'s TIME reads "0.33" where the box says "1/8".
        out = slots_from(desc, params, send,
                         lambda: (self.loaded_tabs[i].get("display") or {}))
        for p in out:
            p.highlight = (self.mode == "copy")
        return out

    def _range_slots(self) -> List[Optional[Param]]:
        t = self._range_target()
        if t is None:
            return []
        lo = Param("LO", 0.0, 1.0, float(t.get("lo", 0.0)), StepType.LINEAR, 0.01)
        hi = Param("HI", 0.0, 1.0, float(t.get("hi", 1.0)), StepType.LINEAR, 0.01)
        curve = Param("CURVE", 0, len(CURVES) - 1,
                      CURVES.index(t.get("curve", "lin"))
                      if t.get("curve", "lin") in CURVES else 0,
                      StepType.DISCRETE, choices=list(CURVES))
        depth = Param("DEPTH", -1.0, 1.0, float(t.get("depth", 1.0)),
                      StepType.LINEAR, 0.02)
        lo.on_change = lambda v: self._set_range(lo=float(v), audition="lo")
        hi.on_change = lambda v: self._set_range(hi=float(v), audition="hi")
        curve.on_change = lambda v: self._set_range(curve=CURVES[int(v)])
        depth.on_change = lambda v: self._set_range(depth=float(v))
        return [lo, hi, curve, depth]

    def _range_target(self) -> Optional[dict]:
        m = self.macro(self.range_slot)
        if not m:
            return None
        targets = m.get("targets") or []
        if not targets:
            return None
        self.range_target = max(0, min(len(targets) - 1, self.range_target))
        return targets[self.range_target]

    def _set_range(self, audition: str = "", **kw):
        msg = {"type": "set_range", "slot": self.range_slot,
               "target": self.range_target}
        msg.update(kw)
        self.native.rack_edit(self.inst_id, msg)
        if audition:
            # DDR-040: while LO or HI is turned the target *is* at that value,
            # so you hear the bound. The macro goes to the end being edited
            # and the host writes the target there every block.
            self.native.set_macro(self.inst_id, self.range_slot,
                                  0.0 if audition == "lo" else 1.0)

    # -- LIFT / DROP (DDR-039) --------------------------------------------
    def _lift(self, idx: int):
        i = self.tab - 1
        loaded = self.loaded_tabs
        if i < 0 or i >= len(loaded):
            return
        rec = loaded[i]
        desc = (rec.get("desc") or {}).get("params") or []
        if idx >= len(desc):
            return
        inst = str((self.engine_tabs[i]).get("instance", ""))
        pid = desc[idx].get("id", "")
        label = "%s.%s" % (inst.upper()[:8],
                           (desc[idx].get("name") or pid).upper())
        self.lifted = (inst, pid, label)
        self.mode = None

    def _renamed(self, i: int, was: str):
        """#711: the browser put another box on tab `i`, renaming it from `was`.

        The host carries every stored address to the new name (#329); a lift
        not yet dropped lives here, so it is carried here: to the new name when
        the new box has the same parameter, cancelled (and said) when not.
        """
        now = str(self.engine_tabs[i].get("instance", ""))
        if not was or now == was:
            return
        params = (self.loaded_tabs[i].get("desc") or {}).get("params") or []
        names = {str(p.get("id", "")): str(p.get("name") or p.get("id", "")).upper()
                 for p in params}
        lost = _lost_here(self)
        lift = (self.lifted if self.lifted and self.lifted[0] == was else
                lost if lost and lost[0] == was else None)
        if lift is not None:
            _, pid, label = lift
            if pid in names:
                self.lifted = (now, pid, "%s.%s" % (now.upper()[:8], names[pid]))
                self._lost = None
            else:
                self.lifted, self._lost = None, (now, pid, label)
                self._lost_in = self.page.app.menu
                if self.mode == "paste":
                    self.mode = None
                self.page.app.flash("LIFT CANCELLED: %s HAS NO %s"
                                    % (now.upper(), label.split(".", 1)[-1]),
                                    "LIFT CANCELLED")
        mod = getattr(self.page, "mod_rack_view", None)
        if mod is not None:
            mod.fx_renamed(was, now, names)     # a lifted route may target it

    def key(self, key: str, pressed: bool, shift: bool) -> bool:
        """Return True if the rack consumed the key."""
        if not pressed:
            return False
        self._lost = None                       # the browse is over
        self.settle()

        if key in ("LEFT", "RIGHT"):
            if self.mode == "range":
                self.range_target += 1 if key == "RIGHT" else -1
                return True
            if not shift:
                return False                    # bare arrows stay the octave
            self._step_tab(1 if key == "RIGHT" else -1)
            return True

        if key == "LIFT":
            if self.tab == MASTER or self.on_empty_tab:
                return True                     # nothing to copy here
            self.mode = "copy"
            self.lifted = None
            return True

        if key == "DROP":
            if shift:
                self._open_range()
                return True
            if self.mode == "copy" and self.lifted is None:
                self.mode = None                # LIFT then DROP, nothing turned
                return True
            if self.tab != MASTER:
                return True
            self.mode = "paste" if self.lifted else None
            return True

        return False

    def _step_tab(self, delta: int):
        # §6.1: master, every engine, and one empty tab past the end — that
        # empty tab is how a player discovers the rack exists (DDR-038).
        last = self.n_tabs + 1
        self.tab = max(0, min(last, self.tab + delta))
        if self.mode in ("copy", "paste", "range") and self.tab == MASTER:
            self.mode = "paste" if self.lifted else None
        elif self.mode == "range":
            self.mode = None

    def _open_range(self):
        if self.tab != MASTER:
            return
        if self.mode == "range":
            self.mode = None
            return
        slot = self.last_macro
        if self.macro(slot) is None:
            for n in range(8):
                if self.macro(n) is not None:
                    slot = n
                    break
            else:
                return
        self.range_slot = slot
        self.range_target = 0
        self.mode = "range"

    # -- encoders ----------------------------------------------------------
    def encoder(self, idx: int, delta: int, shift: bool) -> bool:
        slots = self.slots()
        target = slots[idx + 4] if shift else slots[idx]
        fine = False
        if shift and target is None:
            target, fine = slots[idx], True
        if target is None:
            return False
        if self.mode == "paste" and self.tab == MASTER:
            self._paste(idx + (4 if shift else 0))
            return True
        if self.tab == MASTER and self.mode != "range":
            self.last_macro = idx + (4 if shift else 0)
        target.turn(delta, fine=fine)
        return True

    def _paste(self, slot: int):
        if not self.lifted:
            self.mode = None
            return
        inst, pid, _ = self.lifted
        self.native.rack_edit(self.inst_id,
                              {"type": "bind", "slot": int(slot),
                               "instance": inst, "param": pid})
        self.last_macro = int(slot)
        self.mode = None
        self.lifted = None

    # -- the empty tab takes an engine (SHIFT+3) ---------------------------
    def set_tab_engine(self, engine_id: str) -> bool:
        if self.on_empty_tab:
            reply = self.native.rack_edit(
                self.inst_id, {"type": "add_tab", "engine": engine_id})
            if reply.get("type") == "rack":
                self.tab = len(self.engine_tabs)
                self._settled_for = self.n_tabs
            return reply.get("type") == "rack"
        if self.tab == MASTER:
            return False
        i = self.tab - 1
        tabs = self.engine_tabs
        was = str(tabs[i].get("instance", "")) if i < len(tabs) else ""
        reply = self.native.rack_edit(
            self.inst_id, {"type": "set_tab_engine", "index": i,
                           "engine": engine_id})
        if reply.get("type") != "rack":
            return False
        self._renamed(i, was)
        return True

    # -- strip / help ------------------------------------------------------
    def tickers(self):
        out = []
        if self.mode == "copy":
            out.append((2, COPY_STRIP, "COPY"))
        elif self.mode == "paste":
            out.append((2, PASTE_STRIP, "PASTE"))
        if self.lifted:
            out.append((1, "LIFTED: %s — PARAM" % self.lifted[2],
                        "LIFT %s" % self.lifted[2]))
        if self._bypass():
            out.append((2, "FX BYPASS", "BYPASS"))
        return out

    def help_for(self, target: str) -> str:
        table = {
            "LIFT": "LIFT: copy a parameter's address off this tab (DDR-039)",
            "DROP": "DROP: paste it onto a macro knob. SHIFT+DROP: set its range",
            "LEFT": "SHIFT+‹ : previous rack tab",
            "RIGHT": "SHIFT+› : next rack tab; past the end is an empty one",
            "3": "SHIFT+3: choose an engine. Press 3: toggle bypass",
        }
        if self.mode == "copy":
            return COPY_STRIP
        if self.mode == "paste":
            return PASTE_STRIP
        return table.get(str(target), "")

    # -- render ------------------------------------------------------------
    def render(self, fb, rect, header):
        self.settle()
        if self.mode is None and self._draw_box_screen(fb, rect):
            return
        x, y, w, h = rect
        n = self.n_tabs
        # DDR-038: one engine looks like nothing special. No boxes, no count —
        # just a dim chevron at the end of the header rule saying there is a
        # way through. Anything more and the rack stops being *behind* the
        # first page.
        solo = (n == 1 and self.tab == 1)
        # the count only means anything on an engine tab; on master and on the
        # empty tab the boxes already say where you are
        ratio = ("%d/%d" % (self.tab, n)
                 if (1 <= self.tab <= n and not solo) else "")
        bypass = " BYP" if self._bypass() else ""
        header(fb, rect, "FX  " + self.tab_name() + bypass, ratio)
        top = y + 16
        if solo:
            draw.text(fb, x + w - 10, y + 3, "›", palette.DIM, SMALL)
        else:
            top = self._draw_tabs(fb, x, top, w, n)
        gx, gy, gw, gh = x + 6, top, w - 12, y + h - top - 6
        draw.rect_outline(fb, gx, gy, gw, gh, palette.RULE)
        if self.mode == "range":
            self._draw_range(fb, gx + 2, gy + 2, gw - 4, gh - 4)
        elif self.tab == MASTER:
            self._draw_master(fb, gx + 4, gy + 4, gw - 8, gh - 8)
        elif self.on_empty_tab:
            draw.text_centered(fb, gx + gw // 2, gy + gh // 2 - 12,
                               "EMPTY TAB", palette.DIM, SMALL)
            draw.text_centered(fb, gx + gw // 2, gy + gh // 2 + 2,
                               "SHIFT+3 PICKS AN ENGINE", palette.SUBTLE, SMALL)
        else:
            self._draw_engine(fb, gx + 4, gy + 4, gw - 8, gh - 8)

    def _draw_box_screen(self, fb, rect) -> bool:
        """OP1R-VERB's tab is a window onto op1r's own reverb screen, the whole
        main zone, drawn from the box's four knobs (#1255; notes/ui-choices.md).
        False, and the usual page, for any other tab or without the art."""
        from app.native_engine import slots_from            # noqa: PLC0415

        i = self.tab - 1
        tabs, loaded = self.engine_tabs, self.loaded_tabs
        if not (0 <= i < len(tabs) and i < len(loaded)):
            return False
        if tabs[i].get("engine") != KALI_BOX or not kali.available():
            return False
        rec = loaded[i]
        desc = (rec.get("desc") or {}).get("params") or []
        knobs = [p.value for p in slots_from(desc, rec.get("params") or {}, None)[:4]]
        if len(knobs) < 4:
            return False
        kali.draw(fb, rect, knobs)
        return True

    def _draw_tabs(self, fb, x, y, w, n):
        """One box per tab: master, each engine, then `+` for the empty one."""
        bw, bh, gap = 16, 9, 3
        for i in range(n + 2):
            bx = x + 6 + i * (bw + gap)
            if bx + bw > x + w - 6:
                break
            if i == self.tab:
                draw.fill_rect(fb, bx, y, bw, bh, palette.INVERT_BG)
                fg = palette.INVERT_FG
            else:
                draw.rect_outline(fb, bx, y, bw, bh, palette.RULE)
                fg = palette.SUBTLE if i <= n else palette.DIM
            label = "M" if i == 0 else ("+" if i == n + 1 else str(i))
            draw.text(fb, bx + 6, y + 2, label, fg, SMALL)
        return y + bh + 4

    def _draw_master(self, fb, x, y, w, h):
        macros = sorted((self.rack.get("macros") or []),
                        key=lambda m: m.get("slot", 0))
        if not macros:
            draw.text(fb, x, y, "NO MACROS", palette.DIM, SMALL)
            draw.text(fb, x, y + 10, "LIFT A PARAM ON A TAB, DROP IT HERE",
                      palette.SUBTLE, SMALL)
            return
        for row, m in enumerate(macros[:6]):
            ty = y + row * 10
            slot = int(m.get("slot", 0))
            col = palette.encoder_color(slot)
            draw.text(fb, x, ty, "%d" % (slot + 1), col, SMALL)
            draw.text(fb, x + 10, ty,
                      draw.clip_text((m.get("name") or "?").upper(), 60, SMALL),
                      palette.INK, SMALL)
            targets = m.get("targets") or []
            first = targets[0] if targets else {}
            txt = "%s.%s" % (str(first.get("instance", "")).upper(),
                             str(first.get("param", "")).upper())
            if len(targets) > 1:
                txt += "  +%d" % (len(targets) - 1)
            draw.text(fb, x + 74, ty, draw.clip_text(txt, w - 78, SMALL),
                      palette.SUBTLE, SMALL)

    def _draw_engine(self, fb, x, y, w, h):
        i = self.tab - 1
        tabs = self.engine_tabs
        if i < 0 or i >= len(tabs):
            return
        t = tabs[i]
        draw.text(fb, x, y, draw.clip_text(str(t.get("engine", "")), w, SMALL),
                  palette.INK, SMALL)
        side = t.get("sidechain")
        if side:
            draw.text(fb, x, y + 10, "SIDECHAIN " + str(side).upper(),
                      palette.ACCENT_BROWN, SMALL)
        if self.mode == "copy":
            draw.text(fb, x, y + h - 10, COPY_STRIP, palette.GREEN, SMALL)

    def _draw_range(self, fb, x, y, w, h):
        m = self.macro(self.range_slot)
        t = self._range_target()
        if not m or not t:
            return
        targets = m.get("targets") or []
        draw.text(fb, x + 2, y + 2,
                  draw.clip_text("RANGE  %s" % (m.get("name") or "").upper(),
                                 w - 4, SMALL), palette.INK, SMALL)
        right = "%d/%d" % (self.range_target + 1, len(targets))
        draw.text(fb, x + w - 4 - draw.text_width(right, SMALL), y + 2, right,
                  palette.SUBTLE, SMALL)
        draw.text(fb, x + 2, y + 14,
                  "%s.%s" % (str(t.get("instance", "")).upper(),
                             str(t.get("param", "")).upper()),
                  palette.SUBTLE, SMALL)

        # the LO..HI span, with the live marker where the macro lands (DDR-040)
        bx, by, bw = x + 4, y + 30, w - 8
        draw.hline(fb, bx, by + 4, bw, palette.RULE)
        lo = float(t.get("lo", 0.0))
        hi = float(t.get("hi", 1.0))
        a, b = sorted((lo, hi))
        draw.fill_rect(fb, bx + int(a * (bw - 1)), by + 2,
                       max(1, int((b - a) * (bw - 1))), 5,
                       palette.encoder_color(self.range_slot))
        pos = self.native.macros(self.inst_id)
        here = float(pos[self.range_slot]) if self.range_slot < len(pos) else 0.0
        landed = lo + (hi - lo) * here
        draw.vline(fb, bx + int(max(0.0, min(1.0, landed)) * (bw - 1)),
                   by - 3, 14, palette.RED)
        draw.text(fb, x + 2, y + h - 10, "SHIFT+DROP CLOSES", palette.DIM, SMALL)


# ===========================================================================
# MOD rack — modulation-spec.md §2, §9
# ===========================================================================

MOD_MASTER = 0
CURVES_MOD = ("lin", "exp", "log", "s")

#: the one box whose outputs come from hardware, and what its page says when
#: that hardware is not there (modulation-spec § 3.2, device-os-spec § 6.5).
#: The host reports the sensor on `hello` and in every state document (#116);
#: the box itself needs no change — it already reads `motion[6]` as zero.
MOTION_BOX = "arbutus/motion"
NO_SENSOR = "NO SENSOR"

#: #68 — the **K tab** (modulation-spec § 3.2). `keys` is the host's own source,
#: not an engine: it has no params, no bypass and no browser entry, and it is
#: "always present ... and not counted in the four". So it is not a tab index in
#: the 0..4 run — it sits past the empty tab at the end of the row, and
#: `self.tab` carries this sentinel while it is showing.
MOD_KEYS = 9
KEYS_SOURCE = "keys"
#: what `rack.cpp`'s `resolve_route` accepts for `source == "keys"`, in the
#: order § 3.2 lists them. Shaped like an engine descriptor's `outputs[]` so
#: the copy-mode cells and the tab's drawing are the same code as a box's.
KEYS_OUTPUTS = ({"id": "vel", "name": "VEL", "bipolar": False},
                {"id": "note", "name": "NOTE", "bipolar": True})

#: #60 — the step-entry gesture. A modulator that declares the `pattern` asset
#: is one whose content the UI enters, and `arbutus/stepmod` is the only stock
#: box that does. Two UI-side pseudo-params go into the first two empty slots
#: (5 and 6 for stepmod's five): STEP scrolls the selection and latches, VALUE
#: sets the selected step. Neither reaches the box — see `app/stepmod.py`.
STEP_SEL, STEP_VAL = "STEP", "VALUE"
#: 40 detents across ±1. Coarser than DEPTH's 0.02 on purpose: a step value is
#: typed in, not dialled to taste, and SHIFT-fine still gives 0.005.
STEP_VALUE_STEP = 0.05
#: the id of the box parameter that says how many steps there are. The UI has
#: to name one thing about the box to know how far STEP scrolls; the box's
#: README calls it `steps` and a box without it falls back to the 16 the
#: document itself is limited to.
STEPS_PARAM = "steps"


class ModRackView:
    """One instrument's MOD rack, drawn and edited through the protocol.

    Master tab = four route rows with depth on ENC1-4, curve on the SHIFT row,
    and a small live meter per row from `meters.mod`.  Tabs 1-4 = one modulator
    each, its 8 slots displayed as engine-style params.  LIFT/DROP carries
    either a source output or a whole route (modulation-spec §10).
    """

    def __init__(self, page):
        self.page = page
        self.tab = MOD_MASTER
        self._settled_for = None
        self.mode = None       # copy_source | paste_target | copy_route | paste_route | range
        self.lifted_source = None   # (instance, output, display label)
        self.lifted_route = None    # complete route document removed from the rack
        #: ("source" | "route", payload): a lift the browse in progress
        #: cancelled (#711); a later detent onto a box that has its output or
        #: parameter brings it back, and any key here forgets it, and so does
        #: the browse closing (`_lost_here`, #1134)
        self._lost = None
        self._lost_in = None
        self.last_route = 0
        self.range_route = 0
        #: #60 step entry, keyed by the tab's `instance` so a tab that moves
        #: keeps its selection: instance -> selected step index (0-based)
        self.step_sel = {}
        #: instance -> the values being entered, once one has been turned.
        #: Until then the asset file (or the box's ramp) is the only truth.
        self._entered = {}
        #: the tab index whose values are waiting to go down the socket.
        #: Coalesced to one push per frame: `set_mod_tab_engine` rebuilds the
        #: chain, and a fast turn is many detents.
        self._pattern_dirty = None

    # -- the host's document ------------------------------------------------
    @property
    def native(self):
        return self.page.app.native

    @property
    def inst_id(self):
        return self.page.instrument_id

    @property
    def mod(self) -> dict:
        return self.native.mod_rack(self.inst_id)

    @property
    def engine_tabs(self) -> List[dict]:
        return self.mod.get("tabs") or []

    @property
    def loaded_tabs(self) -> List[dict]:
        return self.native.mod_tabs(self.inst_id)

    @property
    def motion_sensor(self) -> bool:
        """Is a motion sensor fitted and answering (device-os-spec § 6.5)?"""
        return bool(getattr(self.native, "motion_sensor", False))

    def outputs_heading(self, engine: str):
        """`(text, dead)` over an engine tab's list of outputs.

        Only one box reads hardware. `arbutus/motion` with no sensor reports
        four zeroes and cannot say so itself, so its page says it
        (modulation-spec § 3.2, device-os-spec § 6.5).
        """
        dead = str(engine) == MOTION_BOX and not self.motion_sensor
        return (NO_SENSOR if dead else "OUTPUTS"), dead

    @property
    def n_tabs(self) -> int:
        return len(self.engine_tabs)

    @property
    def on_keys_tab(self) -> bool:
        return self.tab == MOD_KEYS

    @property
    def on_empty_tab(self) -> bool:
        return not self.on_keys_tab and self.tab == self.n_tabs + 1

    @property
    def routes(self) -> List[dict]:
        return self.mod.get("routes") or []

    @property
    def routing_active(self) -> bool:
        return bool(self.mode or self.lifted_source or self.lifted_route)

    def clear_payload(self):
        self.mode = None
        self.lifted_source = None
        self.lifted_route = None
        self._lost = None

    # -- #711: a lift not yet dropped follows a browse ------------------------
    def _pending(self, kind: str, match):
        """The lifted source or route (`kind`) that `match` accepts, held or lost."""
        held = self.lifted_source if kind == "source" else self.lifted_route
        if held is not None and match(held):
            return held
        lost = _lost_here(self)
        if lost and lost[0] == kind and match(lost[1]):
            return lost[1]
        return None

    def _carry(self, kind: str, payload):
        if kind == "source":
            self.lifted_source = payload
        else:
            self.lifted_route = payload
        self._lost = None

    def _cancel(self, kind: str, payload, box: str, what: str):
        if kind == "source":
            self.lifted_source = None
        else:
            self.lifted_route = None
        self._lost = (kind, payload)
        self._lost_in = self.page.app.menu
        if self.mode in ("paste_target", "paste_route"):
            self.mode = None
        self.page.app.flash("LIFT CANCELLED: %s HAS NO %s" % (box.upper(), what),
                            "LIFT CANCELLED")

    def _renamed(self, i: int, was: str):
        """The browser put another box on MOD tab `i`, renaming it from `was`.

        The host carries every stored route (#329); a lifted output or route
        is carried here when the new box has the same output, and cancelled,
        said on the strip, when not (#711).
        """
        now = str(self.engine_tabs[i].get("instance", ""))
        if not was or now == was:
            return
        outs = {str(o.get("id", "")): str(o.get("name") or o.get("id", "")).upper()
                for o in (self.loaded_tabs[i].get("desc") or {}).get("outputs") or []}
        src = self._pending("source", lambda p: p[0] == was)
        if src is not None:
            _, output, label = src
            if output in outs:
                self._carry("source", (now, output, "%s.%s" % (now.upper(), outs[output])))
            else:
                self._cancel("source", (now, output, label), now,
                             label.split(".", 1)[-1])
        route = self._pending("route", lambda r: r.get("source") == was)
        if route is not None:
            moved = dict(route, source=now)
            output = str(route.get("output", ""))
            if output in outs:
                self._carry("route", moved)
            else:
                self._cancel("route", moved, now, output.upper())

    def fx_renamed(self, was: str, now: str, names: dict):
        """The FX tab `was` is now `now`, whose parameters are `names` (id ->
        display name): carry or cancel a lifted route that targets it (#711)."""
        old = "fx.%s." % was
        route = self._pending("route", lambda r: str(r.get("target", "")).startswith(old))
        if route is None:
            return
        pid = str(route["target"])[len(old):]
        moved = dict(route, target="fx.%s.%s" % (now, pid))
        if pid in names:
            self._carry("route", moved)
        else:
            self._cancel("route", moved, now, pid.upper())

    def settle(self):
        n = self.n_tabs
        if n != self._settled_for:
            self._settled_for = n
            # a rack with one modulator and no routes opens on that
            # modulator's tab; otherwise on the master tab (DDR-028)
            if n == 1 and not self.routes:
                self.tab = 1
            else:
                self.tab = MOD_MASTER
        if self.on_keys_tab:
            return          # K is always there, whatever the rack holds
        self.tab = max(0, min(min(n + 1, 4), self.tab))

    def tab_name(self) -> str:
        if self.tab == MOD_MASTER:
            return "MASTER"
        if self.on_keys_tab:
            return "KEYS"
        if self.on_empty_tab:
            return "EMPTY"
        tabs = self.engine_tabs
        i = self.tab - 1
        if i >= len(tabs):
            return "—"
        t = tabs[i]
        return str(t.get("instance") or t.get("engine", "")).upper()[:12]

    def _bypass(self) -> bool:
        """Whole-rack bypass, or this tab's bypass."""
        if self.tab == MOD_MASTER:
            return bool(self.mod.get("bypass", False))
        if self.on_keys_tab:
            return False        # nothing to bypass: the source is the player
        i = self.tab - 1
        tabs = self.engine_tabs
        if 0 <= i < len(tabs):
            return bool(tabs[i].get("bypass", False))
        return False

    # -- slots --------------------------------------------------------------
    def slots(self) -> List[Optional[Param]]:
        self.settle()
        if self.mode == "range":
            return pad_slots(self._range_slots())
        if self.tab == MOD_MASTER:
            return pad_slots(self._route_slots())
        if self.on_keys_tab:
            return pad_slots(self._keys_slots())
        return pad_slots(self._modulator_slots())

    def _route_slots(self) -> List[Optional[Param]]:
        """ENC1-4 = depth of routes 1-4; SHIFT = curve of routes 1-4."""
        routes = self.routes
        out: List[Optional[Param]] = []
        for n in range(4):
            r = routes[n] if n < len(routes) else None
            if r is None:
                out.append(None)
                continue
            depth = float(r.get("depth", 0.25))
            p = Param(("DEPTH%d" % (n + 1))[:8], -1.0, 1.0, depth,
                      StepType.LINEAR, 0.02)
            p.on_change = (lambda v, idx=n:
                           self._set_route(idx, depth=float(v)))
            out.append(p)
        # SHIFT row: curve per route
        for n in range(4):
            r = routes[n] if n < len(routes) else None
            if r is None:
                out.append(None)
                continue
            curve = r.get("curve", "lin")
            choices = list(CURVES_MOD)
            p = Param(("CURVE%d" % (n + 1))[:8], 0, len(choices) - 1,
                      choices.index(curve) if curve in choices else 0,
                      StepType.DISCRETE, choices=choices)
            p.on_change = (lambda v, idx=n:
                           self._set_route(idx, curve=CURVES_MOD[int(v)]))
            out.append(p)
        return out

    def _set_route(self, idx: int, **kw):
        msg = {"type": "set_route", "index": idx}
        msg.update(kw)
        self.native.mod_edit(self.inst_id, msg)

    def _range_slots(self) -> List[Optional[Param]]:
        route = self._range_record()
        if route is None:
            return []
        lo = Param("LO", 0.0, 1.0, float(route.get("lo", 0.0)),
                   StepType.LINEAR, 0.01)
        hi = Param("HI", 0.0, 1.0, float(route.get("hi", 1.0)),
                   StepType.LINEAR, 0.01)
        choices = list(CURVES_MOD)
        curve_name = route.get("curve", "lin")
        curve = Param("CURVE", 0, len(choices) - 1,
                      choices.index(curve_name) if curve_name in choices else 0,
                      StepType.DISCRETE, choices=choices)
        depth = Param("DEPTH", -1.0, 1.0, float(route.get("depth", 0.25)),
                      StepType.LINEAR, 0.02)
        lo.on_change = lambda v: self._set_route(self.range_route, lo=float(v))
        hi.on_change = lambda v: self._set_route(self.range_route, hi=float(v))
        curve.on_change = lambda v: self._set_route(
            self.range_route, curve=CURVES_MOD[int(v)])
        depth.on_change = lambda v: self._set_route(
            self.range_route, depth=float(v))
        return [lo, hi, curve, depth]

    def _range_record(self) -> Optional[dict]:
        routes = self.routes
        if not routes:
            return None
        self.range_route = max(0, min(len(routes) - 1, self.range_route))
        return routes[self.range_route]

    def _modulator_slots(self) -> List[Optional[Param]]:
        """The 8 slots of the current modulator tab, built from its descriptor."""
        from app.native_engine import slots_from            # noqa: PLC0415

        i = self.tab - 1
        loaded = self.loaded_tabs
        if self.on_empty_tab or i < 0 or i >= len(loaded):
            return []
        rec = loaded[i]
        desc = (rec.get("desc") or {}).get("params") or []
        params = rec.get("params") or {}

        if self.mode == "copy_source":
            # The physical encoders choose outputs in this mode; the engine's
            # parameter values remain untouched and no selector consumes one
            # of its eight authored slots (§10).
            return self._output_cells((rec.get("desc") or {}).get("outputs"))

        def send(idx, value):
            self.native.set_param(self.inst_id, "mod", idx, value, tab=i)

        out = list(slots_from(desc, params, send,
                              lambda: (self.loaded_tabs[i].get("display") or {})))
        # #60: the box's authored slots first, then the UI's two. A box that
        # declares the asset and seven params has room for neither, so it
        # simply does not get the gesture rather than losing a knob to it.
        if self._takes_pattern(rec) and len(out) <= 6:
            out.extend(self._entry_slots(i))
        return out

    # -- #68: the K tab -----------------------------------------------------
    def _output_cells(self, outputs) -> List[Optional[Param]]:
        """The four copy-mode cells: one per output, `—` past the last (§10)."""
        out: List[Optional[Param]] = []
        for o in list(outputs or [])[:4]:
            p = Param((o.get("name") or o.get("id") or "?").upper(),
                      0, 1, 0, StepType.DISCRETE, choices=["", ""])
            p.highlight = True
            out.append(p)
        while len(out) < 4:
            out.append(None)
        return out

    def tab_outputs(self):
        """`(source, outputs)` of the tab being shown, box or keys.

        The K tab's source is the string the host answers to (`rack.cpp`
        `resolve_route`), not a tab instance, which is the whole difference
        between it and a modulator tab.
        """
        if self.on_keys_tab:
            return KEYS_SOURCE, [dict(o) for o in KEYS_OUTPUTS]
        i = self.tab - 1
        loaded, tabs = self.loaded_tabs, self.engine_tabs
        if self.tab == MOD_MASTER or self.on_empty_tab:
            return "", []
        if i < 0 or i >= len(loaded) or i >= len(tabs):
            return "", []
        return (str(tabs[i].get("instance", "")),
                (loaded[i].get("desc") or {}).get("outputs") or [])

    def _keys_slots(self) -> List[Optional[Param]]:
        """The K tab has no params of its own (§ 3.2) — only its two outputs.

        So the eight cells are empty until LIFT, when the bottom row becomes
        VEL and NOTE exactly as a modulator tab's becomes its outputs.
        """
        if self.mode == "copy_source":
            return self._output_cells(KEYS_OUTPUTS)
        return []

    # -- #60: entering a step pattern ---------------------------------------
    def _takes_pattern(self, rec: dict) -> bool:
        """Does this tab's box open a `pattern` asset (`app/stepmod.py`)?"""
        return SM.ASSET in ((rec.get("desc") or {}).get("assets") or [])

    def _tab_instance(self, i: int) -> str:
        tabs = self.engine_tabs
        return str(tabs[i].get("instance") or "") if 0 <= i < len(tabs) else ""

    def _tab_steps(self, i: int) -> int:
        """The tab's STEPS, in units. `params` is normalised, `def` is not."""
        from app.native_engine import denorm                # noqa: PLC0415

        loaded = self.loaded_tabs
        if not 0 <= i < len(loaded):
            return SM.MAX_VALUES
        rec = loaded[i]
        values = rec.get("params") or {}
        for d in (rec.get("desc") or {}).get("params") or []:
            if d.get("id") != STEPS_PARAM:
                continue
            if STEPS_PARAM in values:
                return int(round(denorm(d, float(values[STEPS_PARAM]))))
            return int(round(float(d.get("def", SM.MAX_VALUES))))
        return SM.MAX_VALUES

    def _tab_asset(self, i: int) -> Optional[str]:
        """The `pattern` file the box actually opened, absolute."""
        loaded = self.loaded_tabs
        if 0 <= i < len(loaded):
            path = (loaded[i].get("assets") or {}).get(SM.ASSET)
            if path:
                return str(path)
        tabs = self.engine_tabs
        if 0 <= i < len(tabs):
            return (tabs[i].get("assets") or {}).get(SM.ASSET)
        return None

    def step_values(self, i: int) -> List[float]:
        """What the tab is playing, one value per drawn cell.

        The file is the truth until the player turns VALUE; after that the
        entered list is, and either is wrapped out to STEPS the way the box
        wraps it, so the picture and the sound agree.
        """
        src = self._entered.get(self._tab_instance(i))
        if src is None:
            src = SM.read_values(self._tab_asset(i)) or []
        return SM.effective(src, self._tab_steps(i))

    def step_index(self, i: int) -> int:
        """The latched selection, clamped to the current STEPS."""
        key = self._tab_instance(i)
        n = len(self.step_values(i))
        sel = max(0, min(n - 1, int(self.step_sel.get(key, 0))))
        self.step_sel[key] = sel
        return sel

    def _entry_slots(self, i: int) -> List[Param]:
        """STEP and VALUE, or nothing if the box left no room for both."""
        values = self.step_values(i)
        sel = self.step_index(i)
        key = self._tab_instance(i)

        step = Param(STEP_SEL, 1, len(values), sel + 1, StepType.DISCRETE,
                     step_size=1.0, display_format="{:.0f}")
        step.on_change = lambda v, k=key: self.step_sel.__setitem__(k, int(v) - 1)
        value = Param(STEP_VAL, -1.0, 1.0, values[sel], StepType.LINEAR,
                      STEP_VALUE_STEP)
        value.formatter = lambda v: "%+.2f" % v
        value.on_change = lambda v, t=i: self._set_step(t, float(v))
        return [step, value]

    def _set_step(self, i: int, v: float):
        """Turning VALUE edits the selected step and marks the tab dirty."""
        values = self.step_values(i)
        values[self.step_index(i)] = max(-1.0, min(1.0, v))
        self._entered[self._tab_instance(i)] = values
        self._pattern_dirty = i

    def flush_pattern(self) -> bool:
        """Send an edited pattern down, at most once a frame.

        `set_mod_tab_engine` rebuilds the tab, so this is not something to do
        per detent. The engine named is the one already on the tab: the host
        keeps the tab's params, sidechain and routes when the engine does not
        change, and takes the new content (PROTOCOL § MOD rack, #60).
        """
        i, self._pattern_dirty = self._pattern_dirty, None
        tabs = self.engine_tabs
        if i is None or not 0 <= i < len(tabs):
            return False
        key = self._tab_instance(i)
        values = self._entered.get(key)
        if not values:
            return False
        path = SM.write_values(SM.scratch_file(self.inst_id, key), values)
        msg = {"type": "set_mod_tab_engine", "index": i,
               "engine": str(tabs[i].get("engine") or ""),
               "assets": {SM.ASSET: path}}
        if tabs[i].get("min_version"):
            msg["min_version"] = str(tabs[i]["min_version"])
        reply = self.native.mod_edit(self.inst_id, msg)
        if reply.get("type") == "error":
            # The box refused the content. Drop the entered list so the page
            # goes back to showing what the box is really playing.
            self._entered.pop(key, None)
            self.page.app.flash("STEP PATTERN REFUSED", "NO PATTERN")
            return False
        return True

    def decorate_targets(self, slots: List[Optional[Param]],
                         addresses: List[Optional[str]]):
        """Add paste highlights and DDR-051 live markers to target cells."""
        live = self.native.mod_route_values()
        routed = {str(r.get("target")) for r in self.routes}
        for p, addr in zip(slots, addresses):
            if p is None:
                continue
            p.ghost = None
            if addr in routed:
                p.ghost = float(live.get(addr, p.get_normalized()))
            if self.mode == "paste_target":
                p.highlight = bool(addr and not p.locked)
            elif self.page.state.subpage != 2:   # FX owns its own copy rings
                p.highlight = False

    def target_key(self, key: str, pressed: bool, shift: bool) -> bool:
        """DROP on a non-MOD page starts source→parameter paste mode."""
        if not pressed or key != "DROP" or shift or not self.lifted_source:
            return False
        self.mode = "paste_target"
        return True

    def route_to(self, target: Optional[str]) -> bool:
        """Turn a highlighted target: create the route and return to master."""
        if self.mode != "paste_target" or not self.lifted_source or not target:
            return False
        source, output, _ = self.lifted_source
        reply = self.native.mod_edit(self.inst_id, {
            "type": "route", "source": source, "output": output,
            "target": target,
        })
        if reply.get("type") == "error":
            if reply.get("code") == "mod_rack_full":
                self.page.app.flash("MOD RACK FULL", "MOD FULL")
            else:
                self.page.app.flash("ROUTE REFUSED", "REFUSED")
            return True
        self.last_route = max(0, len(self.routes) - 1)
        self.lifted_source = None
        self.mode = None
        self.tab = MOD_MASTER
        self.page.state.subpage = 3
        self.page.app.notify_param_changed(self.page)
        return True

    # -- keys ---------------------------------------------------------------
    def key(self, key: str, pressed: bool, shift: bool) -> bool:
        if not pressed:
            return False
        self._lost = None                       # the browse is over
        self.settle()

        if key in ("LEFT", "RIGHT"):
            if self.mode == "range":
                self.range_route += 1 if key == "RIGHT" else -1
                self._range_record()
                return True
            if not shift:
                return False            # bare arrows stay the octave
            self._step_tab(1 if key == "RIGHT" else -1)
            return True

        if key == "LIFT":
            if self.tab == MOD_MASTER:
                self.mode = "copy_route"
                self.lifted_route = None
            elif not self.on_empty_tab:
                self.mode = "copy_source"
                self.lifted_source = None
            return True

        if key == "DROP":
            if shift and self.tab == MOD_MASTER:
                self._open_range()
                return True
            if self.mode in ("copy_source", "copy_route"):
                # The mirror of lift-without-drop: no encoder was turned, so
                # there is no payload and the gesture simply cancels.
                self.mode = None
                return True
            if self.tab == MOD_MASTER and self.lifted_route is not None:
                self.mode = "paste_route"
                return True
            return bool(self.lifted_source or self.lifted_route)

        return False

    def _step_tab(self, delta: int):
        # §3.2: K is "always present ... and not counted in the four", so it is
        # one place further right than the last tab the rack itself has — the
        # empty tab when there is one, tab 4 when the rack is full.
        last = min(self.n_tabs + 1, 4)
        if self.on_keys_tab:
            self.tab = MOD_KEYS if delta > 0 else last
            return
        step = self.tab + delta
        self.tab = MOD_KEYS if step > last else max(0, min(last, step))

    def _open_range(self):
        if self.mode == "range":
            self.mode = None
            return
        if not self.routes:
            return
        self.range_route = max(0, min(len(self.routes) - 1, self.last_route))
        self.mode = "range"

    # -- bypass (DDR-049: press 4 while on MOD toggles) ---------------------
    def toggle_bypass(self):
        if self.on_keys_tab:
            return              # nothing of the player's to bypass
        if self.tab == MOD_MASTER:
            msg = {"type": "set_mod_bypass",
                   "bypass": not self.mod.get("bypass", False)}
        else:
            i = self.tab - 1
            tabs = self.engine_tabs
            if 0 <= i < len(tabs):
                msg = {"type": "set_mod_bypass", "index": i,
                       "bypass": not tabs[i].get("bypass", False)}
            else:
                return
        self.native.mod_edit(self.inst_id, msg)

    # -- encoders -----------------------------------------------------------
    def encoder(self, idx: int, delta: int, shift: bool) -> bool:
        if self.mode == "copy_source" and self.tab != MOD_MASTER:
            self._lift_output(idx)
            return True
        if self.mode == "copy_route" and self.tab == MOD_MASTER:
            self._lift_route(idx)
            return True
        if self.mode == "paste_route" and self.tab == MOD_MASTER:
            self._paste_route(idx)
            return True
        slots = self.slots()
        target = slots[idx + 4] if shift else slots[idx]
        fine = False
        if shift and target is None:
            target, fine = slots[idx], True
        if target is None:
            return False
        if self.tab == MOD_MASTER and self.mode != "range":
            self.last_route = idx
        target.turn(delta, fine=fine)
        return True

    def _lift_output(self, idx: int):
        source, outputs = self.tab_outputs()
        if idx < 0 or idx >= len(outputs):
            return
        o = outputs[idx]
        output = str(o.get("id", ""))
        if not source or not output:
            return
        label = "%s.%s" % (source.upper(),
                            str(o.get("name") or output).upper())
        self.lifted_source = (source, output, label)
        self.lifted_route = None
        self.mode = None

    def _lift_route(self, idx: int):
        routes = self.routes
        if idx < 0 or idx >= len(routes):
            return
        self.lifted_route = dict(routes[idx])
        self.lifted_source = None
        self.native.mod_edit(self.inst_id, {"type": "unroute", "index": idx})
        self.last_route = max(0, min(idx, len(self.routes) - 1))
        self.mode = None
        self.page.app.notify_param_changed(self.page)

    def _paste_route(self, idx: int):
        if self.lifted_route is None:
            self.mode = None
            return
        msg = {"type": "route", "index": min(idx, len(self.routes))}
        msg.update(self.lifted_route)
        reply = self.native.mod_edit(self.inst_id, msg)
        if reply.get("type") == "error":
            self.page.app.flash("ROUTE REFUSED", "REFUSED")
            return
        self.last_route = min(idx, len(self.routes) - 1)
        self.lifted_route = None
        self.mode = None
        self.page.app.notify_param_changed(self.page)

    # -- SHIFT+4: modulator menu for the current tab ------------------------
    def set_tab_engine(self, engine_id: str) -> bool:
        self.settle()
        if self.tab == MOD_MASTER or self.on_keys_tab:
            # K holds no engine, so SHIFT+4 there does what it does on the
            # master tab: it opens on tab 1 (§2.1).
            self.tab = 1
        if self.on_empty_tab:
            reply = self.native.mod_edit(
                self.inst_id, {"type": "add_mod_tab", "engine": engine_id})
            if reply.get("type") == "mod":
                self.tab = len(self.engine_tabs)
                self._settled_for = self.n_tabs
            return reply.get("type") == "mod"
        if self.tab == MOD_MASTER:
            return False
        i = self.tab - 1
        tabs = self.engine_tabs
        was = str(tabs[i].get("instance", "")) if i < len(tabs) else ""
        reply = self.native.mod_edit(
            self.inst_id, {"type": "set_mod_tab_engine",
                           "index": i, "engine": engine_id})
        if reply.get("type") != "mod":
            return False
        self._renamed(i, was)
        return True

    # -- strip / help -------------------------------------------------------
    def tickers(self):
        out = []
        if self.mode == "copy_source":
            out.append((2, "TURN AN OUTPUT TO GRAB IT", "GRAB OUTPUT"))
        elif self.mode == "copy_route":
            out.append((2, "TURN A ROUTE TO LIFT IT", "LIFT ROUTE"))
        elif self.mode == "paste_target":
            out.append((2, "TURN A PARAM TO ROUTE TO IT", "ROUTE PARAM"))
        elif self.mode == "paste_route":
            out.append((2, "TURN A ROW TO MOVE THE ROUTE", "MOVE ROUTE"))
        if self.lifted_source:
            out.append((1, "LIFTED: %s — SOURCE" % self.lifted_source[2],
                        "LIFT %s" % self.lifted_source[2]))
        if self.lifted_route:
            src = "%s.%s" % (self.lifted_route.get("source", "?"),
                              self.lifted_route.get("output", "?"))
            out.append((1, "LIFTED: %s → %s — ROUTE" %
                        (src.upper(), self.lifted_route.get("target", "?")),
                        "LIFT ROUTE"))
        if self._bypass():
            out.append((2, "MOD BYPASS", "BYPASS"))
        return out

    def help_for(self, target: str) -> str:
        table = {
            "LIFT": "LIFT: grab an output here (K = velocity, note), "
                    "or lift a route on master",
            "DROP": "DROP: route a lifted source. SHIFT+DROP: route range",
            "LEFT": "SHIFT+‹ : previous MOD tab",
            "RIGHT": "SHIFT+› : next MOD tab; then an empty one, then K (keys)",
            "4": "SHIFT+4: choose a modulator for this tab. Press 4: toggle bypass",
        }
        return table.get(str(target), "")

    # -- render -------------------------------------------------------------
    def render(self, fb, rect, header):
        self.settle()
        x, y, w, h = rect
        n = self.n_tabs
        solo = (n == 1 and self.tab == 1)
        ratio = ("%d/%d" % (self.tab, n)
                 if (1 <= self.tab <= n and not solo) else "")
        bypass = " BYP" if self._bypass() else ""
        header(fb, rect, "MOD  " + self.tab_name() + bypass, ratio)
        top = y + 16
        if solo:
            draw.text(fb, x + w - 10, y + 3, "›", palette.DIM, SMALL)
        else:
            top = self._draw_tabs(fb, x, top, w, n)
        gx, gy, gw, gh = x + 6, top, w - 12, y + h - top - 6
        draw.rect_outline(fb, gx, gy, gw, gh, palette.RULE)
        if self.tab == MOD_MASTER:
            if self.mode == "range":
                self._draw_range(fb, gx + 2, gy + 2, gw - 4, gh - 4)
            else:
                self._draw_master(fb, gx + 4, gy + 4, gw - 8, gh - 8)
        elif self.on_keys_tab:
            self._draw_keys(fb, gx + 4, gy + 4, gw - 8, gh - 8)
        elif self.on_empty_tab:
            draw.text_centered(fb, gx + gw // 2, gy + gh // 2 - 12,
                               "EMPTY TAB", palette.DIM, SMALL)
            draw.text_centered(fb, gx + gw // 2, gy + gh // 2 + 2,
                               "SHIFT+4 PICKS A MODULATOR", palette.SUBTLE, SMALL)
        else:
            self._draw_engine(fb, gx + 4, gy + 4, gw - 8, gh - 8)

    def _draw_tabs(self, fb, x, y, w, n):
        """One box per tab: master, each modulator, `+` for the empty one, K.

        K is the same box in the same row — the tab idiom, not a new visual
        language (#68) — but set off by a wider gap, because §3.2 says it is
        not one of the four and the row should not read as five modulators.
        """
        bw, bh, gap = 16, 9, 3
        shown = min(n + 2, 5)
        for i in range(shown):
            bx = x + 6 + i * (bw + gap)
            if bx + bw > x + w - 6:
                break
            if i == self.tab:
                draw.fill_rect(fb, bx, y, bw, bh, palette.INVERT_BG)
                fg = palette.INVERT_FG
            else:
                draw.rect_outline(fb, bx, y, bw, bh, palette.RULE)
                fg = palette.SUBTLE if i <= n else palette.DIM
            label = "M" if i == 0 else ("+" if i == n + 1 else str(i))
            draw.text(fb, bx + 6, y + 2, label, fg, SMALL)
        kx = x + 6 + shown * (bw + gap) + gap * 2
        if kx + bw <= x + w - 6:
            if self.on_keys_tab:
                draw.fill_rect(fb, kx, y, bw, bh, palette.INVERT_BG)
                fg = palette.INVERT_FG
            else:
                draw.rect_outline(fb, kx, y, bw, bh, palette.RULE)
                fg = palette.SUBTLE
            draw.text(fb, kx + 6, y + 2, "K", fg, SMALL)
        return y + bh + 4

    def _draw_master(self, fb, x, y, w, h):
        routes = self.routes
        if not routes:
            draw.text(fb, x, y, "NO ROUTES", palette.DIM, SMALL)
            draw.text(fb, x, y + 10, "NO MODULATION ROUTES ASSIGNED",
                      palette.SUBTLE, SMALL)
            return
        # live values from meters.mod: target → value
        live = self.native.mod_route_values()
        for row, r in enumerate(routes[:4]):
            ty = y + row * 12
            col = palette.encoder_color(row)
            # source.output
            src = "%s.%s" % (str(r.get("source", "?")).upper(),
                             str(r.get("output", "?")).upper())
            draw.text(fb, x, ty, draw.clip_text(src, 90, SMALL), col, SMALL)
            # → target
            tgt = str(r.get("target", "—"))
            draw.text(fb, x + 92, ty, "→", palette.SUBTLE, SMALL)
            draw.text(fb, x + 102, ty, draw.clip_text(tgt, 88, SMALL),
                      palette.INK, SMALL)
            # depth + curve
            depth = float(r.get("depth", 0.0))
            sign = "+" if depth >= 0 else ""
            curve = str(r.get("curve", "lin"))[:3]
            draw.text(fb, x + 194, ty,
                      "%s%.2f %s" % (sign, depth, curve),
                      palette.SUBTLE, SMALL)
            # small live meter: a thin bar showing the target's current value
            live_val = live.get(tgt, None)
            if live_val is not None:
                mx, my, mw, mh = x + 260, ty + 1, w - 260, 6
                draw.rect_outline(fb, mx, my, mw, mh, palette.RULE)
                fill = int(max(0.0, min(1.0, float(live_val))) * (mw - 2))
                if fill > 0:
                    draw.fill_rect(fb, mx + 1, my + 1, fill, mh - 2, col)

    def _draw_engine(self, fb, x, y, w, h):
        i = self.tab - 1
        tabs = self.engine_tabs
        if i < 0 or i >= len(tabs):
            return
        t = tabs[i]
        draw.text(fb, x, y, draw.clip_text(str(t.get("engine", "")), w, SMALL),
                  palette.INK, SMALL)
        side = t.get("sidechain")
        if side:
            draw.text(fb, x, y + 10, "SIDECHAIN " + str(side).upper(),
                      palette.ACCENT_BROWN, SMALL)
        # show the modulator's outputs on the main area
        loaded = self.loaded_tabs
        if i < len(loaded):
            desc = (loaded[i].get("desc") or {})
            outputs = desc.get("outputs") or []
            # § 6.5 / modulation-spec § 3.2: with no sensor the four outputs
            # are all zero, so they are named where they always are and the
            # heading says why. The same line covers a sensor that was fitted
            # and has stopped answering -- the spec makes those one case.
            heading, dead = self.outputs_heading(t.get("engine", ""))
            if outputs:
                draw.text(fb, x, y + 22, heading,
                          palette.ACCENT_BROWN if dead else palette.SUBTLE,
                          SMALL)
                for oi, o in enumerate(outputs[:4]):
                    name = str(o.get("name") or o.get("id", "?"))[:8]
                    bip = "+/-" if o.get("bipolar") else "+"
                    draw.text(fb, x + 2, y + 34 + oi * 10,
                              "%s %s" % (bip, name.upper()),
                              palette.DIM if dead
                              else palette.encoder_color(oi), SMALL)
            # #60: the steps themselves, beside the outputs — the picture the
            # STEP/VALUE pair edits. Without it the two encoders are numbers
            # with nothing to aim at.
            if self._takes_pattern(loaded[i]):
                self._draw_steps(fb, x + 78, y + 20, w - 80, 46, i)

    def _draw_keys(self, fb, x, y, w, h):
        """The K tab: the two host outputs, drawn as a modulator tab draws its.

        No engine line and no sidechain line, because there is no box: the
        source is the player. The `NO PARAMS` line is what stops the empty
        bottom row reading as a page that has not loaded (§ 3.2 gives `keys`
        no params at all).
        """
        draw.text(fb, x, y, KEYS_SOURCE.upper(), palette.INK, SMALL)
        draw.text(fb, x, y + 10, "HOST SOURCE - NO PARAMS",
                  palette.SUBTLE, SMALL)
        draw.text(fb, x, y + 22, "OUTPUTS", palette.SUBTLE, SMALL)
        for oi, o in enumerate(KEYS_OUTPUTS):
            draw.text(fb, x + 2, y + 34 + oi * 10,
                      "%s %s" % ("+/-" if o["bipolar"] else "+", o["name"]),
                      palette.encoder_color(oi), SMALL)

    def _draw_steps(self, fb, x, y, w, h, i):
        """The 16-step bar, bipolar about a centre line, the selection lit.

        Always sixteen cells wide, however many STEPS says: a grid that
        resized as you turned STEPS would move the step you were aiming at.
        The cells past STEPS are drawn as an empty outline — they are where
        the pattern would go, not part of it.

        A value is ±1 and the box glides between them, so a cell is a bar
        growing up or down from the middle rather than a level: a step at 0
        draws nothing, which is what 0 means here.
        """
        values = self.step_values(i)
        sel = self.step_index(i)
        cw = max(3, (w - 2) // SM.MAX_VALUES)
        mid = y + h // 2
        draw.hline(fb, x, mid, cw * SM.MAX_VALUES, palette.RULE)
        for k in range(SM.MAX_VALUES):
            cx = x + k * cw
            draw.rect_outline(fb, cx, y, cw, h, palette.DIM)
            if k >= len(values):
                continue
            col = palette.INK if k == sel else palette.SUBTLE
            reach = int(max(-1.0, min(1.0, float(values[k]))) * (h // 2 - 1))
            if reach >= 0:
                draw.fill_rect(fb, cx + 1, mid - reach, cw - 2, max(1, reach), col)
            else:
                draw.fill_rect(fb, cx + 1, mid + 1, cw - 2, -reach, col)
            if k == sel:
                draw.rect_outline(fb, cx, y, cw, h, col)

    def _draw_range(self, fb, x, y, w, h):
        route = self._range_record()
        if route is None:
            return
        draw.text(fb, x + 2, y + 2, "ROUTE RANGE", palette.INK, SMALL)
        right = "%d/%d" % (self.range_route + 1, len(self.routes))
        draw.text(fb, x + w - 4 - draw.text_width(right, SMALL), y + 2,
                  right, palette.SUBTLE, SMALL)
        label = "%s.%s → %s" % (route.get("source", "?"),
                                      route.get("output", "?"),
                                      route.get("target", "?"))
        draw.text(fb, x + 2, y + 14,
                  draw.clip_text(label.upper(), w - 4, SMALL),
                  palette.SUBTLE, SMALL)
        bx, by, bw = x + 4, y + 30, w - 8
        draw.hline(fb, bx, by + 4, bw, palette.RULE)
        lo = float(route.get("lo", 0.0))
        hi = float(route.get("hi", 1.0))
        a, b = sorted((lo, hi))
        draw.fill_rect(fb, bx + int(a * (bw - 1)), by + 2,
                       max(1, int((b - a) * (bw - 1))), 5,
                       palette.encoder_color(self.range_route))
        live = self.native.mod_route_values()
        here = float(live.get(str(route.get("target", "")), 0.0))
        draw.vline(fb, bx + int(max(0.0, min(1.0, here)) * (bw - 1)),
                   by, 9, palette.INK)

    def on_panic(self):
        self.clear_payload()
