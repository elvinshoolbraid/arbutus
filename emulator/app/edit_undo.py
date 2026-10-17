"""One-step undo of the last edit on an instrument page (DDR-054).

SHIFT+STOP means "undo one thing" everywhere, and on SYNTH/DRUM the one thing
is the last edit made there: a run of turns on one param, a browser pick that
swapped the engine, a rack or MOD tab's box, or a whole-sound load. There is
no revert-to-loaded — the patch is still in its folder.

The shape is `Tape.push_undo` / `Tape.undo` (`app/tape.py`), deliberately: one
level, and a `group` that coalesces a run of calls with itself so an encoder
sweep is one entry and not sixty. What is snapshotted here is the page's own
DDR-029 snapshot — the same format-1 document a slot holds — so undo is a
slot recall that nobody had to save.

A push that turns out to have changed nothing (a turn on a locked cell, a pad
copy the host refused) hands back a token; `rollback(token)` puts the previous
entry back, so a no-op never eats the edit before it.

A gesture that is *not* an edit but that the snapshot carries anyway — the
octave (#152) — records nothing and calls `amend` instead, so the entry that
is already open comes back without dragging it along.
"""

from typing import Any, Callable, Optional, Tuple

#: what `push` hands back and `rollback` takes: the entry that was displaced,
#: or None when the push coalesced into a run that is already open
Token = Optional[Tuple[Optional[dict], Any]]


class EditUndo:
    def __init__(self, snapshot: Callable[[], dict],
                 restore: Callable[[dict], None]):
        self._snapshot = snapshot
        self._restore = restore
        self._snap: Optional[dict] = None
        self._group: Any = None

    # -- recording --------------------------------------------------------
    def push(self, group: Any = None) -> Token:
        """Snapshot before an edit; `group` coalesces a run with itself.

        `group=None` is always its own entry — a gesture that happens once.
        """
        if group is not None and group == self._group and self._snap is not None:
            return None
        prev = (self._snap, self._group)
        self._snap = self._snapshot()
        self._group = group
        return prev

    def rollback(self, token: Token):
        """Undo a `push` whose edit did not happen."""
        if token is not None:
            self._snap, self._group = token

    def amend(self, **fields):
        """Move the held entry's snapshot on for a gesture that is not an edit.

        The snapshot is the page's whole document, so it carries state that is
        no business of undo's — the octave (#152), which the player may move
        *after* the edit was recorded. Rewriting those keys in the held
        snapshot (`**fields` is keyword arguments collected into a dict) keeps
        the entry's sound and leaves the gesture where the player put it. With
        no entry open there is nothing to amend and nothing to record.
        """
        if self._snap is not None:
            self._snap.update(fields)

    def clear(self):
        """Forget the entry — the sound under it is gone (slot recall, NEW)."""
        self._snap = None
        self._group = None

    # -- the gesture ------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        return self._snap is not None

    def undo(self) -> bool:
        """SHIFT+STOP — one level. A second press has nothing left."""
        if self._snap is None:
            return False
        snap = self._snap
        self._snap = None
        self._group = None
        self._restore(snap)
        return True
