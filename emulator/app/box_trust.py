"""Which engine boxes run without asking (engine-spec § 3.9, desk 49, D125).

A box signed by Arbutus loads with no question; one with no signature asks
once, on the instrument, before it first runs: RUN ANYWAY · CANCEL (DDR-073's
prompt, never a refusal). The answer is kept for that box's exact contents, so
it asks again when they change.

Where a box lives says where its signature is:

* **not on the card** (the firmware slot's `boxes/`, a checkout's build tree):
  it came with the firmware, which was verified when it was installed. Signed.
* **on the card**, `<card>/engines/<author>/<name>/`: signed only while its
  contents are still the ones the device itself checked, by hash
  (`arbutus_os/engines.py`'s `tree_hash`, which this file repeats; a test holds
  the two to one answer):
  - `engines/.stock.json`'s `installed`: a stock box as the sync copied it
    from the slot (D125: a stock box edited on the card asks);
  - `engines/.signed.json`'s `boxes`: a `.arbox` whose `manifest.sig` the
    installer verified (`arbutus_os/fw/installer.py`).
  `engines/.answers.json`'s `run` holds the hashes the player said RUN ANYWAY
  to.

The records are the device's own word, on a card the owner can write: they
keep an honest player from being asked twice, not a determined one from
skipping the question, which DDR-073 never makes a lock.
"""

import hashlib
import json
import os
from typing import Dict, Optional, Tuple

ENGINES = "engines"
STOCK = ".stock.json"
SIGNED = ".signed.json"
ANSWERS = ".answers.json"

#: `dir` -> (fingerprint, hash): a pick re-hashes only a box that changed
_cache: Dict[str, Tuple[str, str]] = {}


def _files(root: str):
    out = []
    for d, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            full = os.path.join(d, n)
            out.append((os.path.relpath(full, root), full))
    return out


def tree_hash(root: str) -> str:
    """sha256 over a box folder's relative paths and contents
    (`arbutus_os/engines.py`'s, byte for byte)."""
    h = hashlib.sha256()
    for rel, full in _files(root):
        h.update(rel.encode() + b"\0")
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


def _hash(box_dir: str) -> str:
    """`tree_hash`, cached on the files' paths, sizes and mtimes."""
    fp = hashlib.sha256()
    for rel, full in _files(box_dir):
        st = os.stat(full)
        fp.update(("%s\0%d\0%d\0" % (rel, st.st_size, st.st_mtime_ns)).encode())
    key = fp.hexdigest()
    hit = _cache.get(box_dir)
    if hit is not None and hit[0] == key:
        return hit[1]
    value = tree_hash(box_dir)
    _cache[box_dir] = (key, value)
    return value


def card_engines(box_dir: str) -> Optional[str]:
    """The card's `engines/` folder a box sits in, or None for a box that is
    not on a card (the slot's, the build tree's)."""
    engines = os.path.dirname(os.path.dirname(os.path.abspath(box_dir)))
    return engines if os.path.basename(engines) == ENGINES else None


def _read(path: str) -> dict:
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _named(doc: dict, key: str, box_id: str) -> Optional[str]:
    table = doc.get(key)
    value = table.get(box_id) if isinstance(table, dict) else None
    return value if isinstance(value, str) else None


def needs_question(box_id: str, box_dir: str) -> bool:
    """True when this box, as it is now, has to ask before it runs."""
    engines = card_engines(box_dir) if box_dir else None
    if engines is None or not os.path.isdir(box_dir):
        return False
    try:
        now = _hash(box_dir)
    except OSError:
        return True
    for name, key in ((STOCK, "installed"), (SIGNED, "boxes"),
                      (ANSWERS, "run")):
        if _named(_read(os.path.join(engines, name)), key, box_id) == now:
            return False
    return True


def run_anyway(box_id: str, box_dir: str) -> bool:
    """Keep RUN ANYWAY for these exact contents. False when it could not be
    written (the card away, full): the box still runs, and asks next time."""
    engines = card_engines(box_dir)
    if engines is None:
        return False
    path = os.path.join(engines, ANSWERS)
    doc = _read(path)
    run = doc.get("run") if isinstance(doc.get("run"), dict) else {}
    try:
        run[box_id] = _hash(box_dir)
        doc.update(format=1, run=run)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        return False
    return True


# -- the question ------------------------------------------------------------

#: desk 49's words, cut to the overlay's LARGE lines and the strip's one; the
#: key cells hold ten characters, so RUN ANYWAY is the last line's question
ASK_LINES = ["%s: NOT CHECKED", "BY ARBUTUS", "PRE-VERIFIED ENGINES",
             "ARE IN THE APP", "RUN IT ANYWAY?"]
ASK = "%s NOT CHECKED BY ARBUTUS · RUN ANYWAY?"
ASK_CHOICES = ["RUN", "CANCEL"]


def ask(app, box_id: str, box_dir: str, run) -> bool:
    """Ask before `run()` loads an unsigned box. False: no question owed, the
    caller runs it now. True: asked; RUN ANYWAY (key 1) keeps the answer and
    calls `run`, CANCEL (key 2, or any other key: `App.on_key`) loads
    nothing and leaves what was playing."""
    return ask_all(app, [(box_id, box_dir)], run)


def ask_all(app, boxes, run) -> bool:
    """`ask` for a document naming several boxes (#1161): one question
    naming every unsigned one, one answer for them all. `boxes` is
    `(id, dir)` pairs; the signed ones are dropped here."""
    owed = []
    for box_id, box_dir in boxes:
        if (box_id, box_dir) not in owed and needs_question(box_id, box_dir):
            owed.append((box_id, box_dir))
    if not owed:
        return False
    from .file_jobs import Prompt

    names = sorted({i.split("/")[-1].upper() for i, _ in owed})
    name = names[0] if len(names) == 1 else "%d ENGINES" % len(names)

    def answered(choice: int):
        if choice == 0:
            for box_id, box_dir in owed:
                run_anyway(box_id, box_dir)
            run()

    lines = list(ASK_LINES)
    lines[0] = lines[0] % name
    if app.prompt is not None:          # another question: LATER
        other, app.prompt = app.prompt, None
        other.on_answer(1)
    app.prompt = Prompt(lines, ASK % name, list(ASK_CHOICES), answered)
    return True


# -- documents (#1161) -------------------------------------------------------
# A project, a slot record or a rack names its boxes by id, not by a browser
# pick, so the question has to read them out of the document first.

def doc_ids(doc) -> list:
    """Every box id a § 7 document names: a patch's `engine`, `seq`, and its
    `fx` and `mod` racks' tabs, or a bare rack's `tabs` and `requires`."""
    if not isinstance(doc, dict):
        return []
    ids = []
    for key in ("engine", "seq"):
        block = doc.get(key)
        if isinstance(block, dict) and isinstance(block.get("id"), str):
            ids.append(block["id"])
    for rack in (doc, doc.get("fx"), doc.get("mod")):
        if not isinstance(rack, dict):
            continue
        for tab in rack.get("tabs") or []:
            if isinstance(tab, dict) and isinstance(tab.get("engine"), str):
                ids.append(tab["engine"])
        ids += [i for i in rack.get("requires") or [] if isinstance(i, str)]
    return ids


def record_ids(app, page, record) -> list:
    """The box ids a page's slot record loads: its `native_patch`, and the
    library folder it names (`engine_folder`, the SEQ page's only way)."""
    if not isinstance(record, dict):
        return []
    ids = doc_ids(record.get("native_patch"))
    folder = record.get("engine_folder")
    category = getattr(page, "library_category", None)
    find = getattr(getattr(app, "library", None), "find", None)
    if folder and category and find is not None:
        lf = find(category, folder)
        box_id = lf.manifest.get("id") if lf is not None else None
        if box_id:
            ids.append(box_id)
    return ids


def ask_ids(app, ids, run) -> bool:
    """`ask_all` for ids, each found where the host's library has it
    (`NativeLibrary.boxes`). An id the host does not have is not asked
    about: the host refuses it with its own `NEEDS ENGINE`."""
    boxes = getattr(getattr(app, "library", None), "boxes", None) or {}
    pairs = [(i, str((boxes.get(i) or {}).get("dir") or "")) for i in ids]
    return ask_all(app, [(i, d) for i, d in pairs if d], run)
