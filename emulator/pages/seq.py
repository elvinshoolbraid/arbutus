"""SEQ page — the sequencer (screen-sketches § SEQ).

1-4 are the box's own soft-keys (DDR-041), so the strip is whatever it
declares — `arp` and `euclid` declare none and it is blank. All six slot
letters (A B C X Y Z) hold sequencer snapshots (engine + pattern + params,
DDR-029) — C is a slot key here, not the brown LOOP legend.

**The eight encoder cells are the box's, built from its descriptor** (§3.5,
stage H2). Before H2 the row was `endless`'s four knobs looked up by name,
which drew nothing at all for `arp`, `euclid` or `pattern` and hid `endless`'s
own GATE / PATTERN / ROT. ACTIVE lost its cell in the same change — four boxes
with seven or eight declared parameters leave nowhere consistent to put a
UI-only knob — and is the SEQ key (DDR-035) plus a badge in the header.

The main area is dominated by the step number; everything else is trim.
"""

from typing import List, Optional

from app.menu import LibrarySource, MenuPage
from app.page import Page
from app.params import pad_slots
from app.native_sequencer import is_tie, note_name
from ui import draw, palette
from ui.font import LARGE, SMALL

NUM_TAIL = 12          # steps shown in the note row
COL_W = 24
BIG_SCALE = 3          # large font x3 by pixel replication

#: the instruments that own a sequencer each (S13). `App.sequencers` has the
#: same two keys; this is the boot order, and SYNTH is first because it is the
#: instrument the App starts on.
INSTRUMENTS = ("SYNTH", "DRUM")


class SeqPage(Page):
    key_id = "SEQ"
    title = "SEQ"
    category = "recording"
    subpage_names = ()

    def __init__(self, app):
        super().__init__(app)
        self.slot_store = None
        # One sequencer per instrument (S13, DDR-034 as amended). Both are
        # built at boot and neither is armed, so nothing reaches the host:
        # `instantiate` on a `seq` folder only makes the proxy, and the
        # `init.seq` applied to it pushes nothing while the slot is empty.
        for inst_id in INSTRUMENTS:
            self._load_default_engine(inst_id)

    # -- engine ------------------------------------------------------------
    @property
    def sequencer(self):
        """The **active** instrument's — this page is always showing one."""
        return self.app.sequencer

    #: the box a fresh SEQ slot arrives on. Stage H1 added `arp`, `euclid` and
    #: `pattern`, and `arp` sorts first — without this the default sequencer
    #: would have changed because a box was added, which is not a UI decision.
    DEFAULT_BOX = "endless"

    def _load_default_engine(self, inst_id: Optional[str] = None):
        folders = self.app.library.list("seq")
        if not folders:
            return
        names = [f.folder for f in folders]
        idx = names.index(self.DEFAULT_BOX) if self.DEFAULT_BOX in names else 0
        self.load_engine(idx, 0, inst_id)

    def load_engine(self, folder_idx: int, item_idx: int,
                    inst_id: Optional[str] = None):
        """Put a box in one instrument's SEQ slot. `inst_id` defaults to the
        active instrument, which is what the browser and the page mean."""
        inst_id = (inst_id or self.app.active_instrument_id).upper()
        folders = self.app.library.list("seq")
        if not folders:
            return
        lf = folders[max(0, min(len(folders) - 1, folder_idx))]
        item = lf.items[max(0, min(len(lf.items) - 1, item_idx))] if lf.items else ""
        cur = self.app.sequencers.get(inst_id)
        same = cur is not None and getattr(cur, "library_folder", None) is not None \
            and cur.library_folder.folder == lf.folder
        if same:
            engine = cur
        else:
            engine = self.app.library.instantiate(lf, self.app.sample_rate,
                                                  inst_id=inst_id)
            if engine is None:
                return
            engine.library_folder = lf
            # carries the live pattern over, on that instrument alone
            self.app.set_sequencer(engine, inst_id)
        engine.library_folder = lf              # refresh after a library rescan
        engine.item = item
        # Applied last, and in both branches: an item is a pattern file now
        # (`penta-7.seq`), so picking one has to load it — and it has to beat
        # the pattern `set_sequencer` just carried over from the old engine.
        self.app.library.apply_item(engine, lf, item)

    @property
    def engine_name(self) -> str:
        seq = self.sequencer
        if seq is None:
            return "—"
        return seq.library_folder.name if seq.library_folder else seq.name

    def sync_from_host(self, inst_id: str):
        """After a whole-sound load on `inst_id` — a patch, a slot recall,
        NEW — that instrument's SEQ slot in the host is the truth.

        A patch carries the sequencer with it (control-inventory § Patches),
        so `load_patch` loads or empties the slot; until this existed the one
        sequencer up here kept its ACTIVE badge, its ticker and its dead
        soft-keys over a slot the host had already cleared, or drew the old
        riff over a box the patch had just handed a different one. Nothing is
        pushed back down: the host has it, the mirror catches up.

        Since S13 it works on **`inst_id`'s own** sequencer, so a slot recall
        on DRUM while SYNTH is showing lands on DRUM's mirror and the page
        keeps drawing SYNTH's. Before, there was one object and this had to
        refuse the call when the other instrument had it — which left the
        parked instrument's mirror stale over a slot the host had changed.
        """
        inst_id = inst_id.upper()
        seq = self.app.sequencers.get(inst_id)
        rec = self.app.native.slot(inst_id, "seq") or {}
        hid = rec.get("id")
        if not hid:
            if seq is not None and seq.active and hasattr(seq, "mark_unloaded"):
                seq.mark_unloaded()
            return
        if seq is None or getattr(seq, "box_id", None) != hid:
            lf = next((f for f in self.app.library.list("seq")
                       if f.manifest.get("id") == hid), None)
            if lf is None:
                return
            engine = self.app.library.instantiate(lf, self.app.sample_rate,
                                                  inst_id=inst_id)
            if engine is None:
                return
            engine.library_folder = lf
            if seq is not None:
                seq.silence()
            # not `set_sequencer`: that carries the old mirror over and would
            # push it down over the pattern the patch just loaded
            engine.bind(self.app)
            self.app.sequencers[inst_id] = engine
            seq = engine
        if hasattr(seq, "adopt_host"):
            seq.adopt_host(inst_id, rec)
        self.app.notify_param_changed(self)

    # -- Page contract -----------------------------------------------------
    def slots(self) -> List[Optional[object]]:
        seq = self.sequencer
        return seq.slots() if seq is not None else pad_slots([])

    def strip_labels(self):
        """Native: the engine's own soft-key labels (§3.6, DDR-041).

        The host maps 1–4 to whatever a *sequencer* engine declares, and the
        strip says so — which is the whole reason a key may mean something an
        engine chose. Python path: no soft-keys, blank strip, as before.
        """
        seq = self.sequencer
        labels = getattr(seq, "softkey_labels", None)
        if labels is not None:
            return labels()
        return [None, None, None, None]

    def active_strip_cell(self):
        return None

    def tickers(self):
        return []           # sequencer tickers are app-level: it runs off-page

    # -- input -------------------------------------------------------------
    def on_shift_page_key(self) -> bool:
        """SHIFT + SEQ opens the sequencer engine menu (workflow §4)."""
        src = LibrarySource(self.app.library, "seq")
        menu = MenuPage(self.app, self, src,
                        lambda f, i: self.load_engine(f, i), [self.title, "ENGINE"])
        folders = [f.folder for f in self.app.library.list("seq")]
        seq = self.sequencer
        if seq is not None and seq.library_folder is not None \
                and seq.library_folder.folder in folders:
            menu.state.folder_idx = folders.index(seq.library_folder.folder)
        self.app.open_menu(menu)
        return True

    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        seq = self.sequencer
        if seq is None:
            return False
        if key in ("1", "2", "3", "4"):
            # soft-keys are held gestures (REC is), so both edges go down
            send = getattr(seq, "softkey", None)
            if send is None:
                return False
            if send(int(key) - 1, pressed, shift):
                self.app.notify_param_changed(self)
                return True
            return False
        if key in ("LEFT", "RIGHT") and pressed:
            if seq.entry:
                seq.undo_step() if key == "LEFT" else seq.insert_rest()
            else:
                seq.set_octave(seq.octave + (-1 if key == "LEFT" else 1))
                self.state.octave = seq.octave
            self.app.notify_param_changed(self)
            return True
        return False

    def help_for(self, target: str) -> str:
        table = {
            "SHIFT": "SHIFT: hold for the second row of this box's knobs",
            "C": "C: recall slot C (sequencer snapshot)",
            "LEFT": "‹ : undo step (entry) / octave down",
            "RIGHT": "› : rest, or a tie with keys held (entry) / octave up",
            "SEQ": "SEQ: arms and disarms. SHIFT+SEQ picks the box",
        }
        target = str(target)
        labels = getattr(self.sequencer, "softkey_labels", None)
        if labels is not None and target in ("1", "2", "3", "4"):
            name = labels()[int(target) - 1]
            return ("%s: %s — this engine's soft-key (DDR-041)"
                    % (target, name) if name else "%s: unused here" % target)
        if target.startswith("ENC"):
            # the row is the box's (§3.5), so the help is too
            slots = self.slots()
            idx = int(target[3:]) - 1
            p = slots[idx] if 0 <= idx < len(slots) else None
            shift = slots[idx + 4] if 0 <= idx < 4 else None
            if p is None and shift is None:
                return "%s: unused on this box" % target
            txt = "%s %s" % (target, p.name if p is not None else "—")
            if shift is not None:
                txt += " (SHIFT: %s)" % shift.name
            return txt
        if target.startswith("NOTE_"):
            return "KEY: records a step (entry) or plays the pattern"
        return table.get(target, "")

    # -- render ------------------------------------------------------------
    def render(self, fb, rect, app):
        x, y, w, h = rect
        seq = self.sequencer
        cx = x + w // 2
        cy = y + 44

        # The instrument whose sequencer this page is editing (§15.4). Since
        # S13 that is the active one — the page follows the instrument, and
        # the other one's box is parked with its own pattern and row. Still
        # read off the box rather than off the App: the header is a statement
        # about the sequencer that is drawn below it.
        target = getattr(seq, "target_id", None) or app.active_instrument_id
        draw.text(fb, x + 4, y + 3,
                  draw.clip_text(f"→ {target}", w // 2, SMALL),
                  palette.GREEN, SMALL)
        right = f"{self.engine_name}  {self.state.slot_letter}"
        rw = draw.text_width(right, SMALL)
        draw.text(fb, x + w - 4 - rw, y + 3, right, palette.SUBTLE, SMALL)

        if seq is None:
            return
        # ACTIVE is the slot itself (DDR-035) and has no encoder any more: the
        # row is the box's eight. Drawn dim while off, as the sketch asks.
        armed = bool(seq.active)
        draw.text(fb, x + w // 2 - 12, y + 3, "ACTIVE",
                  palette.GREEN if armed else palette.DIM, SMALL)
        self._draw_ring(fb, cx, cy, seq)
        self._draw_number(fb, cx, cy, seq)
        self._draw_notes(fb, x, y + h - 22, w, seq)

    def _draw_ring(self, fb, cx: int, cy: int, seq):
        """Cheap welcome visual: one dot per step around the number."""
        import math
        n = len(seq.pattern)
        if n <= 0:
            return
        shown = min(n, 24)
        for i in range(shown):
            a = -math.pi / 2 + 2.0 * math.pi * i / shown
            px = int(cx + math.cos(a) * 84)
            py = int(cy + math.sin(a) * 40)
            cur = (i == seq.step_index % shown) and (seq.running or seq.entry)
            col = palette.GREEN if cur else palette.RULE
            size = 3 if cur else 2
            draw.fill_rect(fb, px - size // 2, py - size // 2, size, size, col)

    def _draw_number(self, fb, cx: int, cy: int, seq):
        txt = f"{seq.display_step:02d}"
        tw = draw.scaled_width(txt, LARGE, BIG_SCALE)
        th = 12 * BIG_SCALE
        nx, ny = cx - tw // 2, cy - th // 2
        fg = palette.INK
        if seq.entry:
            draw.fill_rect(fb, nx - 6, ny - 4, tw + 12, th + 8, palette.INVERT_BG)
            fg = palette.INVERT_FG
        draw.text_scaled(fb, nx, ny, txt, fg, LARGE, BIG_SCALE)

    def _draw_notes(self, fb, x: int, y: int, w: int, seq):
        pattern = seq.pattern
        tail = pattern[-NUM_TAIL:]
        first = len(pattern) - len(tail)
        draw.hline(fb, x + 4, y - 3, w - 8, palette.RULE)
        for i, step in enumerate(tail):
            col_x = x + 6 + i * COL_W
            idx = first + i
            cur = (idx == seq.step_index) and (seq.running or seq.entry)
            col = palette.GREEN if cur else palette.INK
            if is_tie(step):
                # a tie holds the step before it: a rule, not a note
                draw.hline(fb, col_x, y + 4, 12, col)
                continue
            if not step:
                draw.text(fb, col_x + 6, y + 4, "·", palette.DIM, SMALL)
                continue
            draw.text(fb, col_x, y, note_name(step[0]), col, SMALL)
            if len(step) > 1:
                extra = note_name(step[1]) if len(step) == 2 else f"+{len(step) - 1}"
                draw.text(fb, col_x, y + 8, extra, palette.SUBTLE, SMALL)

    def reset(self):
        """FILE › NEW: the default box with the empty pattern (stage J1).

        Item 0 of every sequencer folder is `init.seq`, the blank one, so
        re-running the boot load also empties the mirror — and the box is
        disarmed, because a fresh instrument does not play on its own.
        """
        self.state.octave = 0
        for inst_id in INSTRUMENTS:
            self._load_default_engine(inst_id)
            seq = self.app.sequencers.get(inst_id)
            if seq is not None and seq.active:
                seq.toggle_active()

    # -- persistence (DDR-029: engine + pattern + params) -------------------
    def snapshot(self) -> dict:
        seq = self.sequencer
        data = {"octave": self.state.octave, "subpage": 0}
        if seq is not None:
            data["engine_folder"] = seq.library_folder.folder if seq.library_folder else None
            data["item"] = seq.item
            data["seq"] = seq.snapshot()
        return data

    def restore(self, data: dict):
        self.state.octave = int(data.get("octave", 0))
        folder = data.get("engine_folder")
        if folder:
            names = [f.folder for f in self.app.library.list("seq")]
            if folder in names:
                lf = self.app.library.find("seq", folder)
                items = lf.items or [""]
                item = data.get("item") or (items[0] if items else "")
                self.load_engine(names.index(folder),
                                 items.index(item) if item in items else 0)
        seq = self.sequencer
        if seq is not None and data.get("seq"):
            seq.restore(data["seq"])
