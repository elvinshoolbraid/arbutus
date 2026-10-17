"""Slot memory: 6 slots (A B C X Y Z) per page, autosaved (DDR-029).

There is no save gesture. Every change writes the active slot's full snapshot
to `emulator/state/<page>.json`, debounced so encoder sweeps do not hammer the
disk. Recall swaps a whole snapshot in.
"""

import json
import os
import shutil
import time
from typing import Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(HERE, "state")

SLOT_LETTERS: List[str] = ["A", "B", "C", "X", "Y", "Z"]

DEBOUNCE_S = 0.25


def wipe_state(state_dir: str = STATE_DIR):
    """--fresh: throw away every persisted slot."""
    if os.path.isdir(state_dir):
        shutil.rmtree(state_dir)


class SlotStore:
    def __init__(self, page_id: str, state_dir: str = STATE_DIR,
                 debounce: float = DEBOUNCE_S):
        self.page_id = page_id
        self.state_dir = state_dir
        self.debounce = debounce
        self.active = "A"
        self.records: Dict[str, dict] = {}
        self._dirty = False
        self._due = 0.0
        self._snapshot_fn: Optional[Callable[[], dict]] = None
        self._last_refusal = ""
        self._load()

    # -- wiring -----------------------------------------------------------
    def bind(self, snapshot_fn: Callable[[], dict]):
        self._snapshot_fn = snapshot_fn

    @property
    def path(self) -> str:
        return os.path.join(self.state_dir, f"{self.page_id.lower()}.json")

    # -- persistence ------------------------------------------------------
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self.active = data.get("active", "A")
        if self.active not in SLOT_LETTERS:
            self.active = "A"
        self.records = {k: v for k, v in (data.get("slots") or {}).items()
                        if k in SLOT_LETTERS}

    def touch(self, now: Optional[float] = None):
        """Record a change into the active slot and schedule a write."""
        if self._snapshot_fn is None:
            return
        self.records[self.active] = self._snapshot_fn()
        self._dirty = True
        self._due = (now if now is not None else time.monotonic()) + self.debounce

    def records_now(self) -> Dict[str, dict]:
        """The records with the active slot as it is now, marking nothing
        dirty: the project autosave's read (#1017, #624)."""
        if self._snapshot_fn is None:
            return self.records
        records = dict(self.records)
        records[self.active] = self._snapshot_fn()
        return records

    def tick(self, now: Optional[float] = None):
        now = now if now is not None else time.monotonic()
        if self._dirty and now >= self._due:
            self.flush()

    def flush(self):
        if not self._dirty:
            return
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"active": self.active, "slots": self.records}, f,
                          indent=1)
            os.replace(tmp, self.path)
        except OSError as exc:
            #: A card that refuses the write (full, read-only) must not take
            #: the frame loop down: the record stays dirty and the next tick
            #: retries. One line per distinct reason (Q3 audit, #31).
            text = str(exc)
            if text != self._last_refusal:
                self._last_refusal = text
                print("slots: cannot write %s — retrying (%s)" % (self.path, exc))
            return
        self._last_refusal = ""
        self._dirty = False

    # -- slot operations --------------------------------------------------
    def select(self, letter: str) -> Optional[dict]:
        """Make `letter` active; return its snapshot (None if never written)."""
        if letter not in SLOT_LETTERS:
            return None
        self.flush()
        if letter != self.active:
            # #624: which slot is active is on disk too, or a reboot after a
            # recall shows one slot and autosaves into another.
            self.active = letter
            self._dirty = True
            self._due = time.monotonic() + self.debounce
        return self.records.get(letter)

    def get(self, letter: str) -> Optional[dict]:
        return self.records.get(letter)
