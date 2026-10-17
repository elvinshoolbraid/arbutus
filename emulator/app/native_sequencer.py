"""The SEQ slot in native mode: a sequencer box as an engine (§5, DDR-041).

Two things move out of Python and one thing stays.

**Out:** the clock and the notes. The box owns both — it fires steps
sample-accurately inside a block, which the Python clock (once per audio
block) never could. `advance()` here does nothing at all.

**Out:** entry. An engine never sees SHIFT (DDR-041), so the Python's
"hold SHIFT on the SEQ page and play" became the box's four **soft-keys**:
REC / REST / UNDO / CLEAR on 1–4, labelled on the strip from the descriptor.

**Stays:** the picture. The host reports no pattern back — there is no message
for it — so this mirrors the entry gesture locally, by the box's own rules, to
draw the note row. It is a mirror and it is drawing only; nothing here can
change a note that sounds.

**Stage H2: a pattern is content, not keystrokes.** N1's bridge replayed the
mirror into the box as the REC/REST/UNDO/CLEAR presses that would have entered
it, because §8.2 had no way to hand a pattern down. It has one now — the
`pattern` asset (DDR-037) — so the mirror is written to a file and the file is
what the box opens, through `load_engine {assets:{"pattern": …}}`. That works
on a box with no soft-keys at all, which the bridge never could.

ACTIVE is the SEQ slot itself: an engine in the slot is active, an empty slot
is not (`audio/engines/arbutus/endless/README.md`). Tapping SEQ on the SEQ
page (DDR-035) loads or unloads the box.
"""

import json
import os
from typing import List, Optional, Union

from . import scratch
from .params import Param, pad_slots

MAX_STEPS = 99
REC, REST, UNDO, CLEAR = 0, 1, 2, 3

#: a step is a chord (list of MIDI notes), `[]` a rest, or the tie sentinel
TIE = "tie"
Step = Union[List[int], str]

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(midi: int) -> str:
    """The note row on SEQ. A pattern is drawn, not computed."""
    return NOTE_NAMES[int(midi) % 12]


def is_tie(step) -> bool:
    return isinstance(step, str)


def pattern_doc(pattern: List[Step], root: Optional[int] = None,
                name: str = "") -> dict:
    """The mirror as a format-1 `pattern` document (`audio/library/README.md`).

    The same shape the boxes read and the stock `.seq` files are written in —
    a step is a list of MIDI notes, `[]` a rest, `"tie"` a tie.
    """
    doc = {"format": 1, "kind": "pattern"}
    if name:
        doc["name"] = name
    if root is not None:
        doc["root"] = int(root)
    doc["pattern"] = [TIE if is_tie(s) else [int(n) for n in s]
                      for s in pattern]
    return doc


def write_pattern(path: str, doc: dict) -> str:
    with open(path, "w") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    return path


class SequencerSlot:
    """What the SEQ page reads: a pattern to draw and the shared parameters.

    The clock, the notes and the entry rules are the box's (`arbutus/endless`,
    §3.6) — `app/sequencer.py`, which owned all three in Python, is gone. This
    base is the page's side: the pattern mirror, the library folder it came
    from, the octave and persistence.
    """

    name = "SEQ"

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = int(sample_rate)
        self.app = None
        self.library_folder = None       # LibraryFolder, set by the page
        self.item = ""

        #: pattern = list of steps; a step is a list of MIDI notes, `[]` a
        #: rest, `"tie"` a tie
        self.pattern: List[Step] = []
        self.root: Optional[int] = None   # MIDI note the pattern was entered on
        self.step_index = 0
        self.octave = 0
        self.trigger_note: Optional[int] = None   # chromatic key currently held

        self._build_params()

    def bind(self, app):
        self.app = app

    def _build_params(self):                     # overridden
        self._params: List[Param] = []

    def slots(self):
        return pad_slots([])

    @property
    def hold(self) -> bool:
        return False

    def set_octave(self, value: int):
        self.octave = max(-3, min(3, int(value)))

    def insert_rest(self) -> bool:
        return False

    def undo_step(self) -> bool:
        return False

    def on_panic(self):
        """Every key let go (`App.panic`); the pattern stays."""
        self.trigger_note = None

    def toggle_active(self):
        self.set_active(not self.active)

    # -- persistence ------------------------------------------------------
    def _param_snapshot(self):
        return [p.value for p in self._params]

    def _param_restore(self, data):
        for p, v in zip(self._params, data or []):
            p.set_value(float(v))

    def snapshot(self) -> dict:
        return {
            "pattern": [TIE if is_tie(s) else list(s) for s in self.pattern],
            "root": self.root,
            "octave": self.octave,
            "params": self._param_snapshot(),
        }

    def restore(self, data: dict):
        self.pattern = _clean_pattern(data.get("pattern"))
        root = data.get("root")
        self.root = int(root) if root is not None else None
        self.octave = int(data.get("octave", 0))
        self._param_restore(data.get("params"))
        self.step_index = 0


class NativeSequencer(SequencerSlot):
    name = "ENDLESS"

    def __init__(self, native, box_id: str, desc: Optional[dict] = None,
                 sample_rate: int = 48000, inst_id: str = "SYNTH"):
        self.native = native
        self.box_id = box_id
        #: which instrument's SEQ slot this object drives, fixed for its life
        #: (S13). There is one of these per instrument now, so the instrument
        #: is a property of the object rather than of whichever page was open
        #: when it was armed.
        self.inst_id = str(inst_id).upper()
        self.desc = desc or {}
        self._loaded = False
        self._entry = False
        self._chord_open = False
        self._pending_clear = False
        #: the library file the mirror came from, while it is still that file
        self._pattern_file: Optional[str] = None
        super().__init__(sample_rate)

    # -- the host's view of this slot -------------------------------------
    @property
    def record(self) -> dict:
        return self.native.slot(self.inst_id, "seq")

    @property
    def display(self) -> dict:
        """What the box's `format_param` wants drawn, per id (§15.11).

        `arbutus/pattern`'s `AUTO <n>` for STEPS 0 arrives this way and needs
        no table here — the same seam the ENGINE page and a MOD tab use.
        """
        rec = self.record
        return rec.get("display") or {} if rec.get("id") == self.box_id else {}

    # -- parameters, from the descriptor ----------------------------------
    def _build_params(self):
        from .native_engine import slot_param

        params = (self.desc.get("params") or [])[:8]
        self._params: List[Param] = []
        for i, d in enumerate(params):
            def send(n, idx=i):
                # an unloaded slot refuses `set_param` ("that slot is empty");
                # `_push_params` sends the row whole when the box arrives
                if self._loaded:
                    self.native.set_param(self.inst_id, "seq", idx, float(n))
            self._params.append(
                slot_param(d, _default_norm(d), send, lambda: self.display))
        self._by_id = {d.get("id"): p for d, p in zip(params, self._params)}

    def param(self, pid: str) -> Optional[Param]:
        """One slot by the box's own id, or None if this box has no such knob."""
        return self._by_id.get(pid)

    # A slot snapshot is keyed **by id**, never by position (§7.1's rule for a
    # patch). `App.set_sequencer` carries one box's values onto the next, and
    # positionally `endless`'s SWING (50 %) landed on `pattern`'s STEPS and
    # clamped it to 32. A list is still read: that is what the old state files
    # on disk hold, and they were all written by `endless`.
    def _param_snapshot(self):
        return {d: p.value for d, p in self._by_id.items() if d}

    def _param_restore(self, data):
        if isinstance(data, dict):
            for pid, v in data.items():
                p = self._by_id.get(pid)
                if p is not None:
                    p.set_value(float(v))
            return
        for p, v in zip(self._params, data or []):
            p.set_value(float(v))

    def slots(self):
        """The whole row, from the descriptor (§3.5) — H1's four boxes are
        seven or eight parameters each, so **every** cell is the box's.

        ACTIVE used to sit at ENC3 and is a badge on the page now: four boxes
        with 7–8 declared parameters between them leave nowhere consistent to
        put a UI-only cell, and the arm gesture is the SEQ key (DDR-035).
        """
        return pad_slots(self._params)

    # -- soft-keys (§3.6) --------------------------------------------------
    def softkey_labels(self) -> List[Optional[str]]:
        keys = (self.desc.get("softkeys") or [])[:4]
        out = [k.get("label") for k in keys]
        return out + [None] * (4 - len(out))

    def _softkey_index(self, pid: str) -> Optional[int]:
        for i, k in enumerate((self.desc.get("softkeys") or [])[:4]):
            if k.get("id") == pid:
                return i
        return None

    def softkey(self, key: int, down: bool, shift: bool = False) -> bool:
        # `softkey` carries no instrument: it reaches whichever one is active.
        if not self._loaded or not self._is_active_instrument():
            return False
        if key >= len(self.softkey_labels()) or self.softkey_labels()[key] is None:
            return False
        self.native.softkey(key, down, shift)
        self._mirror_softkey(key, down)
        return True

    def _tap(self, pid: str) -> bool:
        """‹ › in entry mode (control-inventory § arrow keys): the same key the
        strip offers, pressed and released."""
        idx = self._softkey_index(pid)
        if idx is None:
            return False
        return bool(self.softkey(idx, True) and self.softkey(idx, False))

    def insert_rest(self) -> bool:
        return self._tap("rest")

    def undo_step(self) -> bool:
        return self._tap("undo")

    # -- the slot itself is ACTIVE ----------------------------------------
    @property
    def active(self) -> bool:
        return self._loaded

    def set_active(self, on: bool):
        self._set_loaded(bool(on))

    def _set_loaded(self, on: bool):
        if on == self._loaded:
            return
        if on:
            # The SEQ slot is per instrument (§15.4) and so is this object
            # (S13): it arms on *its* instrument, never on whichever page
            # happens to be open. That is the whole of DDR-034 as amended —
            # before it, arming on DRUM moved SYNTH's sequencer onto DRUM and
            # the riff you left on SYNTH was gone.
            assets = self._pattern_assets()
            reply = self.native.load_engine(self.inst_id, "seq", self.box_id,
                                            assets=assets)
            if assets and reply.get("type") == "error":
                # The box refused the content (a pattern longer than its grid,
                # a file that moved). The player still asked for the box: load
                # it empty rather than leaving the slot dead.
                reply = self.native.load_engine(self.inst_id, "seq", self.box_id,
                                                assets={})
            self._loaded = reply.get("type") != "error"
            if self._loaded:
                self._push_params()
        else:
            self.native.unload_engine(self.inst_id, "seq")
            self._loaded = False
            self.trigger_note = None
            self._entry = False

    def _push_params(self):
        if not self._loaded:
            return
        for i, p in enumerate(self._params):
            self.native.set_param(self.inst_id, "seq", i, p.get_normalized())

    # -- the pattern as content (DDR-037) ---------------------------------
    @property
    def takes_pattern(self) -> bool:
        """`arp` and `euclid` make their pattern out of what you hold and out
        of two numbers; neither declares the asset, so neither is handed one."""
        return "pattern" in ((self.desc.get("assets") or []))

    def _scratch_file(self) -> str:
        """Where an *entered* pattern lives until something saves it.

        `load_kit` does the same thing with a resolved kit document: the asset
        has to be a path, and a pattern that has never been named is not a
        library item yet. One file per instrument slot per process.

        It is removed at exit and not before (#105): the path is the slot's
        loaded asset, and the host re-reads it whenever that box is rebuilt,
        so the file has to outlive every `NativeSequencer` that named it.
        """
        return scratch.path("arbutus-pattern-%s" % self.inst_id.lower())

    def _pattern_assets(self) -> Optional[dict]:
        """`{"pattern": path}` for `load_engine`, or None to leave it alone."""
        if not self.takes_pattern:
            return None
        if self._pattern_file and os.path.exists(self._pattern_file):
            return {"pattern": self._pattern_file}
        if not self.pattern:
            return {}                    # an empty mirror is an empty box
        return {"pattern": write_pattern(self._scratch_file(),
                                         pattern_doc(self.pattern, self.root))}

    def push_pattern(self) -> bool:
        """Hand the mirror to a box that is already in the slot."""
        assets = self._pattern_assets()
        if not self._loaded or assets is None:
            return False
        return self.native.load_engine(
            self.inst_id, "seq", self.box_id,
            assets=assets).get("type") != "error"

    def promote_pattern(self, folder, name: str) -> str:
        """Write the mirror into the library as `<name>.seq` and point the
        slot's asset at it, so a patch saved now names a file that will still
        be there tomorrow (`audio/library/README.md` § Patterns)."""
        from . import patch as P

        fname = P.slug(name) + ".seq"
        path = write_pattern(os.path.join(folder.path, fname),
                             pattern_doc(self.pattern, self.root, name))
        folder.rescan_items()
        self._pattern_file = path
        self.item = fname
        self.push_pattern()
        return fname

    def load_pattern(self, data: dict, path: Optional[str] = None) -> bool:
        """Load a `.seq` library item: steps, root, and any named params.

        The browser applies an item as it lands on it, which may be before the
        slot is armed (DDR-035) — so this fills the mirror either way and the
        box catches up when it arrives (`_set_loaded`). `path` is the file
        itself, which is what goes down as the asset: the box reads the
        document, not a copy of it.
        """
        steps = data.get("pattern")
        if steps is None:
            return False
        self.pattern = _clean_pattern(steps)
        self._chord_open = False
        self._pending_clear = False
        self._entry = False
        self.step_index = max(0, len(self.pattern) - 1)
        self._pattern_file = path

        root = data.get("root")
        if root is None:
            flat = [n for step in self.pattern if not is_tie(step) for n in step]
            root = min(flat) if flat else None
        self.root = int(root) if root is not None else None

        # `named_params` are not part of the asset — the box never sees them.
        # They are the SEQ row, keyed by the *displayed* name so a file works
        # on any box that has a knob by that name.
        by_name = {p.name.upper(): p for p in self._params}
        for name, value in (data.get("named_params") or {}).items():
            p = by_name.get(str(name).upper())
            if p is None:
                continue
            if p.choices and isinstance(value, str):
                want = str(value).upper()
                labels = [str(c).upper() for c in p.choices]
                if want in labels:
                    p.set_value(labels.index(want))
            elif not isinstance(value, str):
                p.set_value(float(value))
        self._push_params()
        self.push_pattern()
        return True

    # -- clock / notes: the box's, not ours --------------------------------
    def advance(self, num_samples: int):
        return

    def silence(self):
        return

    def on_shift(self, pressed: bool, on_seq_page: bool) -> bool:
        return False        # DDR-041: an engine never sees SHIFT

    def on_panic(self):
        """The host's panic resets the box: entry over, the steps kept (#873).

        The mirror does what a REC release does, so ordinary playing after
        it no longer writes steps the box never took."""
        if self._entry:
            self._mirror_softkey(REC, False)
        self._held_count = 0
        self.trigger_note = None

    def on_note(self, midi_note: int, pressed: bool, on_seq_page: bool) -> bool:
        """Never consumes the key: the host routes keys into the SEQ slot."""
        if self._loaded:
            if self._entry:
                self._mirror_note(int(midi_note), pressed)
            elif pressed:
                self.trigger_note = int(midi_note)
            elif self.trigger_note == int(midi_note):
                self.trigger_note = None
        return False

    def _is_active_instrument(self) -> bool:
        return (self.native.state.get("active") or "synth").upper() == self.inst_id

    @property
    def target_id(self) -> str:
        return self.inst_id

    @property
    def hold(self) -> bool:
        """Every H1 box declares HOLD; one that does not simply never holds."""
        p = self._by_id.get("hold")
        return bool(int(p.value)) if p is not None else False

    @property
    def running(self) -> bool:
        # Only the active instrument's SEQ slot is clocked (§15.4), so a box
        # armed on SYNTH is parked while DRUM is active: the keys play DRUM
        # raw, and the picture must not claim a sequencer is driving it.
        return bool(self._loaded and self.pattern and self._is_active_instrument()
                    and (self.trigger_note is not None or self.hold))

    # -- the host's slot is the truth after a whole-sound load ---------------
    def mark_unloaded(self):
        """The slot went empty behind this object's back — a patch with no
        `seq` block was loaded over it (control-inventory § Patches: a patch
        carries the sequencer, so no sequencer is what that patch carries)."""
        self._loaded = False
        self._entry = False
        self._chord_open = False
        self._pending_clear = False
        self.trigger_note = None

    def adopt_host(self, inst_id: str, rec: dict):
        """Become what `inst_id`'s SEQ slot holds, without pushing anything.

        A whole-sound load (a patch, a slot recall, NEW) hands the host a
        `seq` block and the host loads it; this is the mirror catching up —
        the slot's parameters onto the row, the slot's `pattern` asset onto
        the note row. Nothing goes back down the socket: it is already there.
        """
        from .native_engine import denorm
        from .native_library import read_doc

        self.inst_id = inst_id.upper()
        self._loaded = True
        self._entry = False
        self._chord_open = False
        self._pending_clear = False
        self.trigger_note = None
        params = rec.get("params") or {}
        for d in (self.desc.get("params") or [])[:8]:
            p = self._by_id.get(d.get("id"))
            if p is None or d.get("id") not in params:
                continue
            n = float(params[d["id"]])
            # `slot_param`: ENUM / INTEGER live in unit space, CONTINUOUS in
            # normalised space. `.value`, not `set_value`: no echo.
            p.value = denorm(d, n) if d.get("type") in ("enum", "integer") \
                else min(1.0, max(0.0, n))
        path = (rec.get("assets") or {}).get("pattern")
        doc = read_doc(path) if path and os.path.exists(path) else None
        if doc is not None and isinstance(doc.get("pattern"), list):
            self.pattern = _clean_pattern(doc["pattern"])
            root = doc.get("root")
            if root is None:
                flat = [n for s in self.pattern if not is_tie(s) for n in s]
                root = min(flat) if flat else None
            self.root = int(root) if root is not None else None
            self._pattern_file = path
            name = os.path.basename(path)
            items = self.library_folder.items if self.library_folder else []
            self.item = name if name in items else ""
        else:
            self.pattern = []
            self.root = None
            self._pattern_file = None
            self.item = ""
        self.step_index = max(0, len(self.pattern) - 1)

    @property
    def entry(self) -> bool:
        return self._entry

    @property
    def display_step(self) -> int:
        # The box does not report where it is; the number is the length.
        return len(self.pattern)

    # -- the local mirror of the box's entry rules -------------------------
    def _dirty(self):
        """The mirror is no longer the file it was loaded from."""
        self._pattern_file = None

    def _mirror_softkey(self, key: int, down: bool):
        if key == REC:
            if down:
                self._entry = True
                self._pending_clear = True
                self._chord_open = False
                self.step_index = 0
            else:
                self._entry = False
                self._chord_open = False
                self._pending_clear = False
                # Entry over: the mirror becomes the slot's `pattern` asset.
                # The box already holds the steps it was played, but `apply`
                # rebuilds every engine from its assets on any patch, rack or
                # MOD edit — so until the file names them, the next SHIFT+3
                # silently emptied the box while the row still drew the riff,
                # and a slot record written meanwhile named the file entry
                # started from (inspection 2026-09-18).
                if self._pattern_file is None:
                    self.push_pattern()
        elif not down:
            return
        elif key == REST and self._entry:
            self._clear_if_pending()
            if len(self.pattern) < MAX_STEPS:
                # REST is also the tie key (§15.4): keys still down and a step
                # behind it that is not a rest = a tie, exactly the box's rule.
                tie = (self._chord_open and self.pattern
                       and self.pattern[-1] != [])
                self.pattern.append(TIE if tie else [])
                self._chord_open = False
                self.step_index = len(self.pattern) - 1
                self._dirty()
        elif key == UNDO and self._entry and not self._pending_clear:
            if self.pattern:
                self.pattern.pop()
                self._chord_open = False
                if not self.pattern:
                    self.root = None
                self.step_index = max(0, len(self.pattern) - 1)
                self._dirty()
        elif key == CLEAR:
            self.pattern = []
            self.root = None
            self._pending_clear = False
            self._chord_open = False
            self.step_index = 0
            self._dirty()
            self.push_pattern()               # `{}`: the slot names no file now

    def _clear_if_pending(self):
        if not self._pending_clear:
            return
        self._pending_clear = False
        self.pattern = []
        self.root = None
        self.step_index = 0
        self._dirty()

    def _mirror_note(self, note: int, pressed: bool):
        if not pressed:
            self._held_count = max(0, getattr(self, "_held_count", 0) - 1)
            if self._held_count == 0:
                self._chord_open = False
            return
        self._clear_if_pending()
        self._held_count = getattr(self, "_held_count", 0) + 1
        self._dirty()
        last = self.pattern[-1] if self.pattern else None
        if self._chord_open and isinstance(last, list) and last:
            if note not in last and len(last) < 8:
                last.append(note)
            return
        if len(self.pattern) >= MAX_STEPS:
            return
        self.pattern.append([note])
        self._chord_open = True
        if self.root is None:
            self.root = note
        self.step_index = len(self.pattern) - 1

    # -- persistence -------------------------------------------------------
    def snapshot(self) -> dict:
        data = super().snapshot()
        data["active"] = self._loaded
        # which instrument's sequencer this is (§15.4) — without it a project
        # load or a restart landed the box on whichever was active just then
        data["instrument"] = self.inst_id
        if self._pattern_file:
            data["pattern_file"] = self._pattern_file
        return data

    def restore(self, data: dict):
        super().restore(data)
        pf = data.get("pattern_file")
        self._pattern_file = pf if pf and os.path.exists(pf) else None
        self._push_params()
        was = self._loaded
        # `instrument` is written (below) but deliberately **not** read: a SEQ
        # slot letter is a sequencer-only slot (S13, map § Track S), so a
        # snapshot taken on DRUM and recalled while SYNTH is showing belongs
        # to SYNTH. The object already knows its instrument.
        self._set_loaded(bool(data.get("active", False)))
        if was and self._loaded:
            # already in the slot: the engine did not change, only the content
            self.push_pattern()


def _clean_pattern(steps) -> List[Step]:
    """A stored or authored pattern as the mirror holds it: ties are the
    string, everything else is a list of ints."""
    out: List[Step] = []
    for s in (steps or [])[:MAX_STEPS]:
        if isinstance(s, str):
            out.append(TIE)
        else:
            out.append([int(n) for n in s])
    return out


def _default_norm(d: dict) -> float:
    """`slot_param` takes the stored (normalised) value for every type."""
    from .native_engine import norm
    return norm(d, float(d.get("def", 0.0)))
