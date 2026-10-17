"""Browsers are file managers (DDR-055) — stage J2.

    LIFT takes the highlighted user item; DROP in another folder moves it;
    DROP at folder level makes `FOLDER-NN`; never dropping = `.trash/`, which
    the app empties. Stock content does not lift. Consecutive lifts form one
    undo group (SHIFT+STOP puts them all back).

This module is the file half of that: an undo stack that groups, and the two
questions the browser has to be able to answer — *is this item mine to move?*
and *where did it go?*  The gestures themselves are on `app/menu.py`'s
`MenuPage`.

**The moves are the host's, not this module's** (#62 item 3). Every one of
them is a `library {op}` on the audio process — `trash`, `move`, `rename` —
and the rules they enforce (a path inside the library, stock content refusing,
a trash collision renaming rather than failing) live in `audio/host/` once,
where the companion app reaches them too (`PROTOCOL.md` § `library {op}`).
What stays here is the *gesture* layer: what is held, which moves belong to
one undo group, and the words on the strip.

The decision that kept this module on `shutil` was "what does a browser do
with no host to ask?" — and it has no case any more: `App` refuses to exist
without the audio process (stage N1), so there is always one to ask. A host
that has gone away mid-gesture (§ 4.2's reconnect window) refuses the op like
any other refusal, and **nothing moves** — which is the honest answer, and the
one a second implementation here could not give.

**A LIFT moves the item to `.trash/` immediately.** It is tempting to hold the
path and only move on the drop, but then "never dropping" has no moment at
which it happens — you would have to decide that a browser closing, or the
instrument shutting down, is the delete. Moving on the lift makes the rule
self-enforcing: the item leaves the folder the instant you take it (which is
also what a lifted tape clip does, DDR-013), and a DROP is simply the second
move. `.trash/` is dot-prefixed, so the host's box scan and this folder's own
browsers both skip it (`audio/hostlib/library.cpp`, stage J2).

**Stock content is a manifest, not a guess.** `<library>/stock.json` lists the
items the instrument ships with; `audio/tools/mkstock.py` writes it. Nothing on
disk distinguishes a patch Elfy recorded from one that came in the image — both
are a `.patch` written by the same code — so the shipped list is the only
honest answer. No manifest at all means nothing is stock, which is right for a
library someone assembled themselves. The host reads the same file and is what
actually refuses a lift; `read_stock`/`is_stock` stay here because the
manifest is also a question the UI asks *without* moving anything.
"""

import json
import os
import re
from typing import List, Optional, Tuple

#: DDR-055's soft delete. Dot-prefixed, so nothing scans it (stage J2).
TRASH_DIR = ".trash"

#: `DROP at folder level makes FOLDER-NN`
NEW_FOLDER_PREFIX = "FOLDER-"
NEW_FOLDER_RE = re.compile(r"^%s(\d+)$" % NEW_FOLDER_PREFIX)

STOCK_FILE = "stock.json"
STOCK_FORMAT = 1

# ---------------------------------------------------------------------------
# what the instrument shipped with
# ---------------------------------------------------------------------------

def read_stock(root: str) -> set:
    """The set of library-relative paths that came in the image."""
    try:
        with open(os.path.join(root, STOCK_FILE)) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(doc, dict) or doc.get("format") != STOCK_FORMAT:
        return set()
    return {str(p) for p in (doc.get("items") or [])}


def is_stock(root: str, path: str, stock: Optional[set] = None) -> bool:
    if stock is None:
        stock = read_stock(root)
    if not stock:
        return False
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:
        return False
    return rel.replace(os.sep, "/") in stock


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------

def free_name(name: str, *directories: str) -> str:
    """`name`, then `name-2`, `name-3`… — the first free in **every** one.

    A DROP names a folder, not a file, so a collision there has to rename
    rather than refuse: two lifts of one name both have to survive, exactly as
    they do in `.trash/`. The host's `move` refuses `EXISTS` and is right to
    (its caller named the destination), so getting the item to a free name is
    a `rename` first — which happens in the folder the item is in *now*. Hence
    more than one directory: the name has to be free in both.
    """
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        candidate = name if n == 1 else "%s-%d%s" % (stem, n, ext)
        if not any(os.path.exists(os.path.join(d, candidate))
                   for d in directories):
            return candidate
        n += 1


def free_path(directory: str, name: str) -> str:
    """`name` inside `directory`, with `-2`, `-3`… if it is taken.

    The same rule the host applies to a `trash` collision (`free_path` in
    `audio/host/audio_host.cpp`, named after this one).
    """
    return os.path.join(directory, free_name(name, directory))


def next_folder_name(parent: str) -> str:
    """`FOLDER-01`, `FOLDER-02`, … — the first one not already there."""
    used = set()
    try:
        for entry in os.listdir(parent):
            m = NEW_FOLDER_RE.match(entry)
            if m:
                used.add(int(m.group(1)))
    except OSError:
        pass
    n = 1
    while n in used:
        n += 1
    return "%s%02d" % (NEW_FOLDER_PREFIX, n)


# ---------------------------------------------------------------------------
# the buffer and the ops
# ---------------------------------------------------------------------------

class LiftedItem:
    """What LIFT is holding: a file or a folder, already in `.trash/`."""

    def __init__(self, name: str, path: str, origin: str, is_dir: bool):
        self.name = name              # as the browser drew it
        self.path = path              # where it is now (inside `.trash/`)
        self.origin = origin          # the folder it came out of
        self.is_dir = is_dir

    @property
    def description(self) -> str:
        return self.name

    def __repr__(self):                                    # pragma: no cover
        return "<LiftedItem %s>" % self.name


class FileOps:
    """The library's lift / drop / trash / undo, for every browser.

    One of these lives on `App`, not on a page: a lift survives closing the
    browser and opening another one, which is what makes "lift here, drop
    there" work across folders.

    Nothing here touches the filesystem to *move* anything: every move is a
    `library {op}` on the audio process (#62 item 3). What this class owns is
    which moves belong to one undo group and what the strip says about them.
    """

    def __init__(self, app):
        self.app = app
        self.lifted: Optional[LiftedItem] = None
        #: stack of groups; a group is [(from, to), …] applied in order
        self.undo_stack: List[List[Tuple[str, str]]] = []
        self._run_open = False        # consecutive lifts share a group

    # -- helpers ----------------------------------------------------------
    @property
    def root(self) -> str:
        return self.app.library.root

    @property
    def trash(self) -> str:
        return os.path.join(self.root, TRASH_DIR)

    def is_stock(self, path: str) -> bool:
        return is_stock(self.root, path)

    def _record(self, src: str, dst: str, new_group: bool):
        if new_group or not self.undo_stack:
            self.undo_stack.append([])
        self.undo_stack[-1].append((src, dst))

    # -- one `library {op}` on the host ------------------------------------
    #: what the host's named refusals mean to a browser. Everything else --
    #: `BAD PATH`, a write failure, a link that has gone away (§ 4.2) -- is
    #: the gesture's own "cannot", because the player can do nothing about
    #: the difference between them and the message is already in the strip.
    REFUSALS = {"NOT FOUND": "NOTHING THERE", "STOCK": "STOCK ITEM",
                #: #297: the open project's folder holds the live tape
                "OPEN ON THE INSTRUMENT": "OPEN ON THE INSTRUMENT"}

    def _rel(self, path: str) -> str:
        """A library-relative path, forward slashes -- what `library {op}`
        takes. Anything outside the root comes out with `..` in it and the
        host refuses it `BAD PATH`, which is the right answer."""
        try:
            rel = os.path.relpath(os.path.abspath(path),
                                  os.path.abspath(self.root))
        except ValueError:                                  # different drives
            return os.path.abspath(path)
        return rel.replace(os.sep, "/")

    def _abs(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/")) if rel else ""

    def _held(self, what: str) -> Optional[str]:
        """#876: the refusal while the library is a computer's, or None.

        Asked first by every gesture, before a path is made or a message
        sent: from STORAGE's park to the unpark the library's folder may be
        the bare mount point, and the host does not refuse a move into it
        (device OS § 11.1, "the browsers' file ops are refused while
        exported"; `App.library_held` is that state).
        """
        if getattr(self.app, "library_held", False):
            return "NO %s — LIBRARY ON USB" % what
        return None

    def _op(self, op: str, path: str, **fields) -> Tuple[str, Optional[str]]:
        """`library {op}` and its reply: `(where it landed, None)` or
        `("", <the host's refusal>)`.

        The op rescans the host's box index before it replies (`PROTOCOL.md`
        § `library {op}`), so nothing here sends `rescan` afterwards.
        """
        native = getattr(self.app, "native", None)
        if native is None:                                  # pragma: no cover
            return "", "NO HOST"
        msg = {"type": "library", "op": op, "path": self._rel(path)}
        msg.update(fields)
        reply = native.send(msg)
        if reply.get("type") == "error":
            return "", str(reply.get("message") or reply.get("code") or "?")
        return self._abs(reply.get("path") or ""), None

    def _restore(self, dst: str, src: str) -> bool:
        """Put what is at `dst` back at `src` -- one undo step.

        **Move, then rename.** The other order asks the host to free the old
        name in the folder the item is sitting in, and for a `.trash/` item
        that name is taken by definition: it is the collision that gave this
        one its `-2` in the first place.
        """
        cur = dst
        if (os.path.dirname(os.path.abspath(cur))
                != os.path.dirname(os.path.abspath(src))):
            cur, err = self._op("move", cur,
                                to=self._rel(os.path.dirname(src)))
            if err is not None:
                return False
        if os.path.basename(cur) != os.path.basename(src):
            cur, err = self._op("rename", cur, name=os.path.basename(src))
            if err is not None:
                return False
        return True

    # -- the gestures -----------------------------------------------------
    def lift(self, path: str, name: str) -> Optional[str]:
        """Take the highlighted item. Returns an error string, or None.

        The item goes straight to `.trash/`: that is what makes "never
        dropping" the delete rather than a thing that has to be noticed later.
        Missing and stock are the host's refusals, not a second copy of the
        same two checks (#62 item 3).
        """
        held = self._held("LIFT")
        if held is not None:
            return held
        is_dir = os.path.isdir(path)
        origin = os.path.dirname(os.path.abspath(path))
        dst, err = self._op("trash", path)
        if err is not None:
            return self.REFUSALS.get(err, "CANNOT LIFT")
        # a lift that follows a lift joins the same group; anything else
        # starts a new one (DDR-055: "lift, lift, oops")
        self._record(path, dst, new_group=not self._run_open)
        self._run_open = True
        self.lifted = LiftedItem(name, dst, origin, is_dir)
        self.rescan()
        return None

    def drop(self, directory: str) -> Optional[str]:
        """DROP in a folder: move what is held into it. Error string or None.

        A name already taken there is renamed rather than refused, and the
        rename is the host's too -- so a drop is one message in the ordinary
        case and two in the collision.
        """
        item = self.lifted
        if item is None:
            return "NOTHING LIFTED"
        held = self._held("DROP")
        if held is not None:
            return held               # still held: DROP again after the unpark
        if not os.path.isdir(directory):
            return "NO SUCH FOLDER"
        src = item.path
        dst, err = self._op("move", src, to=self._rel(directory))
        if err == "EXISTS":
            name = free_name(os.path.basename(src),
                             directory, os.path.dirname(src))
            renamed, err = self._op("rename", src, name=name)
            if err is None:
                self._record(src, renamed, new_group=False)
                src = renamed
                dst, err = self._op("move", src, to=self._rel(directory))
        if err is not None:
            self.rescan()
            return "CANNOT DROP"
        self._record(src, dst, new_group=False)
        self._run_open = False            # the run is over; the next lift opens one
        self.lifted = None
        self.rescan()
        return None

    def drop_new_folder(self, parent: str) -> Optional[str]:
        """DROP at folder level: make `FOLDER-NN` in `parent` and drop into it.

        The folder is made here, not by the host: `library {op}` is four file
        operations on an item that exists and has no "make a folder" among
        them, and the name is a browser affair (`FOLDER-01`, `FOLDER-02`).
        The *move* into it is still the host's.
        """
        if self.lifted is None:
            return "NOTHING LIFTED"
        held = self._held("DROP")
        if held is not None:
            return held               # before `os.makedirs`, not after
        if not os.path.isdir(parent):
            return "NO SUCH FOLDER"
        made = os.path.join(parent, next_folder_name(parent))
        try:
            os.makedirs(made)
        except OSError:
            return "CANNOT DROP"
        return self.drop(made)

    def undo(self) -> bool:
        """SHIFT+STOP: put the whole last group back, newest move first."""
        if not self.undo_stack:
            return False
        held = self._held("UNDO")
        if held is not None:
            # the group stays on the stack for after the unpark; True so
            # SHIFT+STOP does not fall through to the page's own undo
            self.app.flash(held, "ON USB")
            return True
        group = self.undo_stack.pop()
        ok = False
        for src, dst in reversed(group):
            if os.path.exists(dst) and self._restore(dst, src):
                ok = True
        self.lifted = None
        self._run_open = False
        self.rescan()
        return ok

    # -- after any move ---------------------------------------------------
    def rescan(self):
        """The browser's tree, re-read.

        The emulator walks the library itself, so its own rescan is what makes
        the pane redraw. The host's second index off the same roots is *not*
        sent a `rescan` here: every `library {op}` rescans before it replies
        (`PROTOCOL.md` § `library {op}`), so one round trip both moves the
        file and re-indexes -- and a browser that sent a second one would be
        asking for a scan of a library nothing had changed since.
        """
        try:
            self.app.library.rescan()
        except Exception:                                   # noqa: BLE001
            pass

    # -- the strip --------------------------------------------------------
    def ticker(self):
        """`LIFTED: name` while something is held (DDR-055's "strip shows it")."""
        if self.lifted is None:
            return None
        return ("LIFTED: %s" % self.lifted.name, "LIFT %s" % self.lifted.name)
