"""A5k (#291) — every Learn lesson, played on the emulator as built.

The companion app's lessons (`app/ios/Lessons/*.lesson`, companion-app-spec.md
§ 10.6) say which keys to press and what turns a step green. A lesson written
from memory of the play-test checklist can name a page header or a parameter
the instrument does not have, so each lesson here has a **script**: the presses
and turns a player following its text would make. The test plays each script on
a fresh instrument and records what the phone would hear over D17 (`ui_feed.py`):
every action, with the page and the header the screen was showing.

The record is `app/ios/ArbutusTests/lesson-runs.json`. The app's own test
(`LearnTests.testEveryLessonsRecordedRunTurnsItGreen`) replays it through the
guide's real matcher, so the two halves meet in one file: the instrument says
what happens, the app says whether that turns the step green.

    python3 -m unittest tests.test_lessons          checks the record is current
    ARBUTUS_WRITE_LESSON_RUNS=1 python3 -m unittest tests.test_lessons
                                                    rewrites it (then read the diff)
    ARBUTUS_LESSON_TRACE=1 ...                      prints header and strip per step

A script step is one string:

    SYNTH        tap a key (press, tick, release, tick)
    SHIFT+1      tap a key with another held (any key, not only SHIFT)
    +REC  -REC   press / release only
    ENC1+3       three detents right on ENC1, one tick each (ENC1-3 is left)
    ~4           four seconds of audio pass (a take)
    @0.4         0.4 s of audio and of wall time (a solo hold, tap tempo)
    mic          the fake microphone hears one struck bottle
    app:share    the phone's own action (Library's Share or Save to Files: `app:download`),
                 fed to the guide beside the instrument's (Shell.keep)

One step of each script starts with `!`: the step that makes the lesson's result
(the save, the bound knob's turn, the second take's STOP). Its events carry
`"completes": true` in the record, and the app's test checks that the lesson is
still grey before them and green by their end (#505): a `done:` line met early
is a lesson that turns green before the thing it asks for exists.

**Each step of a lesson has its own lines too** (#854; the grammar is the
README's): a script step written `3:NOTE_9` (or `!3:NOTE_9`, or `2,3:SYNTH`) is
the one that turns the lesson's step 3 (its third line, from 1) green, and the
test checks, with `lesson_conditions.py`'s twin of the app's matcher, that the
step is still grey before it and green by its end. A `manual` step has no mark.
After every script step the test asks the host for the situation document
(#853), and keeps them in `app/ios/ArbutusTests/lesson-situations.json`: per
lesson, per script step, how many of its run's events had happened, the steps
it marks (`meets`), and the document (whole on the first row, then a merge
patch), so the app's test (#855) can replay a run with its situations.

    ARBUTUS_WRITE_LESSON_RUNS=1 ... also rewrites lesson-situations.json
"""

import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # emulator/
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tests.native_support import BLOCK, BOXES, SAMPLE_RATE, host, library_copy  # noqa: E402

from tests import lesson_conditions as lc                               # noqa: E402

LESSONS = os.path.join(REPO, "app", "ios", "Lessons")
RUNS = os.path.join(REPO, "app", "ios", "ArbutusTests", "lesson-runs.json")
SITUATIONS = os.path.join(REPO, "app", "ios", "ArbutusTests", "lesson-situations.json")
#: the public export carries no phone app (#1295): its lessons' tests skip there, by this
NO_APP = unittest.skipUnless(os.path.isdir(os.path.join(REPO, "app", "ios")),
                             "no app/ios/ (the public export): the lessons are the phone app's")
MARK = re.compile(r"^!?(\d+(?:,\d+)*):")
SCREEN_IDS = re.compile(r"^(header|strip|main|param\.[0-7]|key\.[1-4])$")

#: lesson id -> the script a player following its text would play.
SCRIPTS = {
    # -- Sound (A5g, #284) ---------------------------------------------------
    "play-a-melody": ["1:SYNTH", "NOTE_7", "NOTE_9", "NOTE_11", "NOTE_12", "NOTE_14", "NOTE_12",
                      "NOTE_11", "!2:NOTE_9", "3:RIGHT", "NOTE_7", "LEFT"],
    "shape-a-sound": ["SYNTH", "1:1", "+NOTE_7", "2:ENC1+2", "3:ENC2+2", "!ENC3-2", "-NOTE_7",
                      "+SHIFT", "4:ENC1+1", "-SHIFT"],
    "try-another-engine": ["SYNTH", "1:SHIFT+1", "ENC1+1", "2,3:ENC2+1", "NOTE_7", "NOTE_9",
                           "NOTE_11", "!NOTE_12", "4:1"],
    "change-the-envelope": ["SYNTH", "1:2", "+NOTE_7", "2,3:ENC1+3", "!4:ENC4+4", "-NOTE_7"],
    "sample-a-sound": ["1:INPUT", "1", "2:mic", "~0.5", "3:3", "NOTE_7", "NOTE_12", "NOTE_19",
                       "!4:NOTE_2"],
    "keep-a-sound-as-a-patch": ["SYNTH", "1:ENC2+3", "SHIFT+SYNTH", "!2:ENC1_PUSH"],
    "switch-between-slots": ["SYNTH", "1:A", "ENC2+3", "!B", "SHIFT+1", "2:ENC1+1", "1", "3:A"],
    # -- Drums ---------------------------------------------------------------
    "play-a-beat-on-the-pads": ["1:DRUM", "NOTE_0", "NOTE_2", "NOTE_4", "NOTE_2", "NOTE_0",
                                "NOTE_2", "NOTE_4", "!2:NOTE_7", "3,4:RIGHT"],
    "load-a-kit": ["DRUM", "1:SHIFT+1", "ENC1+1", "2,3:ENC2+1", "ENC2+1", "NOTE_0", "NOTE_2",
                   "NOTE_4", "!NOTE_2", "4:1"],
    "tweak-one-drum": ["DRUM", "1", "1:NOTE_4", "2:ENC3+4", "3:NOTE_4", "!4:ENC2-6", "NOTE_4"],
    # -- Effects and movement --------------------------------------------------
    "add-an-effect": ["SYNTH", "1:3", "2:SHIFT+3", "ENC1+10", "3:3", "NOTE_7", "!NOTE_11",
                      "4:ENC1+3"],
    "put-an-effect-on-a-knob": ["SYNTH", "3", "SHIFT+3", "ENC1+10", "1:3", "2:LIFT", "3:ENC2+1",
                                "SHIFT+LEFT", "4:DROP", "ENC1+1", "!5:ENC1+4", "NOTE_7"],
    "make-a-sound-move": ["SYNTH", "4", "SHIFT+4", "ENC1+2", "1:4", "LIFT", "2:ENC1+1", "1",
                          "DROP", "3:ENC2+1", "!ENC1+5", "4:+NOTE_7", "~0.3", "-NOTE_7"],
    "route-a-modulator-somewhere-new": ["SYNTH", "4", "SHIFT+4", "ENC1+2", "4", "LIFT", "1:ENC1+1",
                                        "2", "2:DROP", "3:ENC4+1", "!4:ENC1+5", "+NOTE_7", "~0.3",
                                        "-NOTE_7"],
    # -- Sequencer -----------------------------------------------------------
    "use-the-sequencer": ["SEQ", "1:SEQ", "+1", "NOTE_7", "NOTE_9", "NOTE_11", "!2:NOTE_14", "-1",
                          "3:+NOTE_7", "~0.5", "-NOTE_7"],
    "play-a-riff-in-another-key": ["SEQ", "SEQ", "+1", "NOTE_7", "NOTE_9", "NOTE_11", "-1",
                                   "1:+NOTE_7", "~0.3", "-NOTE_7", "2:+NOTE_12", "~0.3",
                                   "-NOTE_12", "!3:RIGHT", "+NOTE_7", "~0.3", "-NOTE_7"],
    "keep-a-riff-running": ["SEQ", "SEQ", "+1", "NOTE_7", "NOTE_9", "-1", "1:ENC3+1", "2:+NOTE_7",
                            "-NOTE_7", "!3:SYNTH", "~0.3", "SEQ", "4:ENC3-1"],
    # -- Tape ----------------------------------------------------------------
    "record-a-melody": ["TRACK", "1:1", "REC", "2:PLAY", "NOTE_7", "~1", "NOTE_9", "~1", "NOTE_11",
                        "~1", "NOTE_12", "~1", "3:STOP", "STOP", "!4:PLAY", "~1", "STOP"],
    "add-a-second-track": ["TRACK", "1", "REC", "PLAY", "NOTE_7", "~4", "STOP", "STOP", "1:2",
                           "REC", "PLAY", "2:NOTE_11", "~4", "!3:STOP"],
    "punch-in-a-fix": ["TRACK", "1", "REC", "PLAY", "NOTE_7", "~4", "STOP", "STOP", "1:PLAY", "~1",
                       "2:REC", "NOTE_9", "~1", "!3:REC", "~1", "STOP"],
    "move-and-cut-a-clip": ["TRACK", "1", "REC", "PLAY", "NOTE_7", "~4", "STOP", "STOP", "1:RIGHT",
                            "2:SLICE", "3:LIFT", "RIGHT", "!4:DROP"],
    "turn-tape-into-an-instrument": ["TRACK", "1", "REC", "PLAY", "NOTE_7", "~4", "STOP", "STOP",
                                     "RIGHT", "1:LIFT", "SYNTH", "2:1", "3:DROP", "NOTE_7",
                                     "NOTE_9", "NOTE_11", "!4:NOTE_12"],
    "loop-a-section": ["TRACK", "1", "REC", "PLAY", "NOTE_7", "~4", "STOP", "STOP", "1:A", "RIGHT",
                       "2:B", "!C", "3:PLAY", "~4", "STOP", "4:C"],
    "play-the-tape-backwards-or-slower": ["TRACK", "1", "REC", "PLAY", "NOTE_7", "~4", "STOP",
                                          "STOP", "PLAY", "1:+X", "~1", "-X", "+Y", "!2:ENC4-3",
                                          "~1", "3:-Y", "STOP"],
    # -- Mix and time --------------------------------------------------------
    "balance-the-mix": ["MIXER", "1:1", "2:ENC1-2", "ENC2+1", "!SHIFT+2", "3:SHIFT+2", "+3",
                        "4:@0.4", "-3"],
    "put-an-effect-on-everything": ["MIXER", "1:3", "SHIFT+3", "ENC1+9", "2:3", "!3:ENC2+3"],
    "set-the-tempo-by-tapping": ["SYNTH", "TEMPO", "@0.4", "TEMPO", "@0.4", "!1,2:TEMPO", "@0.4",
                                 "TEMPO", "3:ENC1+1"],
    "count-in-with-the-click": ["TEMPO", "1:ENC2+4", "+SHIFT", "2:ENC2+1", "-SHIFT", "REC",
                                "!3:PLAY", "~0.3", "STOP"],
    # -- Input ---------------------------------------------------------------
    "play-through-a-microphone": ["SYNTH", "SHIFT+1", "ENC1+1", "2,3:1", "!4:ENC2+2", "mic", "3",
                                  "SHIFT+3", "ENC1+10", "5:3", "mic"],
    "resample-the-mix": ["SYNTH", "SHIFT+1", "ENC1+1", "ENC2+1", "1,2:1", "TRACK", "3", "REC",
                         "!3:PLAY", "~4", "4:STOP"],
    # -- Files and help ------------------------------------------------------
    # SAVE went with D97 (#817): the lesson is NEW, under its old id; since
    # D98 (#818) NEW opens the name menu and its push makes the project
    "save-and-start-a-new-project": ["FILE", "1:1", "2:ENC1+1", "ENC1+1", "!3:ENC1_PUSH"],
    "bounce-a-song": ["TRACK", "1", "REC", "PLAY", "NOTE_7", "~4", "STOP", "FILE", "1:1",
                      "!2:ENC4+1"],
    "undo-a-mistake": ["SYNTH", "2", "1:ENC1+10", "!2:SHIFT+STOP", "3:SHIFT+STOP"],
    "find-a-sound-and-file-it": ["SYNTH", "ENC2+3", "SHIFT+SYNTH", "1:ENC1_PUSH", "2:SHIFT+1", "3:LIFT",
                                 "ENC1+2", "!4:DROP", "1"],
    "ask-help-about-a-knob": ["SYNTH", "1:+HELP", "ENC2+1", "-HELP", "+HELP", "!2:LIFT", "3:-HELP",
                              "4:HELP"],
    "plug-in-a-midi-keyboard": ["FILE", "2:2", "!3:ENC1+1", "ENC1-1"],
    # -- Workflows -----------------------------------------------------------
    "make-a-beat": ["DRUM", "SHIFT+1", "1:ENC2+2", "1", "SEQ", "SEQ", "+1", "NOTE_0", "NOTE_4",
                    "NOTE_2", "2:NOTE_4", "-1", "ENC3+1", "3:+NOTE_0", "-NOTE_0", "TRACK", "1",
                    "REC", "!PLAY", "~4", "4:STOP"],
    "sketch-a-song": ["FILE", "1", "ENC1+1", "1:ENC1_PUSH", "TEMPO", "2:ENC2+4", "SYNTH", "LEFT",
                      "TRACK", "1", "REC", "PLAY", "NOTE_7", "~2", "NOTE_5", "~2", "3:STOP",
                      "STOP", "DRUM", "TRACK", "2", "REC", "PLAY", "NOTE_0", "~1", "NOTE_4", "~1",
                      "NOTE_0", "~1", "NOTE_4", "~1", "4:STOP", "STOP", "SYNTH", "RIGHT", "TRACK",
                      "3", "REC", "PLAY", "NOTE_14", "~1", "NOTE_16", "~1", "NOTE_19", "~2",
                      "5:STOP", "STOP", "MIXER", "1", "!ENC1-2", "ENC3+1", "TRACK", "6:PLAY", "~4",
                      "STOP"],
    "sample-and-flip": ["INPUT", "1", "1:mic", "~0.5", "2:3", "ENC1+3", "3:ENC2-3", "NOTE_7",
                        "NOTE_9", "NOTE_12", "NOTE_14", "SHIFT+SYNTH",
                        "!4:ENC1_PUSH"],
    "build-a-performance-patch": ["SYNTH", "SHIFT+1", "ENC1+6", "1:1", "3", "SHIFT+3", "ENC1+9",
                                  "3", "LIFT", "ENC2+1", "SHIFT+LEFT", "2:DROP", "ENC1+1",
                                  "ENC1+4", "4", "SHIFT+4", "ENC1+2", "4", "LIFT", "ENC1+1", "1",
                                  "3:DROP", "ENC2+1", "ENC1+5", "SHIFT+SYNTH",
                                  "!4:ENC1_PUSH"],
    "jam-with-tape-loops": ["TRACK", "1", "REC", "PLAY", "NOTE_7", "~4", "1:STOP", "STOP", "A",
                            "RIGHT", "B", "C", "2:PLAY", "~1", "3:+X", "~1", "-X", "+Y", "ENC4-3",
                            "~1", "-Y", "NOTE_11", "!4:NOTE_14", "~1", "STOP"],
    "finish-and-share": ["TRACK", "1", "REC", "PLAY", "NOTE_7", "~4", "STOP", "MIXER", "1",
                         "1:ENC1-1", "FILE", "1", "3:ENC4+1", "!4:app:share"],
}


def bare(step):
    """A script step without its `!` and its step mark: what the player does."""
    return MARK.sub("", step).lstrip("!")


def marks(script):
    """-> {lesson step (from 0): the script step (from 0) that turns it green}."""
    out = {}
    for i, st in enumerate(script):
        m = MARK.match(st)
        for n in (m.group(1).split(",") if m else []):
            if int(n) - 1 in out:
                raise ValueError("step %s marked twice" % n)
            out[int(n) - 1] = i
    return out


def lesson_files():
    """-> {id: text} for every `.lesson` under app/ios/Lessons."""
    out = {}
    for root, _, files in os.walk(LESSONS):
        for f in files:
            if f.endswith(".lesson"):
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    out[f[:-len(".lesson")]] = fh.read()
    return out


def screen_targets(text):
    """The screen-element arrow targets a lesson's lines name (after `->`)."""
    out = set()
    for line in text.splitlines():
        if "->" in line and not line.startswith("//"):
            out |= {t for t in line.split("->", 1)[1].split() if SCREEN_IDS.match(t)}
    return out


class Player:
    """One fresh instrument, driven through the input seam, heard as the phone."""

    def __init__(self, lib_root, state, trace=None):
        from app.app import App
        from app.native_library import NativeLibrary
        from drivers.native_backend import NativeOfflineAudio
        from testing.fake_mic import FakeMic

        self.native = host(None, library_root=lib_root)
        self.events = []                 # [{action, page, header[, completes]}]
        self.completing = False          # inside the script's `!` step
        self.seen = set()                # element ids the screen showed
        self.page = self.header = None
        self.docs = []                   # #854: [(step, events so far, situation)]
        # the UI's messages reach the host too (its half of the situation, #853),
        # and the recorder hears them
        self._native_publish = type(self.native).publish
        self.native.publish = self._both
        self.app = App(sample_rate=SAMPLE_RATE, fresh=True, state_dir=state,
                       native=self.native, library=NativeLibrary(self.native, root=lib_root))
        self.mic = FakeMic()
        self.app.mic_driver_factory = lambda: self.mic
        self.audio = NativeOfflineAudio(self.native, blocksize=BLOCK)
        self.audio.initialize(SAMPLE_RATE, 2, None)
        self.audio.start()
        self.trace = trace
        self.tick()

    def close(self):
        self.audio.stop()
        self.native.close()

    def _both(self, msg):
        self._native_publish(self.native, msg)
        return self._publish(msg)

    def situation(self):
        """The whole situation document, as a `live` subscriber has it (#853)."""
        r = self.native.conn.request({"type": "situation"})
        assert r.get("type") == "situation", r
        return r["situation"]

    def _publish(self, msg):
        # What the phone's session does with it (DeviceSession, Shell.open):
        # the map sets the page and header the next actions are matched against.
        if msg["type"] == "ui_elements":
            self.page = msg["page"]
            self.header = next((e["label"] for e in msg["elements"] if e["id"] == "header"), None)
            self.seen |= {e["id"] for e in msg["elements"]}
        elif msg["type"] == "ui_action":
            for a in msg["actions"]:
                a = {k: v for k, v in a.items() if k != "value"}   # moves every detent
                if a.get("kind") == "project":
                    a.pop("id", None)          # #853: names today's date folder
                self._event(a)
        return True

    def _event(self, a):
        e = {"action": a, "page": self.page, "header": self.header}
        if self.completing:
            e["completes"] = True
        self.events.append(e)

    def tick(self, blocks=2):
        self.audio.pull(blocks)
        self.app.tick()

    def key(self, k, down):
        self.app.on_key(k, down)
        self.tick()

    def step(self, s):
        self.completing = s.startswith("!")
        s = bare(s)
        m = re.match(r"^ENC([1-4])([+-]\d+)$", s)
        if s.startswith("app:"):
            # The phone's own action: what Shell.keep feeds the guide, on the
            # screen the session last saw.
            self._event({"kind": "app", "id": s[len("app:"):]})
        elif m:
            n = int(m.group(2))
            for _ in range(abs(n)):
                self.app.on_encoder(int(m.group(1)) - 1, 1 if n > 0 else -1)
                self.tick()
        elif s[0] in "~@":
            # `~t`: t seconds of audio (a take is t long). `@t`: the wall clock
            # moves by t as well, for what the UI times with time.monotonic()
            # (a held key becoming a solo, tap tempo).
            end = time.monotonic() + float(s[1:])
            for _ in range(int(float(s[1:]) * SAMPLE_RATE / BLOCK / 8)):
                self.tick(8)
            if s[0] == "@":
                time.sleep(max(0.0, end - time.monotonic()))
            self.tick()
        elif s == "mic":
            self.mic.tap()
            self.tick(16)
        elif s[0] in "+-" and len(s) > 1:
            self.key(s[1:], s[0] == "+")
        elif "+" in s:
            held, k = s.split("+", 1)
            self.key(held, True)
            self.key(k, True)
            self.key(k, False)
            self.key(held, False)
        else:
            self.key(s, True)
            self.key(s, False)
        if self.trace:
            from app.ui_feed import element_map
            from ui.frame import _strip_text
            m = {e["id"]: e["label"] for e in element_map(self.app)}
            cells = " ".join(m.get("param.%d" % i) or "-" for i in range(8))
            menu = self.app.menu
            if menu is not None:
                st, src = menu.state, menu.source
                items = src.items(st.folder_idx) or ["—"]
                cells += " | %s / %s" % (src.folders()[st.folder_idx],
                                         items[min(st.item_idx, len(items) - 1)])
            self.trace("  %-12s %-30s %-34s [%s]" % (s, m["header"], _strip_text(self.app), cells))
        self.completing = False

    def play(self, script):
        for s in script:
            self.step(s)
            self.docs.append((bare(s), len(self.events), self.situation()))
        self.tick(4)
        self.docs.append(("(end)", len(self.events), self.situation()))
        return self.events


def record_all(trace=None):
    """-> ({id: events}, {id: element ids seen}, {id: situation rows}), each
    lesson on a fresh instrument."""
    tmp = tempfile.mkdtemp(prefix="arbutus-lessons-")
    os.environ["ARB_NO_BATTERY"] = "1"          # the same record on every machine
    from app import battery
    battery.reset_cache()
    try:
        runs, seen, situations = {}, {}, {}
        only = os.environ.get("ARBUTUS_LESSONS")       # a comma list, while writing one
        for lid in sorted(SCRIPTS):
            if only and lid not in only.split(","):
                continue
            if trace:
                trace(lid)
            # A library of its own: one lesson's save or bounce never feeds the next.
            lib = os.path.join(tmp, "library-" + lid)
            library_copy(lib)
            p = Player(lib, os.path.join(tmp, "state-" + lid), trace)
            try:
                runs[lid] = p.play(SCRIPTS[lid])
                seen[lid] = p.seen
                situations[lid] = situation_rows(SCRIPTS[lid], p.docs)
            finally:
                p.close()
        return runs, seen, situations
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        battery.reset_cache()


def steady(doc, timed):
    """The document with what moves from one machine or day to the next taken
    out: a project id carries today's date and an auto-name, and in a script
    with `@` steps (`timed`) the tempo is tapped in wall-clock time. The
    playhead's seconds and a take's length depend on when the audio thread ran
    against the keys (#1042), and no lesson reads them."""
    doc = json.loads(json.dumps(doc))
    p = doc.get("project", {})
    if "id" in p:
        p["id"], p["name"] = "<project>", "<name>"
    doc.get("transport", {}).pop("position_s", None)
    for track in doc.get("tracks", {}).values():
        track.pop("length_s", None)
    if timed:
        doc.get("tempo", {}).pop("bpm", None)
    return doc


def situation_rows(script, docs):
    """[{step, events, [meets], situation | patch}], one a script step and the end."""
    from tests.test_live_situation import merge_diff
    rows, last = [], None
    by_step = {}
    for k, i in marks(script).items():
        by_step.setdefault(i, []).append(k + 1)
    timed = any(bare(st).startswith("@") for st in script)
    for i, (step, n, doc) in enumerate(docs):
        doc = steady(doc, timed)
        row = {"step": step, "events": n}
        if i in by_step:
            row["meets"] = sorted(by_step[i])
        if last is None:
            row["situation"] = doc
        else:
            row["patch"] = merge_diff(last, doc)
        rows.append(row)
        last = doc
    return rows


def whole_rows(rows):
    """The rows with every document whole again (what a subscriber holds)."""
    from tests.test_live_situation import merge
    out, doc = [], None
    for r in rows:
        doc = r["situation"] if "situation" in r else merge(doc, r["patch"])
        out.append(dict(r, situation=doc))
    return out


def write_situations(situations):
    """One script step a line, so a change reads as a short diff."""
    def line(r):
        return json.dumps(r, sort_keys=True, ensure_ascii=False)
    blocks = ['  %s: [\n%s\n  ]' % (json.dumps(lid), ",\n".join("    " + line(r) for r in situations[lid]))
              for lid in sorted(situations)]
    with open(SITUATIONS, "w", encoding="utf-8") as f:
        f.write("{\n" + ",\n".join(blocks) + "\n}\n")


def write_runs(runs):
    """One event a line, so a change to the instrument reads as a short diff."""
    def line(e):
        return json.dumps(e, sort_keys=True, ensure_ascii=False)
    blocks = ['  %s: [\n%s\n  ]' % (json.dumps(lid), ",\n".join("    " + line(e) for e in runs[lid]))
              for lid in sorted(runs)]
    with open(RUNS, "w", encoding="utf-8") as f:
        f.write("{\n" + ",\n".join(blocks) + "\n}\n")


@NO_APP
class EveryLessonPlays(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(os.path.join(BOXES)):
            raise unittest.SkipTest("no audio/build: cmake --build audio/build")
        trace = print if os.environ.get("ARBUTUS_LESSON_TRACE") else None
        cls.runs, cls.seen, cls.situations = record_all(trace)
        if os.environ.get("ARBUTUS_WRITE_LESSON_RUNS"):
            write_runs(cls.runs)
            write_situations(cls.situations)

    def test_every_lesson_has_a_script_and_every_script_a_lesson(self):
        self.assertEqual(sorted(lesson_files()), sorted(SCRIPTS))

    def test_every_script_marks_one_completing_step_that_the_phone_hears(self):
        for lid, script in SCRIPTS.items():
            self.assertEqual(sum(st.startswith("!") for st in script), 1,
                             "%s: mark the one step that makes its result with !" % lid)
            if lid in self.runs:
                self.assertTrue(any(e.get("completes") for e in self.runs[lid]),
                                "%s: its ! step sends the phone nothing" % lid)

    def test_the_screen_shows_every_element_a_lesson_points_at(self):
        for lid, text in lesson_files().items():
            missing = screen_targets(text) - self.seen.get(lid, set())
            self.assertFalse(missing, "%s points at %s, never on screen in its run"
                             % (lid, sorted(missing)))

    def test_the_recorded_runs_are_current(self):
        with open(RUNS, encoding="utf-8") as f:
            committed = json.load(f)
        for lid in sorted(self.runs):
            self.assertEqual(committed.get(lid), self.runs[lid],
                             "%s: the instrument now plays it differently; rerun with "
                             "ARBUTUS_WRITE_LESSON_RUNS=1 and read the diff" % lid)
        self.assertEqual(sorted(committed), sorted(self.runs))

    def test_the_recorded_situations_are_current(self):
        with open(SITUATIONS, encoding="utf-8") as f:
            committed = json.load(f)
        for lid in sorted(self.situations):
            self.assertEqual(committed.get(lid), self.situations[lid],
                             "%s: the instrument now publishes it differently; rerun with "
                             "ARBUTUS_WRITE_LESSON_RUNS=1 and read the diff" % lid)
        self.assertEqual(sorted(committed), sorted(self.situations))

    def test_each_step_turns_green_at_its_marked_script_step(self):
        """#854: a lesson step is grey before the script step marked for it and
        green by its end, read by `lesson_conditions`' twin of the app's matcher."""
        for lid, text in sorted(lesson_files().items()):
            if lid not in self.runs:
                continue
            lesson = lc.parse(text, lid)
            met, _ = lc.replay(lesson, self.runs[lid], whole_rows(self.situations[lid]))
            want = marks(SCRIPTS[lid])
            script = [bare(st) for st in SCRIPTS[lid]] + ["(end)"]
            for k, st in enumerate(lesson.steps):
                where = lambda i: "never" if i is None else "at script step %d (%s)" % (i, script[i])
                if st.manual:
                    self.assertNotIn(k, want, "%s step %d is manual: no script step marks it"
                                     % (lid, k + 1))
                    continue
                self.assertIn(k, want, "%s step %d: no script step is marked %d:"
                              % (lid, k + 1, k + 1))
                self.assertEqual(met.get(k), want[k], "%s step %d (%s) green %s, marked %s"
                                 % (lid, k + 1, st.text[:40], where(met.get(k)), where(want[k])))

    def test_each_lessons_own_block_turns_green_at_its_completing_step(self):
        """The twin agrees with the app's #505 check: the lesson's own block is
        met in the script's `!` step, not before."""
        for lid, text in sorted(lesson_files().items()):
            lesson = lc.parse(text, lid)
            if lid not in self.runs or not lesson.done:
                continue
            _, at = lc.replay(lesson, self.runs[lid], whole_rows(self.situations[lid]))
            bang = next(i for i, st in enumerate(SCRIPTS[lid]) if st.startswith("!"))
            self.assertEqual(at, bang, "%s: its own block green at script step %s, its ! is %d"
                             % (lid, at, bang))


class TheRecordIsTheSameOnEveryMachine(unittest.TestCase):
    """#1042: a take's length and the playhead's seconds hang on when the
    audio thread ran against the keys, so one machine records 6.02 s and the
    next 6.03; no lesson's `is` line reads either, so the record leaves both out."""

    def test_steady_drops_the_playhead_and_take_lengths(self):
        doc = {"transport": {"playing": True, "bar": 4, "position_s": 1.1},
               "tracks": {"1": {"clips": 2, "length_s": 6.02}, "2": {"mute": False}}}
        self.assertEqual(steady(doc, False),
                         {"transport": {"playing": True, "bar": 4},
                          "tracks": {"1": {"clips": 2}, "2": {"mute": False}}})


@NO_APP
class EveryStepSaysWhatTurnsItGreen(unittest.TestCase):
    """#854's grammar, without the instrument."""

    def test_every_step_of_every_lesson_has_its_lines_or_says_manual(self):
        for lid, text in sorted(lesson_files().items()):
            for k, st in enumerate(lc.parse(text, lid).steps):
                self.assertTrue(st.conds or st.manual,
                                "%s step %d (%s): give it its own `  done:` lines, or `  manual`"
                                % (lid, k + 1, st.text[:40]))

    def test_every_marked_step_is_a_step(self):
        for lid, script in SCRIPTS.items():
            n = len(lc.parse(lesson_files()[lid], lid).steps)
            for k in marks(script):
                self.assertLess(k, n, "%s: a script step marks step %d of %d" % (lid, k + 1, n))

    def test_a_steps_lines_parse_and_belong_to_it(self):
        lesson = lc.parse("# T\nshelf: S\n\nOne.  -> SYNTH\n  done: key SYNTH\n"
                          "  then: is octave < 0\nTwo.\n  manual\n\ndone: page SYNTH\n", "t")
        self.assertEqual([len(s.conds) for s in lesson.steps], [2, 0])
        self.assertTrue(lesson.steps[1].manual)
        self.assertTrue(lesson.steps[0].conds[1].ordered)
        self.assertEqual(len(lesson.done), 1)
        for bad in ("# T\nshelf: S\n\n  done: key SYNTH\nOne.\n",          # under no step
                    "# T\nshelf: S\n\nOne.\n  then: key SYNTH\n",          # then: first
                    "# T\nshelf: S\n\nOne.\n  manual\n  done: key 1\n",   # manual and lines
                    "# T\nshelf: S\n\nOne.\n   done: key 1\n",             # three spaces
                    "# T\nshelf: S\n\nOne.\n  done: is octave ~ 1\n"):     # no such op
            with self.assertRaises(lc.LessonError, msg=bad):
                lc.parse(bad, "t")

    def test_is_reads_the_situation(self):
        doc = {"octave": -1, "tempo": {"click": {"on": True}}, "screen": {"page": "SYNTH"},
               "tracks": {"1": {"has_clip": False, "clips": 2}}}
        def holds(s):
            return lc.Cond.parse(s).holds(doc)
        self.assertTrue(holds("is octave < 0"))
        self.assertFalse(holds("is octave >= 0"))
        self.assertTrue(holds("is tempo.click.on"))
        self.assertTrue(holds("is screen.page = synth"))
        self.assertFalse(holds("is screen.page != SYNTH"))
        self.assertFalse(holds("is tracks.1.has_clip"))
        self.assertTrue(holds("is tracks.1.has_clip = false"))
        self.assertTrue(holds("is tracks.1.clips = 2"))
        self.assertFalse(holds("is tracks.2.has_clip = false"), "an absent field is never met")
        self.assertTrue(holds("is octave < 0 and screen.page = SYNTH"))
        self.assertFalse(holds("is octave < 0 and tempo.click.on = false"))
        doc["keys"] = {"held": ["NOTE_7"]}
        self.assertTrue(holds("is keys.held = NOTE_*"))
        doc["keys"] = {"held": ["1", "NOTE_7"]}
        self.assertFalse(holds("is keys.held = NOTE_*"), "every held key, not one of them")
        doc["keys"] = {"held": []}
        self.assertFalse(holds("is keys.held = NOTE_*"))
        self.assertFalse(holds("is keys.held"))

    def test_a_step_counts_only_after_the_step_before_it(self):
        lesson = lc.parse("# T\nshelf: S\n\nOne.\n  done: key SYNTH\nTwo.\n  done: key SYNTH\n"
                          "Three.\n  done: is octave = 1\n", "t")
        press = {"action": {"kind": "key", "id": "SYNTH", "down": True}, "page": "SYNTH", "header": None}
        rows = [{"events": 1, "situation": {"octave": 1}},
                {"events": 2, "situation": {"octave": 1}}]
        met, _ = lc.replay(lesson, [press, press], rows)
        self.assertEqual(met, {0: 0, 1: 1, 2: 1})


if __name__ == "__main__":
    unittest.main()
