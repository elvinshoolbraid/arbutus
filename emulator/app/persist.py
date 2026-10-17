"""Project persistence (emulator-spec §5, user story 18): the tape's half.

`emulator/state/` holds:
    project.json   the working state's pointer to the open project — written
                   by `project_file.Document` (`app.doc`) since #1017, which
                   owns the document half; this file keeps the tape's
    tape.json      the clip lists: start, length, gain and where each clip
                   reads in the medium
    tape-<n>.wav   each track's medium (n = 1..8), **while no project is
                   open** — the audio process writes it here as the take
                   happens (C1 step 3b, #297)

**The media are not saved by this file any more.** Since C1 step 3b the audio
process writes every take straight into the folder the tape lives in
(`tape_dir`, `PROTOCOL.md`): this working folder for a session that was never
saved, the open project's own folder once a SAVE has moved it there or a LOAD
has opened one. So STOP writes `tape.json` (kilobytes) and no audio, and
`tape_folder` is the one thing here that knows where the audio is.

Slot memories are written separately by `app/slots.py`. The clip list is
written only when the clip layout actually changed and only at safe moments —
on STOP and on exit (DDR-029, debounced like slots).

**Since D97 (#817) there is always a project open** and the working state
is the boot's pointer to it, not a session of its own: `Document.tick`
writes the open project's whole document into its folder, so the folder is
the project with no SAVE. The working folder only ever holds media on the
way into a project: an old autosave's, or a refused move's.
"""

import json
import os
import re
import shutil
from typing import List, Optional, Tuple

from . import tape as T

AUDIO_FILE = "tape.json"

#: the audio process's names for the media and their peaks caches, 1..8
MEDIUM_RE = re.compile(r"^tape-(\d+)\.(wav|peaks)$")
#: an old-format medium moved aside while it is converted (#297 item 6) — a
#: name the audio process never opens, so the folder can be opened around it
ASIDE_SUFFIX = ".old.wav"


def medium_name(track: int) -> str:
    """`tape-1.wav` for track 0: the audio process's name (`tape_dir`)."""
    return "tape-%d.wav" % (int(track) + 1)


def is_old_medium(path: str) -> bool:
    """Is this a WAV `tape_save` wrote as a copy — every project before #297?

    That writer puts `data` straight after a 16-byte `fmt ` chunk, so the
    payload starts at byte 44. One of the audio process's own media has a
    4 KiB header (`fact`, a `JUNK` pad, then `data` at 4088) and is opened in
    place; an old one is refused by `tape_dir` and has to be converted.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(44)
    except OSError:
        return False
    return (len(head) == 44 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"
            and head[36:40] == b"data")


def old_medium_frames(path: str) -> int:
    """Frames in an old-format medium: its `data` size over its block size
    (the header `is_old_medium` checks), or -1 if it cannot be read."""
    try:
        with open(path, "rb") as f:
            head = f.read(44)
    except OSError:
        return -1
    if len(head) < 44:
        return -1
    block = int.from_bytes(head[32:34], "little")
    size = int.from_bytes(head[40:44], "little")
    return size // block if block else -1


def _track_signature(tape, t: int) -> tuple:
    """One track's clip geometry — the proxy for "its medium changed".

    `c.src` is where the clip reads in that track's medium, so a new take —
    which always appends to the medium and always reads at a new offset —
    always moves this tuple. The one thing it cannot see is medium *past* the
    last clip that reads it (an undone take leaves audio nothing references);
    that tail is unplayable by definition, and not writing it is the point.
    """
    return tuple((c.start, c.length, round(c.gain, 6), c.src,
                  -1 if c.src_track is None else int(c.src_track))
                 for c in tape.tracks[t])


class ProjectStore:
    def __init__(self, app, state_dir: str):
        self.app = app
        self.state_dir = state_dir
        #: per track, the clip geometry as it was when that track's medium was
        #: last written, and the `used_frames` the host reported for it. #93:
        #: a STOP after one take used to re-write every track with clips
        #: (~0.9 s each on the CM4's eMMC, the screen frozen for all of it);
        #: § 7.3 asks for dirty tracks only, and this is the pair that says
        #: which are dirty. `None` = never written, so the first save writes it.
        self._track_sig: list = [None] * T.NUM_TRACKS
        self._medium_frames: list = [0] * T.NUM_TRACKS
        #: the last refused write's reason, so a card that stays full is one
        #: journal line and not one every 2 s
        self._last_refusal = ""
        #: where the tape lives: this folder until a SAVE moves it into a
        #: project or a LOAD opens one (C1 step 3b, #297)
        self.tape_folder = os.path.abspath(state_dir)
        #: #303: the folder the audio process was pointed at instead while USB
        #: storage mode has `/data`, or None. `tape_folder` keeps the real one.
        self.parked: Optional[str] = None

    # -- paths -------------------------------------------------------------
    @property
    def audio_path(self) -> str:
        return os.path.join(self.state_dir, AUDIO_FILE)

    def medium_path(self, track: int) -> str:
        """Where track `track`'s medium is now — inside `tape_folder`."""
        return os.path.join(self.tape_folder, medium_name(track))

    @property
    def working(self) -> str:
        return os.path.abspath(self.state_dir)

    # -- where the tape lives (C1 step 3b, #297) ---------------------------
    def open_folder(self, folder: str, quiet: bool = False) -> bool:
        """LOAD, NEW, a boot: the tape becomes the media already in `folder`.

        Opened **in place** — nothing is read into memory and nothing copied
        (`tape_dir` without `move`). Two things happen around it:

        * **old media are converted first** (#297 item 6). A project saved
          before this change holds `tape_save`'s 44-byte-header WAVs, which
          the audio process refuses to open in place. Each is moved aside,
          the folder is opened without it, and `tape_load` writes it back in
          as a medium of the new kind — one pause a track, the first time.
          The aside copy goes only once its load said ok, so a failure loses
          nothing. The working folder's old autosave named its files from 0
          (`tape-0.wav` for track 0); a project's from 1.
          It goes only if the load carried **every** frame (#410): a file
          longer than the ruler converts cut short, and its aside stays.
        * leaving the **working** folder for a project leaves the unsaved
          session's media behind, and nothing can play them again — so they
          are removed. (A LOAD has always replaced an unsaved session.) An
          aside that did not convert is not a medium and stays.
        """
        native = self.app.native
        folder = os.path.abspath(folder)
        first = 0 if folder == self.working else 1
        old = self._set_aside(folder, first)
        reply = native.tape_dir(folder)
        if reply.get("type") == "error":
            if not quiet:
                print("persist: cannot open the tape in %s (%s)"
                      % (folder, reply.get("message") or reply.get("code")))
            for _t, aside, original in old:
                try:
                    os.replace(aside, original)
                except OSError:
                    pass
            return False
        previous, self.tape_folder = self.tape_folder, folder
        for t, aside, _original in old:
            done = native.tape_load(t, aside)
            if done.get("type") == "error":
                print("persist: kept %s — it would not convert (%s)"
                      % (aside, done.get("message") or done.get("code")))
                continue
            whole = old_medium_frames(aside)
            if int(done.get("frames", -1)) != whole:
                # `tape_load` answers ok with fewer frames when the file is
                # longer than the ruler (or the pool ran dry): the aside is
                # then the only full copy, and it stays (#410)
                print("persist: kept %s — %s of its %d frames converted"
                      % (aside, done.get("frames"), whole))
                continue
            try:
                os.remove(aside)
            except OSError:
                pass
        if previous == self.working and folder != self.working:
            self.clear_working()
        return True

    def _set_aside(self, folder: str, first: int) -> List[Tuple[int, str, str]]:
        """Old-format media in `folder`, renamed out of the audio process's way.

        Returns `(track, aside path, original path)` for each. A track number
        outside 0..7 is left where it is (and `tape_dir` will refuse it, which
        is harmless: that file was never a track).
        """
        out = []
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return out
        for name in names:
            m = MEDIUM_RE.match(name)
            if not m or m.group(2) != "wav":
                continue
            track = int(m.group(1)) - first
            path = os.path.join(folder, name)
            if not 0 <= track < T.NUM_TRACKS or not is_old_medium(path):
                continue
            aside = path[:-len(".wav")] + ASIDE_SUFFIX
            try:
                os.replace(path, aside)
            except OSError:
                continue
            out.append((track, aside, path))
        return out

    def move_to(self, folder: str) -> str:
        """SAVE: rename every medium into `folder`; the next take goes there.

        A rename on one disk, so it costs nothing however long the tape is
        (`tape_dir` with `move`). Returns `""` once moved, otherwise the
        audio process's refusal: `exists` (the folder already holds a medium
        under one of those names; nothing moved) or `move_failed` (another
        filesystem; every rename put back) — `PROTOCOL.md` § `tape_dir`.
        """
        folder = os.path.abspath(folder)
        reply = self.app.native.tape_dir(folder, move=True)
        if reply.get("type") == "error":
            print("persist: cannot move the tape to %s (%s)"
                  % (folder, reply.get("message") or reply.get("code")))
            return str(reply.get("code") or "refused")
        self.tape_folder = folder
        return ""

    def park(self, folder: str) -> bool:
        """USB storage mode: let go of every medium before `/data` unmounts.

        Since #297 the audio process keeps the tape's files open in
        `tape_folder`, which is on `/data`, and `arbutus-usb` refuses a lazy
        unmount — so the export failed `BUSY` every time (#303). `tape_dir`
        without `move` flushes, closes every medium and opens nothing (the
        `folder` has none), and nothing on `/data` is held. `tape_folder` stays
        what it was: `unpark` opens it again. False if the audio process
        refused (a take open: `RECORDING`), and then nothing changed.
        """
        if self.parked is not None:
            return True
        folder = os.path.abspath(folder)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass                      # `tape_dir` says whether it matters
        # Scratch, and it must hold no medium: `tape_dir` would open one. The
        # clip lists stay in the audio process while parked, so its read-ahead
        # opens (and creates, empty) the media they name here; `unpark` sweeps.
        self._remove_media(folder)
        reply = self.app.native.tape_dir(folder)
        if reply.get("type") == "error":
            print("persist: cannot park the tape in %s (%s)"
                  % (folder, reply.get("message") or reply.get("code")))
            return False
        self.parked = folder
        return True

    def repark(self) -> bool:
        """#763: the audio process restarted while parked. The new one is
        pointed at the scratch folder again, never at `tape_folder` (on an
        unmounted `/data` its `tape_dir` creates the folder on the bare mount
        point). Still parked whatever the answer: `unpark` opens the real one."""
        folder, self.parked = self.parked, None
        done = self.park(folder)
        self.parked = folder
        return done

    def unpark(self, quiet: bool = False) -> bool:
        """`/data` is back: the media `park` let go of, opened in place again.
        The clip lists never left the audio process, so they read them at once.
        `quiet`: a retry of a refusal already journalled (#763)."""
        if self.parked is None:
            return True
        if not self.open_folder(self.tape_folder, quiet):
            return False              # still parked; the next tick tries again
        self._remove_media(self.parked)
        self.parked = None
        return True

    @staticmethod
    def holds_live_media(folder: str) -> bool:
        """Does `folder` hold a medium of the post-#297 kind — one that some
        tape has been recording into, and that a copy must never overwrite?
        (An old 44-byte-header WAV is a pre-#297 save's copy; #410's first
        boot over an old autosave copies the live tape over it on purpose.)"""
        try:
            names = os.listdir(folder)
        except OSError:
            return False
        return any(MEDIUM_RE.match(n) and n.endswith(".wav")
                   and not is_old_medium(os.path.join(folder, n))
                   for n in names)

    def copy_media(self, folder: str) -> List[int]:
        """Save-as-copy: the media copied into `folder`; the tape stays put.

        Each medium is flushed first (`tape_save` onto its own path, which the
        audio process answers with a flush, not a copy) and then copied byte
        for byte, so the copy is a medium of the new kind and opens in place —
        rather than `tape_save` to the new path, whose 44-byte-header copy
        would need converting on its first LOAD. Returns the tracks copied.
        """
        native = self.app.native
        copied = []
        os.makedirs(folder, exist_ok=True)
        if not self.on_storage():
            # A host started without `--tape-dir` (by hand; the unit and every
            # spawned host pass one) keeps the tape in RAM: the old copy is
            # the only way out, and the next open converts it.
            for t, frames in enumerate(self.media_frames()):
                if frames > 0 and native.tape_save(
                        t, os.path.join(folder, medium_name(t))
                        ).get("type") != "error":
                    copied.append(t)
            return copied
        for t in range(T.NUM_TRACKS):
            src = self.medium_path(t)
            if not os.path.exists(src):
                continue
            if native.tape_save(t, src).get("type") == "error":
                continue
            try:
                shutil.copyfile(src, os.path.join(folder, medium_name(t)))
            except OSError as exc:
                self.write_refused(medium_name(t), exc)
                continue
            peaks = src[:-len(".wav")] + ".peaks"
            if os.path.exists(peaks):
                try:
                    shutil.copyfile(peaks, os.path.join(
                        folder, os.path.basename(peaks)))
                except OSError:
                    pass             # a cache: the copy rebuilds it
            copied.append(t)
        return copied

    def on_storage(self) -> bool:
        """Is the tape in files at all? (`state.tape.pool.ranges` is 0 when
        the host keeps it in RAM — no `--tape-dir`.)"""
        tape = self.app.native.state.get("tape") or {}
        return bool(int((tape.get("pool") or {}).get("ranges", 0) or 0))

    def clear_working(self):
        """Remove the media in the working folder (and only the media).

        An aside (`*.old.wav`) is left: one is there only when it did not
        convert whole, and then it is the one full copy of that take (#410).
        """
        self._remove_media(self.working)

    @staticmethod
    def _remove_media(folder: str):
        """Every medium and peaks cache in `folder`, and nothing else."""
        try:
            names = os.listdir(folder)
        except OSError:
            return
        for name in names:
            if MEDIUM_RE.match(name):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass

    def fresh_working(self) -> bool:
        """NEW: an empty working folder, and the tape in it."""
        self.clear_working()
        return self.open_folder(self.working)

    def media_frames(self) -> List[int]:
        """Each track's medium length, as the audio process last said."""
        tracks = ((self.app.native.state.get("tape") or {}).get("tracks")
                  or [])
        out = [0] * T.NUM_TRACKS
        for t, doc in enumerate(tracks[:T.NUM_TRACKS]):
            out[t] = int((doc or {}).get("used_frames", 0) or 0)
        return out

    # -- write -------------------------------------------------------------
    def exported(self) -> bool:
        """Is the library partition in a computer's hands right now?

        `device-os-spec.md` § 11.1: storage mode **unmounts `/data`**, and this
        store's directory is `/data/.state/` (§ 3.3). A write while it is
        exported does not fail — `/data` is a mount point on the root
        filesystem, so `os.makedirs` and `open` succeed on the empty directory
        underneath, and the remount then hides what was written. A `STOP`
        during an export would have put every dirty track's medium (up to
        eight 184 MB WAVs) on the OS partition, invisible forever. So while
        `app.usb_exported` is set (`arbutus_os/pumps.py`'s `UsbPump`), nothing
        here writes; the dirty flags keep their memory and the next tick after
        the remount writes what was pending. Q3 audit, #31. Since #617 the
        state is `app.library_held`, which also covers the tape's park: from
        the STORAGE request until the unpark.
        """
        if self.parked is not None:
            return True
        return bool(getattr(self.app, "library_held",
                            getattr(self.app, "usb_exported", False)))

    def write_refused(self, what: str, exc: OSError) -> None:
        """A write the card refused: say so once, keep the dirty state, retry.

        `App.tick` runs on the frame loop and `arbutus_os/main.py` catches
        nothing out of it, so an `OSError` here — `/data` full (a phone's
        upload can fill it; REC's own floor guards only the tape), or the
        mount gone read-only after an unclean cut — took the UI down, and
        `Restart=on-failure` brought it back into the same write 2 s later
        until systemd's start limit left the instrument dark. A refused
        autosave is a journal line and a retry next tick, never a crash
        (Q3 audit, #31). One line per distinct reason, not one a tick.
        """
        text = "%s: %s" % (what, exc)
        if text != self._last_refusal:
            self._last_refusal = text
            print("persist: cannot write %s — retrying (%s)" % (what, exc))

    def write_landed(self) -> None:
        """The working state's `project.json` was written: the card takes
        writes again (`project_file.Document.save_working`)."""
        self._last_refusal = ""

    @property
    def refusing(self) -> bool:
        """A write was refused and `project.json` has not landed since."""
        return bool(self._last_refusal)

    def save_audio(self, force: bool = False):
        """Write the clip list — `tape.json` — when the clip layout moved.

        **No audio.** Since C1 step 3b (#297) the audio process writes every
        take into `tape_folder` as it happens and closes each one with an
        exact header, so there is nothing left to copy: STOP used to call
        `tape_save` per dirty track, ~0.9 s each on the CM4's eMMC with the
        screen frozen for all of it (C6 #79, #93), and that is gone.
        `medium_frames` is still written per track, from the audio process's
        own count, for anything reading the file.
        """
        if self.exported():
            return False           # § 11.1: there is no `/data` to write to
        tape = self.app.tape
        sigs = [_track_signature(tape, t) for t in range(T.NUM_TRACKS)]
        if not force and sigs == self._track_sig:
            return False
        try:
            os.makedirs(self.state_dir, exist_ok=True)
        except OSError as exc:
            self.write_refused("tape.json", exc)
            return False
        frames = self.media_frames()
        doc = {"format": 1, "tracks": []}
        for t in range(T.NUM_TRACKS):
            clips = [{"start": int(c.start), "len": int(c.length),
                      "gain": float(c.gain), "src_offset": int(c.src),
                      "src_track": (t if c.src_track is None
                                    else int(c.src_track))}
                     for c in tape.tracks[t]]
            self._medium_frames[t] = frames[t]
            doc["tracks"].append({"clips": clips, "medium_frames": frames[t]})
        tmp = self.audio_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(doc, f)
            os.replace(tmp, self.audio_path)
        except OSError as exc:
            #: the signatures stay as they were, so the next STOP retries
            self.write_refused("tape.json", exc)
            return False
        self._track_sig = sigs
        return True

    def forget_media(self):
        """The clip list on disk is not known to match the tape any more.

        LOAD, NEW and a host restart replace the clip lists wholesale, and
        geometry is no proxy for which project they belong to: two projects
        can carry a clip of the same length in the same place, and the dirty
        check would then skip writing `tape.json`. `project_file.resync` is
        the one door all three paths go through.
        """
        self._track_sig = [None] * T.NUM_TRACKS
        self._medium_frames = [0] * T.NUM_TRACKS

    # -- read --------------------------------------------------------------
    @staticmethod
    def holds_old_media(folder: str) -> bool:
        """Does `folder` hold a medium in the pre-#297 format?"""
        try:
            names = os.listdir(folder)
        except OSError:
            return False
        return any(MEDIUM_RE.match(n) and n.endswith(".wav")
                   and is_old_medium(os.path.join(folder, n)) for n in names)

    def load_audio(self):
        """The clips, over the media `open_folder` has already opened."""
        if not os.path.exists(self.audio_path):
            return False
        try:
            with open(self.audio_path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return False
        tape = self.app.tape
        self._medium_frames = [0] * T.NUM_TRACKS
        for t, track in enumerate((doc.get("tracks") or [])[:T.NUM_TRACKS]):
            clips = []
            for c in track.get("clips") or []:
                n = int(c.get("len", 0))
                if n <= 0:
                    continue
                clips.append(T.Clip(int(c.get("start", 0)), n,
                                    float(c.get("gain", 1.0)),
                                    int(c.get("src_offset", c.get("start", 0))),
                                    int(c.get("src_track", t))))
            tape.tracks[t] = clips
            self._medium_frames[t] = int(track.get("medium_frames", 0) or 0)
        # What is on disk now is what was loaded, so nothing is dirty yet.
        self._track_sig = [_track_signature(tape, t) for t in range(T.NUM_TRACKS)]
        return True
