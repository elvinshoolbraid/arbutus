"""The emulator's client for `arbutus-audio` — the only audio path (stage N1).

The C++ process (`audio/`, `docs/design/engine-spec.md` §8) makes every
sample; `App` raises without one and `emulator/run.sh` starts it. The Python
audio graph this module once sat beside is deleted (last commit with it:
`3f64451`).

What moved, and what did not:

| | was (Python, ≤ 2026-09-17) | is |
|---|---|---|
| sound | `engines/*.py` through `Instrument` | boxes in the audio process |
| the 8 slots | built from `engine.get_parameters()` | built from the host's `state` descriptor |
| tape clip model | `app/tape.py` | **still `app/tape.py`** — pushed down as `set_clips` |
| tape audio | numpy in `Tape.render` | the host's eight media |
| pages, chrome, look editor | unchanged | unchanged |

This module owns the connection and the state mirror. `native_library.py`
turns the host's box list into the emulator's `Library`, `native_engine.py`
wears the `ISynthEngine` face the pages already speak, and `pages/rack.py`
draws the §6 rack.
"""

import os
import select
import subprocess
import sys
import time
from typing import Callable, List, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # emulator/
REPO = os.path.dirname(HERE)
AUDIO = os.path.join(REPO, "audio")
CTL_DIR = os.path.join(AUDIO, "tools", "arbutus-ctl")
if CTL_DIR not in sys.path:
    sys.path.insert(0, CTL_DIR)

EXE = os.path.join(AUDIO, "build", "host", "arbutus-audio")
BOXES = os.path.join(AUDIO, "build", "boxes")
LIBRARY = os.path.join(AUDIO, "library")

BUILD_COMMAND = ("cmake -S audio -B audio/build -G Ninja && "
                 "cmake --build audio/build")

PROTO = 1
ABI = 2

#: How long an **attached** client waits for the host's socket to appear
#: (audit Q2, #31). `arbutus-ui.service` is `After=arbutus-audio.service`,
#: and on a `Type=simple` unit that is "after the fork": the host binds its
#: socket only once `AudioHost::init` (every `--library` root scanned, every
#: box opened) and the ALSA device open have finished, so the UI can be
#: attaching to a socket that does not exist yet. The wait used to be 1 s
#: (20 tries of `arbutus_ctl.connect`'s 50 ms), which is a number nobody
#: measured on a board with a full `/data`; past it the UI exited, systemd
#: restarted it 2 s later and the boot lost ~4 s and left a red unit in
#: `systemctl --failed`. § 2.1's boot ceiling is the honest bound.
ATTACH_WAIT_S = 20.0
_ATTACH_RETRIES = int(ATTACH_WAIT_S / 0.05)   # connect() sleeps 50 ms a try

#: messages that change the state document. The host pushes `state` after any
#: edit, but a push arrives *after* the reply, so the client would draw one
#: frame of stale values. Asking for the document instead makes every edit
#: synchronous: the page always renders what the host actually holds, which is
#: the §6 rule ("the UI never computes rack state") applied to everything.
STATEFUL = frozenset((
    "load_engine", "unload_engine", "load_patch", "load_rack", "load_kit",
    "set_param", "set_macro", "bind", "unbind", "set_range", "add_tab",
    "remove_tab", "set_tab_engine", "set_sidechain", "set_bypass",
    "env", "tempo", "transport", "arm", "record", "set_clips", "mixer", "eq",
    "instrument", "reset", "rescan", "metronome", "monitor", "tape_speed",
    "tape_filter", "tape_load", "tape_dir", "set_note_param", "copy_pad",
    "add_mod_tab", "remove_mod_tab", "set_mod_tab_engine", "input_source",
    "route", "unroute", "set_route", "set_mod_bypass", "set_mod_sidechain",
    "host_param"))

#: §6.8 / DDR-042; the *placement* is the dynamic strip, which is where every
#: other non-universal status goes (DDR-031). The words are D103's (Elvin, Play
#: date 1, #843): `CPU UNHAPPY: OVERLOAD`, the same as the app's Status, which
#: replaced the spec's `CPU NO HAPPY — EFFECT TOO BIG`. The short form keeps
#: the app's first two words (run's call, 2026-10-14).
XRUN_LONG = "CPU UNHAPPY: OVERLOAD"
XRUN_SHORT = "CPU UNHAPPY"
XRUN_FLASH_S = 3.0

#: C1 step 7 (`tape-streaming-design.md` § 3.7): the card did not deliver a
#: piece of tape in time and a track played silence for that moment. **Elvin's
#: words, § 7 question 2 answered 2026-09-23: `HARD DRIVE SLOW`, not the
#: note's own `TAPE LATE`** — and in the slot `CPU UNHAPPY` uses, on the same
#: "3 in 2 s" rule, which is why both read their flag off the push rather than
#: counting here.
LATE_LONG = "HARD DRIVE SLOW — TAPE PLAYED SILENCE"
LATE_SHORT = "HARD DRIVE SLOW"

#: The three ways a take ends early (§ 3.7), in **two** words — Elvin, § 7
#: question 3: the player's next action differs. `TAPE FULL` means delete
#: something (`disk`) or the medium is at its end (`format`); `CARD STALLED`
#: means the card stopped answering and it is worth trying again.
TAPE_FULL_LONG = "TAPE FULL — NOTHING MORE FITS"
TAPE_FULL_SHORT = "TAPE FULL"
CARD_STALLED_LONG = "CARD STALLED — TAKE ENDED EARLY"
CARD_STALLED_SHORT = "CARD STALLED"
#: `reason` -> what the strip says. An unknown reason reads as `TAPE FULL`,
#: which is the safe half of the pair: it never tells a player to try again.
TAPE_FULL_WORDS = {
    "stall": (CARD_STALLED_LONG, CARD_STALLED_SHORT),
    "disk": (TAPE_FULL_LONG, TAPE_FULL_SHORT),
    "format": (TAPE_FULL_LONG, TAPE_FULL_SHORT),
}


#: see `NativeAudio.__init__`
_SOCKET_SEQ = 0


def _die_with_parent():
    """A `preexec_fn` that ends a spawned host with the process that spawned it.

    The host is a socket server: nothing tells it to stop except our `close()`,
    and a test process killed outright (CI cancelling a superseded gate) never
    gets there, so its hosts ran on for days (#517). On Linux `prctl(PR_SET_
    PDEATHSIG, SIGTERM)` has the kernel send SIGTERM when the parent dies, and
    the host exits cleanly on it, unlinking its socket. The signal follows the
    *thread* that forked, so a host spawned from a short-lived thread would die
    with that thread; nothing here spawns one off the main thread. None (no
    hook) elsewhere: macOS has no such call, and a Mac has no CI runner.
    """
    if not sys.platform.startswith("linux"):
        return None
    import ctypes                                               # noqa: PLC0415
    import signal                                               # noqa: PLC0415

    libc = ctypes.CDLL(None, use_errno=True)
    parent = os.getpid()

    def hook():
        # Runs in the child between fork and exec. PR_SET_PDEATHSIG is 1.
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)
        if os.getppid() != parent:       # the parent died before the line above
            os._exit(1)
    return hook


class NativeUnavailable(RuntimeError):
    """No audio process. Never a silent fall back to the Python path."""


class NativeAudio:
    """One `arbutus-audio` process, its socket, and a mirror of its state.

    The host owns every piece of rack and engine state; this object never
    computes any of it. `state` is the last document the host pushed or
    answered with, and every edit here is a message followed by the document
    that came back.
    """

    def __init__(self, offline: bool = False, sample_rate: int = 48000,
                 block: int = 128, exe: str = EXE,
                 libraries: Optional[List[str]] = None,
                 socket_path: Optional[str] = None,
                 tape_seconds: Optional[float] = None,
                 attach: bool = False, client: str = "arbutus-emulator",
                 extra: Optional[List[str]] = None,
                 tape_dir: Optional[str] = None):
        """Spawn a host and talk to it, or (`attach`) talk to one already up.

        `attach=True` is the device (`device-os-spec.md` § 4.1): systemd owns
        `arbutus-audio`, the socket is `/run/arbutus/audio.sock` and this object
        must neither start it nor shut it down — it connects, and when the unit
        restarts after a crashed box (engine-spec §8.1) it reconnects.

        `extra` goes on the host's command line as it stands. It is how a test
        asks for a flag this class has no opinion about.

        `tape_seconds` is passed as `--tape-seconds` only when given; with
        none the host's own default is the tape's length (#1039: one constant,
        DDR-072's 480 s, which `app/tape.py`'s `TRACK_SECONDS` follows). A
        test asks for less to keep its medium small.

        **The tape is always on storage** (C1 step 3b, #297): a spawned host
        gets `--tape-dir`, `tape_dir` when the caller names one and otherwise
        a private scratch folder this object removes on `close`. The App
        points the tape at the folder it belongs in as soon as it starts
        (`persist.ProjectStore.open_folder`), so the starting folder only has
        to exist. An `extra` that already carries `--tape-dir` wins. An
        attached host has its own, from `arbutus-audio.service`.
        """
        if attach:
            if not socket_path:
                raise NativeUnavailable("attach needs a socket path")
        elif not os.path.isfile(exe) or not os.access(exe, os.X_OK):
            raise NativeUnavailable(
                "no arbutus-audio at %s — build it first:\n    %s"
                % (exe, BUILD_COMMAND))
        from arbutus_ctl import ProtocolError, connect      # noqa: E402

        self._ProtocolError = ProtocolError
        self._connect = connect
        self.attached = bool(attach)
        self.client = client
        self.offline = bool(offline)
        self.sample_rate = int(sample_rate)
        self.block = int(block)
        self.libraries = list(libraries or [BOXES, LIBRARY])
        # C5b (#98): `waveform` replies, cached until a non-meters push.
        # `_wave_gen` is bumped by every drop, so a reply that raced a push
        # while it was in flight is not stored on top of the drop.
        self._wave_cache: dict = {}
        self._wave_gen = 0
        #: #678: moves on every push but `meters` and on a reconnect — the
        #: host told the UI something its screen may show. The device's frame
        #: loop draws a frame when it moves (`arbutus_os/frame_gate.py`).
        self.changed = 0

        # One socket per object, not per process: a test file builds several
        # rigs and two hosts sharing a path means the second client connects
        # to the first host, whose pushes then have nobody reading them.
        global _SOCKET_SEQ
        _SOCKET_SEQ += 1
        self.sock = socket_path or os.path.join(
            os.environ.get("TMPDIR", "/tmp"),
            "arbutus-emulator-%d-%d.sock" % (os.getpid(), _SOCKET_SEQ))
        self.proc = None
        #: the scratch `--tape-dir` this object made, removed on `close`
        self._own_tape_dir: Optional[str] = None
        if not self.attached:
            cmd = [exe, "--socket", self.sock, "--quiet",
                   "--sr", str(self.sample_rate), "--block", str(self.block)]
            if tape_seconds is not None:
                cmd += ["--tape-seconds", str(tape_seconds)]
            for root in self.libraries:
                cmd += ["--library", root]
            extra_args = [str(a) for a in (extra or [])]
            if "--tape-dir" not in extra_args:
                if not tape_dir:
                    import tempfile                             # noqa: PLC0415
                    tape_dir = tempfile.mkdtemp(prefix="arbutus-tape-")
                    self._own_tape_dir = tape_dir
                cmd += ["--tape-dir", tape_dir]
            cmd += extra_args
            if self.offline:
                cmd.append("--offline")
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.STDOUT,
                                             preexec_fn=_die_with_parent())
            except OSError as exc:      # passed X_OK, still not runnable (#1180)
                self._drop_own_tape_dir()
                raise NativeUnavailable("cannot start %s: %s" % (exe, exc))
        try:
            self.conn = connect(self.sock,
                                retries=_ATTACH_RETRIES if self.attached else 100)
        except ProtocolError as exc:
            if self.proc is not None:
                self.proc.kill()
                self.proc.wait()
            self._drop_own_tape_dir()       # a host that never answered (#657)
            raise NativeUnavailable(
                "no arbutus-audio on %s: %s" % (self.sock, exc))

        hello = self.conn.hello(PROTO, ABI, self.client)
        if hello.get("type") != "hello" or hello.get("proto") != PROTO \
                or hello.get("abi") != ABI:
            self.close()
            raise NativeUnavailable(
                "handshake refused: %s" % hello.get("message", hello))
        self.firmware = hello.get("firmware", "")
        self.host_block = int(hello.get("block", self.block))
        #: is a motion sensor fitted and answering (device-os-spec.md § 6.5,
        #: PROTOCOL.md § Handshake)? The FILE page's `KEY VEL` row and the
        #: `arbutus/motion` box's `NO SENSOR` are drawn from this. False on a
        #: laptop and on every board but P3.
        self.motion_sensor = False
        self._note_motion_sensor(hello)

        self.state = self.conn.state()
        self._note_motion_sensor(self.state)
        self.meters = {}
        #: finished takes the host has pushed, oldest first (PROTOCOL.md
        #: `take`). The UI drains these rather than diffing two clip lists:
        #: since stage E1 a take *absorbs* what it covered, so the host's list
        #: can come back the same length it went in.
        self.takes: list = []
        self.song_frame = 0
        #: where the *tape* is. The clock (`song_frame`) is the ruler and runs
        #: forward at 1x; SPEED, REV and the loop move this one.
        self.tape_pos = 0
        self.xrun_flag = False
        #: The same edge rule for `tape_late` (C1 step 7): the flag is the
        #: host's, and the strip says it once per rise rather than per push.
        self.late_flag = False
        #: True once the socket has died under us. Only an attached host
        #: (`device-os-spec.md` § 4.2) is expected to come back; the emulator
        #: spawns its own and a dead one there is fatal, as it always was.
        self.link_down = False
        #: app.flash / app.tick hooks, set by App when it adopts this object
        self.on_xrun: Optional[Callable[[str, str], None]] = None
        self.on_meters: Optional[Callable[[dict], None]] = None
        #: C1 step 8 (#164): a finished compaction, whose `map` the UI remaps
        #: its own geometry through. Only the push carries it — the reply to
        #: `tape_compact` says `started` and nothing more.
        self.on_compact: Optional[Callable[[dict], None]] = None
        #: Bumped whenever a medium (the file a track's audio lives in) may
        #: have been replaced: `tape_dir`, `tape_load`, a finished compaction.
        #: A cache of audio read back out of the host (TRACK's waveforms) is
        #: stale once it moves, because a new take can sit at an old offset
        #: (#632, review m-emu-pages row 4).
        self.media_gen = 0
        #: #920: a bounce job ended -- `bounce {path}`, `{cancelled}` or
        #: `{error}`. The reply to `clip_to_asset {job}` only says `started`.
        self.on_bounce: Optional[Callable[[dict], None]] = None
        #: #334: the phone's light or dark, pushed by the host whenever a
        #: client sets it (PROTOCOL.md § The phone's appearance)
        self.on_appearance: Optional[Callable[[str], object]] = None
        #: #821: the phone renamed the open project and the host renamed its
        #: folder (`tape_dir_renamed {from, to}`); the UI's autosave follows
        self.on_tape_dir_renamed: Optional[Callable[[dict], None]] = None
        #: #853: how many phones watch the live stream, as the host last said
        #: (the reply to `ui_situation`, a `live {watchers}` push). The UI
        #: hashes its frames for `ui_frame` only while it is above 0.
        self.live_watchers = 0
        self.errors: List[str] = []
        self.send({"type": "meters", "on": True, "hz": 30})

    # -- lifecycle --------------------------------------------------------
    def close(self):
        """Let go of the host. An attached host is never shut down or unlinked.

        On the device the unit outlives the UI (§ 4.1) and `Restart=always`
        would bring it back anyway: a UI that shut it down on exit would take
        the sound away on every `systemctl restart arbutus-ui`.
        """
        if not self.attached:
            try:
                self.conn.request({"type": "shutdown"})
            except Exception:                               # noqa: BLE001
                pass
        try:
            self.conn.close()
        except Exception:                                   # noqa: BLE001
            pass
        if self.proc is not None:
            try:
                self.proc.wait(timeout=3)
            except Exception:                               # noqa: BLE001
                self.proc.kill()
        if not self.attached and self.sock and os.path.exists(self.sock):
            try:
                os.unlink(self.sock)
            except OSError:
                pass
        self._drop_own_tape_dir()

    def _drop_own_tape_dir(self):
        if self._own_tape_dir:
            import shutil                                       # noqa: PLC0415
            shutil.rmtree(self._own_tape_dir, ignore_errors=True)
            self._own_tape_dir = None

    # -- reconnect (device-os-spec § 4.2) ---------------------------------
    def connected(self) -> bool:
        """Has the socket died under us? Free — a flag, not a round trip.

        There is no `ping` in `PROTOCOL.md` and inventing one to poll a live
        host every frame would be worse than watching the two places a death
        actually shows: a failed `send` and a failed `poll`.
        """
        return not self.link_down

    def reconnect(self, tries: int = 8) -> bool:
        """Re-open the socket after the host restarted; `hello` and meters back.

        `device-os-spec.md` § 4.2: "`arbutus-audio` restarting is normal (a
        crashed box, engine-spec §8.1): the UI keeps its own state, reconnects
        with backoff (0.2 s → 2 s), re-sends `hello` …". This half is the
        connection; the state walk that follows is
        `app/project_file.py::rewalk`, because what to push down is the UI's
        business and not the socket's.

        Returns True once `hello` is accepted. The host it reconnects to is a
        **fresh** one: no engines, no tape, boot defaults.
        """
        try:
            self.conn.close()
        except Exception:                                   # noqa: BLE001
            pass
        delay = 0.2
        for attempt in range(max(1, int(tries))):
            if attempt:
                time.sleep(delay)
                delay = min(2.0, delay * 2)
            try:
                conn = self._connect(self.sock, retries=1)
            except Exception:                               # noqa: BLE001
                continue
            try:
                hello = conn.hello(PROTO, ABI, self.client)
            except Exception:                               # noqa: BLE001
                try:
                    conn.close()
                except Exception:                           # noqa: BLE001
                    pass
                continue
            if hello.get("type") != "hello" or hello.get("proto") != PROTO \
                    or hello.get("abi") != ABI:
                self.errors.append("handshake refused on reconnect")
                try:
                    conn.close()
                except Exception:                           # noqa: BLE001
                    pass
                continue
            self.conn = conn
            self.firmware = hello.get("firmware", self.firmware)
            self.host_block = int(hello.get("block", self.host_block))
            # § 4.2: the host that comes back may not be the one that went
            # away (a restart can follow a sensor being unplugged), so the
            # flag is taken from the new handshake, never kept across it.
            self._note_motion_sensor(hello)
            self.takes.clear()
            self.meters = {}
            self._wave_cache.clear()
            self._wave_gen += 1
            self.changed += 1
            self.xrun_flag = False
            self.late_flag = False
            self.link_down = False
            self.send({"type": "meters", "on": True, "hz": 30})
            self.state = self.conn.state()
            self._note_motion_sensor(self.state)
            return True
        return False

    # -- messaging --------------------------------------------------------
    def send(self, msg: dict) -> dict:
        """One message, one reply. A refusal is recorded, never raised.

        §7.4 refusals are for the player, not for a traceback: the caller gets
        the reply and the message goes in the strip.
        """
        try:
            reply = self.conn.request(msg)
        except Exception as exc:                            # noqa: BLE001
            self.errors.append(str(exc))
            self.link_down = True
            return {"type": "error", "code": "disconnected", "message": str(exc)}
        if reply.get("type") == "error":
            self.errors.append(reply.get("message") or reply.get("code", "?"))
        self._absorb_pushes()
        if msg.get("type") in STATEFUL:
            try:
                self.state = self.conn.state()
            except Exception as exc:                        # noqa: BLE001
                self.errors.append(str(exc))
            self._absorb_pushes()
        return reply

    def publish(self, msg: dict) -> bool:
        """D17 (#276): one of the UI's own facts for the phone (`ui_elements`,
        `ui_action`). Unlike `send`, a refusal is not the player's business and
        never reaches the strip, and a link that is down is not tried: the
        reconnect walk republishes the map. True when the host took it."""
        if self.link_down:
            return False
        try:
            reply = self.conn.request(msg)
        except Exception:                                   # noqa: BLE001
            self.link_down = True
            return False
        self._absorb_pushes()
        if isinstance(reply.get("watchers"), int):
            self.live_watchers = reply["watchers"]
        return reply.get("type") == "ok"

    def appearance(self) -> Optional[str]:
        """The mode the phone last sent the host, or None (#334).

        None too when nobody has said, the link is down, or the host is a
        build without the message: none of those is the player's business,
        so like `publish` nothing reaches the strip.
        """
        if self.link_down:
            return None
        try:
            reply = self.conn.request({"type": "appearance"})
        except Exception:                                   # noqa: BLE001
            self.link_down = True
            return None
        self._absorb_pushes()
        mode = reply.get("mode") if reply.get("type") == "appearance" else None
        return mode if isinstance(mode, str) else None

    def take_error(self) -> Optional[str]:
        return self.errors.pop() if self.errors else None

    def poll(self):
        """Drain whatever the host pushed. Called once a frame from App.tick.

        A push usually arrives in the same write as the reply before it, so it
        is already in the client's line buffer and the socket has nothing to
        report — that buffer is drained first, then the socket.
        """
        conn = self.conn
        for _ in range(256):
            try:
                if b"\n" in conn._buf:                      # noqa: SLF001
                    conn._pushes.append(conn.recv())        # noqa: SLF001
                    continue
                if not select.select([conn._sock], [], [], 0)[0]:   # noqa: SLF001
                    break
                conn._pushes.append(conn.recv())            # noqa: SLF001
            except (OSError, self._ProtocolError) as exc:
                self.errors.append(str(exc))
                self.link_down = True
                break
            except Exception:                               # noqa: BLE001
                break
        self._absorb_pushes()

    def _absorb_pushes(self):
        pushes = self.conn.pushes()
        if not pushes:
            return
        # C5b (#98): a `waveform` reply only goes stale when the sample does,
        # and a new sample reaches the UI as a push. `meters` arrives at 30 Hz
        # whether anything moved or not, so it is the one kind that keeps the
        # cache.
        if any(m.get("type") != "meters" for m in pushes):
            self._wave_cache.clear()
            self._wave_gen += 1
            self.changed += 1
        self.conn.clear_pushes()
        for m in pushes:
            kind = m.get("type")
            if kind == "state":
                self.state = m
                # a sensor that stopped answering arrives here, not at hello
                self._note_motion_sensor(m)
            elif kind == "rack":
                self._merge_rack(m)
            elif kind == "mod":
                self._merge_mod(m)
            elif kind == "meters":
                self.meters = m
                self.song_frame = int(m.get("song_frame", self.song_frame))
                self.tape_pos = int(m.get("tape_pos", self.tape_pos))
                if self.on_meters:
                    self.on_meters(m)
            elif kind == "take":
                self.takes.append(m)
            elif kind == "xrun":
                flag = bool(m.get("flag"))
                if flag and not self.xrun_flag and self.on_xrun:
                    self.on_xrun(XRUN_LONG, XRUN_SHORT)
                self.xrun_flag = flag
            elif kind == "tape_late":
                # The same hook and the same edge as `xrun`: one alarm rule,
                # one slot on the screen, two sets of words.
                flag = bool(m.get("flag"))
                if flag and not self.late_flag and self.on_xrun:
                    self.on_xrun(LATE_LONG, LATE_SHORT)
                self.late_flag = flag
            elif kind == "tape_compact":
                # The medium's audio has moved. Every reference this UI holds
                # moves with it, and the corrected clip list goes back down —
                # the host cannot renumber under a list that is not its own.
                self.media_gen += 1
                if self.on_compact:
                    self.on_compact(m)
            elif kind == "bounce":
                if self.on_bounce:
                    self.on_bounce(m)
            elif kind == "tape_dir_renamed":
                if self.on_tape_dir_renamed:
                    self.on_tape_dir_renamed(m)
            elif kind == "appearance":
                if self.on_appearance and isinstance(m.get("mode"), str):
                    self.on_appearance(m["mode"])
            elif kind == "live":
                if isinstance(m.get("watchers"), int):
                    self.live_watchers = m["watchers"]
            elif kind == "tape_full":
                # Not a flag: a take ended, which happens once and is not a
                # rate. Every one of them is said.
                if self.on_xrun:
                    self.on_xrun(*TAPE_FULL_WORDS.get(
                        str(m.get("reason", "")), (TAPE_FULL_LONG, TAPE_FULL_SHORT)))

    def _merge_rack(self, m: dict):
        inst = m.get("instrument") or self.state.get("active", "synth")
        # the master bus keeps its rack under `state.mixer`, not under an
        # instrument (stage F1) — one document, one home
        slot = (self.state.setdefault("mixer", {}) if inst == "master"
                else self.state.setdefault("instruments", {}).setdefault(inst, {}))
        slot["fx"] = m.get("rack", {})
        slot["tabs"] = m.get("tabs", [])

    def _merge_mod(self, m: dict):
        inst = m.get("instrument") or self.state.get("active", "synth")
        slot = self.state.setdefault("instruments", {}).setdefault(inst, {})
        slot["mod"] = m.get("mod", {})
        slot["mod_tabs"] = m.get("tabs", [])

    def refresh(self) -> dict:
        """The host's `state`, asked now. Guarded like `send` (#1123, app risk
        9): a host that died this frame marks the link down and the last state
        comes back, never a BrokenPipeError out of a key handler."""
        try:
            self.state = self.conn.state()
        except Exception as exc:                            # noqa: BLE001
            self.errors.append(str(exc))
            self.link_down = True
            return self.state
        self._note_motion_sensor(self.state)
        return self.state

    def _note_motion_sensor(self, doc: dict):
        """Both `hello` and `state` carry § 6.5's flag; either may update it.

        Absent means "an older host that does not report it", which reads the
        same as no sensor — the flag is only ever used to *hide* something.
        """
        if "motion_sensor" in doc:
            self.motion_sensor = bool(doc["motion_sensor"])

    # -- state views ------------------------------------------------------
    def instrument(self, inst_id: str) -> dict:
        """The document for one instrument — or for the master bus.

        `master` is a rack and nothing else (§15.5): the host reports it under
        `state.mixer` with the same `fx` / `tabs` / `macros` keys an instrument
        uses, so `rack()`, `tabs()` and `macros()` address it unchanged and
        `pages/rack.py` needs no idea which bus it is drawing.
        """
        key = inst_id.lower()
        if key == "master":
            return self.state.get("mixer") or {}
        return (self.state.get("instruments") or {}).get(key, {})

    def track(self, index: int) -> dict:
        """One tape track's host state — gain, pan, mute, solo, eq (F1)."""
        tracks = (self.state.get("tape") or {}).get("tracks") or []
        return tracks[index] if 0 <= index < len(tracks) else {}

    def track_eq(self, index: int) -> dict:
        """The clamped EQ block the host is actually running (DDR-062)."""
        return self.track(index).get("eq") or {}

    def slot(self, inst_id: str, name: str) -> dict:
        return self.instrument(inst_id).get(name) or {}

    def rack(self, inst_id: str) -> dict:
        return self.instrument(inst_id).get("fx") or {}

    def tabs(self, inst_id: str) -> List[dict]:
        return self.instrument(inst_id).get("tabs") or []

    def macros(self, inst_id: str) -> List[float]:
        return self.instrument(inst_id).get("macros") or [0.0] * 8

    def mod_rack(self, inst_id: str) -> dict:
        return self.instrument(inst_id).get("mod") or {}

    def mod_tabs(self, inst_id: str) -> List[dict]:
        return self.instrument(inst_id).get("mod_tabs") or []

    def mod_route_values(self) -> dict:
        """The `meters.mod` map: target address → live modulated value (§9)."""
        return (self.meters.get("mod") or {})

    # -- offline clock ----------------------------------------------------
    def render(self, frames: int, file: Optional[str] = None) -> dict:
        msg = {"type": "render", "frames": int(frames)}
        if file:
            msg["file"] = file
        reply = self.send(msg)
        self.song_frame = int(reply.get("song_frame", self.song_frame))
        self.tape_pos = int(reply.get("tape_pos", self.tape_pos))
        return reply

    def bake(self, path: str, fmt: str = "wav16", frames: Optional[int] = None,
             tracks: Optional[List[int]] = None) -> dict:
        msg = {"type": "bake", "file": path, "format": fmt}
        if frames is not None:
            msg["frames"] = int(frames)
        if tracks is not None:
            msg["tracks"] = list(tracks)
        return self.send(msg)

    # -- the calls the UI makes ------------------------------------------
    #: a `probe` id (PROTOCOL.md § Timing) for the next note-on this client
    #: writes, then None again. Set by a transport that measures itself —
    #: `arbutus_os/midi.py` under `ARBUTUS_MIDI_PROBE` (P9, #89) — right
    #: before it dispatches one message, so it rides whichever note that
    #: message turns into, and none at all if it turns into no note.
    probe_next: Optional[int] = None

    def note(self, note: int, on: bool, vel: float = 1.0):
        msg = {"type": "note", "on": bool(on), "note": int(note),
               "vel": float(vel)}
        if on and self.probe_next is not None:
            msg["probe"] = int(self.probe_next)
            self.probe_next = None
        self.send(msg)

    def panic(self):
        self.send({"type": "panic"})

    def set_active(self, inst_id: str):
        self.send({"type": "instrument", "active": inst_id.lower()})

    def env(self, inst_id: str, a, d, s, r, mode="poly", glide=None,
            detune=None, bend_range=None):
        """The ENV page, both rows. Every field of `env` is optional on the
        wire, so the SHIFT row (DDR-052) only travels when it has a value."""
        msg = {"type": "env", "instrument": inst_id.lower(),
               "a": float(a), "d": float(d), "s": float(s), "r": float(r),
               "mode": mode}
        for key, value in (("glide", glide), ("detune", detune),
                           ("bend_range", bend_range)):
            if value is not None:
                msg[key] = float(value)
        self.send(msg)

    def bend(self, inst_id: str, value: float):
        """SHIFT+‹ › held, and MIDI bend. The host ramps to `value` over GLIDE
        and `value 0` is the release, so this is one message per edge — not a
        per-frame push, and not stateful."""
        self.send({"type": "bend", "instrument": inst_id.lower(),
                   "value": max(-1.0, min(1.0, float(value)))})

    def set_param(self, inst_id: str, slot: str, idx: int, value: float,
                  tab: Optional[int] = None):
        msg = {"type": "set_param", "instrument": inst_id.lower(),
               "slot": slot, "idx": int(idx), "value": float(value)}
        if tab is not None:
            msg["tab"] = int(tab)
        return self.send(msg)

    # -- per-pad values (§15.3) -------------------------------------------
    def set_note_param(self, inst_id: str, pad: int, idx: int, value: float):
        """One pad's slot. The host writes it into the kit document *and*
        hands it to the engine, so `state` reports it and a saved kit keeps
        it — there is no `save_kit` (PROTOCOL § Per-pad values)."""
        return self.send({"type": "set_note_param", "instrument": inst_id.lower(),
                          "slot": "engine", "pad": int(pad), "idx": int(idx),
                          "value": float(value)})

    def copy_pad(self, inst_id: str, src: int, dst: int):
        """LIFT on a pad, DROP on another (DDR-039): file *and* params. The
        file changes, so the kit reloads — crossfaded, and refusable."""
        return self.send({"type": "copy_pad", "instrument": inst_id.lower(),
                          "from": int(src), "to": int(dst)})

    def waveform(self, bins: int = 256, **where) -> Optional[dict]:
        """A peak envelope, read on the host's control thread (§15.6).

        Addressed by `path=`, by `slot=`/`key=`, or by `pad=` — the caller
        picks one shape; the samples never come over the socket.

        Cached until the host pushes anything but `meters` (C5b, #98): DRUM ›
        ENGINE asks for the same envelope inside `render`, 60 times a second,
        and every ask is a synchronous round trip answered on the host's
        control thread.
        """
        key = (int(bins), tuple(sorted(where.items(), key=lambda kv: kv[0])))
        if key in self._wave_cache:
            return self._wave_cache[key]
        msg = {"type": "waveform", "bins": int(bins)}
        for name, value in where.items():
            if value is not None:
                msg[name] = value.lower() if name == "instrument" else value
        gen = self._wave_gen
        reply = self.send(msg)
        out = None if reply.get("type") == "error" else reply
        if gen == self._wave_gen:
            self._wave_cache[key] = out
        return out

    def set_macro(self, inst_id: str, slot: int, value: float):
        return self.send({"type": "set_macro", "instrument": inst_id.lower(),
                          "slot": int(slot), "value": float(value)})

    def softkey(self, key: int, down: bool, shift: bool = False):
        return self.send({"type": "softkey", "key": int(key),
                          "down": bool(down), "shift": bool(shift)})

    def tempo(self, bpm=None, link=None, base_bpm=None):
        """`{bpm?, link?, base_bpm?}` (DDR-059). LINK is the TEMPO page's key
        and nothing else; `base_bpm` — the tempo the medium is written at —
        moves only when a project load says so (E1)."""
        msg = {"type": "tempo"}
        if bpm is not None:
            msg["bpm"] = float(bpm)
        if link is not None:
            msg["link"] = bool(link)
        if base_bpm is not None:
            msg["base_bpm"] = float(base_bpm)
        return self.send(msg)

    def transport(self, op: str, position: Optional[float] = None):
        msg = {"type": "transport", "op": op}
        if position is not None:
            msg["position"] = int(position)
        reply = self.send(msg)
        if op == "locate" and position is not None:
            # the clock and the tape both move there; no reply carries it, and
            # a caller that reads `tape_pos` next would get the old one
            self.song_frame = self.tape_pos = int(position)
        return reply

    def arm(self, track: int):
        return self.send({"type": "arm", "track": int(track)})

    def record(self, track: int, on: bool):
        return self.send({"type": "record", "track": int(track),
                          "on": bool(on)})

    def tape_compact(self, track: int, keep: List[dict]) -> dict:
        """Give one medium's dead audio back (§ 3.8, PROTOCOL.md).

        `keep` is this UI's **whole** reference set for that medium — its
        clips, its lift buffer and its undo record (`Tape.keep_spans`). The
        reply only says the copy `started`; the map arrives as a push, because
        a full medium is seconds of copying and SAVE may not wait for it.
        """
        return self.send({"type": "tape_compact", "track": int(track),
                          "keep": list(keep)})

    def set_clips(self, track: int, clips: List[dict]):
        return self.send({"type": "set_clips", "track": int(track),
                          "clips": clips})

    # -- the tape keys and the click (stage N1) ---------------------------
    def metronome(self, level: float, pitch: float):
        return self.send({"type": "metronome", "level": float(level),
                          "pitch": float(pitch)})

    def monitor(self, on: bool, gain: float = 1.0):
        return self.send({"type": "monitor", "on": bool(on),
                          "gain": float(gain)})

    # -- the live input (stage G, DDR-063) ---------------------------------
    SOURCES = ("mic", "line", "usb", "resample")

    def input_source(self, source=None):
        """Read or set where the input comes from (`PROTOCOL.md`).

        The host owns it, and `arbutus/live-input`'s SOURCE knob is the same
        value — so a page that turns that knob does not also send this, and a
        page that sends this does not also write the knob.
        """
        if source is None:
            return (self.state.get("input") or {}).get("source", "mic")
        if isinstance(source, int):
            source = self.SOURCES[max(0, min(len(self.SOURCES) - 1, source))]
        return self.send({"type": "input_source", "source": str(source)})

    def loop(self, loop_in: int, loop_out: int, on: bool):
        return self.send({"type": "transport", "op": "loop",
                          "in": int(loop_in), "out": int(loop_out),
                          "on": bool(on)})

    def tape_speed(self, speed: float, reverse: bool):
        return self.send({"type": "tape_speed", "speed": float(speed),
                          "reverse": bool(reverse)})

    def tape_filter(self, on: bool, cutoff: float, mode: str = "LP"):
        return self.send({"type": "tape_filter", "on": bool(on),
                          "cutoff": float(cutoff), "mode": mode})

    def scrub(self, position: float, frames: int, reseed: bool = True):
        return self.send({"type": "scrub", "position": int(position),
                          "frames": int(frames), "reseed": bool(reseed)})

    def clip_peaks(self, track: int, src_offset: int, length: int,
                   bins: int, src_track: Optional[int] = None) -> Optional[dict]:
        """TRACK's waveform: min/max per bin, read out of the host's medium."""
        msg = {"type": "clip_peaks", "track": int(track),
               "src_offset": int(src_offset), "len": int(length),
               "bins": int(bins)}
        if src_track is not None:
            msg["src_track"] = int(src_track)
        reply = self.send(msg)
        return None if reply.get("type") == "error" else reply

    #: the host answers at most this many requests in one `clip_peaks` batch
    #: (`AudioHost::kPeaksBatchMax`), so a longer want-list is chunked.
    PEAKS_BATCH_MAX = 64

    def clip_peaks_batch(self, reqs: List[dict]) -> Optional[List[dict]]:
        """Several clips' peaks in one round trip (#169).

        A redraw of TRACK is one `clip_peaks` per clip drawn, and on the CM4 a
        round trip costs 2.1-2.5 ms whatever the host does with it — the
        message itself. One message for the whole bank removes all but one of
        those fixed costs. `reqs` are `{track, src_offset, len, bins,
        src_track?}` dicts; the reply is one body per request, in order.
        Returns None if any chunk is refused — the caller then asks one at a
        time, which is the path that was there before this.
        """
        if not reqs:
            return []
        out: List[dict] = []
        for i in range(0, len(reqs), self.PEAKS_BATCH_MAX):
            chunk = reqs[i:i + self.PEAKS_BATCH_MAX]
            reply = self.send({"type": "clip_peaks", "reqs": chunk})
            if reply.get("type") == "error":
                return None
            got = reply.get("peaks")
            if not isinstance(got, list) or len(got) != len(chunk):
                return None
            out.extend(got)
        return out

    def tape_edit(self, track: int, sources: List[dict], length: int,
                  op: str = "copy", **kw) -> dict:
        msg = {"type": "tape_edit", "track": int(track), "len": int(length),
               "op": op, "sources": sources}
        msg.update(kw)
        return self.send(msg)

    def clip_to_asset(self, track, start_frame: int, length: int,
                      file: Optional[str] = None,
                      master_fx: bool = False,
                      job: bool = False) -> Optional[dict]:
        """A window of the timeline as a 16-bit WAV (§15.12) -> `path`.

        `track` is 0..7 for that track's own clips, or `"all"` for the mixdown.
        `master_fx` is FILE › BOUNCE's switch (stage J1): the mixdown leaves
        COMP and the master rack out, and this puts the rack back in, latency
        compensated by the host. It means nothing on a single track.
        `job` (#920) returns at once with `status: started`; the file is
        written when the render ends and `on_bounce` hears of it.
        """
        msg = {"type": "clip_to_asset",
               "track": track if track == "all" else int(track),
               "start_frame": int(start_frame), "len": int(length)}
        if file:
            msg["file"] = file
        if master_fx:
            msg["master_fx"] = True
        if job:
            msg["job"] = True
        reply = self.send(msg)
        return None if reply.get("type") == "error" else reply

    def bounce_cancel(self) -> dict:
        """Drop a running bounce job (#920); nothing has been written."""
        return self.send({"type": "bounce_cancel"})

    def tape_save(self, track: int, path: str) -> dict:
        return self.send({"type": "tape_save", "track": int(track), "file": path})

    def tape_load(self, track: int, path: str) -> dict:
        self.media_gen += 1
        return self.send({"type": "tape_load", "track": int(track), "file": path})

    def tape_dir(self, folder: str, move: bool = False) -> dict:
        """Where the tape lives (C1 step 3b, `PROTOCOL.md` § `tape_dir`).

        `move` is SAVE's rename of every medium into `folder`; without it the
        media already in `folder` are opened in place (LOAD, NEW, a boot).
        """
        self.media_gen += 1
        msg = {"type": "tape_dir", "dir": folder}
        if move:
            msg["move"] = True
        return self.send(msg)

    def mixer(self, **kw):
        """Track gain/pan/mute/solo and the MASTER strip.

        Anything the host's `mixer` message takes: `track` with `gain`, `pan`,
        `mute` or `solo` (DDR-061), and `master`, `comp`, `comp_rel`, `drive`,
        `drive_tone`, `bal` for the MASTER page (§15.5).
        """
        msg = {"type": "mixer"}
        msg.update(kw)
        return self.send(msg)

    def eq(self, track: int, **kw):
        """One track's EQ (DDR-062): low/mid/high in dB, mid_f/hp/lp in Hz.

        Every field is optional and what is not sent does not move, so a page
        can push one encoder. Host DSP, not an engine — the values are real
        units, not normalised.
        """
        msg = {"type": "eq", "track": int(track)}
        msg.update(kw)
        return self.send(msg)

    # -- loading ----------------------------------------------------------
    def load_engine(self, inst_id: str, slot: str, engine_id: str,
                    min_version: str = "", assets: Optional[dict] = None,
                    dir: Optional[str] = None):
        """`assets` is content for the slot (§ Assets): a map replaces whatever
        the slot held, `{}` clears it, and None leaves it alone. That is the
        door a pattern goes through (DDR-037)."""
        msg = {"type": "load_engine", "instrument": inst_id.lower(),
               "slot": slot, "id": engine_id, "min_version": min_version}
        if assets is not None:
            msg["assets"] = dict(assets)
        if dir:
            msg["dir"] = dir
        return self.send(msg)

    def unload_engine(self, inst_id: str, slot: str):
        return self.send({"type": "unload_engine",
                          "instrument": inst_id.lower(), "slot": slot})

    def load_patch(self, inst_id: str, patch: dict):
        return self.send({"type": "load_patch", "instrument": inst_id.lower(),
                          "patch": patch})

    def load_rack(self, inst_id: str, rack: dict):
        return self.send({"type": "load_rack", "instrument": inst_id.lower(),
                          "rack": rack})

    def load_kit(self, inst_id: str, kit: dict):
        return self.send({"type": "load_kit", "instrument": inst_id.lower(),
                          "kit": kit})

    def library(self) -> List[dict]:
        reply = self.send({"type": "library"})
        return reply.get("engines") or []

    def rescan(self) -> dict:
        """Re-read the `--library` roots. The browser's file ops (DDR-055)
        move real folders inside one of them, and the host keeps its own box
        index off the same roots (stage J2, `audio/tests/host_files.py`)."""
        return self.send({"type": "rescan"})

    # -- rack edits (every one replies with the whole document) -----------
    def rack_edit(self, inst_id: str, msg: dict) -> dict:
        msg = dict(msg)
        msg["instrument"] = inst_id.lower()
        reply = self.send(msg)
        if reply.get("type") == "rack":
            # the bus is the one we addressed: trust the request, not the
            # reply's label, so a master edit can never land on an instrument
            merged = dict(reply)
            merged["instrument"] = msg["instrument"]
            self._merge_rack(merged)
        return reply

    # -- MOD rack edits (modulation-spec §2, §9; PROTOCOL § MOD rack) --------
    def mod_edit(self, inst_id: str, msg: dict) -> dict:
        """Any MOD-rack message; replies with the whole §11 document."""
        msg = dict(msg)
        msg["instrument"] = inst_id.lower()
        reply = self.send(msg)
        if reply.get("type") == "mod":
            self._merge_mod(reply)
        return reply

    def host_param(self, inst_id: str, level=None, pan=None, pitch=None):
        msg = {"type": "host_param", "instrument": inst_id.lower()}
        for key, value in (("level", level), ("pan", pan), ("pitch", pitch)):
            if value is not None:
                msg[key] = float(value)
        return self.send(msg)

    def wait_blocks(self, n: int = 1):
        """Offline only: give the host `n` blocks of its own clock."""
        if self.offline:
            self.render(self.host_block * max(1, int(n)))
        else:
            time.sleep(self.host_block / float(self.sample_rate) * max(1, int(n)))
