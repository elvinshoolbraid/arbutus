"""The browser's save row: down loads, up saves (D98, #820).

`control-inventory.md` § Patches, *Saving an effect rack*: the effect
browser's top level has a green row **above** the racks folder,
`SAVE THIS RACK`. The cursor still opens on the racks folder; scrolled up onto
the row, the right pane is the name menu (tool names, `app/name_menu.py`)
under `PICK A NAME TO SAVE RACK`, and its commit writes the rack into SAVED
RACKS, listed under the stock racks.

The row is the browser's, not the library's: the library's folder list (and
every index a page's pick callback gets) is unchanged, and this page shifts
by one on the way through. The commit is the name menu's: the push of the
encoder just turned, or the red key (ENC1's column, the encoder that reached
the row), which `App` hands the parent page as a number key closing a menu,
so the page asks `commit_by_key` first.
"""

from typing import Callable, Iterable, List, Optional

from ui import draw, palette
from ui.font import LARGE, SMALL

from . import keys as K
from .menu import MenuPage, MenuSource
from .name_menu import NameMenu, STRIP, STRIP_SHORT

ROW = "SAVE THIS RACK"
HEADING = "PICK A NAME TO SAVE RACK"
#: the red key's label: what pressing it does
SAVE = "SAVE"
#: the encoder whose turn reaches the row: its push commits until ENC2
#: turns, and the red key sits under its cell
OPENER = 0


class _WithRow(MenuSource):
    """`inner` with the save row on top; row 0's items are the three names."""

    def __init__(self, inner: MenuSource, page: "SaveRowMenu"):
        self.inner = inner
        self.page = page

    def folders(self) -> List[str]:
        return [ROW] + self.inner.folders()

    def items(self, folder_idx: int) -> List[str]:
        if folder_idx == 0:
            return self.page.names().names()
        return self.inner.items(folder_idx - 1)

    def item_path(self, folder_idx: int, item_idx: int) -> Optional[str]:
        if folder_idx == 0:
            return None
        return self.inner.item_path(folder_idx - 1, item_idx)

    def folder_path(self, folder_idx: int) -> Optional[str]:
        if folder_idx == 0:
            return None
        return self.inner.folder_path(folder_idx - 1)

    def marked(self, folder_idx: int) -> bool:
        return folder_idx > 0 and self.inner.marked(folder_idx - 1)

    def box(self, folder_idx: int):
        return None if folder_idx == 0 else self.inner.box(folder_idx - 1)


class SaveRowMenu(MenuPage):
    """A two-pane browser with SAVE THIS RACK above its first folder.

    `apply_cb` gets the inner source's indices, as a plain `MenuPage`'s
    would; `taken` lists the names the card already holds, and `on_save`
    gets the picked one.
    """

    def __init__(self, app, parent, source: MenuSource,
                 apply_cb: Callable[[int, int], None],
                 taken: Callable[[], Iterable[str]],
                 on_save: Callable[[str], None],
                 crumb: Optional[List[str]] = None):
        self._names: Optional[NameMenu] = None
        self._taken = taken
        self.on_save = on_save
        #: the encoder a push must come from to commit on the row
        self.last_turned = OPENER
        super().__init__(app, parent, _WithRow(source, self),
                         lambda f, i: apply_cb(f - 1, i), crumb)
        self.state.folder_idx = 1          # the cursor opens on the racks

    # -- the names ---------------------------------------------------------
    def names(self) -> NameMenu:
        """The name menu the row shows, made when first needed: the names
        are drawn from what the card holds then."""
        if self._names is None:
            self._names = NameMenu(None, self, "rack", self._taken(),
                                   self._saved, opener=OPENER)
        return self._names

    @property
    def on_row(self) -> bool:
        return self.state.folder_idx == 0

    def _saved(self, name: str):
        self._names = None                 # the card holds one more now
        self.on_save(name)

    def commit(self):
        """Write the highlighted name and put the browser away."""
        if self.app is not None and self.app.menu is self:
            self.app.close_menu()
        self.names().commit()

    def commit_by_key(self, idx: int) -> bool:
        """A number key closed this browser: the red key on the row saves.
        True when it did (the page then does nothing else with the key)."""
        if self.on_row and idx == OPENER:
            self.names().commit()
            return True
        return False

    # -- input -------------------------------------------------------------
    def on_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        if idx == 1 and self.on_row:
            self.last_turned = 1
            self.names().on_encoder(1, delta, shift)
            self.state.item_idx = self.names().sel
            if self.app is not None:
                self.app.notify_param_changed(self.parent)
            return True
        if idx == 0 and delta:
            self.last_turned = 0
            n = len(self.source.folders())
            to = max(0, min(n - 1, self.state.folder_idx + delta))
            if to == 0:
                # up saves, it does not load: nothing is applied on the row
                self.state.folder_idx = 0
                self.state.item_idx = self.names().sel
                if self.app is not None:
                    self.app.notify_param_changed(self.parent)
                return True
        return super().on_encoder(idx, delta, shift)

    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        if pressed and self.on_row and K.push_index(key) == self.last_turned:
            self.commit()
            return True
        return super().on_key(key, pressed, shift)

    # -- the Page contract -------------------------------------------------
    def strip_labels(self):
        labels: List[Optional[str]] = [None] * 4
        if self.on_row:
            labels[OPENER] = SAVE
        return labels

    def red_strip_cell(self):
        return OPENER if self.on_row else None

    def tickers(self):
        if not self.on_row:
            return []
        from .app import TICKER_OTHER
        return [(TICKER_OTHER, STRIP, STRIP_SHORT)]

    def render(self, fb, rect, app):
        if not self.on_row:
            self._render_panes(fb, rect)
            return
        x, y, w, h = rect
        mid = x + w // 2
        draw.vline(fb, mid, y + 2, h - 4, palette.RULE)
        self._pane(fb, x + 4, y + 4, mid - x - 10, h - 8,
                   self.source.folders(), 0, palette.encoder_color(0),
                   own={0: palette.GREEN})
        # the right pane is the name menu: its sentence, then the names
        rx, rw = mid + 5, x + w - mid - 10
        draw.text(fb, rx, y + 4, draw.clip_text(HEADING, rw, SMALL),
                  palette.INK, SMALL)
        names = self.names()
        row_h = 20
        top = y + 22
        for i, name in enumerate(names.names()):
            ry = top + i * row_h
            if i == names.sel:
                draw.fill_rect(fb, rx - 2, ry - 3, rw, row_h - 2,
                               palette.INVERT_BG)
            fg = palette.INVERT_FG if i == names.sel \
                else palette.encoder_color(1)
            draw.text(fb, rx + 2, ry, draw.clip_text(name, rw - 6, LARGE), fg,
                      LARGE)

    def _render_panes(self, fb, rect):
        """The plain browser, the row still drawn green above the racks."""
        x, y, w, h = rect
        mid = x + w // 2
        draw.vline(fb, mid, y + 2, h - 4, palette.RULE)
        self._pane(fb, x + 4, y + 4, mid - x - 10, h - 8,
                   self.source.folders(), self.state.folder_idx,
                   palette.encoder_color(0), own={0: palette.GREEN})
        self._pane(fb, mid + 5, y + 4, x + w - mid - 10, h - 8,
                   self.source.items(self.state.folder_idx),
                   self.state.item_idx, palette.encoder_color(1))
