"""D17 (#276) — what the screen shows and what the player just did, for the phone.

companion-app-spec.md § 10.6: Learn's guide points arrows at things on the
instrument's screen and turns a step green when the player has done it; the
wizard answers from the last ~10 actions. Both are the UI's facts, and the phone
already reaches the host, so the UI publishes them there (`PROTOCOL.md` § The
screen and the player's actions):

    ui_elements {page, elements:[{id, label, rect}]}   when the map changes
    ui_action   {actions:[{kind, ...}]}                 once a tick, batched

The map is the frame's fixed zones (`ui/zones.py`, DDR-032: the same on every
page), named by ids a lesson can rely on:

    header  strip  main  param.0 .. param.7  key.1 .. key.4

`param.N` is encoder slot N (0-3 the bottom row, turned plain; 4-7 the top row,
turned with SHIFT); `key.N` is the strip cell over number key N. A slot or a
strip cell with nothing on it is left out, so an arrow never points at a blank.
The labels are what the cell says, never its value: a value moves with every
turn and would make the map change, and the phone redraw, on every detent. So
`strip`'s label is always empty: what the strip says (`SPEED ×1.20`, `BATT n%`,
a flash) is a value (#412).

Actions (physical controls are named by `chrome/keymap.json`'s ids):

    {kind: "key", id: "PLAY", down: true}
    {kind: "encoder", id: "ENC2", delta: 1, shift: false, param: "CUTOFF", value: "2.1k"}
    {kind: "page", page: "TRACK"}
    {kind: "rec", on: true}

and since #853 (the live stream, `PROTOCOL.md` § The live stream):

    {kind: "note", note: 60, key: "NOTE_7", on: true, vel: 0.8, source: "midi"}
    {kind: "capture", file: "mic-003.wav", seconds: 0.42}
    {kind: "project", op: "new", id: "2026-10-13/sunny-fox"}
    {kind: "track", selected: 2}       {kind: "track", armed: 2}   (0 = none)

#853 also has the UI publish its half of the live stream's *situation*
document (`situation`, as `ui_situation`, whenever it differs from the last
one sent) and say when the drawn frame changed (`frame_drawn`, as `ui_frame
{rev}`, only while someone watches: a CRC of 230 KB is not free on a Zero).
`audio/host/live-schema.json` says what every field is.

Read-only by construction: nothing here reads anything back from the phone.
"""

import os
import zlib
from typing import List, Optional

from ui import zones

#: the host's limit per `ui_action` (PROTOCOL.md); a tick with more is sent in
#: chunks, because a refused message is a whole tick lost (#412)
MAX_ACTIONS = 64

#: an idle tick rebuilds the map only this often (0.5 s at 60 Hz). Building it
#: is ~80 us on the CM4 and was done every tick (#413): the map moves on input,
#: on a page or menu change and on a new host, which rebuild it at once; this
#: catches what moves it with no input (a hold that fires late, a box loading)
REMAP_TICKS = 30


def element_map(app) -> List[dict]:
    """The current frame's named elements, in the order they are drawn."""
    page = app.current_page

    def el(id_: str, label: str, rect) -> dict:
        return {"id": id_, "label": label, "rect": list(rect)}

    out = [el("header", " › ".join(page.breadcrumb()), zones.HEADER),
           el("strip", "", zones.STRIP),
           el("main", app.page_id, zones.MAIN)]
    slots = page.slots()
    # one row (#1231): slots 0-3, or 4-7 while SHIFT is held
    first = 4 if app.shift_held else 0
    for idx in range(first, first + 4):
        slot = slots[idx] if idx < len(slots) else None
        if slot is not None:
            out.append(el("param.%d" % idx, str(slot.name),
                          zones.param_cell(idx % 4)))
    labels = page.strip_labels()
    for col in range(4):
        label = labels[col] if col < len(labels) else None
        if label is not None:
            out.append(el("key.%d" % (col + 1), str(label), zones.key_cell(col)))
    return out


def situation(app) -> dict:
    """The UI's half of the situation document (#853; `live-schema.json`).

    What only the UI knows: the screen, the keys held, the octave, the selected
    track and the count-in, the project, the INPUT page's listening, the
    battery. The host merges it over its own half (transport, tempo, tracks,
    the four slots, the input, the flags). No None anywhere: the host refuses
    a null, since a merge patch reads it as "remove" — an unknown is absent.
    """
    page = app.current_page
    crumbs = [str(c) for c in page.breadcrumb()]
    screen = {"page": app.page_id, "header": " › ".join(crumbs),
              "menu": app.menu is not None, "help": bool(app.help_held)}
    shown = app.pages.get(app.page_id)
    names = getattr(shown, "subpage_names", None)
    if names and app.menu is None:
        i = int(shown.state.subpage)
        if 0 <= i < len(names):
            screen["subpage"] = str(names[i])
            screen["subpage_key"] = i + 1
    store = getattr(shown, "slot_store", None)
    if store is not None:
        screen["slot"] = str(store.active)
    front = app.pages.get(app.active_instrument_id)
    octave = int(getattr(getattr(front, "state", None), "octave", 0) or 0)
    tape = app.tape
    out = {"screen": screen,
           "keys": {"held": sorted(app.feed.held)},
           "octave": octave,
           "transport": {"selected": int(tape.selected) + 1,
                         "counting_in": bool(tape.counting_in)}}
    project = {}
    if app.open_project:
        project["id"] = str(app.open_project)
        project["name"] = os.path.basename(str(app.open_project))
    doc = getattr(app, "doc", None)
    project["new"] = bool(getattr(doc, "made_new", False))
    out["project"] = project
    mic = getattr(app, "mic", None)
    out["input"] = {"listening": bool(mic is not None and mic.armed)}
    from . import battery
    b = battery.read()
    out["power"] = ({"battery_percent": int(b["percent"]),
                     "charging": bool(b["charging"])} if b else
                    {"telemetry": False})
    return out


class UiFeed:
    """Collects actions as they happen; `flush` sends them once a tick."""

    def __init__(self):
        self.actions: List[dict] = []
        self._sent_map: Optional[tuple] = None
        self._shown = None                      # (page id, page object, SHIFT held) last mapped
        self._idle = 0                          # ticks since the map was last built
        self._conn = None                       # the host connection last sent on
        self._recording: Optional[bool] = None
        #: #853: the keys held now, by keymap id (the situation's `keys`)
        self.held = set()
        #: #853: set by the MIDI decoder around its `on_key` call: the MIDI
        #: note number of the event being routed, else None
        self.midi: Optional[int] = None
        self._sent_situation: Optional[dict] = None
        self._track: Optional[tuple] = None     # (selected, armed) last told
        #: how many phones watch the live stream: the host says so in the
        #: reply to `ui_situation` and in a `live {watchers}` push
        self.watchers = 0
        self._frame_crc: Optional[int] = None
        self.frame_rev = 0

    def key(self, key_id: str, down: bool, vel: float = 1.0):
        if self.midi is not None:
            # a MIDI keyboard's note, routed through the same `on_key` (#853):
            # a `note`, never a key the player pressed on the instrument
            a = {"kind": "note", "note": int(self.midi), "key": key_id,
                 "on": bool(down), "source": "midi"}
            if down:
                a["vel"] = round(float(vel), 3)
            self.actions.append(a)
            return
        self.actions.append({"kind": "key", "id": key_id, "down": bool(down)})
        if down:
            self.held.add(key_id)
        else:
            self.held.discard(key_id)

    def capture(self, path: str, seconds: float):
        """INPUT took a sample (#853)."""
        self.actions.append({"kind": "capture", "file": os.path.basename(path),
                             "seconds": round(float(seconds), 2)})

    def project(self, op: str, project_id: Optional[str]):
        """FILE › PROJECT made, loaded or saved one (#853)."""
        a = {"kind": "project", "op": op}
        if project_id:
            a["id"] = str(project_id)
        self.actions.append(a)

    def frame_drawn(self, app, fb) -> bool:
        """After a frame is drawn: tell the host if its pixels changed (#853).

        Only while a phone watches the live stream; then a CRC of the frame
        against the last one. True when a `ui_frame` went out."""
        if self.watchers <= 0:
            self._frame_crc = None
            return False
        crc = zlib.crc32(memoryview(fb).cast("B"))
        if crc == self._frame_crc:
            return False
        self._frame_crc = crc
        self.frame_rev += 1
        publish = getattr(app.native, "publish", None)
        return bool(publish and publish({"type": "ui_frame", "rev": self.frame_rev}))

    def encoder(self, app, idx: int, delta: int, shift: bool):
        a = {"kind": "encoder", "id": "ENC%d" % (idx + 1), "delta": int(delta),
             "shift": bool(shift)}
        slots = app.current_page.slots()
        slot = slots[idx + 4] if shift and idx + 4 < len(slots) else None
        if slot is None and idx < len(slots):
            slot = slots[idx]                   # SHIFT on an empty top cell fine-tunes
        if slot is not None:
            a["param"] = str(slot.name)
            a["value"] = str(slot.format_value())
        self.actions.append(a)

    def page(self, page_id: str):
        self.actions.append({"kind": "page", "page": page_id})

    def flush(self, app):
        """Publish what changed since the last tick. Never raises, never refuses
        anything the player can see: this is the phone's, not the instrument's."""
        native = app.native
        publish = getattr(native, "publish", None)
        if publish is None:
            self.actions.clear()
            return
        rec = bool(app.recording)
        if self._recording is not None and rec != self._recording:
            self.actions.append({"kind": "rec", "on": rec})
        self._recording = rec
        tape = app.tape
        armed = next((t + 1 for t, a in enumerate(tape.armed) if a), 0)
        track = (int(tape.selected) + 1, armed)
        if self._track is not None:
            if track[0] != self._track[0]:
                self.actions.append({"kind": "track", "selected": track[0]})
            if track[1] != self._track[1]:
                self.actions.append({"kind": "track", "armed": track[1]})
        self._track = track

        conn = getattr(native, "conn", None)
        if conn is not self._conn:              # a restarted host knows no map
            self._conn = conn
            self._sent_map = None
            self._sent_situation = None
        # #413: the map is built when something that moves it happened, not
        # every tick. `is not` on the page object is what sees a menu open.
        shown = (app.page_id, app.current_page, bool(app.shift_held))
        self._idle += 1
        if (self.actions or self._sent_map is None or self._idle >= REMAP_TICKS
                or self._sent_situation is None
                or shown[0] != self._shown[0] or shown[1] is not self._shown[1]
                or shown[2] != self._shown[2]):
            self._shown, self._idle = shown, 0
            elements = element_map(app)
            key = (app.page_id, elements)       # lists of dicts compare by value
            if key != self._sent_map:
                if publish({"type": "ui_elements", "page": app.page_id,
                            "elements": elements}):
                    self._sent_map = key
            # #853: the situation's UI half, on the map's cadence (it moves on
            # the same things), sent only when it differs
            doc = situation(app)
            if doc != self._sent_situation:
                if publish({"type": "ui_situation", "situation": doc}):
                    self._sent_situation = doc
        w = getattr(native, "live_watchers", None)
        self.watchers = w if isinstance(w, int) else 0
        if self.actions:
            batch, self.actions = self.actions, []
            for i in range(0, len(batch), MAX_ACTIONS):
                publish({"type": "ui_action", "actions": batch[i:i + MAX_ACTIONS]})
