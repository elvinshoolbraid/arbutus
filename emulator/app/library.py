"""The library as folders (DDR-030 / emulator-spec §6).

    audio/library/{synth,drum,fx,seq}/<box>/<items>

Nothing about engines is hardcoded in UI code: the folder tree *is* the menu.
What is left here is the shape of a folder and the lookup the pages do;
`native_library.py` fills it from the audio process's box list and the
documents on disk (§7), which is where the engines live now.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CATEGORIES = ("synth", "drum", "fx", "mod", "seq")

#: promoted-patch extension; mirrors app/patch.py (imported the other way)
PATCH_SUFFIX = ".patch"


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

@dataclass
class LibraryFolder:
    category: str
    folder: str                 # directory name, e.g. "osc"
    name: str                   # display name from the manifest
    path: str
    manifest: dict = field(default_factory=dict)
    items: List[str] = field(default_factory=list)

    def item_path(self, item: str) -> str:
        return os.path.join(self.path, item)

    def rescan_items(self):
        """(Re-)list this folder's items. Overridden by `NativeFolder`."""
        if not os.path.isdir(self.path):
            return
        self.items = sorted(f for f in os.listdir(self.path)
                            if f.endswith(PATCH_SUFFIX))


class Library:
    """The folder tree, by category. `native_library.NativeLibrary` is the one
    that knows how to scan and how to instantiate; this is the surface the
    pages and menus speak."""

    folders: Dict[str, List["LibraryFolder"]] = {}

    def list(self, category: str) -> List[LibraryFolder]:
        return self.folders.get(category, [])

    def find(self, category: str, folder: str) -> Optional[LibraryFolder]:
        for f in self.list(category):
            if f.folder == folder:
                return f
        return None
