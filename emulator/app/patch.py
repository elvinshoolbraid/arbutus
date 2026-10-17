"""Patches — a named, permanent copy of a slot.

A slot autosaves (DDR-029) and a patch is that same snapshot written into the
library under a name, so it survives the slot being overwritten and shows up
in the browsers. Slot record and patch payload are deliberately the same
document: promoting is a copy, not a conversion.

The file is a **format-1 §7.2 patch** now, written by
`native_library.write_patch`; what is left here is the slug, the folder's
taken names and the strip copy. The names themselves come from the name
menu's plant and landmark list (`app/name_menu.py`, D98, #819).
"""

import os
import re
from typing import List, Tuple

SUFFIX = ".patch"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))


def existing_names(folder) -> List[str]:
    if folder is None:
        return []
    return [os.path.splitext(i)[0].upper() for i in (folder.items or [])]


def display_name(item: str) -> str:
    return os.path.splitext(item)[0].upper()


def saved_message(name: str, engine: str) -> Tuple[str, str]:
    """(long, short) for the dynamic strip."""
    return f"SAVED PATCH {name} → {engine}", f"SAVED {name}"
