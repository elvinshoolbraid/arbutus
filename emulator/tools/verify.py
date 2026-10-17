#!/usr/bin/env python3
"""Drive ArbutusOS headlessly from a feature script and capture evidence.

The seam is the same one the unit tests use — `testing/scripted_input.py`,
`testing/headless_display.py`, and the audio process offline with `render` as
the clock. pygame is never initialised for a window; nothing here reaches into
the renderer.

    venv/bin/python tools/verify.py <script.yaml> [--diff] [--promote]
    venv/bin/python tools/verify.py --all
    venv/bin/python tools/verify.py --list

Script format (YAML or JSON; a mapping with `feature` and `events`)::

    feature: track-record
    fresh: true                 # wipe the scratch state dir first (default)
    temp_library: true          # run against a throwaway copy of library/
    seed: 7                     # pin random.seed / np.random.seed at boot
    events:
      - key: DRUM               # tap: down then up
      - key: SHIFT              # explicit edge
        down: true
      - key: SHIFT
        up: true
      - note: 4                 # chromatic key 0..23, tap unless down/up
      - enc: 2                  # encoder 1..4
        delta: -3
      - wait_blocks: 90         # pull N audio blocks (512 frames each)
      - sleep: 0.3              # real wall-clock seconds (debounce); tap tempo
                                #   sees exactly 0.3 (the rig's clock, #928)
      - flush: true             # what the app does on exit: write state/ to disk
      - card: full              # the card refuses the project document (readonly, ok)
      - reload: true            # restart the app from state/ (fresh: true wipes)
      - shot: 01-armed          # render + write validation/ui/<feature>/01-armed.png
      - midi_clock: 120.0       # feed CLOCK IN a steady tempo (stage J2)
      - assert: len(tape.tracks[0]) == 1

A `chrome:` list instead of `events:` runs `main.py` in a subprocess::

    chrome:
      - shot: chrome
        args: ["--headless", "--exit-after", "1"]

Exit code 0 = every assertion held (and, under --diff, every baseline matched).
"""

import argparse
import errno
import glob
import json
import os
import shutil
import subprocess
import random
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # emulator/
REPO = os.path.dirname(HERE)

# A HELP overlay carries the battery (stage J2) and a battery percentage is
# different on every machine and every hour. Evidence must not be: every
# verify run reports the honest "no telemetry" state, so a promoted baseline
# is a baseline and not a snapshot of one afternoon's charge.
os.environ.setdefault("ARB_NO_BATTERY", "1")
# The same for the screen's mode (#334): a feature is drawn light unless it
# says otherwise (`--appearance`, or an `appearance:` event), whatever mode a
# player left in `audio/library/settings.json`.
os.environ.setdefault("ARB_APPEARANCE", "light")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.app import App, SAMPLE_RATE                      # noqa: E402
from app import keys as K                                 # noqa: E402
from testing.fake_mic import FakeMic                      # noqa: E402
from testing.headless_display import HeadlessDisplay      # noqa: E402
from testing.scripted_input import ScriptedInput          # noqa: E402
from testing import stock_library                         # noqa: E402

#: the library every feature browses, unless it asks for its own copy
LIBRARY_ROOT = os.path.join(REPO, "audio", "library")

BLOCK = 512
EVIDENCE_ROOT = os.path.join(REPO, "validation", "ui")
BASELINE_ROOT = os.path.join(EVIDENCE_ROOT, "baseline")
SCRIPT_DIR = os.path.join(REPO, ".claude", "skills", "verify-arbutus",
                          "features", "scripts")


# ---------------------------------------------------------------------------
# script loading
# ---------------------------------------------------------------------------

def load_script(path):
    with open(path) as f:
        text = f.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:                               # pragma: no cover
            sys.exit("PyYAML missing: venv/bin/pip install pyyaml "
                     "(or hand the tool a .json script)")
        doc = yaml.safe_load(text)
    else:
        doc = json.loads(text)
    if not isinstance(doc, dict) or "feature" not in doc:
        sys.exit(f"{path}: script must be a mapping with a `feature` key")
    return doc


def _take_peak(app):
    """The loudest sample of the take that is rolling right now.

    The Python tape kept a live envelope (`Tape.take_peaks`); the audio goes
    straight into the host's medium now, so TRACK draws the open take by
    reading it back — `clip_peaks` over `take_src` — and so does this.
    """
    tp = app.tape
    n = tp.take_length
    if n <= 0:
        return 0.0
    got = app.native.clip_peaks(tp.selected, tp.take_src, n, 64)
    if got is None:
        return 0.0
    return max(max(got["max"], default=0.0), -min(got["min"], default=0.0))


def _projects(app):
    """`{date: [name, ...]}` under the library's `projects/` (stage J1).

    An assert runs with no builtins and no `os`, so anything that has to look
    at the filesystem lives here rather than in a script.
    """
    from app import project_file as PF
    return PF.list_projects(app)


def _project_doc(app, name=None):
    """The document in a project's folder (the autosave's since D97, #817).
    `name` defaults to the newest."""
    from app import project_file as PF
    listing = PF.list_projects(app)
    for date, names in listing.items():
        for n in names:
            if name is None or n == name:
                doc = PF.read(PF.project_path(app, date, n))
                if doc is not None:
                    return doc
    return {}


def _trash(app):
    """What DDR-055's soft delete is holding, by name."""
    from app.library_ops import TRASH_DIR
    path = os.path.join(app.library.root, TRASH_DIR)
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def _settings_doc(app):
    """`<library>/settings.json` as it is on disk, or `{}`."""
    try:
        with open(app.settings.path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _wav_frames(path):
    """How long a WAV on disk is, in frames — BOUNCE's trim, measured."""
    import wave
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes()
    except Exception:
        return -1


def _clip_peak(app, track, index=0):
    """The loudest sample of a clip, read back out of the host's medium."""
    clips = app.tape.tracks[track]
    if index >= len(clips):
        return 0.0
    c = clips[index]
    got = app.native.clip_peaks(track, c.src, c.length, 64,
                                src_track=c.on(track))
    if got is None:
        return 0.0
    return max(max(got["max"], default=0.0), -min(got["min"], default=0.0))


# ---------------------------------------------------------------------------
# the rig
# ---------------------------------------------------------------------------

class Rig:
    """App + the three test drivers, plus the evidence writers."""

    def __init__(self, feature, fresh=True, out_dir=None,
                 isolate_library=False, seed=None, native=True):
        self.feature = feature
        #: one audio process per feature, `--offline` so `render` is the
        #: clock. It outlives `reload`, the way the real one outlives a UI
        #: restart (engine-spec.md §8.1). `native:` in a script is a no-op.
        self.native = None
        self.out_dir = out_dir or os.path.join(EVIDENCE_ROOT, feature)
        self.state_dir = os.path.join(HERE, "state", f"verify-{feature}")
        self.isolate_library = bool(isolate_library)
        os.makedirs(self.out_dir, exist_ok=True)

        self.display = HeadlessDisplay()
        self.display.initialize()
        self.input = ScriptedInput()
        self.input.initialize()
        from app.native_audio import NativeAudio
        from drivers.native_backend import NativeOfflineAudio
        # never the checkout's own library, which a player may have written
        # in (#851): a stock copy per feature, or the run's shared one
        self.libraries = [os.path.join(REPO, "audio", "build", "boxes"),
                          self._library_copy() if isolate_library
                          else stock_library.scratch()]
        self.native = NativeAudio(offline=True, block=BLOCK,
                                  libraries=self.libraries)
        self.audio = NativeOfflineAudio(self.native, blocksize=BLOCK)

        self.seed = seed
        self._fb = np.zeros((240, 320, 3), dtype=np.uint8)
        self.shots = []          # [(name, path)]
        self.last_pull = np.zeros(0, dtype=np.float32)
        #: the time tap tempo sees: only `sleep:` moves it, by exactly what
        #: the script asked, however long the machine took (#928)
        self.clock = 0.0
        self.boot(fresh=fresh)

    def boot(self, fresh=False):
        """(Re)start the app on the same scratch state dir — a restart."""
        if self.seed is not None:
            # anything the app generates at random (patch names) is pinned,
            # so a feature can assert on it
            random.seed(self.seed)
            np.random.seed(self.seed)
        self.app = App(sample_rate=SAMPLE_RATE, fresh=fresh,
                       state_dir=self.state_dir,
                       library=self._library(fresh),
                       native=self.native)
        self.app.tap_clock = lambda: self.clock
        if self.isolate_library:
            # D97: a project is always open, as `main` makes sure. Only on a
            # library copy: on the host's own it would land in the repo.
            from app import project_file
            project_file.ensure_open(self.app)
        self.input.on_control = self.app.on_key
        self.input.on_encoder_turn = self.app.on_encoder
        self.audio.initialize(SAMPLE_RATE, 2, None)
        self.audio.start()
        self.mic = FakeMic()
        self.app.mic_driver_factory = lambda: self.mic

    def close(self):
        if getattr(self, "_real_write", None) is not None:
            self.card("ok")          # the next feature's card writes
        if self.native is not None:
            self.native.close()
            self.native = None

    def _library_copy(self) -> str:
        """`isolate_library` gives the feature its own copy of the library.

        A feature that *writes* into it (sampling does) must not leave takes
        behind in the repo, and copying rather than scaffolding keeps the real
        content — so what the feature sees is what a user sees. The copy is a
        sibling of the state dir, never inside it: `App(fresh=True)` wipes the
        state dir, which would delete the copy moments after making it.
        """
        # the checkout's stock only: not a player's `settings.json`,
        # `projects/` or new patches (#851; `testing/stock_library.py`)
        return stock_library.copy_stock(self.state_dir + "-library",
                                        LIBRARY_ROOT)

    def _library(self, fresh):
        """The feature's own copy, or the run's shared stock copy."""
        from app.native_library import NativeLibrary
        return NativeLibrary(self.native, root=self.libraries[-1])

    # -- input ------------------------------------------------------------
    def send(self, *events):
        self.input.feed(*events)
        self.input.poll_events()

    def key(self, name, down=None, up=None):
        name = str(getattr(name, "value", name))
        if down:
            self.send(("key", name, True))
        elif up:
            self.send(("key", name, False))
        else:
            self.send(("key", name, True), ("key", name, False))

    def enc(self, idx1, delta):
        self.send(("enc", int(idx1) - 1, int(delta)))

    def pick(self, folder=None, item=None):
        """Select a row in the open selector **by name**.

            - pick: {folder: ENDLESS, item: penta-7}

        A menu scrolled by `delta` is a hostage to the library: every stage
        that adds a box moves every index in every script past it (stages G,
        I1 and I2 each did). A name does not move, and a name that no longer
        exists fails loudly with the list that is actually there.
        """
        menu = self.app.menu
        if menu is None:
            raise RuntimeError("pick: no selector is open")

        def find(rows, want):
            want = str(want).upper()
            for n, row in enumerate(rows):
                if str(row).upper() == want:
                    return n
            raise RuntimeError("pick: no %r in %s" % (want, list(rows)))

        if folder is not None:
            n = find(menu.source.folders(), folder)
            menu.on_encoder(0, n - menu.state.folder_idx, False)
        if item is not None:
            n = find(menu.source.items(menu.state.folder_idx), item)
            menu.on_encoder(1, n - menu.state.item_idx, False)
        self.app.tick()

    # -- the phone's appearance (#334) -------------------------------------
    def appearance(self, mode):
        """Say `mode` the way the phone does: a second client on the host's
        socket sends `appearance {mode}` and the App takes the push on its
        next tick (PROTOCOL.md § The phone's appearance). Fails the event
        when the screen did not follow."""
        import arbutus_ctl                              # noqa: PLC0415

        phone = arbutus_ctl.connect(self.native.sock)
        try:
            reply = phone.request({"type": "appearance", "mode": str(mode)})
        finally:
            phone.close()
        if reply.get("type") != "appearance":
            raise RuntimeError("appearance: the host said %r" % (reply,))
        self.app.tick()
        if self.app.appearance != mode:
            raise RuntimeError("appearance: the screen is still %s"
                               % self.app.appearance)

    # -- MIDI clock (stage J2) ---------------------------------------------
    def midi_clock(self, bpm, beats=2):
        """Feed the clock meter a steady tempo, as a cable would.

        There is no MIDI hardware in a verify run and the port is opened once
        at boot, so the pulses go straight into `app.midi_clock` — the same
        seam `drivers/desktop/midi_in.py` writes to, and the same shape as
        `FakeMic` feeding the capture ring.
        """
        from app.midi_clock import PPQN

        clock = self.app.midi_clock
        dt = 60.0 / (float(bpm) * PPQN)
        t0 = time.monotonic()
        for i in range(PPQN * int(beats)):
            clock.pulse(now=t0 + i * dt)
        clock._seen_at = time.monotonic()
        self.app.tick()

    # -- mic ---------------------------------------------------------------
    def mic_event(self, what, ev):
        """Push audio in through the same seam the real mic uses.

            - mic: tap            # room tone, one bottle strike, quiet again
            - mic: silence        # room tone only (proves ARMED does not fire)
        """
        if self.app.mic is None:
            raise RuntimeError("no mic: visit the INPUT page before `mic:`")
        if not self.mic.running:            # INPUT normally opens it on arrival
            self.mic.initialize(SAMPLE_RATE, self.app.mic.feed)
            self.mic.start()
        if what == "tap":
            self.mic.tap(amp=float(ev.get("amp", 0.55)),
                         f0=float(ev.get("f0", 392.0)))
        elif what == "silence":
            self.mic.silence(float(ev.get("seconds", 0.3)))
        else:
            raise ValueError(f"unknown mic event: {what!r}")
        self.app.tick()

    # -- the card ---------------------------------------------------------
    def card(self, what):
        """`full` or `readonly`: the card refuses the open project's document
        from now on, with that errno (D117, #1006); `ok` writes again. The
        tape and the working state are not refused: the strip's mark is the
        project document's."""
        from app import project_file as PF
        real = getattr(self, "_real_write", None) or PF._write_text
        self._real_write = real
        codes = {"full": errno.ENOSPC, "readonly": errno.EROFS}
        if what == "ok":
            PF._write_text = real
            return
        if what not in codes:
            raise ValueError(f"unknown card state: {what!r}")
        code = codes[what]

        def refuse(folder, text):
            raise OSError(code, os.strerror(code))
        PF._write_text = refuse

    # -- time -------------------------------------------------------------
    def wait_blocks(self, n):
        self.last_pull = self.audio.pull(max(1, int(n)))
        self.app.tick()
        return self.last_pull

    def wait_bounce(self, most=400):
        """Render until FILE › BOUNCE's job ends (#920): offline the host
        renders one chunk a `render`, so this is one block at a time."""
        for _ in range(int(most)):
            if self.app.bounce_job is None:
                return
            self.wait_blocks(1)
        raise AssertionError("the bounce job never ended")

    # -- evidence ---------------------------------------------------------
    def shot(self, name):
        self._fb[:] = 0
        self.app.render(self._fb)
        self.display.update(self._fb)
        path = self.display.save_png(os.path.join(self.out_dir, f"{name}.png"))
        self.shots.append((name, path))
        return path

    def save_wav(self):
        if not self.audio.buffers:
            return None
        return self.audio.save_wav(
            os.path.join(self.out_dir, f"{self.feature}.wav"))

    # -- assertions -------------------------------------------------------
    def namespace(self):
        app = self.app
        from ui.frame import _strip_text
        return {
            "app": app,
            "tape": app.tape,
            "mixer": app.mixer,
            "seq": app.sequencer,
            # what the host actually holds in the SEQ slot: the box and the
            # `pattern` asset it was handed (DDR-037)
            "seq_slot": lambda: app.native.slot(
                app.sequencer.inst_id, "seq") if app.sequencer else {},
            "seq_asset": lambda: (app.native.slot(
                app.sequencer.inst_id, "seq").get("assets") or {}
            ).get("pattern", "") if app.sequencer else "",
            # S13: there is one sequencer per instrument and `seq` above is
            # the **active** one. These two name an instrument, so a script
            # can look at the parked one without going there.
            "seq_for": lambda inst: app.sequencers.get(str(inst).upper()),
            "host_seq": lambda inst: (
                app.native.slot(str(inst).upper(), "seq") or {}).get("id"),
            # #60: the same two things for a MOD tab's `pattern` asset — the
            # file arbutus/stepmod opens, and the step values in it
            "mod_asset": lambda inst, tab=0: _mod_asset(app, inst, tab),
            "mod_steps": lambda inst, tab=0: _mod_steps(app, inst, tab),
            "page": app.current_page,
            "page_id": app.page_id,
            "menu": app.menu,
            "bpm": app.bpm,
            "np": np,
            "len": len, "abs": abs, "float": float, "int": int, "str": str,
            "any": any, "all": all, "round": round, "sorted": sorted,
            "breadcrumb": lambda: app.current_page.breadcrumb(),
            "strip": lambda: _strip_text(app),
            "tickers": lambda: " ".join(t[1] for t in app.tickers()),
            "slot_names": lambda: [s.name if s else None
                                   for s in app.current_page.slots()],
            "slot_values": lambda: [s.format_value() if s else None
                                    for s in app.current_page.slots()],
            "library_items": lambda cat, folder: list(
                getattr(app.library.find(cat, folder), "items", [])),
            "help_lines": lambda: " ".join(app.help_lines()),
            "peak": lambda: float(np.max(np.abs(self.last_pull)))
                            if len(self.last_pull) else 0.0,
            "total_peak": lambda: self.audio.peak(),
            "clip_peak": lambda t, i=0: _clip_peak(app, t, i),
            # a comprehension in an assert cannot see these names (it gets its
            # own scope), so anything that folds the clip list lives here
            "tape_end": lambda: max([c.end for track in app.tape.tracks
                                     for c in track] or [0]),
            "clip_count": lambda: sum(len(t) for t in app.tape.tracks),
            "take_peak": lambda: _take_peak(app),
            "native": app.native,
            "rack": lambda: app.native.rack(app.active_instrument_id),
            "rack_view": getattr(app.pages.get("SYNTH"), "rack_view", None),
            "mic": app.mic,
            "sample_len": lambda: (0.0 if app.mic is None
                                   else app.mic.length_s),
            "sample_peak": lambda: (0.0 if app.mic is None or app.mic.captured is None
                                    else float(np.max(np.abs(app.mic.captured)))),
            "sample_name": lambda: (os.path.basename(app.mic.captured_path)
                                    if app.mic and app.mic.captured_path else ""),
            "engine_folder": lambda: getattr(
                getattr(app.instrument, "engine_folder", None), "folder", None),
            "state_file": lambda n: os.path.join(self.state_dir, n),
            "exists": lambda n: os.path.exists(os.path.join(self.state_dir, n)),
            "state_json": lambda n: json.load(
                open(os.path.join(self.state_dir, n))),
            # stage J1: FILE › PROJECT saves into the library's `projects/`
            "projects": lambda: _projects(app),
            "project_doc": lambda name=None: _project_doc(app, name),
            "wav_frames": _wav_frames,
            # stage J2: the browser is a file manager (DDR-055) and an assert
            # runs with no `os`, so anything that looks at the library tree
            # lives up here beside `projects()`
            "lib_exists": lambda rel: os.path.exists(
                os.path.join(app.library.root, *str(rel).split("/"))),
            "trash": lambda: _trash(app),
            "lifted": lambda: (app.files.lifted.name
                               if app.files.lifted is not None else ""),
            "settings_doc": lambda: _settings_doc(app),
            "midi_clock": lambda: app.midi_clock,
        }

    def check(self, expr):
        ok = bool(eval(expr, {"__builtins__": {}}, self.namespace()))  # noqa: S307
        return ok


def _mod_asset(app, inst, tab=0):
    """The `pattern` file a MOD tab was handed, "" when it has none."""
    tabs = (app.native.mod_rack(str(inst).upper()).get("tabs") or [])
    if not 0 <= int(tab) < len(tabs):
        return ""
    return (tabs[int(tab)].get("assets") or {}).get("pattern", "")


def _mod_steps(app, inst, tab=0):
    """The step values in that file, [] when there is no file yet."""
    from app.stepmod import read_values

    return read_values(_mod_asset(app, inst, tab)) or []


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def _sleep_until(deadline):
    """`sleep:` in short steps to a deadline. One `time.sleep(0.3)` ran
    0.35-0.45 s in the Mac runner's background process (macOS stretches its
    timers, #242), which moved tap tempo off by up to 60 BPM."""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.002))


def run_events(rig, events, verbose=True):
    """Returns a list of failure strings (empty = pass)."""
    failures = []
    for i, ev in enumerate(events):
        if isinstance(ev, str):
            ev = {"shot": ev} if not ev.startswith("assert ") \
                else {"assert": ev[len("assert "):]}
        if not isinstance(ev, dict):
            failures.append(f"event {i}: not a mapping: {ev!r}")
            continue

        if "key" in ev:
            rig.key(ev["key"], ev.get("down"), ev.get("up"))
        elif "note" in ev:
            rig.key(K.note_key(int(ev["note"])), ev.get("down"), ev.get("up"))
        elif "enc" in ev:
            rig.enc(ev["enc"], ev.get("delta", 1))
        elif "pick" in ev:
            rig.pick(ev["pick"].get("folder"), ev["pick"].get("item"))
        elif "appearance" in ev:
            try:
                rig.appearance(ev["appearance"])
            except RuntimeError as e:
                failures.append(f"event {i}: {e}")
        elif "midi_clock" in ev:
            rig.midi_clock(ev["midi_clock"], ev.get("beats", 2))
        elif "mic" in ev:
            rig.mic_event(ev["mic"], ev)
        elif "wait_blocks" in ev:
            rig.wait_blocks(ev["wait_blocks"])
        elif "wait_bounce" in ev:
            rig.wait_bounce()
        elif "sleep" in ev:
            _sleep_until(time.monotonic() + float(ev["sleep"]))
            rig.clock += float(ev["sleep"])
            rig.app.tick()
        elif "card" in ev:
            rig.card(ev["card"])
        elif "flush" in ev:
            rig.app.flush()
        elif "reload" in ev:
            rig.boot(fresh=bool(ev.get("fresh", False)))
        elif "shot" in ev:
            path = rig.shot(ev["shot"])
            if verbose:
                print(f"    shot {ev['shot']} -> {os.path.relpath(path, REPO)}")
        elif "assert" in ev:
            expr = ev["assert"]
            try:
                ok = rig.check(expr)
            except Exception as e:                        # noqa: BLE001
                failures.append(f"event {i}: assert {expr!r} raised {e!r}")
                continue
            if not ok:
                failures.append(f"event {i}: assert failed: {expr}")
            elif verbose:
                print(f"    ok   {expr}")
        else:
            failures.append(f"event {i}: unknown event {ev!r}")
    return failures


def run_chrome(doc, feature, verbose=True):
    """Feature that must go through the real pygame window (photo chrome)."""
    out_dir = os.path.join(EVIDENCE_ROOT, feature)
    os.makedirs(out_dir, exist_ok=True)
    py = os.path.join(HERE, "venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    # main.py boots on the checkout's library and emulator/state/ unless told
    # otherwise, and its boot saves a project there (D97): the run's stock
    # copy and the feature's own state, as Rig has (#851, #1075)
    state_dir = os.path.join(HERE, "state", f"verify-{feature}")
    shutil.rmtree(state_dir, ignore_errors=True)
    failures, shots = [], []
    for step in doc["chrome"]:
        name = step["shot"]
        png = os.path.join(out_dir, f"{name}.png")
        args = [py, "main.py"] + list(step.get(
            "args", ["--headless", "--exit-after", "1"])) + ["--screenshot", png]
        if "--library" not in args:
            args += ["--library", stock_library.scratch()]
        if "--state-dir" not in args:
            args += ["--state-dir", state_dir]
        proc = subprocess.run(args, cwd=HERE, capture_output=True, text=True,
                              timeout=step.get("timeout", 120))
        if proc.returncode != 0:
            failures.append(f"{name}: main.py exit {proc.returncode}\n"
                            f"{proc.stderr[-800:]}")
            continue
        if not os.path.exists(png):
            failures.append(f"{name}: no screenshot written to {png}")
            continue
        shots.append((name, png))
        if verbose:
            print(f"    shot {name} -> {os.path.relpath(png, REPO)}")
    return failures, shots, out_dir


# ---------------------------------------------------------------------------
# baseline diff / promote
# ---------------------------------------------------------------------------

def _read_png(path):
    try:
        from PIL import Image
        return np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    except ImportError:
        import pygame
        surf = pygame.image.load(path)
        arr = pygame.surfarray.array3d(surf).transpose(1, 0, 2)
        return arr.astype(np.int16)


def diff_shots(feature, shots):
    """Compare each shot to validation/ui/baseline/<feature>/<name>.png."""
    base_dir = os.path.join(BASELINE_ROOT, feature)
    failures = []
    for name, path in shots:
        bpath = os.path.join(base_dir, f"{name}.png")
        if not os.path.exists(bpath):
            print(f"    diff {name}: NO BASELINE -> grade this PNG "
                  f"(see SKILL.md 'Grading'), then --promote")
            continue
        a, b = _read_png(path), _read_png(bpath)
        if a.shape != b.shape:
            failures.append(f"{name}: size {a.shape} != baseline {b.shape}")
            continue
        n = int(np.count_nonzero(np.any(a != b, axis=2)))
        print(f"    diff {name}: {n} px")
        if n:
            failures.append(f"{name}: {n} pixels differ from baseline")
    return failures


def promote_shots(feature, shots):
    base_dir = os.path.join(BASELINE_ROOT, feature)
    os.makedirs(base_dir, exist_ok=True)
    for name, path in shots:
        shutil.copyfile(path, os.path.join(base_dir, f"{name}.png"))
        print(f"    promoted {name} -> "
              f"{os.path.relpath(os.path.join(base_dir, name + '.png'), REPO)}")


# ---------------------------------------------------------------------------

def run_script(path, do_diff=False, do_promote=False, verbose=True):
    doc = load_script(path)
    feature = doc["feature"]
    print(f"[{feature}] {os.path.relpath(path, REPO)}")

    if "chrome" in doc:
        failures, shots, out_dir = run_chrome(doc, feature, verbose)
    else:
        if doc.get("native"):
            from app.native_audio import EXE, BUILD_COMMAND
            if not os.path.exists(EXE):
                print(f"  SKIP {feature}: no {os.path.relpath(EXE, REPO)} — "
                      f"build it with: {BUILD_COMMAND}")
                return []
        rig = Rig(feature, fresh=doc.get("fresh", True),
                  isolate_library=bool(doc.get("isolate_library")
                                       or doc.get("temp_library")),
                  seed=doc.get("seed"), native=bool(doc.get("native")))
        try:
            failures = run_events(rig, doc.get("events", []), verbose)
            wav = rig.save_wav()
            if wav and verbose:
                print(f"    wav  -> {os.path.relpath(wav, REPO)}")
            shots, out_dir = rig.shots, rig.out_dir
        finally:
            rig.close()

    if do_diff:
        failures += diff_shots(feature, shots)
    if do_promote:
        if failures:
            print("    NOT promoting: run failed")
        else:
            promote_shots(feature, shots)

    if failures:
        print(f"  FAIL {feature}")
        for f in failures:
            print(f"    - {f}")
    else:
        print(f"  PASS {feature}  ({len(shots)} shots in "
              f"{os.path.relpath(out_dir, REPO)})")
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scripts", nargs="*", help="feature script(s), .yaml or .json")
    ap.add_argument("--all", action="store_true",
                    help=f"run every script in {os.path.relpath(SCRIPT_DIR, REPO)}")
    ap.add_argument("--list", action="store_true", help="list known scripts")
    ap.add_argument("--diff", action="store_true",
                    help="compare shots to validation/ui/baseline/<feature>/")
    ap.add_argument("--promote", action="store_true",
                    help="copy this run's shots into the baseline")
    ap.add_argument("--appearance", choices=("light", "dark"), default=None,
                    help="draw every feature in this mode (#334); use with "
                         "--out, so the tracked light evidence is untouched")
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="write evidence under DIR/<feature>/ instead of "
                         "validation/ui/ (no --diff/--promote with it)")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.appearance:
        os.environ["ARB_APPEARANCE"] = args.appearance
    if args.out:
        if args.diff or args.promote:
            sys.exit("--out writes outside validation/ui/: no --diff or --promote")
        global EVIDENCE_ROOT
        EVIDENCE_ROOT = os.path.abspath(args.out)

    known = sorted(glob.glob(os.path.join(SCRIPT_DIR, "*.yaml"))
                   + glob.glob(os.path.join(SCRIPT_DIR, "*.json")))
    if args.list:
        for p in known:
            print(os.path.relpath(p, REPO))
        return 0

    paths = list(args.scripts)
    if args.all or not paths:
        paths = known
    if not paths:
        sys.exit(f"no scripts given and none found in {SCRIPT_DIR}")

    resolved = []
    for p in paths:
        # a bare feature name always means a script in SCRIPT_DIR, never a
        # same-named file or directory in the cwd
        if p.endswith((".yaml", ".yml", ".json")) or os.sep in p:
            if not os.path.exists(p):
                sys.exit(f"no such script: {p}")
            resolved.append(p)
            continue
        for ext in (".yaml", ".json"):
            cand = os.path.join(SCRIPT_DIR, p + ext)
            if os.path.exists(cand):
                resolved.append(cand)
                break
        else:
            sys.exit(f"no such script: {p}")

    failed = []
    for p in resolved:
        if run_script(p, args.diff, args.promote, not args.quiet):
            failed.append(os.path.basename(p))
    print()
    print(f"{len(resolved) - len(failed)}/{len(resolved)} features passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
