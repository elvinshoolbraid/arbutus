"""App: page registry, input routing, and the audio process.

Routing follows control-inventory.md. Pages are constructed once and kept
forever, so navigation never costs state (DDR-028).

The sound is `arbutus-audio`'s (stage N1): `App.native` is required, every
gesture that changes it is a message, and `_native_sync` is where the plain
Python state the pages write becomes those messages.
"""

import os
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from ui import frame as ui_frame
from ui import palette

from . import battery
from . import box_trust
from . import keys as K
from . import file_jobs
from . import project_file
from .instrument import Instrument
from .library import Library
from .menu import MenuPage
from .mixer import METER_DECAY, Mixer
from .page import Page
from .persist import ProjectStore
from .slots import SLOT_LETTERS, SlotStore, STATE_DIR, wipe_state
from .tape import NUM_TRACKS, STOPPED, Tape
from .ui_feed import UiFeed

#: pins the screen's mode for a run (`light`/`dark`) without writing the
#: setting -- `tools/verify.py` and the tests set it (#334)
APPEARANCE_ENV = "ARB_APPEARANCE"

SAMPLE_RATE = 48000

#: pages that own 6 memory slots (control-inventory § slot keys)
SLOT_PAGES = ("SYNTH", "DRUM", "SEQ")

#: dynamic-strip priorities — REC > LIFTED > SEQ > other (DDR-031).
#: a flash outranks them all, briefly: it is a confirmation you asked for.
TICKER_FLASH = -1
TICKER_REC, TICKER_LIFT, TICKER_SEQ, TICKER_OTHER = 0, 1, 2, 5

#: how long a flash message holds the strip
FLASH_S = 2.5

#: transport keys work on every page (workflow §2.2)
TRANSPORT_KEYS = ("PLAY", "STOP", "REC")

#: tap tempo: 3+ taps of the TEMPO key inside this window set the BPM
TAP_WINDOW_S = 2.0
TAP_MIN = 3


class App:
    def __init__(self, sample_rate: int = SAMPLE_RATE, fresh: bool = False,
                 state_dir: str = STATE_DIR, library: Optional[Library] = None,
                 native=None):
        if fresh:
            wipe_state(state_dir)
        self.sample_rate = sample_rate
        self.state_dir = state_dir
        #: the audio process. There is no other audio path (stage N1): the
        #: engines, the library, the rack and the tape all live in it.
        if native is None:
            raise ValueError(
                "App needs the audio process: App(native=NativeAudio(...)). "
                "There is no Python audio path any more (stage N1).")
        self.native = native
        #: D17 (#276): the element map and the recent actions, for the phone
        self.feed = UiFeed()
        if library is None:
            from .native_library import NativeLibrary
            library = NativeLibrary(native)
        self.library = library
        #: the device's own settings — FILE › MIDI / USB / SYSTEM (stage J2).
        #: Not a patch and not a project: `<library>/settings.json`.
        from .settings import Settings
        self.settings = Settings(getattr(library, "root", "") or "")
        #: #617: a setting turned while `/data` is the computer's (MODE back
        #: to OFF is one) waits in memory; `tick` writes it once it is back
        self.settings.held = lambda: self.library_held
        #: #334: the screen comes up in the mode it last had (light the first
        #: time); the phone moves it from then on (`set_appearance`).
        #: `ARB_APPEARANCE` pins it without touching the setting, so a
        #: screenshot run is never in whatever mode the last player left.
        pinned = os.environ.get(APPEARANCE_ENV)
        palette.set_mode(pinned if pinned in palette.MODES
                         else self.settings.appearance)
        #: every browser is a file manager (DDR-055). The lift lives here and
        #: not on a page, so "lift here, drop there" survives closing one
        #: browser and opening another.
        from .library_ops import FileOps
        self.files = FileOps(self)
        #: MIDI clock in, when FILE › MIDI turns it on (stage J2). TEMPO's
        #: SYNC `midi` follows it; nothing else reads it.
        from .midi_clock import MidiClock
        self.midi_clock = MidiClock()
        self.midi_clock.set_enabled(self.settings.clock_in)
        #: the open MIDI input, when there is one (`main.py` sets it). FILE ›
        #: MIDI names the port; nothing else reads it.
        self.midi_in = None

        self.shift_held = False
        self.help_held = False
        self._help_consumed = False
        self.help_target = None            # key / encoder under HELP (DDR-025)
        #: keys pressed under HELP: their releases are the overlay's too (#623)
        self._help_swallowed = set()
        #: held key -> the page (or menu) that took its press. Its release goes
        #: back there, not to whatever page is up by then (#623), the way
        #: `_note_owner` keeps a note_off on the instrument that sounded it.
        self._key_owner = {}
        #: REC down: a note played now rolls a take, and TRACK's cursor is hot
        self.rec_held = False
        #: STOP down: on TRACK, ‹ › jump to the loop or the tape ends (DDR-057)
        self.stop_held = False
        self._flash = None                 # (long, short, expiry) — see flash()
        #: True while `_flash` is § 7.4's power-off line (`flash(hold=True)`)
        self._flash_held = False
        #: #821 (D99): the file job holding the keys, as `arbutus-net` says
        #: (`set_file_job`), or None — always None on a desktop; a question
        #: over the main area (`file_jobs.Prompt`), or None; and § 7.3's
        #: low-storage watch, which asks one
        self.file_job: Optional[dict] = None
        #: #956: the id of the last job this UI flushed the open project for,
        #: in the working state (`persist`), where `arbutus-net` reads it
        self.flushed_job: Optional[str] = None
        self.prompt = None
        self._prompt_keys = set()
        self.storage_watch = file_jobs.StorageWatch(self)
        #: #920 (D98): FILE › BOUNCE rendering in the audio process
        #: (`project_file.bounce(job=True)`), `{path, frames}`, or None; and
        #: the key whose press asked CANCEL BOUNCE TO PLAY?, while it is up
        self.bounce_job: Optional[dict] = None
        self._bounce_ask: Optional[dict] = None
        self._bounce_release: Optional[str] = None
        #: § 7.2's idle row (device-os-spec, #176): when the player last
        #: touched a key or an encoder. The device's `PowerPump` is the only
        #: reader — this process never powers anything off — but the facts the
        #: rule is made of (what was touched, what is rolling) are all here,
        #: so the predicate is here and the socket is not.
        self._last_input_at = time.monotonic()
        #: set by the device when an orderly power-off has begun (§ 7.4),
        #: to one of `critical` / `idle` / `requested`. `None` everywhere
        #: else, which is every emulator run.
        self.powering_off = None
        self._tap_times: List[float] = []  # TEMPO tap tempo
        #: the clock tap tempo reads. verify's rig hands it one that only its
        #: `sleep:` events move, so a loaded machine cannot stretch a gap (#928)
        self.tap_clock = time.monotonic
        self.bpm = 120.0                       # DDR-016: BPM is the ruler

        self.tape = Tape(sample_rate, host=native)
        self.tape.edit_refused = self._edit_refused
        self.mixer = Mixer(sample_rate)

        self.instruments: Dict[str, Instrument] = {
            "SYNTH": Instrument(sample_rate),
            "DRUM": Instrument(sample_rate),
        }
        self.active_instrument_id = "SYNTH"
        self._note_owner = {}          # sounding note -> instrument that has it

        #: Which saved project the working state came from — `"<date>/<name>"`,
        #: the same id `GET /projects/<id>` uses, or None for a session that has
        #: never been saved or loaded. `project_file.save/load/new` own it, so
        #: every route to a project sets it, not just the FILE page's buttons.
        #: It rides in `persist.snapshot()` so `arbutus-net` can read it off
        #: `/data/.state/project.json` and tell the phone whether the project it
        #: asked to bounce is the one the instrument has open (#95).
        self.open_project: Optional[str] = None

        #: `/data` is in a computer's hands (`device-os-spec.md` § 11.1 —
        #: storage mode unmounts it). `arbutus_os/pumps.py`'s `UsbPump` sets
        #: this off `usb.sock`; on the emulator it is never true. FILE locks
        #: LOAD/SAVE/BOUNCE on it, and the working state's autosave (slots,
        #: `project.json`, the media on STOP) pauses on it: the state
        #: directory is `/data/.state/`, and a write while exported lands on
        #: the bare mount point of the OS partition (`persist.exported`).
        self.usb_exported = False

        #: the ENGINE scope: a peak history, one entry per `meters` push. The
        #: UI cannot see samples any more — `[TBD, Elvin's call]`, and the one place a
        #: native screen differs from the Python one for a reason that is not
        #: the rack.
        self.scope = np.zeros(160, dtype=np.float32)
        #: peak of what the record tap is hearing, decayed — TRACK's cursor meter
        self.input_peak = 0.0

        #: One sequencer **per instrument** (S13, DDR-034 as amended). The host
        #: has kept a SEQ slot per instrument since §15.4 and clocks only the
        #: active one; up here there was a single object with an `inst_id`, so
        #: editing SYNTH's sequence overwrote DRUM's. The SEQ page shows and
        #: edits whichever instrument is active — that is the `sequencer`
        #: property below — and the parked one keeps its pattern, its box and
        #: its knobs until you come back to it.
        self.sequencers: Dict[str, Optional[object]] = {
            "SYNTH": None, "DRUM": None,      # set by the SEQ page from library/seq
        }
        self.metronome = None              # owned by the TEMPO page
        self.mic = None                    # MicCapture, built by the INPUT page
        #: () -> AudioInputDriver. main.py supplies the real mic, tests a fake.
        #: The device is opened on arrival at INPUT, never at boot (macOS asks
        #: for microphone permission the first time one is opened).
        self.mic_driver_factory = None

        self.pages: Dict[str, Page] = {}
        self.menu: Optional[MenuPage] = None
        self.closed_menu: Optional[MenuPage] = None
        self._page_id = "SYNTH"
        self._native_wire()
        self._build_pages()
        self._native_wire_pages()

        #: the tape's folder and clip list; the open project's document
        #: and the working state's pointer to it are `doc`'s (#1017)
        self.project = ProjectStore(self, self.state_dir)
        self.doc = project_file.Document(self, self.state_dir)
        self.doc.boot()
        self._native_sync()      # the restored project is new to the host
        self.adopt_host_appearance()

    # -- the audio process -------------------------------------------------
    def _native_wire(self):
        from .native_audio import XRUN_FLASH_S

        self.native.on_xrun = lambda long, short: self.flash(long, short,
                                                             XRUN_FLASH_S)
        self.native.on_meters = self._native_meters
        self.native.on_compact = self._native_compact
        self.native.on_bounce = self._native_bounce
        #: #693: a push can arrive inside any request, a page's draw
        #: included, so the hook only stores the mode; `tick` applies it
        self.native.on_appearance = self._appearance_heard
        self._appearance_pending: Optional[str] = None
        self.native.on_tape_dir_renamed = self.tape_dir_renamed
        #: C1 step 8 (#164): tracks SAVE asked to compact and the host has not
        #: been given yet. The host runs **one** job at a time (`BUSY`), so a
        #: SAVE that finds three half-dead media sends the first and hands the
        #: rest over as each result arrives.
        self._compact_queue: List[int] = []
        self._compacting = -1
        self._clip_fingerprint = None
        self._transport_fingerprint = None
        #: #99: the transport is the *host's* value and this UI holds a mirror.
        #: These are the last `playing` / `recording` seen in the host's own
        #: state document, so that a **change** there is adopted and the mere
        #: value — a document that has not caught up with a gesture of ours
        #: yet — is not. Seeded False/False, which is a fresh host's transport:
        #: a UI that starts against one already rolling adopts it on its first
        #: sync.
        self._host_playing = False
        self._host_recording = False
        #: the playhead as this UI left it at the end of the last sync. A
        #: difference is what "the UI moved it" means, and only that is sent
        #: down as a `locate` (#99). `None` is "not known" — before the first
        #: sync, and after a project load forgets the fingerprints
        #: (`project_file.resync`) — and it means the playhead is sent.
        self._playhead_seen = None
        self._tape_fingerprint = None
        self._mixer_fingerprint = None
        self._click_fingerprint = None
        self._monitor_fingerprint = None
        self._scrub_clock = None

    def _native_wire_pages(self):
        """Once the pages exist, tell the host what they start at."""
        self.native.set_active(self.active_instrument_id)
        self.native.tempo(self.bpm)

    def _native_meters(self, m: dict):
        """Peaks from the host feed the meters the pages already draw.

        The scope is the one casualty: the UI no longer sees samples, so
        SYNTH › ENGINE draws a **peak history** rather than a waveform —
        `[TBD, Elvin's call]`, and the one place a native screen differs from a Python
        one for a reason that is not a rack.
        """
        inst = m.get("instrument") or [0.0, 0.0]
        peak = float(max(inst)) if inst else 0.0
        self.input_peak = max(peak, self.input_peak * METER_DECAY)
        master = m.get("master") or [0.0, 0.0]
        self.mixer.master_peak = float(max(master)) if master else 0.0
        for t, v in enumerate((m.get("tracks") or [])[:NUM_TRACKS]):
            self.mixer.track_peaks[t] = float(v)
        self.mixer.comp.gain_reduction = float(m.get("comp_gr", 1.0))
        self.scope[:-1] = self.scope[1:]
        self.scope[-1] = peak

    def _native_compact(self, m: dict):
        """A compaction finished: move the UI's own references with the audio.

        C1 step 8's other half (#164, design § 3.8). The medium is the file one
        track's audio lives in; a compaction copies only the spans the UI said
        to keep, so every reference it holds — its clips, its lift buffer and
        its one undo record — has a new address. The map says where each run
        of audio landed; the corrected clip list goes down on this same frame,
        because the host is still holding the pre-compaction one.

        A `cancelled` push (PLAY or REC arrived) and an `error` push move
        nothing: the old medium was never touched.
        """
        track = int(m.get("track", -1))
        if track == self._compacting:
            self._compacting = -1
        self._compact_next()
        if not 0 <= track < NUM_TRACKS:
            return
        if m.get("cancelled") or m.get("error") or not isinstance(m.get("map"), list):
            return
        self.tape.remap_medium(track, m["map"], int(m.get("frames", 0) or 0))
        # the open project's saved document reads the same medium (#410)
        project_file.remap_saved(self, track, m["map"],
                                 int(m.get("frames", 0) or 0))
        # the host's list is the one it was given before the copy: re-send it
        # whatever the diff says, because the addresses in it are now wrong
        self._clip_fingerprint = None

    # -- #920 (D98): BOUNCE as a job -----------------------------------------
    def _native_bounce(self, m: dict):
        """The host's `bounce` push: the job is over, whichever way.

        Only the state moves here (this runs inside `NativeAudio.send`'s
        read): the question goes, and the FILE page says what came of it.
        """
        self.bounce_job = None
        ask = self._bounce_ask
        if ask is not None and self.prompt is ask["prompt"]:
            self.prompt = None
            self._bounce_ask = None
            if ask["held"]:
                self._prompt_keys.add(ask["key"])  # its release was the question's
        page = self.pages.get("FILE")
        done = getattr(page, "bounce_done", None)
        if done is not None:
            done(m)

    def _played(self, key: str) -> bool:
        """A key that makes sound or moves the tape: a note, PLAY, REC."""
        return K.is_note(key) or key in ("PLAY", "REC")

    def _bounce_key(self, key: str, pressed: bool, velocity: float) -> bool:
        """#920: a played key during a bounce asks CANCEL BOUNCE TO PLAY?

        True when the event was the question's. The asking key waits for the
        answer: YES (1) cancels the render, nothing is written, and the key
        then does what it does; NO (2) keeps rendering and the key does
        nothing. Played keys while the question is up wait with it; any other
        key answers NO and then does what it does (D107: the question never
        costs a move), through the ordinary `Prompt` path below.
        """
        ask = self._bounce_ask
        if not pressed:
            if ask is not None and key == ask["key"] and ask["held"]:
                ask["held"] = False
                return True
            return False
        if ask is not None and self.prompt is ask["prompt"]:
            if key in (K.Key.K1.value, K.Key.K2.value):
                self.prompt = None
                self._prompt_keys.add(key)
                ask["prompt"].on_answer(0 if key == K.Key.K1.value else 1)
                return True
            return self._played(key)
        if self.bounce_job is None or not self._played(key):
            return False
        if self.prompt is not None:          # another question: LATER
            other, self.prompt = self.prompt, None
            other.on_answer(1)
        ask = {"key": key, "velocity": velocity, "held": True}
        ask["prompt"] = file_jobs.Prompt(
            list(file_jobs.BOUNCE_ASK_LINES), file_jobs.BOUNCE_ASK,
            list(file_jobs.BOUNCE_ASK_CHOICES),
            lambda choice: self._bounce_answer(ask, choice))
        self._bounce_ask = ask
        self.prompt = ask["prompt"]
        return True

    def _bounce_answer(self, ask: dict, choice: int):
        if self._bounce_ask is ask:
            self._bounce_ask = None
        if choice != 0:
            # NO: the render goes on, and the asking key's release is the
            # question's too, so the note it never started does not end
            if ask["held"]:
                self._prompt_keys.add(ask["key"])
            return
        self.native.bounce_cancel()
        self.bounce_job = None             # the push may come later; it is over
        # YES: the key does what it does, held or already let go (its
        # release then on the next tick, `tick`)
        self.on_key(ask["key"], True, ask["velocity"])
        if not ask["held"]:
            self._bounce_release = ask["key"]

    # -- #821 (D99): the phone's file jobs ---------------------------------
    def set_file_job(self, job: Optional[dict]):
        """What `arbutus-net` says is running (`net.sock`'s `job`), or None.

        On the edge into a job the audio stops: the transport (a take ends as
        STOP ends it) and every sounding note. The job refused to start
        under a moving tape; this is for a PLAY that crossed it in flight.
        Then the open project is written (#956: a Duplicate copies the card,
        which § 7.3's clock leaves up to 10 s behind) and, only once that
        write was not refused, the job's id goes into the working state:
        the word a Duplicate of the open project waits for.
        """
        job = job if isinstance(job, dict) and job.get("kind") else None
        if job is not None and self.file_job is None:
            self.prompt = None
            project_file.halt(self)
            self.panic()
            self.flush()
            if not self.library_held and not self.project.refusing:
                self.flushed_job = job.get("id")
                self.doc.save_working()
        self.file_job = job

    def release_project(self, project: str) -> bool:
        """`arbutus-net` is deleting the open project for an offload (#821
        item 1): leave it, as NEW does (its last document, a new folder,
        the tape moved there), under an auto-name the name menu then asks to
        replace (`project_offloaded`, #818). False when `project` is not the
        open one."""
        if not project or project != self.open_project:
            return False
        if project_file.new(self) is None and project_file.not_saved(self):
            return False      # #954: its last document refused, it stays open
        self._released_project = project
        return True

    def project_offloaded(self, project: str):
        self.flash(*file_jobs.OFFLOADED)
        self.storage_watch.offloaded()
        #: screen-sketches § FILE › PROJECT: "the name menu for a new
        #: project" (#818); cancelled, the auto-name stays
        page = self.pages.get("FILE")
        if page is not None and project \
                and project == getattr(self, "_released_project", None):
            self._released_project = None
            page.name_after_offload()

    def tape_dir_renamed(self, m: dict):
        """#821: the phone renamed the open project; the host renamed its
        folder, the tape with it, and says so. The autosave writes into the
        folder the tape is in (`project_file._in_project`), and the
        document's name is the folder's, so both follow from two fields and
        the boot pointer is written now (a cut must not boot the old name)."""
        store = getattr(self, "project", None)
        src, dst = m.get("from"), m.get("to")
        if store is None or not isinstance(src, str) or not isinstance(dst, str):
            return
        if os.path.abspath(store.tape_folder) != os.path.abspath(src):
            return
        store.tape_folder = os.path.abspath(dst)
        root = os.path.abspath(project_file.projects_root(self))
        if os.path.dirname(os.path.dirname(os.path.abspath(dst))) == root:
            self.open_project = project_file.project_id(dst)
        self.doc.commit()

    def _compact_next(self):
        """Hand the host the next queued compaction, if it is free to take it.

        The running track is recorded **before** the message goes out, because
        offline there is no I/O thread: the copy runs inside the request and the
        result push is already in the buffer when the reply comes back, so this
        re-enters through `_native_compact` before the send returns.
        """
        if getattr(self, "library_held", False):
            #: #617: the park cancelled the running copy, and a compaction
            #: sent now would run on the parked scratch medium and remap the
            #: clips by its map. The queue is dropped: the next SAVE
            #: re-triggers it (§ 3.8).
            self._compact_queue.clear()
            return
        while self._compacting < 0 and self._compact_queue:
            track = self._compact_queue.pop(0)
            self._compacting = track
            reply = self.native.tape_compact(track, self.tape.keep_spans(track))
            if reply.get("type") == "error":
                # RECORDING / PLAYING / BUSY / NO TAPE DIR: the retry is the
                # next trigger's (§ 3.8), and nothing has been copied.
                if self._compacting == track:
                    self._compacting = -1

    def compact_dead_media(self) -> List[int]:
        """§ 3.8's SAVE trigger: compact a medium that is more than half dead.

        Returns the tracks asked for, in order. The dead count is the host's
        (`state.tape.tracks[t].dead_frames`) and can only over-report, which is
        exactly right for a trigger — the keep set is still computed here.

        `state.tape.pool.ranges` is 0 when the host has no tape on storage at
        all — a host started without `--tape-dir`, which since C1 step 3b
        (#297) only a hand-run one is: there is nothing to compact and nothing
        is sent, so the trigger cannot flash `NO TAPE DIR` at a player.

        The **other** trigger § 3.8 names — the head passing *k* × ruler — is
        deliberately not built: a medium cannot exceed the ruler, so *k* can
        only be 1 and the state it watches for does not exist (#168, Elvin's).
        """
        doc = self.native.state.get("tape") or {}
        if not int((doc.get("pool") or {}).get("ranges", 0) or 0):
            return []
        # One round trip, and only where a medium exists: the dead count has to
        # be the host's current one, and a take that ended a moment ago is
        # exactly the case this trigger is for.
        doc = self.native.refresh().get("tape") or {}
        asked: List[int] = []
        for t, track in enumerate((doc.get("tracks") or [])[:NUM_TRACKS]):
            used = int(track.get("used_frames", 0) or 0)
            dead = int(track.get("dead_frames", 0) or 0)
            if used <= 0 or dead * 2 <= used:
                continue
            if t in self._compact_queue or t == self._compacting:
                continue
            self._compact_queue.append(t)
            asked.append(t)
        self._compact_next()
        return asked

    def forget_host_sync(self):
        """Forget what the audio process was last told (#1017).

        `_native_sync` sends a value only when it differs from its
        fingerprint, so after a project load (`project_file.resync`) every
        fingerprint, and the playhead this UI last left (#99), is forgotten
        and the next sync sends everything. Every `self._*_fingerprint` is
        cleared by its name's suffix, so a new one needs no edit here.
        """
        for name in list(vars(self)):    # vars(): the instance's attributes
            if name.endswith("_fingerprint"):
                setattr(self, name, None)
        self._playhead_seen = None

    def resync_host(self):
        """Forget what the host was told, then tell it everything
        (`project_file.resync`, after a LOAD, a NEW or a host restart)."""
        self.forget_host_sync()
        self._native_sync()

    def _native_sync(self):
        """Push anything the UI owns and the host has to be told about.

        One diff per thing, every frame: the clip list, the transport, the
        tape keys (loop / SPEED / REV / FILTER), the mixer, the click and
        INPUT MONITOR. The pages keep writing plain Python state — a page has
        no idea the audio is in another process — and this is where that
        state becomes messages.
        """
        tp = self.tape
        native = self.native
        # the host owns the transport (#99); adopt anything another client did
        # before the diffs below decide what this UI still has to say
        self._adopt_host_transport()
        # TEMPO's COUNT-IN: the punch is pending on a frame, so the playhead
        # has to be current *before* it is tested — the transport fingerprint
        # below is what then tells the host about the take
        if tp.playing:
            tp.playhead = float(native.tape_pos)
        tp.poll_count_in()
        clips = tuple(tuple((c.start, c.length, round(c.gain, 6), c.src,
                             -1 if c.src_track is None else int(c.src_track))
                            for c in track) for track in tp.tracks)
        if clips != self._clip_fingerprint:
            # only the tracks that moved (#74): every message costs the host a
            # state document (~3 ms on the CM4), and a take changes one track,
            # so pushing all eight made STOP's frame ~50 ms longer. `None` (a
            # reconnect, a LOAD) or a `None` entry still pushes that much.
            old = self._clip_fingerprint
            if old is None or len(old) != len(clips):
                old = (None,) * len(clips)
            self._clip_fingerprint = clips
            for t, track in enumerate(tp.tracks):
                if clips[t] == old[t]:
                    continue
                native.set_clips(t, [
                    {"start_frame": int(c.start), "len": int(c.length),
                     "src_offset": int(c.src), "gain": float(c.gain),
                     "src_track": t if c.src_track is None else int(c.src_track)}
                    for c in track])
        # the playhead first: `record` and `start` both act from where the
        # host's tape is, and REC + a note moves it before rolling. Only a
        # playhead **this UI** moved is sent (#99): when the two disagree and
        # the UI did not touch its own, the host is the one that is right and
        # the UI follows. Sending it back unconditionally is what made the UI
        # relocate a tape another client was rolling, 20-30 times a second.
        if not tp.playing and abs(tp.playhead - native.tape_pos) >= 1.0:
            if self._playhead_seen is None \
                    or float(tp.playhead) != self._playhead_seen:
                native.transport("locate", tp.playhead)
            else:
                tp.playhead = float(native.tape_pos)

        fp = (tuple(tp.armed), tp.recording, tp.playing, tp.selected)
        if fp != self._transport_fingerprint:
            prev = self._transport_fingerprint
            self._transport_fingerprint = fp
            armed = [i for i, a in enumerate(tp.armed) if a]
            native.arm(armed[0] if armed else -1)
            if prev is None or prev[1] != tp.recording:
                if tp.recording:
                    # the take appends at the medium's head, and TRACK draws it
                    # from there while it rolls (`clip_peaks`)
                    tracks = ((native.state.get("tape") or {}).get("tracks") or [])
                    used = 0
                    if tp.selected < len(tracks):
                        used = int(tracks[tp.selected].get("used_frames", 0) or 0)
                    tp.feed_take_length(0, medium=used)
                native.record(tp.selected, tp.recording)
            if (prev is None or prev[2] != tp.playing) \
                    and bool(native.state.get("playing")) != tp.playing:
                # REC + a note rolls the tape without a transport key
                native.transport("start" if tp.playing else "stop")

        # the tape keys: IN / OUT / LOOP, ENC3 and hold-SPEED, hold-FILTER
        tape_fp = ((int(tp.loop_in), int(tp.loop_out), bool(tp.loop_on)),
                   (round(float(tp.effective_speed), 4), bool(tp.rev_held)),
                   (bool(tp.filter_held), round(float(tp.filter_cutoff), 2),
                    tp.filter_mode))
        if tape_fp != self._tape_fingerprint:
            prev = self._tape_fingerprint
            self._tape_fingerprint = tape_fp
            if prev is None or prev[0] != tape_fp[0]:
                native.loop(tp.loop_in, tp.loop_out, tp.loop_on)
            if prev is None or prev[1] != tape_fp[1]:
                native.tape_speed(tp.effective_speed, tp.rev_held)
            if prev is None or prev[2] != tape_fp[2]:
                native.tape_filter(tp.filter_held, tp.filter_cutoff,
                                   tp.filter_mode)

        # MIXER: the page moves plain floats, the host owns the gains
        mx = self.mixer
        mix_fp = (tuple(round(float(v), 4) for v in mx.levels),
                  tuple(round(float(v), 4) for v in mx.pans),
                  round(float(mx.master_level), 4),
                  round(float(mx.comp.amount), 4),
                  round(float(mx.comp_rel), 4),
                  round(float(mx.drive), 4),
                  round(float(mx.drive_tone), 4),
                  round(float(mx.bal), 4))
        if mix_fp != self._mixer_fingerprint:
            prev = self._mixer_fingerprint
            self._mixer_fingerprint = mix_fp
            for t in range(NUM_TRACKS):
                if prev is None or prev[0][t] != mix_fp[0][t] \
                        or prev[1][t] != mix_fp[1][t]:
                    native.mixer(track=t, gain=mix_fp[0][t], pan=mix_fp[1][t])
            if prev is None or prev[2:] != mix_fp[2:]:
                # the whole MASTER strip in one message (§15.5): LEVEL, COMP,
                # COMP REL, DRIVE, DRIVE TONE, BAL. Mute and solo are not here
                # — they are gestures, sent by the page as they happen.
                native.mixer(master=mix_fp[2], comp=mix_fp[3],
                             comp_rel=mix_fp[4], drive=mix_fp[5],
                             drive_tone=mix_fp[6], bal=mix_fp[7])

        # TEMPO's click, and INPUT's monitor
        met = self.metronome
        click_fp = (round(float(met.level), 4), round(float(met.pitch), 2))
        if click_fp != self._click_fingerprint:
            self._click_fingerprint = click_fp
            native.metronome(*click_fp)
        mic = self.mic
        mon_fp = ((bool(mic.monitor), round(float(mic.gain), 3))
                  if mic is not None else (False, 1.0))
        if mon_fp != self._monitor_fingerprint:
            self._monitor_fingerprint = mon_fp
            native.monitor(*mon_fp)

        if tp.recording:
            tp.feed_take_length(int(native.tape_pos) - tp.take_start)
        if tp.playing:
            tp.playhead = float(native.tape_pos)
        self._playhead_seen = float(tp.playhead)

    def _adopt_host_transport(self):
        """Follow the transport when something other than this UI moved it.

        `arbutus-audio` serves several clients at once — the instrument's own
        UI and, since D2, the companion app over TCP — and the transport is
        **its** value, exactly like every other value the pages draw. So a
        `start`, a `stop` or a `locate` from another client is adopted here
        and the diffs in `_native_sync` then send only what this UI changed.

        Adopted on a **change** in the host's document, never on its value: a
        state document that has not caught up with our own gesture yet says
        the same thing twice, and only a change is news. And a frame on which
        this UI changed the transport itself is the UI's: its own diff goes
        down and the host's next document carries it back.
        """
        tp, native = self.tape, self.native
        st = native.state
        if not st:
            return
        playing = bool(st.get("playing"))
        recording = bool((st.get("tape") or {}).get("recording"))
        was_playing, was_recording = self._host_playing, self._host_recording
        self._host_playing, self._host_recording = playing, recording
        if playing == was_playing and recording == was_recording:
            return
        fp = (tuple(tp.armed), tp.recording, tp.playing, tp.selected)
        if self._transport_fingerprint is not None \
                and fp != self._transport_fingerprint:
            return                      # this UI moved it: its diff wins
        if was_recording and not recording and not tp.recording:
            # a take somebody else ran has become a clip in the host's list;
            # take its geometry the same way our own STOP does
            self._native_adopt_clips()
        if playing == tp.playing:
            return
        ours = tp.recording             # a take of ours a remote stop ends
        tp.follow(playing, float(native.tape_pos))
        if ours and not playing:
            self._native_adopt_clips()
        # adopted, so there is nothing to tell the host about it
        self._transport_fingerprint = (tuple(tp.armed), tp.recording,
                                       tp.playing, tp.selected)
        self._playhead_seen = float(tp.playhead)

    def _native_scrub(self, now: float):
        """‹ › held on TRACK while stopped: the cursor runs and is audible.

        The Python tape moved it inside `Tape.render`, once per audio block.
        There is no audio block here any more, so it moves on wall time at the
        same 4x the block-rate version did.
        """
        tp = self.tape
        last, self._scrub_clock = self._scrub_clock, now
        if tp.playing or not tp.scrub_dir or last is None:
            return
        frames = min(0.25, max(0.0, now - last)) * self.sample_rate * 4.0
        if frames <= 0:
            return
        tp.seek(tp.playhead + tp.scrub_dir * frames)
        tp.scrub_preview(tp.scrub_dir)

    def _native_transport(self, key: str, was_recording: bool = False):
        """PLAY / STOP / REC, in the order the host needs them.

        `record` writes from the *current* transport position, so the locate
        has to land first; and the take only becomes a clip once recording
        ends, which is when the UI adopts it.
        """
        tp = self.tape
        if key == "PLAY":
            self.native.transport("locate", tp.playhead)
            self._native_sync()
            self.native.transport("start")
        elif key == "STOP":
            self.native.transport("stop")
            self._native_sync()
            self._native_adopt_clips(was_recording)
            self.native.transport("locate", tp.playhead)
        else:
            self._native_sync()
            if not tp.recording:
                self._native_adopt_clips(was_recording)

    def halt_transport(self):
        """Stop, a take included, before a save, load, bounce or clear
        (`project_file.halt`)."""
        was = self.tape.recording
        self.tape.stop()
        self._native_transport("STOP", was)

    def _native_adopt_clips(self, wait: bool = False):
        """A finished take is a host clip; give the UI's model the same block.

        The audio stays in the host's medium — nothing in §8.2 hands samples
        back up — so the Python clip carries the *geometry* and silence. Every
        clip op still runs in Python and is pushed down with `set_clips`.

        The host announces the take as its own `take` push (stage E1). Waiting
        on that rather than on the clip *count* is what keeps a retake quick:
        a take absorbs what it covered, so the list can come back the same
        length it went in.
        """
        from .tape import record_commit

        # A take becomes a clip in the host's *next* control poll, not in the
        # reply to `transport stop` (PROTOCOL.md, commit_take). Only a run that
        # was recording waits for it.
        self.native.refresh()
        if wait:
            for _ in range(40):
                if self.native.takes:
                    break
                if self.native.offline:
                    # offline the audio half only runs inside `render`, so the
                    # stop command (and with it the take) needs a block
                    self.native.render(self.native.host_block)
                else:
                    time.sleep(0.005)
                self.native.refresh()
        takes, self.native.takes = self.native.takes, []
        changed = set()
        for m in takes:
            t = int(m.get("track", -1))
            length = int(m.get("len", 0))
            if not (0 <= t < NUM_TRACKS) or length <= 0:
                continue
            start = int(m.get("start_frame", 0))
            # the take absorbs what it overlaps, the same geometry the host
            # applied to its own list
            self.tape.tracks[t] = record_commit(
                self.tape.tracks[t], start, length, float(m.get("gain", 1.0)),
                src_offset=int(m.get("src_offset", start)),
                src_track=int(m.get("src_track", t)))
            changed.add(t)
        if changed and self._clip_fingerprint is not None:
            # push the merged lists back down: those tracks, not all eight
            self._clip_fingerprint = tuple(
                None if t in changed else fp
                for t, fp in enumerate(self._clip_fingerprint))

    # -- construction -----------------------------------------------------
    def _build_pages(self):
        from pages.synth import SynthPage

        from pages.drum import DrumPage
        from pages.file import FilePage
        from pages.help import HelpPage
        from pages.input import InputPage
        from pages.mixer import MixerPage
        from pages.seq import SeqPage
        from pages.tempo import TempoPage
        from pages.track import TrackPage

        self.pages["SYNTH"] = SynthPage(self)
        self.pages["DRUM"] = DrumPage(self)
        self.pages["TRACK"] = TrackPage(self)
        self.pages["MIXER"] = MixerPage(self)
        self.pages["SEQ"] = SeqPage(self)
        self.pages["HELP"] = HelpPage(self)
        self.pages["TEMPO"] = TempoPage(self)
        self.pages["INPUT"] = InputPage(self)
        self.metronome = self.pages["TEMPO"].metronome
        # USB/FILE is a real page since stage J1: PROJECT is built, MIDI / USB
        # / SYSTEM are J2's and are labelled placeholders on it.
        self.pages["FILE"] = FilePage(self)

        # #874: the SEQ record restores onto the active instrument's
        # sequencer, so that has to be the saved one before the loop, as
        # `project_file.apply()` does. `doc.boot()` sets it again later.
        active = project_file.Document.saved_active_instrument(self.state_dir)
        if active in self.instruments:
            self.active_instrument_id = active
        for pid in SLOT_PAGES:
            page = self.pages[pid]
            store = SlotStore(pid, self.state_dir)
            store.bind(page.snapshot)
            page.slot_store = store
            page.state.slot_letter = store.active    # the breadcrumb (#624)
            record = store.get(store.active)
            if record:
                page.restore(record)
            else:
                store.touch()

    # -- navigation -------------------------------------------------------
    @property
    def current_page(self) -> Page:
        return self.menu if self.menu is not None else self.pages[self._page_id]

    @property
    def page_id(self) -> str:
        return self._page_id

    def goto(self, page_id: str):
        # A bad id was a silent no-op until #174, which hid a test driving the
        # UI to "MIX" (the page is MIXER). `page` is a contract with another
        # process now -- `arbutus-net` reads it off the autosave snapshot
        # (device-os-spec.md 9.11) -- so a rename has to go red here.
        if page_id not in self.pages:
            raise ValueError("%r is not a page. The pages are: %s."
                             % (page_id, ", ".join(sorted(self.pages))))
        self.menu = None
        if page_id != self._page_id:
            self.feed.page(page_id)
        self._page_id = page_id
        if page_id in self.instruments:
            self.active_instrument_id = page_id   # DDR-034
            self.native.set_active(page_id)       # one sounds at a time (§5)
        hook = getattr(self.pages[page_id], "on_enter", None)
        if hook is not None:
            hook()

    def open_mic(self, page):
        """Open the capture device for `page`. Silent no-op with no factory."""
        if self.mic_driver_factory is None:
            return False
        return page.attach(self.mic_driver_factory())

    def close_mic(self):
        page = self.pages.get("INPUT")
        if page is not None:
            page.release()

    def open_menu(self, menu: MenuPage):
        self.menu = menu

    def close_menu(self):
        #: the menu this closed, so a page's `on_menu_number` can tell which
        #: of its menus a number key just shut (#819)
        self.closed_menu = self.menu
        self.menu = None

    @property
    def instrument(self) -> Instrument:
        return self.instruments[self.active_instrument_id]

    @property
    def sequencer(self):
        """The active instrument's sequencer (S13) — what the SEQ page shows.

        Everything that used to read `app.sequencer` still reads the one that
        matters: the keys play the active instrument, so its sequencer is the
        only one that can be running, entering or holding.
        """
        return self.sequencers.get(self.active_instrument_id)

    @sequencer.setter
    def sequencer(self, engine):
        self.sequencers[self.active_instrument_id] = engine

    def set_sequencer(self, engine, inst_id: Optional[str] = None):
        """Swap one instrument's sequencer engine, keeping the pattern where
        it fits. `inst_id` defaults to the active instrument."""
        inst_id = (inst_id or self.active_instrument_id).upper()
        old = self.sequencers.get(inst_id)
        if old is not None:
            old.silence()
            engine.restore(old.snapshot())
        engine.inst_id = inst_id
        engine.bind(self)
        self.sequencers[inst_id] = engine

    # -- persistence ------------------------------------------------------
    def notify_param_changed(self, page: Page):
        store = getattr(page, "slot_store", None)
        if store is not None:
            store.touch()
        # The SEQ slot rides in the instrument's patch (control-inventory
        # § Patches, §15.4), so arming, entering or re-knobbing the sequencer
        # changes that instrument's slot record too. Without this the record
        # predates the arm, and recalling it — even the letter you are already
        # on — hands the host a patch with no `seq` block and takes the box
        # away (inspection 2026-09-18).
        seq = self.sequencer
        if seq is not None and page is self.pages.get("SEQ"):
            inst = self.pages.get(getattr(seq, "target_id", ""))
            store = getattr(inst, "slot_store", None)
            if store is not None:
                store.touch()

    def tick(self, now: Optional[float] = None):
        now = now if now is not None else time.monotonic()
        self.native.poll()
        if self._bounce_release is not None:
            # #920: a key let go before YES ends a tick after it started, so
            # the note it plays is a note and not a press and release in
            # the same block, which sounds nothing
            key, self._bounce_release = self._bounce_release, None
            self.on_key(key, False)
        self._native_scrub(now)
        self._native_sync()
        for page in self.pages.values():
            hook = getattr(page, "tick", None)
            if hook is not None:
                hook()
        if not self.library_held:          # § 11.1: nothing to write to
            self.settings.flush()
            for pid in SLOT_PAGES:
                store = getattr(self.pages[pid], "slot_store", None)
                if store is not None:
                    store.tick(now)
            self.doc.tick(now)
            self.storage_watch.tick()
        if self._appearance_pending is not None:
            mode, self._appearance_pending = self._appearance_pending, None
            self.set_appearance(mode)
        self.feed.flush(self)

    # -- § 7.2's idle row -------------------------------------------------
    def note_input(self, now: Optional[float] = None):
        """The player did something. Keys and encoders only — a frame is not
        activity and neither is a sequencer step playing to nobody."""
        self._last_input_at = time.monotonic() if now is None else now

    def idle_seconds(self, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        return max(0.0, now - self._last_input_at)

    def idle_power_off_due(self, now: Optional[float] = None,
                           network_busy: bool = False) -> bool:
        """§ 7.2 row 3: has this instrument earned an idle power-off?

        The row's four conditions, in its own words: "idle >=
        `settings.system.power_off` minutes (0 = never), no transport
        rolling, no take open, no client on the network sockets". The first
        three are this process's own knowledge; the fourth is not — the file
        server is `arbutus-net`'s and the control port is the host's — so it
        arrives as `network_busy` from the caller that can see it.

        A count-in counts as rolling: the take has been armed and the click is
        running, which is the one transport state with nothing else to show
        it (see `tickers`).
        """
        minutes = 0
        try:
            minutes = int(self.settings.get("system", "power_off") or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes <= 0:
            return False                 # `OFF` on FILE › SYSTEM, the default
        if network_busy:
            return False
        if self.recording or self.playing or self.tape.counting_in:
            return False
        return self.idle_seconds(now) >= minutes * 60.0

    @property
    def library_held(self) -> bool:
        """#617: § 11.1's one state, "export asked": from the moment STORAGE
        parks the tape (`ProjectStore.park`, before the request is even sent)
        until `/data` is back and the tape has unparked into it.

        `usb_exported` alone is the service's flag, and it is late at both
        ends: it turns true only when the reply comes (a sync, an unmount with
        retries, the gadget bind), and false before the remount is proven. In
        either window the tape is on `/run`, so a take, a tape edit or a
        compaction lands in a folder the unpark sweeps, and a settings write
        lands on the bare mount point. Every guard reads this, never the flag.
        """
        store = getattr(self, "project", None)
        return bool(self.usb_exported
                    or getattr(store, "parked", None) is not None)

    def flush(self):
        if self.library_held:
            #: § 11.1: `/data` is unmounted, so the working state has nowhere
            #: to go; the stores keep their dirty flags for the remount.
            return
        for pid in SLOT_PAGES:
            store = getattr(self.pages[pid], "slot_store", None)
            if store is not None:
                store.flush()
        self.doc.flush()
        self.settings.flush()

    # -- status -----------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self.tape.recording

    @property
    def playing(self) -> bool:
        return self.tape.playing

    def tickers(self) -> List[Tuple[int, str, str]]:
        """(priority, long, short) — DDR-031 order REC > LIFTED > SEQ > other."""
        out: List[Tuple[int, str, str]] = []
        tp = self.tape
        n = tp.selected + 1
        if tp.recording:
            out.append((TICKER_REC, f"RECORDING → TRACK {n}", f"REC→{n}"))
        elif tp.counting_in:
            # TEMPO's COUNT-IN: the take is armed and waiting out the click,
            # which is the one transport state with nothing else to show it
            bars = -(-tp.count_in_left() // max(1, int(self.bar_samples())))
            out.append((TICKER_REC, f"COUNT-IN {bars} → TRACK {n}",
                        f"CNT {bars}"))
        if tp.lift_buffer is not None:
            desc = tp.lift_buffer.description
            short = desc.split(" ")[0]
            out.append((TICKER_LIFT, f"LIFTED: {desc}", f"LIFT {short}"))
        # a browser lift is the same sentence about a different kind of thing
        # (DDR-055: "the strip shows the lifted name")
        held = self.files.ticker()
        if held is not None:
            out.append((TICKER_LIFT, held[0], held[1]))
        # D117 (#1006): a refused autosave is said on every page, first of
        # the "other" class: it is the one of them that can lose work
        unsaved = project_file.not_saved_words(self)
        if unsaved is not None:
            out.append((TICKER_OTHER, unsaved[0], unsaved[1]))
        if tp.rev_held:
            out.append((TICKER_OTHER, "REV", "REV"))
        if tp.speed_held:
            out.append((TICKER_OTHER, f"SPEED ×{tp.momentary_speed:.2f}",
                        f"×{tp.momentary_speed:.2f}"))
        if tp.filter_held:
            hz = tp.filter_cutoff
            txt = f"{hz / 1000.0:.1f}k" if hz >= 1000 else f"{int(hz)}"
            out.append((TICKER_OTHER, f"FILTER {tp.filter_mode} {txt}",
                        f"{tp.filter_mode}{txt}"))
        seq = self.sequencer
        if seq is not None and seq.running:
            # `sequencer` is the **active** instrument's since S13, and only
            # the active one can be running (§15.4), so the two always agree.
            # The box is still the source: the ticker names the instrument the
            # box is on, not the one the App thinks is in front.
            target = getattr(seq, "target_id", None) or self.active_instrument_id
            if seq.hold:
                out.append((TICKER_SEQ, "SEQUENCER HOLD", "SEQ HOLD"))
            else:
                out.append((TICKER_SEQ, f"SEQUENCER → {target}",
                            f"SEQ→{target[:3]}"))
        low = battery.low_ticker()
        if low is not None:
            # screen-sketches § FILE › SYSTEM: "a `BATT 12%` ticker appears
            # under 15 %". It is the one status nobody asked for, so it sits
            # at the bottom of the priority list and is never the whole strip.
            out.append((TICKER_OTHER, low[0], low[1]))
        if self._flash is not None and time.monotonic() >= self._flash[2]:
            self._flash = None
        if self._flash is not None and self.powering_off is not None:
            # § 7.4's line is an instruction (wait, then pull the pack or
            # charge with the Pi out), and the strip joins short forms beside
            # any other ticker, `BATT 0%` first among them: so it stands
            # alone (#808), above a prompt's question too (D100, #932).
            long, short, _ = self._flash
            return [(TICKER_FLASH, long, short)]
        if self.prompt is not None:
            return [(TICKER_FLASH, self.prompt.strip, self.prompt.strip)]
        if self._flash is not None:
            long, short, _ = self._flash
            out.append((TICKER_FLASH, long, short))
        out.extend(self.current_page.tickers())
        return out

    def flash(self, long: str, short: str, seconds: float = FLASH_S,
              hold: bool = False):
        """Say something happened, in the strip, then get out of the way.

        ``hold``: § 7.4's power-off line (`PowerPump`), an instruction no
        later flash may replace (#1137: answering the storage question
        flashed TRASH EMPTIED in its place, and the pump says it once). A
        question still up is answered LATER and closed: the line stands
        above it (D100), so a key would answer a question nobody can see.
        """
        if self._flash_held and self._flash is not None and not hold \
                and time.monotonic() < self._flash[2]:
            return
        if hold and self.prompt is not None:
            prompt, self.prompt = self.prompt, None
            prompt.on_answer(1)
        self._flash = (long, short, time.monotonic() + seconds)
        self._flash_held = hold

    def unflash(self):
        """A menu the player just opened owns the strip: an older flash
        would crowd its sentence (#818's grading). Never the power-off
        line (`flash(hold=True)`)."""
        if not self._flash_held:
            self._flash = None

    def help_lines(self) -> List[str]:
        from pages.help import overlay_lines
        return overlay_lines(self)

    # -- input routing ----------------------------------------------------
    def _recall(self, page, store, key: str):
        """A slot letter: make it active and put its record back."""
        record = store.select(key)
        page.state.slot_letter = key
        if record:
            page.restore(record)
        else:
            store.touch()

    def on_key(self, key, pressed: bool, velocity: float = 1.0):
        """Route one key event. ``velocity`` only matters for chromatic keys."""
        self.note_input()                  # § 7.2's idle row (#176)
        key = key.value if hasattr(key, "value") else str(key)
        self.feed.key(key, pressed, velocity)

        if self.file_job is not None:
            #: #821 item 6: a file job holds every key; a played one says
            #: what the instrument is doing. Only the phone cancels it.
            if pressed:
                self.flash(*file_jobs.job_words(self.file_job))
            return
        if (self.bounce_job is not None or self._bounce_ask is not None) \
                and self._bounce_key(key, pressed, velocity):
            return
        if self.prompt is not None and pressed:
            prompt, self.prompt = self.prompt, None
            if key in (K.Key.K1.value, K.Key.K2.value):
                prompt.on_answer(0 if key == K.Key.K1.value else 1)
                self._prompt_keys.add(key)
                return
            prompt.on_answer(1)            # any other key: LATER, and it plays
        if not pressed and key in self._prompt_keys:
            self._prompt_keys.discard(key)  # its press answered the question
            return

        if key == K.Key.SHIFT.value:
            self.shift_held = pressed
            if self.sequencer is not None and self.sequencer.on_shift(
                    pressed, self._page_id == "SEQ" and self.menu is None):
                self.notify_param_changed(self.pages["SEQ"])
            return

        if key == K.Key.HELP.value:
            if pressed:
                self.help_held = True
                self._help_consumed = False
            else:
                self.help_held = False
                if not self._help_consumed:
                    self.goto("HELP")
                self.help_target = None
            return

        if self.help_held and pressed:
            # overlays never navigate (DDR-033)
            self._help_consumed = True
            self.help_target = key
            self._help_swallowed.add(key)
            return
        if not pressed and key in self._help_swallowed:
            # its press was the overlay's, so its release is too
            self._help_swallowed.discard(key)
            return
        if self.help_held:
            # a key held from before HELP went down still lets go (#623):
            # a note stops, REC and STOP stop being held
            self._help_consumed = True

        if pressed and (key in TRANSPORT_KEYS or K.is_note(key)):
            # #1123: these never reach the page, so `1 2 PLAY 3 4` ran RESET.
            # The key still does its own job after putting the run down.
            cancel = getattr(self.pages.get("FILE"), "cancel_confirm", None)
            if cancel is not None:
                cancel()

        if key in TRANSPORT_KEYS:
            if key == "REC":
                # held REC arms: a note played now punches straight into a
                # take, and TRACK's cursor goes hot while you reach for the key
                self.rec_held = pressed
            if key == "STOP":
                # held STOP turns ‹ › into the jump keys (DDR-057); the press
                # still stops, which is what you want before you travel
                self.stop_held = pressed
            if pressed:
                self._transport(key)
            return

        if K.is_note(key):
            note = K.NOTE_BASE_MIDI + K.note_index(key)
            # a page may claim a chromatic key as part of a gesture rather
            # than as a note — DRUM's LIFT/DROP pad copy (§15.3). It gets
            # first refusal, and refusing is the normal answer.
            if not pressed and key in self._key_owner:
                # the page that claimed the press gets its release (#623)
                self._key_owner.pop(key).on_note_key(note, False)
                return
            # Presses only (#873): a release with no owner is a note's, and
            # goes to the instrument that sounded it even while LIFT is held.
            hook = getattr(self.current_page, "on_note_key", None)
            if (pressed and hook is not None and self.menu is None
                    and hook(note, True)):
                self._key_owner[key] = self.current_page
                return
            if pressed and self.rec_held:
                self._rec_from_note()
            seq = self.sequencer
            if seq is not None and seq.on_note(
                    note, pressed,
                    self._page_id == "SEQ" and self.menu is None):
                self.notify_param_changed(self.pages["SEQ"])   # autosave (DDR-029)
                if not seq.entry:
                    return
                # entry mode: the step is recorded AND you hear what you play,
                # so a pattern can be entered by ear.
            if pressed:
                self._note_owner[note] = self.active_instrument_id
                self.instrument.note_on(note, float(velocity))
            else:
                # release it on whichever instrument sounded it: the page (and
                # so the active instrument, DDR-034) can change while a key is
                # held, and the note_off used to land on the wrong one.
                owner = self._note_owner.pop(note, self.active_instrument_id)
                self.instruments[owner].note_off(note)
            return

        if not pressed:
            owner = self._key_owner.pop(key, None) or self.current_page
            owner.on_key(key, False, self.shift_held)
            return

        # --- page keys: navigate, and dismiss any open menu ---------------
        if key in [k.value for k in K.PAGE_KEYS]:
            if key == K.Key.TEMPO.value:
                self._tap_tempo()
            if self.menu is not None:
                self.close_menu()
                if key == self._page_id and not self.shift_held:
                    return
            if (key == K.Key.SEQ.value and self._page_id == "SEQ"
                    and not self.shift_held and self.sequencer is not None):
                # already here: the key arms and disarms the pattern (DDR-035)
                self.sequencer.toggle_active()
                self.notify_param_changed(self.pages["SEQ"])
                return
            self.goto(key)
            if self.shift_held:
                hook = getattr(self.pages[key], "on_shift_page_key", None)
                if hook is not None:
                    hook()
                    # a held view (SHIFT+MIXER): the release puts it away
                    # where the gesture landed (the page, or the menu it
                    # opened), wherever you are by then (#873)
                    self._key_owner[key] = self.current_page
            return

        # --- 1-4 dismiss a menu the same way a page key does ---------------
        # SHIFT+1 opened it, so 1 closes it and selects that subpage.
        if key in [k.value for k in K.NUMBER_KEYS] and self.menu is not None:
            self.close_menu()
            self._key_owner[key] = self.current_page
            hook = getattr(self.current_page, "on_menu_number", None)
            if hook is not None:
                hook(key)
            else:
                self.current_page.on_key(key, True, self.shift_held)
            return

        # --- slot letters -------------------------------------------------
        page = self.current_page
        self._key_owner[key] = page
        if key in SLOT_LETTERS and self.menu is None:
            # the page gets first refusal: on TRACK and SEQ these six keys
            # carry their brown legends instead (control-inventory § tape keys)
            if page.on_key(key, True, self.shift_held):
                return
            store = getattr(page, "slot_store", None)
            if store is not None:
                # a record naming an unsigned box asks first (#1161); CANCEL
                # stays on the slot that was playing, so no autosave writes
                # that sound over the record that was asked about
                if box_trust.ask_ids(
                        self, box_trust.record_ids(self, page,
                                                   store.get(key)),
                        lambda: self._recall(page, store, key)):
                    return
                self._recall(page, store, key)
                # SHIFT lands on the slot *and* opens the whole-sound browser
                # for it — the OP-1's SHIFT+1-8, on our lettered slot keys.
                if self.shift_held:
                    opener = getattr(page, "open_patch_menu", None)
                    if opener is not None:
                        opener()
                return

        page.on_key(key, True, self.shift_held)

    def panic(self):
        """All notes off, everywhere, and the modifiers let go.

        The rescue hatch for a stuck voice: an unfocused window eats a key
        release, a MIDI cable is unplugged mid-note. Transport and tape are
        left alone — this silences the live instruments, it does not stop the
        song.
        """
        for inst in self.instruments.values():
            inst.all_notes_off()
        self.native.panic()
        self._note_owner.clear()
        self.shift_held = False
        self.help_held = False
        self.help_target = None
        self.rec_held = False
        self.stop_held = False
        # no key is held now, so no release has an owner or belongs to the
        # overlay: a later one is a stray, not the end of a gesture (#873)
        self._key_owner.clear()
        self._help_swallowed.clear()
        for seq in self.sequencers.values():
            if seq is not None:
                seq.on_panic()
        # a page may be mid-gesture with a key the release never came for —
        # SYNTH's bend is the first (DDR-052). The instruments are already
        # clear; this is the page's own view of the held control.
        for page in self.pages.values():
            hook = getattr(page, "on_panic", None)
            if hook is not None:
                hook()

    def _transport(self, key: str):
        """PLAY / STOP / REC mean the same thing on every page (workflow §2.2)."""
        was_recording = self.tape.recording
        count_in = self.count_in_frames()
        if key == "PLAY":
            self.tape.play(count_in)
        elif key == "REC":
            if self._rec_refused():
                return
            self.tape.rec(count_in)
        elif key == "STOP":
            if self.shift_held:
                # DDR-055 puts a browser's lift group under the same key. It
                # only outranks the tape's undo while a browser is open or
                # something is still held — otherwise SHIFT+STOP would stop
                # meaning "undo the edit I just made on this page".
                if (self.menu is not None or self.files.lifted is not None) \
                        and self.files.undo():
                    return
                # DDR-054: on SYNTH/DRUM the key undoes the last edit *there*
                # instead of the tape's — never both, and never the tape when
                # the page has nothing left (`tape-sampling-workflow.md`
                # § Gestures). The transport does not move either: the page
                # said "undo", not "stop".
                # the page *under* any browser: a browser open over SYNTH is
                # how a patch was loaded, so undoing there is undoing that
                undo_edit = getattr(self.pages[self._page_id], "undo_edit", None)
                if undo_edit is not None:
                    undo_edit()
                    return
                # SHIFT+STOP = 1-level undo, and only that: it never stops
                # the transport or commits a take (#932). What it should mean
                # on the pages that have no edits of their own is Elvin's
                # (desk M5); until then it is the tape's undo there too.
                self.tape.undo()
                return
            else:
                self.tape.stop()
        self._native_transport(key, was_recording)
        if key == "STOP" and not self.shift_held:
            # audio hits disk at safe moments, and after the take is adopted
            self.project.save_audio()

    def sampler_file(self, stem: str = "tape") -> Optional[str]:
        """A free `<library>/synth/sampler/<stem>-NN.wav`, or None when the
        card refuses the folder.

        The same folder INPUT's captures land in, and the same naming: content
        made on the device goes where the browser already looks (§7).

        #1123 (findings m-emu-pages risk 11): making the folder is a write. On
        a full card, or one mounted read-only after a power cut, it raised
        inside BOUNCE's or DROP's key handler and took the UI down; the
        callers now say REFUSED instead.
        """
        from .mic import next_sample_path
        try:
            return next_sample_path(
                os.path.join(self.library.root, "synth", "sampler"), stem)
        except OSError as exc:
            print("sampler: no file for %s (%s)" % (stem, exc))
            return None

    def bar_samples(self) -> float:
        return 4.0 * 60.0 / max(1.0, self.bpm) * self.sample_rate

    def count_in_frames(self) -> int:
        """TEMPO's COUNT-IN, in frames. 0 = punch in immediately, as before."""
        page = self.pages.get("TEMPO")
        return int(getattr(page, "count_in_bars", 0) * self.bar_samples())

    def rec_tick(self) -> float:
        """The grid REC+note snaps to: TRACK's current tick (DDR-058).

        Zoom is the precision control, so a punch-in lands on whatever the
        ruler is showing — bars zoomed out, a sixteenth zoomed in. From a page
        that has no ruler the answer is a bar, which is where it used to be.
        """
        page = self.pages.get("TRACK")
        tick = getattr(page, "tick", None)
        return tick().samples if tick is not None else self.bar_samples()

    def _rec_refused(self) -> bool:
        """No take while the library is a computer's (#303, run's call).

        USB storage mode parks the tape off `/data` (`ProjectStore.park`), so a
        take now would land in a RAM folder and be gone when the library comes
        back. Entering the mode stopped the transport and disarmed every
        track (DDR-021's save), so REC is the only way a take could start.
        """
        if not self.library_held:
            return False
        self.flash("NO REC — LIBRARY ON USB", "ON USB")
        return True

    def _edit_refused(self) -> bool:
        """No `tape_edit` while the library is a computer's (#617).

        REV / SPEED / FILTER baked while stopped, MERGE and the bounces all
        write the medium, and a parked medium is scratch on `/run`: the clip
        would be moved to an offset in it and point into other audio once the
        real medium is back. `Tape._edit` asks this before every one."""
        return self.library_refused("EDIT")

    def library_refused(self, what: str) -> bool:
        """Any write to the library refused while it is a computer's (#617,
        #876): True, and the strip says `NO <what> — LIBRARY ON USB`. Asked
        before a destination path is even made: `sampler_file` and a new
        folder create directories, and on the bare mount point that is
        already a write."""
        if not self.library_held:
            return False
        self.flash("NO %s — LIBRARY ON USB" % what, "ON USB")
        return True

    def _rec_from_note(self):
        """REC held + a note key: start the take on the grid, from that note.

        Hunting for the downbeat after PLAY is how takes end up a few
        milliseconds off and never layer. Here the first note *is* the start:
        the playhead snaps to the nearest tick, the selected track arms, and
        recording rolls — so every take begins on the grid and locks up
        with the ones under it.
        """
        tp = self.tape
        if tp.playing:
            return False          # already rolling: REC alone punches in
        if self._rec_refused():
            return False
        tick = max(1.0, self.rec_tick())
        tp.seek(round(tp.playhead / tick) * tick)
        tp.armed[tp.selected] = True
        tp.play()
        # the host has to know now, not at the next frame: the take's first
        # block is the note you just played
        self._native_transport("PLAY")
        return tp.recording

    def _tap_tempo(self):
        """3+ TEMPO taps inside 2 s set the BPM (control-inventory § left block)."""
        now = self.tap_clock()
        taps = [t for t in self._tap_times if now - t <= TAP_WINDOW_S]
        taps.append(now)
        self._tap_times = taps[-8:]
        if len(self._tap_times) < TAP_MIN:
            return
        gaps = [b - a for a, b in zip(self._tap_times, self._tap_times[1:])]
        avg = sum(gaps) / len(gaps)
        if avg <= 0:
            return
        self.set_bpm(60.0 / avg)

    def set_bpm(self, bpm: float):
        page = self.pages.get("TEMPO")
        setter = getattr(page, "set_bpm", None)
        if setter is not None:
            setter(bpm)
        else:
            self.bpm = float(bpm)
        self.native.tempo(self.bpm)              # DDR-016: the grid, not audio

    def on_encoder(self, idx: int, delta: int):
        self.note_input()                  # § 7.2's idle row (#176)
        if self.file_job is not None:
            self.flash(*file_jobs.job_words(self.file_job))
            return
        if self.help_held:
            self._help_consumed = True
            self.help_target = f"ENC{int(idx) + 1}"
            return
        if self.prompt is not None:
            # D111 (#932): a turn answers LATER, as any other key does, and
            # then turns — the question never costs the player a move (D107)
            prompt, self.prompt = self.prompt, None
            prompt.on_answer(1)
        self.current_page.on_encoder(int(idx), int(delta), self.shift_held)
        self.feed.encoder(self, int(idx), int(delta), self.shift_held)

    # -- light or dark (#334) ---------------------------------------------
    @property
    def appearance(self) -> str:
        """The mode the screen is drawing in: `light` or `dark`."""
        return palette.mode()

    def set_appearance(self, mode, persist: bool = True) -> bool:
        """Draw in `mode` from the next frame, and remember it.

        The phone's `appearance` push lands here (PROTOCOL.md § The phone's
        appearance), as do the emulator's `V` key and `--appearance`. With no
        phone the screen keeps the last mode it had, so the setting is written
        on every change. Anything but `light`/`dark` is ignored: a push from a
        newer build must not blank the screen. True when the mode is `mode`.
        Under `ARB_APPEARANCE` the mode still changes and nothing is written.
        """
        if mode not in palette.MODES:
            return False
        if mode != palette.mode():
            palette.set_mode(mode)
        # a pinned run (screenshots, tests) leaves no trace in the library
        if persist and not os.environ.get(APPEARANCE_ENV):
            self.settings.set("system", "appearance", mode)
        return True

    def _appearance_heard(self, mode) -> None:
        """The host's `appearance` push: kept for the next `tick` (#693)."""
        self._appearance_pending = mode

    def adopt_host_appearance(self) -> None:
        """Ask the host which mode the phone last sent and take it.

        A UI that starts (or restarts) after the phone said so would otherwise
        wait for the phone's next change. A host nobody has told answers
        null and the screen keeps the instrument's own setting; under
        `ARB_APPEARANCE` the pin wins.
        """
        if os.environ.get(APPEARANCE_ENV):
            return
        mode = self.native.appearance()
        if mode:
            self.set_appearance(mode)

    # -- render / audio ---------------------------------------------------
    def render(self, fb=None) -> np.ndarray:
        return ui_frame.render_frame(self, fb)
