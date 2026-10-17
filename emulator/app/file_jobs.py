"""The instrument's half of moving projects — D99, #821 items 1, 6 and 7.

`device-os-spec.md` § 7.3 (*Decided 2026-10-12*), `companion-app-spec.md` § 11:

* **File jobs hold the keys.** While the phone offloads, restores, duplicates
  or bounces, the audio stops and a played key shows what the instrument is
  doing; only the phone cancels its job. `arbutus-net` says which job runs
  (`net.sock`'s `job`); the words are here, the UI's.
* **Low storage.** Under 2 GB free, never during a take, at most once per boot
  or offload: `<n> TRASHED PROJECTS · <size> · EMPTY TRASH?` (`EMPTY` /
  `LATER`), or, with an empty trash, `STORAGE LOW · OFFLOAD PROJECTS WITH THE
  APP`. A full card refusing the autosave (D117, #1006) asks it again,
  outside the once-per-boot limit, still never during a take.

The instrument's font draws printable ASCII and `·` (`ui/font.py`), not `…`,
so the spec's `OFFLOADING TO PHONE…` is drawn with three dots (run's call,
2026-10-13: the font is not a run's to edit).
"""

import os
import time
from typing import Callable, List, Optional, Tuple

#: kind -> (the strip's sentence, its short form). `export` is a bounce.
JOB_WORDS = {
    "export": ("WORKING ON EXPORT · HOLD TIGHT", "EXPORT"),
    "offload": ("OFFLOADING TO PHONE...", "OFFLOAD"),
    "restore": ("RESTORING FROM PHONE...", "RESTORE"),
    "duplicate": ("DUPLICATING PROJECT · HOLD TIGHT", "DUPLICATE"),
}
JOB_FALLBACK = ("WORKING ON A FILE · HOLD TIGHT", "WORKING")

OFFLOADED = ("PROJECT OFFLOADED TO PHONE", "OFFLOADED")

#: #920 (D98, `screen-sketches.md` § FILE › PROJECT): a key played while the
#: instrument's own BOUNCE renders. A choice, so neither answer is red.
BOUNCE_ASK_LINES = ["CANCEL BOUNCE", "TO PLAY?"]
BOUNCE_ASK = "CANCEL BOUNCE TO PLAY? · 1 YES · 2 NO"
BOUNCE_ASK_CHOICES = ["YES", "NO"]
STORAGE_LOW = ("STORAGE LOW · OFFLOAD PROJECTS WITH THE APP", "STORAGE LOW")

#: § 7.3: "under 2 GB free"
LOW_BYTES = 2 * 1000 ** 3
#: how often the card's free space is asked (seconds): `statvfs` is cheap,
#: but nothing about free space changes at frame rate
CHECK_S = 10.0
#: how long STORAGE LOW stays in the strip: it is a sentence to read, and
#: there is nothing to answer
LOW_SAY_S = 8.0


def job_words(job: Optional[dict]) -> Tuple[str, str]:
    kind = (job or {}).get("kind")
    return JOB_WORDS.get(kind, JOB_FALLBACK)


def size_words(n: int) -> str:
    """`1.2 GB`, `340 MB`, `12 KB`: decimal, as a card's label counts."""
    for unit, scale in (("GB", 1000 ** 3), ("MB", 1000 ** 2), ("KB", 1000)):
        if n >= scale:
            v = n / scale
            return ("%.1f %s" % (v, unit)) if v < 10 else ("%d %s" % (v, unit))
    return "%d B" % n


def _tree_bytes(path: str) -> int:
    if not os.path.isdir(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for base, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(base, n))
            except OSError:
                pass
    return total


def trash_contents(root: str) -> Tuple[List[str], int, int]:
    """`(names, trashed projects, bytes)` of `<root>/.trash`.

    A trashed project is a folder holding a `project.json`: the trash also
    takes patches and samples (DDR-055), which are not projects.
    """
    trash = os.path.join(root, ".trash")
    try:
        names = sorted(os.listdir(trash))
    except OSError:
        return [], 0, 0
    projects = sum(1 for n in names
                   if os.path.isfile(os.path.join(trash, n, "project.json")))
    size = sum(_tree_bytes(os.path.join(trash, n)) for n in names)
    return names, projects, size


class Prompt:
    """A question over the main area, its answers on keys 1 and 2.

    Any other key is `LATER` and then does what it always does: the question
    must never cost the player a note (run's call, 2026-10-13, the FILE
    page's "any other key cancels").
    """

    def __init__(self, lines: List[str], strip: str, choices: List[str],
                 on_answer: Callable[[int], None]):
        self.lines = lines
        self.strip = strip
        self.choices = choices
        self.on_answer = on_answer


class StorageWatch:
    """§ 7.3's low-storage question, asked at most once per boot or offload."""

    def __init__(self, app, free: Optional[Callable[[str], int]] = None,
                 clock=time.monotonic):
        self.app = app
        self.free = free or _free_bytes
        self.clock = clock
        self.asked = False
        self._next = 0.0
        #: a full card refused the autosave and the question is owed
        self._full = False

    def offloaded(self):
        """An offload is one of the two moments it may ask again."""
        self.asked = False
        self._next = 0.0

    def card_full(self):
        """The autosave was refused for a full card (`project_file.autosave`
        calls it once a fill): ask on the next tick that may, asked before
        this boot or not, and whatever `statvfs` says."""
        self._full = True

    def tick(self):
        app = self.app
        now = self.clock()
        if not self._full and (self.asked or now < self._next):
            return
        if not self._full:
            self._next = now + CHECK_S
        if app.tape.recording or getattr(app, "file_job", None) \
                or app.prompt is not None:
            return
        root = app.library.root
        if self._full:
            self._full = False
        else:
            free = self.free(root)
            if free is None or free >= LOW_BYTES:
                return
        self.asked = True
        names, projects, size = trash_contents(root)
        if not names:
            app.flash(*STORAGE_LOW, seconds=LOW_SAY_S)
            return
        what = ("%d TRASHED PROJECT%s" % (projects, "" if projects == 1 else "S")
                if projects else "TRASH")
        question = "%s · %s · EMPTY TRASH?" % (what, size_words(size))
        app.prompt = Prompt(
            [what, size_words(size), "EMPTY TRASH?"], question,
            ["EMPTY", "LATER"],
            lambda choice: self._answer(choice, names))

    def _answer(self, choice: int, names: List[str]):
        if choice != 0:
            return
        app = self.app
        emptied = 0
        for name in names:
            #: through the audio process's `library {op: delete}`, as the
            #: phone's Empty trash is: stock and the open project refused there
            reply = app.native.send({"type": "library", "op": "delete",
                                     "path": ".trash/%s" % name})
            if reply.get("type") != "error":
                emptied += 1
        doc = getattr(app, "doc", None)
        if emptied and doc is not None and doc.refused:
            doc.retry_now()                   # retry the refused write now
        app.flash("TRASH EMPTIED" if emptied == len(names)
                  else "TRASH: %d OF %d EMPTIED" % (emptied, len(names)),
                  "EMPTIED")


def _free_bytes(root: str) -> Optional[int]:
    try:
        st = os.statvfs(root)
    except (OSError, AttributeError):
        return None
    return st.f_bavail * st.f_frsize
