"""FILE › PROJECT — one document that is the whole instrument (DDR-019/020).

`app/persist.py` autosaves the *working* state into `emulator/state/` so a
restart lands where you left off. This is the other thing: a **named** project
the player asks for, filed in the library under the date it was made, holding
everything needed to come back to the same sound —

  * every instrument's engine + patch, kit, FX rack, MOD rack and assets, and
    the macro positions, which are sound and are not in a §6.7 document;
  * the six memory slots on every slot page (SYNTH, DRUM, SEQ);
  * the SEQ box with its pattern — **promoted into the library on save**, because
    an entered pattern otherwise lives on a scratch file that dies with the
    session (stage H2's note);
  * the tape: the clip geometry, each track's medium as a WAV beside the
    document, the loop, the selection (and with it the bank, DDR-060). Since
    C1 step 3b (#297) the WAVs are not copied in: the audio process writes
    every take straight into the folder the tape lives in, and since D97
    (#817) that is always the open project's folder;
  * **both sides of the mixer** — `Mixer.snapshot()` up here and the host's
    per-track gain/pan/mute/solo/EQ, which nothing wrote to disk before (F2);
  * tempo: `bpm`, `link` and `base_bpm` — a project load is the only thing
    allowed to move `base_bpm` (E1, stage H2's note) — and the click.

**The format is defined in `audio/library/README.md` § Projects.** No document
in `v1-spec-package.md` specifies one; screen-sketches § FILE says what a
project *holds*, not how it is written down. That is stage J1's deviation.

A project is a folder, not a file, because the media are WAVs:

    <library>/projects/<YYYY-MM-DD>/<NAME>/project.json
                                          tape-1.wav ... tape-8.wav

which is also the two-pane browser the sketch asks for — folder = date.

**Always saved (D97, #817; `device-os-spec.md` § 7.3).** There is no SAVE:
NEW makes the project's folder at once, a boot with none open makes one
(`ensure_open`), and the autosave (`autosave`, from `Document.tick` and
`flush`) writes this document into the open folder, so the folder on the card
is always the project. A project nothing was done to since NEW is deleted
when it is left (`leave`): NEW, LOAD, power-off. `save` stays as the one
writer underneath — the migration of an old unsaved session, a copy (the
phone's Duplicate), and the tests.

**One owner for the open project (#1017).** `Document` (`app.doc`, at the
end of this file) holds what the document half remembers — the last text
written, a refused write, the NEW mark — and the working state's pointer to
the open project (`state/project.json`). `persist.ProjectStore`
(`app.project`) keeps the tape's folder and its clip list (`tape.json`), and
neither reaches into the other's private state.
"""

import errno
import json
import os
import re
import shutil
import time
from typing import Dict, List, Optional

from . import persist, stepmod
from .slots import SLOT_LETTERS
from .tape import NUM_TRACKS, Clip, compacted_address, compaction_rows

PROJECT_FORMAT = 1
PROJECT_DOC = "project.json"
PROJECTS_DIR = "projects"

#: the working state's own file in the state folder: the pointer to the open
#: project (`Document`), written on a 2 s timer when it changed
WORKING_DOC = "project.json"
WORKING_INTERVAL = 2.0
#: § 7.3's clock for the open project's own document (D97, #817): checked
#: every 10 s with the transport stopped (the STOP debounce), every 2 min
#: while it rolls (DDR-070), and written only when it changed
DOC_INTERVAL = 10.0
DOC_ROLLING_INTERVAL = 120.0

#: DDR-020: projects auto-name, always. Renaming is the companion app's job.
NAME_PREFIX = "PROJ_"
NAME_RE = re.compile(r"^%s(\d+)$" % NAME_PREFIX)

#: the instrument at boot — NEW puts it back (screen-sketches § FILE)
BOOT_TEMPO = {"bpm": 120.0, "click_level": 0.0, "click_pitch": 1600.0,
              "count_in": 0, "sync": "free", "link": True}
BOOT_BASE_BPM = 120.0
#: a §6.7 rack with nothing in it — what NEW puts on the master bus. `kind`
#: is not decoration: the host refuses a rack document without it.
EMPTY_RACK = {"format": 1, "kind": "rack", "name": "RACK", "tabs": [],
              "macros": []}
#: the EQ at which the host's five sections are a bit-exact bypass (F1)
FLAT_EQ = {"low": 0.0, "mid": 0.0, "high": 0.0, "mid_f": 1000.0,
           "mid_q": 0.7, "hp": 20.0, "lp": 20000.0}

#: pages that own 6 slots — the same three `app.py` lists, imported late to
#: keep this module free of a cycle
_SLOT_PAGES = ("SYNTH", "DRUM", "SEQ")
_INSTRUMENT_PAGES = ("SYNTH", "DRUM")


# ---------------------------------------------------------------------------
# where projects live
# ---------------------------------------------------------------------------

def projects_root(app) -> str:
    return os.path.join(app.library.root, PROJECTS_DIR)


def date_folder(when: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(when))


def list_projects(app) -> Dict[str, List[str]]:
    """`{date: [name, ...]}` — the two-pane browser's two panes, newest first."""
    root = projects_root(app)
    out: Dict[str, List[str]] = {}
    if not os.path.isdir(root):
        return out
    for date in sorted(os.listdir(root), reverse=True):
        d = os.path.join(root, date)
        if not os.path.isdir(d):
            continue
        names = sorted(n for n in os.listdir(d)
                       if os.path.exists(os.path.join(d, n, PROJECT_DOC)))
        if names:
            out[date] = names
    return out


def next_name(app) -> str:
    """`PROJ_0001`, `PROJ_0002`, … across the whole tree (DDR-020).

    The counter is the largest number already used anywhere, not per date:
    two projects saved on different days must not share a name, because the
    browser shows the name and not the folder above it.
    """
    n = 0
    for names in list_projects(app).values():
        for name in names:
            m = NAME_RE.match(name)
            if m:
                n = max(n, int(m.group(1)))
    return "%s%04d" % (NAME_PREFIX, n + 1)


def project_path(app, date: str, name: str) -> str:
    return os.path.join(projects_root(app), date, name)


def project_id(folder: str) -> str:
    """`<date>/<name>` — the id `GET /projects/<id>` names (#95).

    A project's identity is its two path components under `projects/`, so the
    id is derived from the folder rather than stored in the document: a folder
    someone moved is a different project, and the document would lie.
    """
    folder = folder.rstrip("/")
    name = os.path.basename(folder)
    date = os.path.basename(os.path.dirname(folder))
    return "%s/%s" % (date, name) if date and name else ""


# ---------------------------------------------------------------------------
# the transport, before anything that touches the tape
# ---------------------------------------------------------------------------

def halt(app):
    """Stop cleanly before saving, loading, bouncing or clearing.

    `tape_save`, `tape_load` and `clip_to_asset` are all refused while a take
    is open, and `Tape.stop()` is also what clears a pending COUNT-IN punch
    (stage H2's note) — a punch left armed would fire into the next project.
    """
    app.halt_transport()


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

def _track_block(app, t: int) -> dict:
    """One tape track: the UI's clips, the host's strip. Both sides (F2)."""
    host = app.native.track(t)
    return {
        "clips": [{"start": int(c.start), "len": int(c.length),
                   "gain": float(c.gain), "src_offset": int(c.src),
                   "src_track": (t if c.src_track is None
                                 else int(c.src_track))}
                  for c in app.tape.tracks[t]],
        "medium": None, "medium_frames": 0,
        "gain": float(host.get("gain", 1.0)),
        "pan": float(host.get("pan", 0.0)),
        "mute": bool(host.get("mute", False)),
        "solo": bool(host.get("solo", False)),
        "eq": dict(host.get("eq") or FLAT_EQ),
    }


def _instrument_block(app, pid: str) -> dict:
    """SYNTH or DRUM as a §7.2 patch, plus the two things a patch never carries.

    A patch does not hold the kit (the host says so in `load_patch`) and a §6.7
    rack holds macro *bindings*, not where the knob happens to be — and where
    it happens to be is the sound.
    """
    from .native_library import patch_from_state

    page = app.pages[pid]
    iid = page.instrument_id
    inst = app.native.instrument(iid)
    block = {"patch": patch_from_state(app.native, iid, "",
                                       page.state.octave),
             "macros": [float(v) for v in app.native.macros(iid)]}
    kit = inst.get("kit")
    if kit:
        block["kit"] = json.loads(json.dumps(kit))     # detached copy
    return block


def capture(app, name: str, date: Optional[str] = None,
            touch: bool = True) -> dict:
    """The whole instrument as a format-1 project document.

    `touch=False` is the autosave's (D97, #817): the active slot is read as it
    is now without marking the slot store dirty, or every 10 s check would
    rewrite the three slot files whether anything changed or not (#624).
    """
    native = app.native
    link = (native.state.get("tape") or {}).get("link") or {}

    slots = {}
    for pid in _SLOT_PAGES:
        store = app.pages[pid].slot_store
        if store is None:
            continue
        if touch:
            store.touch()                # the *active* slot is only recorded on change
            records = store.records
        else:
            records = store.records_now()
        slots[pid] = {"active": store.active,
                      "slots": {k: v for k, v in records.items()
                                if k in SLOT_LETTERS}}

    pages = {}
    for pid, page in app.pages.items():
        if getattr(page, "slot_store", None) is not None:
            continue                     # those ride in `slots`
        try:
            pages[pid] = page.snapshot()
        except Exception:
            pass

    return {
        "format": PROJECT_FORMAT,
        "kind": "project",
        "name": name,
        "date": date or date_folder(),
        "bpm": float(app.bpm),
        # E1: `base_bpm` is the tempo the medium is written at and nothing
        # moves it on its own. A project load is the one thing allowed to.
        "base_bpm": float(link.get("base_bpm", app.bpm)),
        "link": bool(link.get("on", True)),
        "metronome": {"level": float(app.metronome.level),
                      "pitch": float(app.metronome.pitch)},
        "page": app.page_id,
        "active_instrument": app.active_instrument_id,
        "instruments": {pid: _instrument_block(app, pid)
                        for pid in _INSTRUMENT_PAGES},
        # the master bus is a third rack (F1/F2) and is nobody's patch
        "master": {"fx": native.rack("master"),
                   "macros": [float(v) for v in native.macros("master")]},
        "slots": slots,
        "pages": pages,
        "mixer": app.mixer.snapshot(),
        "tape": {"ui": app.tape.snapshot(),
                 "tracks": [_track_block(app, t) for t in range(NUM_TRACKS)]},
    }


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

def open_folder(app) -> Optional[str]:
    """The open project's folder, if one is open and still there (#95)."""
    open_id = getattr(app, "open_project", None)
    if not open_id or "/" not in open_id:
        return None
    folder = os.path.join(projects_root(app), *open_id.split("/"))
    return folder if os.path.isdir(folder) else None


def save(app, name: Optional[str] = None, copy: bool = False) -> Optional[str]:
    """Write the project out; returns the folder (DDR-020, amended), or
    `None` when the tape could not go there and nothing was written.

    No player gesture calls this since D97 (#817): `ensure_open` does, for a
    session with no folder of its own (an old autosave, a first boot), and
    `copy` is what the phone's Duplicate will be. It means the project that
    is open (Elfy's answer (a) on #157):

    * a project is open → `project.json` is rewritten in its folder, and no
      audio moves at all — the takes are already in that folder;
    * none is (a session never saved or loaded) → the first SAVE makes the
      auto-named folder and **moves** the tape into it (`tape_dir` with
      `move`, a rename, so it costs nothing however long the tape is). From
      then on that folder is the open project and the next take records
      into it.

    `copy=True` is **save-as-copy**: a new auto-named folder with copies of
    the media, and the instrument stays on whatever it had open. `name` forces
    a new folder of that name (tests).

    **SAVE never copies over a folder's live media** (#411): the move is
    refused `exists` when the folder already holds media, and copying then
    put another folder's tape over the project's. Only old-format media (a
    pre-#297 save's copies, #410's first boot) are copied over; otherwise
    nothing is written and `None` says so.
    """
    halt(app)
    store = getattr(app, "project", None)
    folder = None if (copy or name) else open_folder(app)
    if folder is None:
        date = date_folder()
        name = name or next_name(app)
        folder = project_path(app, date, name)
    else:
        name = os.path.basename(folder)
        date = os.path.basename(os.path.dirname(folder))
    os.makedirs(folder, exist_ok=True)

    # An entered pattern rides to the box on a scratch file under $TMPDIR and
    # dies with the session (stage H2). Promoting it is what makes a project
    # reloaded tomorrow come back with the riff in it. A stepmod tab's entered
    # step values ride the same way and are promoted the same way (#60).
    promote_pattern(app, name)
    promote_step_patterns(app, name)

    doc = capture(app, name, date)
    if store is not None:
        if copy or not store.on_storage():
            store.copy_media(folder)          # save-as-copy, or a RAM tape
        elif os.path.abspath(folder) != store.tape_folder:
            if not _move_tape(store, folder, doc):
                return None
        _write_doc(folder, _with_media(doc, folder, store))
    else:
        _write_doc(folder, doc)
    if not copy:
        #: this is now the open project (#95) — set here rather than in the
        #: FILE page, so DDR-021's auto-save on the way into USB mode sets it
        #: too. A copy leaves the instrument on the project it had open.
        app.open_project = project_id(folder)
        _commit(app)                  # the folder a boot opens, now (#411)
    # § 3.8's SAVE trigger (#164), last: SAVE starts a compaction and never
    # waits for one. Since #297 the live medium is in the open project's
    # folder, so the compaction rewrites the very medium this document reads:
    # its result rewrites the document's clips too (`remap_saved`, #410).
    app.compact_dead_media()
    _said(app, "save", project_id(folder))
    return folder


def _said(app, op: str, pid: Optional[str]):
    """#853: the live stream's `project {op}` action (`ui_feed.UiFeed.project`)."""
    feed = getattr(app, "feed", None)
    if feed is not None:
        feed.project(op, pid)


def _write_doc(folder: str, doc: dict):
    _write_text(folder, _doc_text(doc))


def _doc_text(doc: dict) -> str:
    return json.dumps(doc, indent=1)


def _write_text(folder: str, text: str):
    """Temp file, then rename: a cut mid-write leaves the last one (§ 7.3)."""
    tmp = os.path.join(folder, PROJECT_DOC + ".tmp")
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, os.path.join(folder, PROJECT_DOC))


# ---------------------------------------------------------------------------
# always saved (D97, #817)
# ---------------------------------------------------------------------------

def _in_project(app) -> Optional[str]:
    """The open project's folder, when the tape is really living in it."""
    store = getattr(app, "project", None)
    folder = open_folder(app)
    if store is None or folder is None \
            or os.path.abspath(folder) != store.tape_folder:
        return None
    return folder


def autosave(app, promote: bool = False) -> bool:
    """The autosave's half of D97: this document, into the open folder.

    `Document.tick` calls it on § 7.3's clock and `flush` on the moments
    that already force-save (exit, power-off, USB storage); it writes only a
    document that differs from the last one written. `promote` is the
    pattern promotion SAVE used to do (`promote_pattern`), kept to those
    flush moments because it re-points the host's MOD assets, which is not
    something to do under a player's hands every ten seconds. True if it
    wrote.
    """
    store = getattr(app, "project", None)
    doc = getattr(app, "doc", None)
    folder = _in_project(app)
    if folder is None or doc is None:
        if doc is not None:
            doc.refused = False           # no project open, none to keep
        return False
    if store.exported():
        return False
    name = os.path.basename(folder)
    date = os.path.basename(os.path.dirname(folder))
    if promote:
        promote_pattern(app, name)
        promote_step_patterns(app, name)
    text = _doc_text(_with_media(capture(app, name, date, touch=False),
                                 folder, store))
    if text == doc.written and os.path.exists(
            os.path.join(folder, PROJECT_DOC)):
        doc.refused = False
        return False
    try:
        _write_text(folder, text)
    except OSError as exc:
        store.write_refused(PROJECT_DOC, exc)
        was_full = doc.refused and doc.full
        doc.refused = True
        doc.full = exc.errno in (errno.ENOSPC, errno.EDQUOT)
        watch = getattr(app, "storage_watch", None)
        if doc.full and not was_full and watch is not None:
            watch.card_full()   # D117: the trash question again, once a fill
        return False
    doc.written = text
    doc.refused = False
    # § 3.8's SAVE trigger (#164), which has no SAVE to ride on any more:
    # a document that moved, with the transport stopped, is the moment
    if not (app.tape.playing or app.tape.recording):
        app.compact_dead_media()
    return True


def _untouched_key(doc: dict) -> str:
    """What "nothing changed since NEW" compares: the document without where
    the player was standing (the page, FILE's own cursor) or its name."""
    doc = dict(doc)
    for k in ("name", "date", "page"):
        doc.pop(k, None)
    doc["pages"] = {k: v for k, v in (doc.get("pages") or {}).items()
                    if k != "FILE"}
    return json.dumps(doc, sort_keys=True)


def _mark_new(app):
    """Remember the instrument as NEW left it, for `untouched`."""
    doc = getattr(app, "doc", None)
    if doc is not None:
        doc.fresh_key = _untouched_key(capture(app, "", touch=False))


def untouched(app) -> bool:
    """Nothing recorded and nothing changed since NEW made this project."""
    store = getattr(app, "project", None)
    doc = getattr(app, "doc", None)
    if store is None or doc is None or doc.fresh_key is None \
            or _in_project(app) is None:
        return False
    if any(app.tape.tracks[t] for t in range(NUM_TRACKS)) \
            or any(store.media_frames()):
        return False
    return _untouched_key(capture(app, "", touch=False)) == doc.fresh_key


def _drop(app, folder: str):
    """Delete an untouched project's folder — only ever one under
    `projects/<date>/`, and only the files NEW and the tape put there."""
    root = os.path.abspath(projects_root(app))
    folder = os.path.abspath(folder)
    if os.path.dirname(os.path.dirname(folder)) != root:
        return
    shutil.rmtree(folder, ignore_errors=True)


def leave(app) -> Optional[str]:
    """The open project is being left (NEW, LOAD, power-off).

    An untouched one is answered with its folder, for the caller to delete
    once the tape has moved off it (`_drop`); any other gets its last
    document now, patterns promoted. None when there is nothing to delete.

    A last document the card refuses leaves nothing: the project stays
    open as it is, and the caller asks `not_saved` and goes no further
    (#954; D97 promises no work lost, and the folder holds an older one).
    """
    doc = getattr(app, "doc", None)
    if getattr(app, "project", None) is None or doc is None:
        return None
    gone = _in_project(app) if untouched(app) else None
    if gone is None:
        autosave(app, promote=True)
        if doc.refused:
            return None
    else:
        doc.refused = False               # an untouched one needs no document
    doc.fresh_key = None
    return gone


def not_saved(app) -> bool:
    """The open project's last document was refused by the card: `leave`
    left it open, and the player is told so (#954)."""
    doc = getattr(app, "doc", None)
    return doc is not None and doc.refused


#: D117 (#1006): the strip's mark while `not_saved`, (long, short)
NOT_SAVED = ("NOT SAVED", "NOT SAVED")
NOT_SAVED_FULL = ("NOT SAVED, CARD FULL", "NOT SAVED")


def not_saved_words(app):
    """The strip's mark for a refused autosave, on every page, until a write
    succeeds; None while the open project is saved."""
    if not not_saved(app):
        return None
    return NOT_SAVED_FULL if app.doc.full else NOT_SAVED


#: `fresh_key` once `power_off` has deleted an untouched project: not a key
#: anything compares equal to, but not None, so the working state still says
#: `untouched` and the next boot's project counts as a NEW too
DROPPED = ""


def power_off(app):
    """§ 7.4 step 1's half of D97: an untouched project does not outlive the
    instrument. The tape is pointed off its folder first (the working folder,
    empty), so nothing the audio process holds is under the delete, and the
    next boot finds no project open and makes one (`ensure_open`). A last
    document the card refuses keeps the project as it is, and the shutdown
    goes on (#954)."""
    store = getattr(app, "project", None)
    doc = getattr(app, "doc", None)
    if doc is not None and doc.fresh_key == DROPPED \
            and _in_project(app) is None:
        #: already done: the device calls this twice, `PowerPump` and then
        #: `main`'s `finally` on the SIGTERM, and a second `leave` would
        #: clear the mark the next boot's project is a NEW by (#955)
        return
    gone = leave(app)
    if gone is None or store is None or doc is None:
        return
    if store.open_folder(store.working, quiet=True):
        app.open_project = None
        doc.fresh_key = DROPPED
        doc.commit()
        _drop(app, gone)


def ensure_open(app) -> Optional[str]:
    """A boot: there is always a project open, its folder holding the tape.

    Nothing to do when the autosave named one and the tape opened in it. A
    session with no folder of its own — an autosave from before D97 (a
    session never saved), a pre-#297 one (#410), a first boot, or one whose
    project was deleted under it — becomes one now, the way its first SAVE
    used to: the working media move in. **It counts as a NEW only when the
    last session said so** (`untouched` in the working state, or no working
    state at all: a first boot): an old session with only its knobs turned
    is a project worth keeping. Called by the two `main`s, not
    `App.__init__`: a test's instrument makes no folder it did not ask for.
    """
    store = getattr(app, "project", None)
    doc = getattr(app, "doc", None)
    folder = _in_project(app)
    if folder is None:
        folder = save(app)
    else:
        _seed_written(app, folder)
    if folder is not None and store is not None and doc is not None \
            and doc.boot_untouched \
            and not any(app.tape.tracks[t] for t in range(NUM_TRACKS)) \
            and not any(store.media_frames()):
        _mark_new(app)
    return folder


def _seed_written(app, folder: str):
    """The document `folder` already holds, as the last one written: the
    first tick after a boot or a LOAD writes only what changed since (#955;
    #624's rule). A document the instrument does not capture the same way
    still differs, and is written once."""
    doc = getattr(app, "doc", None)
    if doc is None:
        return
    try:
        with open(os.path.join(folder, PROJECT_DOC)) as f:
            doc.written = f.read()
    except OSError:
        doc.written = None


def _with_media(doc: dict, where: str, store) -> dict:
    """`doc` with each track's `medium` record: the media now in `where`."""
    frames = store.media_frames()
    for t in range(NUM_TRACKS):
        rel = "tape-%d.wav" % (t + 1)
        if os.path.exists(os.path.join(where, rel)):
            doc["tape"]["tracks"][t]["medium"] = rel
            doc["tape"]["tracks"][t]["medium_frames"] = frames[t]
    return doc


def _move_tape(store, folder: str, doc: dict) -> bool:
    """The first SAVE of a session: the tape moves into `folder` (#297).

    `project.json` goes in **before** the move (#411), so a crash between the
    two leaves the audio in a folder the browser lists, not an orphan. A
    folder holding live media of its own is refused before anything is
    written; a refused move falls back to copying only when copying cannot
    overwrite a take — another filesystem (`move_failed`), or a folder of
    old-format media (`exists`, #410's first boot over an old autosave, whose
    live tape belongs in the project it had open).
    """
    if store.holds_live_media(folder):
        print("persist: not saved — %s holds media of another tape" % folder)
        return False
    _write_doc(folder, _with_media(doc, store.tape_folder, store))
    refused = store.move_to(folder)
    if refused in ("move_failed", "exists"):
        store.copy_media(folder)
    elif refused:
        return False
    return True


def remap_saved(app, medium: int, moves, frames: int) -> bool:
    """A compaction rewrote `medium` in the open project's folder: move the
    saved document's clips with it (#410).

    Since #297 the project does not hold a copy — its `project.json` and the
    live medium are the same audio — so without this a LOAD after SAVE reads
    `src_offset`s past the end of the compacted file: silence, or once the
    medium grows again, another take. The rule is `Tape.remap_medium`'s: a
    clip no kept run covers whole is parked at `frames`, so it reads silence
    rather than somebody else's take. Returns True if the document changed.
    """
    store = getattr(app, "project", None)
    folder = open_folder(app)
    if store is None or folder is None \
            or os.path.abspath(folder) != store.tape_folder:
        return False                  # the medium is not the open project's
    doc = read(folder)
    if doc is None:
        return False
    rows = compaction_rows(moves)
    end = max(0, int(frames))
    tracks = (doc.get("tape") or {}).get("tracks") or []
    for t, block in enumerate(tracks[:NUM_TRACKS]):
        for c in block.get("clips") or []:
            if int(c.get("src_track", t)) != medium:
                continue
            landed = compacted_address(rows, int(c.get("src_offset", 0)),
                                       int(c.get("len", 0)))
            c["src_offset"] = int(end if landed is None else landed)
            c["src_track"] = int(medium)
    if 0 <= medium < len(tracks) and tracks[medium].get("medium"):
        tracks[medium]["medium_frames"] = end
    tmp = os.path.join(folder, PROJECT_DOC + ".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=1)
        os.replace(tmp, os.path.join(folder, PROJECT_DOC))
    except OSError as exc:
        print("project: cannot rewrite %s after a compaction (%s)" % (folder, exc))
        return False
    return True

def promote_pattern(app, name: str) -> Dict[str, str]:
    """SAVE's half of DDR-037: every SEQ mirror becomes a library `.seq`.

    Since S13 there is one sequencer **per instrument**, so there are up to two
    patterns to write and they may not share a filename. Each is named
    `<project> <instrument>` — symmetric, because neither instrument's riff is
    the project's "real" one, and the name on the browser's row then says which
    instrument it came off. Returns `{instrument: filename}` for the ones that
    had a pattern to promote.
    """
    out: Dict[str, str] = {}
    for inst_id, seq in sorted(app.sequencers.items()):
        if seq is None or not seq.pattern:
            continue
        if not getattr(seq, "takes_pattern", False) or seq.library_folder is None:
            continue
        out[inst_id] = seq.promote_pattern(seq.library_folder,
                                           "%s %s" % (name, inst_id))
    return out


def mod_pattern_folder(app, engine: str):
    """The `mod/<box>/` folder a promoted step pattern goes in.

    It is the box's own folder in the MOD browser, which already exists as a
    row (`native_library._from_boxes`) and holds no items — `rescan_items`
    keeps `.patch`, `.rack` and `.seq` and not `.stepmod`, so the promoted
    file stays out of the browser. That is deliberate: a step pattern is
    content a patch names, not a modulator you can drop on a tab.
    """
    for f in app.library.list("mod"):
        if (f.manifest or {}).get("id") == engine:
            return f
    return None


def promote_step_patterns(app, name: str,
                          only: Optional[str] = None) -> Dict[str, str]:
    """#60's half of DDR-037: every *entered* stepmod pattern becomes a file.

    The host's own document says which tabs need it — an entered pattern's
    asset path is the scratch file this process wrote, and a loaded one's is
    a library item already. `only` narrows it to one instrument, which is what
    promoting a patch wants: SHIFT+DRUM saves DRUM's sound, never SYNTH's.
    Returns `{"<instrument>.<tab>": filename}` for the ones that were promoted.
    """
    from . import patch as P                                 # noqa: PLC0415

    out: Dict[str, str] = {}
    wanted = _INSTRUMENT_PAGES if only is None else (str(only).upper(),)
    for inst_id in wanted:
        tabs = (app.native.mod_rack(inst_id).get("tabs") or [])
        for i, t in enumerate(tabs):
            path = (t.get("assets") or {}).get(stepmod.ASSET)
            if not stepmod.is_scratch(path):
                continue
            values = stepmod.read_values(path)
            if not values:
                continue
            folder = mod_pattern_folder(app, str(t.get("engine") or ""))
            if folder is None:
                continue
            instance = str(t.get("instance") or stepmod.KIND)
            fname = P.slug("%s %s %s" % (name, inst_id, instance)) + stepmod.SUFFIX
            os.makedirs(folder.path, exist_ok=True)
            saved = stepmod.write_values(os.path.join(folder.path, fname),
                                         values, name)
            msg = {"type": "set_mod_tab_engine", "index": i,
                   "engine": str(t.get("engine") or ""),
                   "assets": {stepmod.ASSET: saved}}
            if t.get("min_version"):
                msg["min_version"] = str(t["min_version"])
            if app.native.mod_edit(inst_id, msg).get("type") == "error":
                continue
            out["%s.%s" % (inst_id, instance)] = fname
    return out


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

def read(folder: str) -> Optional[dict]:
    try:
        with open(os.path.join(folder, PROJECT_DOC)) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def load(app, folder: str) -> bool:
    """Put the instrument back where the document says. Returns False if it
    is not a project."""
    doc = read(folder)
    if doc is None:
        return False
    # The tape first, and nothing else unless it opened (#411): a refused
    # `tape_dir` used to be ignored, so the project's clips played over the
    # previous folder's media with the project marked open — and the next
    # SAVE copied that other tape over the project's own.
    halt(app)
    store = getattr(app, "project", None)
    gone = leave(app)
    if not_saved(app):
        return False                  # the open project stays (#954)
    if gone is not None and os.path.abspath(gone) == os.path.abspath(folder):
        gone = None                   # LOAD of the open project: it stays
    if store is not None and not store.open_folder(folder):
        if gone is not None:
            _mark_new(app)            # still NEW
        return False
    if gone is not None:
        _drop(app, gone)
    apply(app, doc, folder, open_tape=False)
    #: after `apply`, not before: a document that is not a project leaves the
    #: id alone. Set here and not in `apply`, because `rewalk` (§ 4.2's host
    #: restart) applies a document the UI captured from itself and must not
    #: move which project is open (#95).
    app.open_project = project_id(folder)
    if getattr(app, "doc", None) is not None:
        app.doc.made_new = False      # opened by LOAD, not made by NEW
    _seed_written(app, folder)
    _commit(app)                      # the folder a boot opens, now (#411)
    _said(app, "load", app.open_project)
    return True


def apply(app, doc: dict, folder: str = "", open_tape: bool = True):
    """Walk a format-1 project document into the UI and the host.

    Split out of `load` so the same walk serves a **restarted host**
    (`rewalk`, `device-os-spec.md` § 4.2): the document is then one `capture`
    took from the UI's own state mirror rather than one read off disk, and
    `folder` is wherever the tape already lives.

    The media are not named track by track any more (#297): `folder` is
    opened as a whole, in place (`ProjectStore.open_folder`), and a track's
    `medium` field is only a record of which files the folder holds.
    `open_tape=False`: the caller has opened it already (`load`, #411).
    """
    halt(app)
    native = app.native

    # Tempo first, and `base_bpm` with it: the tape's playback rate is
    # `bpm / base_bpm` (DDR-059) and the media about to be loaded were written
    # at the stored one.
    app.bpm = float(doc.get("bpm", app.bpm))
    native.tempo(bpm=app.bpm, link=bool(doc.get("link", True)),
                 base_bpm=float(doc.get("base_bpm", app.bpm)))

    # **Which instrument is active, before the slots and not after** (S13).
    # `SeqPage` shows and edits the *active* instrument's sequencer, so its
    # own slot record is applied to whichever one that is — and until this
    # moved up, that was whatever the player happened to be on before the
    # load, so a project made on DRUM came back with its SEQ bank on SYNTH.
    # The two instrument pages address themselves by `instrument_id` and do
    # not care when this happens; it is set again at the end because `goto`
    # moves it (a page key *is* the instrument selector, DDR-034).
    active = doc.get("active_instrument")
    if active in app.instruments:
        app.active_instrument_id = active
        native.set_active(active)

    # The six slots per page *are* the instruments: the active slot's record
    # carries a whole §7.2 patch, so restoring it loads engine, rack and MOD.
    # `_SLOT_PAGES` order, not the document's: SEQ is applied last on purpose.
    # SYNTH's and DRUM's records each carry a §7.2 patch with that
    # instrument's own `seq` block, and the SEQ page's bank is a
    # sequencer-only slot over the top of whichever instrument is active
    # (S13). A document whose keys arrived in another order used to decide
    # which of the two won.
    blocks = doc.get("slots") or {}
    for pid in _SLOT_PAGES:
        block = blocks.get(pid)
        if block is None:
            continue
        page = app.pages.get(pid)
        store = getattr(page, "slot_store", None)
        if store is None:
            continue
        store.records = {k: v for k, v in (block.get("slots") or {}).items()
                         if k in SLOT_LETTERS}
        store.active = block.get("active", "A")
        if store.active not in SLOT_LETTERS:
            store.active = "A"
        page.state.slot_letter = store.active
        record = store.records.get(store.active)
        if record:
            page.restore(record)

    # ...and then the two things a patch does not carry.
    for pid, block in (doc.get("instruments") or {}).items():
        page = app.pages.get(pid)
        if page is None:
            continue
        iid = page.instrument_id
        if block.get("kit"):
            native.load_kit(iid, block["kit"])
        for i, v in enumerate((block.get("macros") or [])[:8]):
            native.set_macro(iid, i, float(v))

    master = doc.get("master") or {}
    if master.get("fx"):
        native.load_rack("master", master["fx"])
    for i, v in enumerate((master.get("macros") or [])[:8]):
        native.set_macro("master", i, float(v))

    for pid, data in (doc.get("pages") or {}).items():
        page = app.pages.get(pid)
        if page is None or getattr(page, "slot_store", None) is not None:
            continue
        try:
            page.restore(data)
        except Exception:
            pass

    # The click has two homes — TEMPO's two knobs and `app.metronome` — and
    # the document carries the second one, so it is applied after the page.
    met = doc.get("metronome") or {}
    if "level" in met:
        app.metronome.level = float(met["level"])
    if "pitch" in met:
        app.metronome.pitch = float(met["pitch"])
    tempo_page = app.pages.get("TEMPO")
    if tempo_page is not None:
        # `set_value` clamps, and its `on_change` clamps the metronome too: a
        # hand-edited document's level past 1.0 is not played or drawn (#1123)
        tempo_page.p_level.set_value(app.metronome.level)
        tempo_page.p_pitch.set_value(app.metronome.pitch)

    app.mixer.restore(doc.get("mixer") or {})

    # The media first, then the clips that read them (PROTOCOL.md § tape_dir).
    tape = doc.get("tape") or {}
    store = getattr(app, "project", None)
    if folder and store is not None and open_tape:
        if getattr(store, "parked", None) is not None:
            # #763: a restarted host while USB storage has (or had) `/data`:
            # the tape stays on scratch, and only `UsbPump`'s guarded unpark
            # opens the real folder
            store.repark()
        else:
            store.open_folder(folder)
    for t, block in enumerate((tape.get("tracks") or [])[:NUM_TRACKS]):
        clips = []
        for c in block.get("clips") or []:
            n = int(c.get("len", 0))
            if n <= 0:
                continue
            clips.append(Clip(int(c.get("start", 0)), n, float(c.get("gain", 1.0)),
                              int(c.get("src_offset", c.get("start", 0))),
                              int(c.get("src_track", t))))
        app.tape.tracks[t] = clips
        # the host's half of the mixer (F2): nothing else writes these
        native.mixer(track=t, gain=float(block.get("gain", 1.0)),
                     pan=float(block.get("pan", 0.0)),
                     mute=bool(block.get("mute", False)),
                     solo=bool(block.get("solo", False)))
        native.eq(t, **{k: float(v) for k, v in
                        (block.get("eq") or FLAT_EQ).items()})
    app.tape.restore(tape.get("ui") or {})

    page_id = doc.get("page")
    if page_id in app.pages:
        app.goto(page_id)
        if active in app.instruments:
            app.active_instrument_id = active
            native.set_active(active)

    resync(app)


def rewalk(app, doc: Optional[dict] = None) -> bool:
    """The host restarted: push the whole instrument back down (§ 4.2).

    `device-os-spec.md` § 4.2 — "the UI keeps its own state, reconnects with
    backoff, re-sends `hello`, `load_*` for every slot, `tape_load`,
    `set_clips`, the mixer — **the same walk a project load does**". This is
    that sentence: `capture` + `apply`, with the tape reopened in the folder
    it was living in (`tape_dir`, #297) — the open project's, or the working
    folder `app/persist.py` keeps (§ 7.3).

    `doc` must be captured **before** the connection dies, because `capture`
    reads host-owned state (the racks, the MOD routing, the macros, the tape
    strips) out of `NativeAudio.state` — the UI's mirror, and once the socket
    is re-opened that mirror is the *new* host's. `main.py` keeps a rolling
    one; passing `None` captures now, which is right only if the mirror is
    still the dead host's.

    Since the tape is on storage (C1), a restart loses at most the take in
    flight's last range or two (`tape-streaming-design.md` § 3.3), not the
    tape: every finished take is already in the folder this reopens.
    """
    if doc is None:
        doc = capture(app, "")
    store = getattr(app, "project", None)
    apply(app, doc, store.tape_folder if store is not None else "")
    return True


def resync(app):
    """Push everything down again.

    `App`'s host sync is a diff against a fingerprint, and a project load
    moves the *host* as well as the UI — so a value that happens to land back
    on the one the fingerprint remembers would never be sent. Forgetting the
    fingerprints is the whole of it.

    The autosave's per-track dirty flags (#93) are the same kind of memory
    about the same moment, and LOAD / NEW / `rewalk` all arrive here, so they
    are forgotten in the same breath.
    """
    store = getattr(app, "project", None)
    if store is not None:
        store.forget_media()
    #: and the playhead this UI last left behind (#99): a load moves it, and a
    #: value that happened to land back on the remembered one would be read as
    #: "the host moved it, not us" and adopted away instead of sent
    app.resync_host()


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------

def new(app, name: Optional[str] = None) -> Optional[str]:
    """NEW: back to the boot state (screen-sketches § FILE), in a new
    project whose folder exists from this moment (D97, #817). Returns it.

    Not "a blank document loaded" — the pages are built once and kept forever
    (DDR-028), so this walks the same pieces `App.__init__` sets up and puts
    each one back to what it was before anything was played.

    `name`: the name menu's pick (#818); until it lands, DDR-020's auto-name.
    """
    halt(app)
    native = app.native

    # The project being left gets its last document, or — untouched since
    # its own NEW — is deleted once the tape is off it. Then an empty folder
    # and the tape in it (#297): nothing of the old tape plays after NEW, and
    # the project's folder keeps its own files.
    # (`project_store`, because the slot loops below reuse `store`.)
    project_store = getattr(app, "project", None)
    gone = leave(app)
    if not_saved(app):
        return None                   # the open project stays (#954)
    folder = None
    if project_store is not None:
        folder = project_path(app, date_folder(), name or next_name(app))
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            project_store.write_refused(folder, exc)
        if not project_store.open_folder(folder):
            folder = None             # the tape stays where it was
            project_store.fresh_working()
    if gone is not None and folder is not None:
        _drop(app, gone)
    for t in range(NUM_TRACKS):
        native.set_clips(t, [])
        native.mixer(track=t, gain=1.0, pan=0.0, mute=False, solo=False)
        native.eq(t, **FLAT_EQ)
    app.tape.reset()
    app.mixer.reset()

    for pid in _SLOT_PAGES:
        store = app.pages[pid].slot_store
        if store is None:
            continue
        store.records.clear()
        store.active = "A"
        app.pages[pid].state.slot_letter = "A"
    for pid in _SLOT_PAGES:
        reset = getattr(app.pages[pid], "reset", None)
        if reset is not None:
            reset()
    for pid in _SLOT_PAGES:
        store = app.pages[pid].slot_store
        if store is not None:
            store.touch()

    native.load_rack("master", EMPTY_RACK)
    for i in range(8):
        native.set_macro("master", i, 0.0)

    app.pages["TEMPO"].restore(dict(BOOT_TEMPO))
    app.bpm = BOOT_TEMPO["bpm"]
    native.tempo(bpm=app.bpm, link=BOOT_TEMPO["link"], base_bpm=BOOT_BASE_BPM)

    app.active_instrument_id = "SYNTH"
    native.set_active("SYNTH")
    #: the new project is the open one (#95). RESET goes through here too,
    #: so the old id cannot survive a reset-to-defaults.
    app.open_project = project_id(folder) if folder else None
    resync(app)
    if project_store is not None:
        if folder is not None:
            #: before the commit: the working state it writes carries the
            #: mark, so a cut right after NEW boots it untouched (#955)
            _mark_new(app)
            if getattr(app, "doc", None) is not None:
                app.doc.made_new = True   # the live `project.new` (#1179)
        _commit(app)                         # the folder a boot opens, now (#411)
        if folder is not None:
            autosave(app)                    # the folder is the project at once
    _said(app, "new", app.open_project)
    return folder


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------

def taken_names(app) -> List[str]:
    """Every project name on the card, any date: what the name menu never
    offers (D98, #818)."""
    return [n for names in list_projects(app).values() for n in names]


def rename(app, name: str) -> Optional[str]:
    """FILE › PROJECT › RENAME (#818, D98): the open project's folder takes
    `name`, under the same date. Returns the new folder, or None when nothing
    changed (no project open, the name taken, the audio process refused).

    The phone's rename of the open project, from the instrument (#821, D99):
    `library {op: rename}` on the folder the tape is in, which the audio
    process renames under its I/O lock with every medium's handle following.
    Its `tape_dir_renamed` push re-points the UI on the next poll; this does
    it now (`App.tape_dir_renamed`, which the push then finds done), so the
    document goes into the new folder before anything else is written.
    """
    halt(app)
    store = getattr(app, "project", None)
    old = _in_project(app)
    if store is None or old is None or not name or "/" in name:
        return None
    new = os.path.join(os.path.dirname(old), name)
    if os.path.lexists(new):
        return None
    reply = app.native.send({"type": "library", "op": "rename",
                             "path": os.path.relpath(old, app.library.root),
                             "name": name})
    if reply.get("type") == "error":
        print("persist: not renamed to %s (%s)"
              % (name, reply.get("message") or reply.get("code")))
        return None
    app.tape_dir_renamed({"type": "tape_dir_renamed", "from": old, "to": new})
    autosave(app)                        # its document carries the new name
    return new


# ---------------------------------------------------------------------------
# bounce
# ---------------------------------------------------------------------------

def bounce_span(app):
    """`(start, len)` — the timeline trimmed to where clips exist (§ FILE)."""
    starts = [c.start for track in app.tape.tracks for c in track]
    ends = [c.end for track in app.tape.tracks for c in track]
    if not ends:
        return 0, 0
    start = max(0, int(min(starts)))
    return start, int(max(ends)) - start


def bounce(app, master_fx: bool = False, file: Optional[str] = None,
           job: bool = False):
    """FILE › BOUNCE: the project rendered offline to one WAV.

    `clip_to_asset {track: "all"}` is §5.4's mixdown — the per-track EQ,
    mute/solo, gain and pan, then DRIVE, LEVEL, BAL and the limiter. COMP and
    the master rack are deliberately not in it (F1); `master_fx` is the
    sketch's SHIFT+ENC4 switch and turns the rack back on, latency and all
    (the host renders past the window and drops the rack's own delay).

    `job` (#920, D98) starts the render and returns: `app.bounce_job` holds
    it until the host's `bounce` push ends it (`App._native_bounce`), and a
    key played meanwhile asks `CANCEL BOUNCE TO PLAY?`. The job is set before
    the message goes, because the push can arrive in the same read as the
    reply.
    """
    halt(app)
    start, length = bounce_span(app)
    if length <= 0:
        return None
    if file is None:
        file = app.sampler_file("bounce")
        if file is None:
            return None                   # the card refused the folder (#1123)
    if not job:
        return app.native.clip_to_asset("all", start, length, file=file,
                                        master_fx=bool(master_fx))
    app.bounce_job = {"path": file, "frames": length}
    reply = app.native.clip_to_asset("all", start, length, file=file,
                                     master_fx=bool(master_fx), job=True)
    if reply is None and app.bounce_job is not None \
            and app.bounce_job.get("path") == file:
        app.bounce_job = None             # refused: no job, no push to wait for
    return reply


# ---------------------------------------------------------------------------
# the open project's owner (#1017)
# ---------------------------------------------------------------------------

def _commit(app):
    doc = getattr(app, "doc", None)
    if doc is not None:
        doc.commit()


class Document:
    """The open project's document half: what it remembers, and the working
    state's pointer to it (`state/project.json`). `app.doc` (#1017).

    Moved here from `persist.ProjectStore`, which keeps the tape's folder and
    its clip list (`tape.json`): since D97 (#817) the working state is the
    boot's pointer to the open project, not a session of its own, and the
    document was written by two modules reaching into each other.

    `project.json` holds BPM, the page, the active instrument, the transport
    and loop, the mixer, one snapshot per non-slot page, and `open_project`
    — the id of the project this state points at (#95), which is the one
    field a device service reads out of it. It is written on a 2 s timer,
    only when it changed (#624); the open project's own document on § 7.3's
    clock (`DOC_INTERVAL`), only when it changed.
    """

    def __init__(self, app, state_dir: str):
        self.app = app
        self.state_dir = state_dir
        self._due = 0.0
        #: the text of the last `project.json` written, so the timer skips an
        #: unchanged one (#624); None = nothing written yet
        self._working_text: Optional[str] = None
        #: the open project's document as last written into its folder
        #: (`autosave`)
        self.written: Optional[str] = None
        #: the card refused that document's last write, so the folder holds
        #: an older one: NEW, LOAD and power-off do not leave it (#954)
        self.refused = False
        #: …and that refusal was a full card (ENOSPC), so the strip says
        #: `NOT SAVED, CARD FULL` (D117, #1006); read only with `refused`
        self.full = False
        #: when the document clock last checked; `retry_now` rewinds it
        self.checked = float("-inf")
        #: the instrument as NEW left it (`untouched`), or None once anything
        #: could have changed it. It rides in `project.json` as `untouched`,
        #: so a restart keeps an untouched NEW untouched.
        self.fresh_key: Optional[str] = None
        #: FILE › PROJECT › NEW made the open project in this session: the
        #: live `project.new` (#1179). Never saved, so a boot starts False;
        #: `fresh_key` cannot say it, as a boot's own project sets that too.
        self.made_new = False
        #: what the last boot's `project.json` said about that
        #: (`load_working`); True with no file at all, a first boot
        #: (`ensure_open`)
        self.boot_untouched = True

    @property
    def path(self) -> str:
        return os.path.join(self.state_dir, WORKING_DOC)

    # -- capture -----------------------------------------------------------
    def snapshot(self) -> dict:
        app = self.app
        pages = {}
        for pid, page in app.pages.items():
            if getattr(page, "slot_store", None) is not None:
                continue        # slot pages persist through their SlotStore
            try:
                pages[pid] = page.snapshot()
            except Exception:
                pass
        return {
            "bpm": float(app.bpm),
            "page": app.page_id,
            "active_instrument": app.active_instrument_id,
            #: #95: which saved project this working state came from, as
            #: `"<date>/<name>"` — `null` for a session that was never saved
            #: or loaded. `arbutus-net` reads this field and nothing else out
            #: of here, so that `GET /projects/<id>/bounce` can tell whether
            #: the id the phone named is the tape the device actually has.
            "open_project": app.open_project,
            #: D97 (#817): the open project is one NEW made and nothing has
            #: been done to since — leaving it deletes it
            "untouched": self.fresh_key is not None,
            #: #956: the file job the open project was last written for
            #: (`App.set_file_job`); `arbutus-net`'s `FLUSHED_KEY`
            "flushed_job": getattr(app, "flushed_job", None),
            "tape": app.tape.snapshot(),
            "mixer": app.mixer.snapshot(),
            "pages": pages,
        }

    # -- write -------------------------------------------------------------
    def tick(self, now: Optional[float] = None):
        now = now if now is not None else time.monotonic()
        store = self.app.project
        if store.exported():
            return                 # `_due` stays put: the remount's first tick writes
        if now >= self._due:
            self._due = now + WORKING_INTERVAL
            self.save_working(if_changed=True)
        # the interval is chosen at each check, not at the last one, so a STOP
        # brings the 10 s back at once rather than after the 2 min. Compared
        # as `last + interval`, the sum a caller schedules by: `now - last`
        # can round a hair under the interval (`(t + 120.0) - t` is
        # 119.99999999999989 at t = 957.209516383), so a tick exactly on
        # time wrote nothing (#1069)
        tape = getattr(self.app, "tape", None)
        rolling = bool(getattr(tape, "playing", False)
                       or getattr(tape, "recording", False))
        if now >= self.checked + (DOC_ROLLING_INTERVAL if rolling
                                  else DOC_INTERVAL):
            self.checked = now
            self._autosave()

    def retry_now(self):
        """The next tick checks the document whatever the clock says (a
        refused write, once the trash has made room)."""
        self.checked = float("-inf")

    def _autosave(self, promote: bool = False):
        if getattr(self.app, "open_project", None) is None:
            return
        autosave(self.app, promote=promote)

    def save_working(self, if_changed: bool = False):
        """Write `project.json`. The 2 s timer passes `if_changed`: it writes
        only a snapshot that differs from the last one written (or a file that
        has gone), not ~43 000 identical ones a day onto the card (#624)."""
        store = self.app.project
        if store.exported():
            return
        text = json.dumps(self.snapshot(), indent=1)
        if (if_changed and text == self._working_text
                and os.path.exists(self.path)):
            return
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                f.write(text)
            os.replace(tmp, self.path)
        except OSError as exc:
            store.write_refused(WORKING_DOC, exc)
            return
        store.write_landed()
        self._working_text = text

    def commit(self):
        """Both working files now: the tape just changed folders (#411).

        A boot trusts `project.json`'s `open_project` for where the tape is
        and `tape.json` for the clips over it. Left to the 2 s tick and the
        next STOP, a crash in between booted one folder's media under another
        session's clips. Kilobytes, and nothing else is waiting on disk.
        """
        self.save_working()
        self.app.project.save_audio(force=True)

    def flush(self):
        self.save_working()
        self.app.project.save_audio()
        self._autosave(promote=True)

    # -- read --------------------------------------------------------------
    def boot(self):
        """A boot: the tape where the last session left it, then the clips,
        then this."""
        store = self.app.project
        store.open_folder(self.boot_folder())
        store.load_audio()
        self.load_working()

    def boot_folder(self) -> str:
        """The open project's folder if the working state names one that is
        still there, otherwise the working folder.

        Since #297 a session with a project open has been recording into that
        project's folder, so that is where the takes the clip list reads are.

        **Except over a pre-#297 autosave** (#410): then the working folder's
        old-format media *are* the live tape — every take since the last SAVE
        is in them and in nothing else, because SAVE used to copy — and
        opening the project's folder would delete them (`open_folder` leaving
        the working folder). The working folder opens, converting them, and
        `open_project` stays as the working state says.
        """
        working = self.app.project.working
        if persist.ProjectStore.holds_old_media(working):
            return working
        try:
            with open(self.path) as f:
                open_id = (json.load(f) or {}).get("open_project")
        except (OSError, ValueError, AttributeError):
            return working
        if not isinstance(open_id, str) or not open_id:
            return working
        folder = os.path.join(projects_root(self.app), *open_id.split("/"))
        return folder if os.path.isdir(folder) else working

    @staticmethod
    def saved_active_instrument(state_dir: str) -> Optional[str]:
        """The instrument `project.json` says was active, or None (#874).

        Read before the slot pages restore: the SEQ page's record goes onto
        the *active* instrument's sequencer, and `load_working` sets that
        only after them, so a boot left on DRUM put DRUM's bank on SYNTH.
        """
        try:
            with open(os.path.join(state_dir, WORKING_DOC)) as f:
                active = (json.load(f) or {}).get("active_instrument")
        except (OSError, ValueError, AttributeError):
            return None
        return active if isinstance(active, str) else None

    def load_working(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        self.boot_untouched = bool(data.get("untouched", False))
        app = self.app
        app.bpm = float(data.get("bpm", app.bpm))
        open_project = data.get("open_project")
        app.open_project = open_project if isinstance(open_project, str) and open_project else None
        app.tape.restore(data.get("tape") or {})
        app.mixer.restore(data.get("mixer") or {})
        for pid, pdata in (data.get("pages") or {}).items():
            page = app.pages.get(pid)
            if page is None:
                continue
            try:
                page.restore(pdata)
            except Exception:
                pass
        active = data.get("active_instrument")
        if active in app.instruments:
            app.active_instrument_id = active
        page_id = data.get("page")
        if page_id in app.pages:
            app.goto(page_id)
            if active in app.instruments:
                app.active_instrument_id = active
        # #624: `goto` tells the host only for SYNTH and DRUM, and notes go
        # out with no instrument, so a boot on TRACK after DRUM played SYNTH.
        app.native.set_active(app.active_instrument_id)
        return True
