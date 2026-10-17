"""Generic two-pane selector page (DDR-030), and the file manager on it.

Left pane = folder (engine), right pane = item (patch/sample). ENC1 scrolls
folders, ENC2 items, selection applies live, any page key dismisses.
Breadcrumb = parent breadcrumb + SELECT.

Since stage J2 a browser is also a **file manager** (DDR-055): LIFT takes the
highlighted user item, DROP moves it into the highlighted folder, SHIFT+DROP
makes `FOLDER-NN` there and drops into that, and an item that is never dropped
stays in `.trash/`. The moves themselves are `app/library_ops.py`; what is
here is the gesture and the two questions a source has to answer for it —
where does this row live on disk, and where does that folder live.
"""

import os
from typing import Callable, Dict, List, Optional, Tuple

from ui import draw, palette
from ui.font import SMALL

from . import box_trust
from .page import Page
from .params import Param, StepType, pad_slots


class MenuSource:
    """Where a menu's two panes come from."""

    def folders(self) -> List[str]:
        raise NotImplementedError

    def items(self, folder_idx: int) -> List[str]:
        raise NotImplementedError

    # -- the file manager (DDR-055) ---------------------------------------
    # A source that cannot answer these is simply not a file manager: the
    # gesture reports "NOT A FILE" and nothing moves.

    def item_path(self, folder_idx: int, item_idx: int) -> Optional[str]:
        """Absolute path of one right-pane row, or None."""
        return None

    def folder_path(self, folder_idx: int) -> Optional[str]:
        """Absolute path of one left-pane row, or None."""
        return None

    # -- engines on the card (#1122) --------------------------------------
    def marked(self, folder_idx: int) -> bool:
        """True for a folder whose engine is not installed (`NEEDS`)."""
        return False

    def box(self, folder_idx: int) -> Optional[Tuple[str, str]]:
        """(box id, the folder the host found it in) a pick would run."""
        return None


class LibrarySource(MenuSource):
    def __init__(self, library, category: str):
        self.library = library
        self.category = category

    def _entries(self):
        return self.library.list(self.category)

    def folders(self) -> List[str]:
        return [("NEEDS %s" % f.name) if f.manifest.get("needs") else f.name
                for f in self._entries()]

    def marked(self, folder_idx: int) -> bool:
        lf = self.folder(folder_idx)
        return lf is not None and bool(lf.manifest.get("needs"))

    def box(self, folder_idx: int) -> Optional[Tuple[str, str]]:
        lf = self.folder(folder_idx)
        box_dir = getattr(self.library, "box_dir", None)
        if lf is None or box_dir is None or not lf.manifest.get("id"):
            return None
        return lf.manifest["id"], box_dir(lf)

    def items(self, folder_idx: int) -> List[str]:
        """Display names, positionally aligned with the folder's real items.

        The extension is filing, not information — the cells are 5 px glyphs
        wide and `.patch` on every row buys nothing.
        """
        entries = self._entries()
        if not entries:
            return []
        lf = entries[max(0, min(len(entries) - 1, folder_idx))]
        return [os.path.splitext(i)[0] for i in lf.items] or ["—"]

    def item_path(self, folder_idx: int, item_idx: int) -> Optional[str]:
        """The real file behind a row — `items()` strips the extension, so the
        name on screen is not a filename and cannot be joined to a folder."""
        lf = self.folder(folder_idx)
        if lf is None or not lf.items:
            return None
        if not 0 <= item_idx < len(lf.items):
            return None
        return os.path.abspath(lf.item_path(lf.items[item_idx]))

    def folder_path(self, folder_idx: int) -> Optional[str]:
        lf = self.folder(folder_idx)
        return os.path.abspath(lf.path) if lf is not None else None

    def folder(self, folder_idx: int):
        entries = self._entries()
        if not entries:
            return None
        return entries[max(0, min(len(entries) - 1, folder_idx))]


class StaticSource(MenuSource):
    def __init__(self, table: Dict[str, List[str]]):
        self.table = table
        self._keys = list(table.keys())

    def folders(self) -> List[str]:
        return list(self._keys)

    def items(self, folder_idx: int) -> List[str]:
        if not self._keys:
            return []
        k = self._keys[max(0, min(len(self._keys) - 1, folder_idx))]
        return self.table[k] or ["—"]


class MenuPage(Page):
    category = "utility"

    def __init__(self, app, parent: Page, source: MenuSource,
                 apply_cb: Callable[[int, int], None],
                 crumb: Optional[List[str]] = None):
        super().__init__(app)
        self.parent = parent
        self.source = source
        self.apply_cb = apply_cb
        self._crumb = crumb
        self.category = parent.category
        self.state.folder_idx = 0
        self.state.item_idx = 0
        self._sync_params()

    # -- content ----------------------------------------------------------
    def breadcrumb(self) -> List[str]:
        base = list(self._crumb) if self._crumb else list(self.parent.breadcrumb())
        return base + ["SELECT"]

    def strip_labels(self):
        return [None, None, None, None]

    def active_strip_cell(self):
        return None

    def _sync_params(self):
        folders = self.source.folders() or ["—"]
        # a file op can take the highlighted row out from under the cursor
        self.state.folder_idx = max(0, min(len(folders) - 1,
                                           self.state.folder_idx))
        items = self.source.items(self.state.folder_idx) or ["—"]
        self.state.item_idx = max(0, min(len(items) - 1, self.state.item_idx))
        self._p_folder = Param("FOLDER", 0, max(0, len(folders) - 1),
                               self.state.folder_idx, StepType.DISCRETE,
                               choices=folders)
        self._p_item = Param("ITEM", 0, max(0, len(items) - 1),
                             min(self.state.item_idx, len(items) - 1),
                             StepType.DISCRETE, choices=items)

    def slots(self):
        self._sync_params()
        return pad_slots([self._p_folder, self._p_item, None, None])

    # -- input ------------------------------------------------------------
    def on_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        folders = self.source.folders()
        if idx == 0 and folders:
            self.state.folder_idx = max(0, min(len(folders) - 1,
                                               self.state.folder_idx + delta))
            self.state.item_idx = 0
        elif idx == 1:
            items = self.source.items(self.state.folder_idx)
            if items:
                self.state.item_idx = max(0, min(len(items) - 1,
                                                 self.state.item_idx + delta))
        else:
            return False
        f, i = self.state.folder_idx, self.state.item_idx
        box = self.source.box(f)
        if box is None or self.app is None \
                or not box_trust.ask(self.app, box[0], box[1],
                                     lambda: self._picked(f, i)):
            self.apply_cb(f, i)
        if self.app is not None:
            self.app.notify_param_changed(self.parent)
        return True

    def _picked(self, f: int, i: int):
        """RUN ANYWAY: the pick the question held, if it is still the one
        showing (a turn answers CANCEL before it moves, so it is)."""
        self.apply_cb(f, i)
        self.app.notify_param_changed(self.parent)

    # -- the file manager (DDR-055, stage J2) ------------------------------
    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        """LIFT / DROP on a browser row. Anything else is not ours.

        The selection is **not** re-applied afterwards: moving a file is
        filing, not playing, and a browser that loaded whatever slid into the
        highlighted row would change the sound every time you tidied up.
        """
        key = str(getattr(key, "value", key))
        if not pressed or key not in ("LIFT", "DROP"):
            return False
        ops = getattr(self.app, "files", None)
        if ops is None:
            return False
        self._sync_params()
        if key == "LIFT":
            path = self.source.item_path(self.state.folder_idx,
                                         self.state.item_idx)
            if path is None:
                self.app.flash("NOT A FILE", "NOT A FILE")
                return True
            name = self.source.items(self.state.folder_idx)[self.state.item_idx]
            err = ops.lift(path, name)
            self.app.flash(err or "LIFTED %s" % name, err or "LIFT %s" % name)
            return True

        folder = self.source.folder_path(self.state.folder_idx)
        if folder is None:
            self.app.flash("NOT A FOLDER", "NOT A FOLDER")
            return True
        name = ops.lifted.name if ops.lifted is not None else ""
        err = (ops.drop_new_folder(folder) if shift else ops.drop(folder))
        self.app.flash(err or "DROPPED %s" % name, err or "DROP %s" % name)
        return True

    # -- render -----------------------------------------------------------
    def render(self, fb, rect, app):
        x, y, w, h = rect
        mid = x + w // 2
        draw.vline(fb, mid, y + 2, h - 4, palette.RULE)
        folders = self.source.folders()
        items = self.source.items(self.state.folder_idx)
        # #1122: a folder whose engine is not installed, and its rows, dim
        dim = {n: palette.DIM for n in range(len(folders))
               if self.source.marked(n)}
        rows = ({n: palette.DIM for n in range(len(items))}
                if self.state.folder_idx in dim else None)
        self._pane(fb, x + 4, y + 4, mid - x - 10, h - 8,
                   folders, self.state.folder_idx, palette.encoder_color(0),
                   own=dim)
        self._pane(fb, mid + 5, y + 4, x + w - mid - 10, h - 8,
                   items, self.state.item_idx, palette.encoder_color(1),
                   own=rows)

    @staticmethod
    def _pane(fb, x, y, w, h, entries, sel, color, own=None):
        """`own` maps a row to its own colour, which its highlight takes too
        (the save row's green, D98)."""
        own = own or {}
        rows = max(1, h // 10)
        top = max(0, min(max(0, len(entries) - rows), sel - rows // 2))
        for i in range(rows):
            n = top + i
            if n >= len(entries):
                break
            ry = y + i * 10
            active = (n == sel)
            if active:
                draw.fill_rect(fb, x - 2, ry - 1, w, 9,
                               own.get(n, palette.INVERT_BG))
            fg = palette.INVERT_FG if active else own.get(n, color)
            draw.text(fb, x, ry, draw.clip_text(entries[n], w - 4, SMALL), fg, SMALL)
