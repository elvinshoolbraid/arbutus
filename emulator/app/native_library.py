"""The native library: `audio/library/` + the host's box list, as a `Library`.

DDR-030 says engine menus are discovered, never hardcoded, and the emulator
implements that by making the folder tree *be* the menu. Nothing changes here:

* `synth/<box>/` and `drum/<box>/` come from `audio/library/`. The folder name
  is the box name and the author is `arbutus`, so `synth/osc/` is
  `arbutus/osc`. A box with no folder is simply not offered — the library is
  the menu. A folder whose box the host does not list stays, marked
  `NEEDS <ENGINE>` (`needs`): its patches are the player's, and a pick says
  the host's own `NEEDS ENGINE <id>` (#1122).
* `fx` and `seq` have no documents of their own to browse, so their folders
  come from the **box list the host answers `library` with**, filtered by caps
  (§3.1): `audio_in`+`audio_out` is an effect, `sequencer` is a sequencer.
  `fx/racks/` is the one folder of documents: the stock §6.7 racks, and
  `fx/racks/saved/` the player's own, as SAVED RACKS under them (D98, #820).

Everything below `apply_item` is protocol: a `.patch` is a §7.2 document, a kit
folder is a §7.3 document, a `.rack` is §6.7.
"""

import json
import os
from typing import Dict, List, Optional

from .library import CATEGORIES, Library, LibraryFolder, PATCH_SUFFIX
from .native_audio import LIBRARY as NATIVE_ROOT

RACK_SUFFIX = ".rack"
#: a sequencer pattern as a file, not a code change (DDR-037)
SEQ_SUFFIX = ".seq"
KIT_FILE = "kit.json"
#: where SAVE THIS RACK writes (D98, #820), under the stock racks' folder
SAVED_RACKS = "saved"
#: the `fx` folders whose items are whole §6.7 racks, not effect boxes
RACK_FOLDERS = ("racks", "saved-racks")


def is_rack_folder(lf) -> bool:
    """True for RACKS and SAVED RACKS: a pick there loads a whole rack."""
    return lf.category == "fx" and lf.folder in RACK_FOLDERS

#: `arbtest/*` boxes are build-time fixtures (the xrun load generator), not
#: content. They stay loadable over the protocol and out of the browsers.
HIDDEN_AUTHORS = ("arbtest",)

#: which instrument a library category drives
CATEGORY_INSTRUMENT = {"synth": "SYNTH", "drum": "DRUM"}


class NativeFolder(LibraryFolder):
    """A folder of *documents* — and, for a sampler, of samples.

    Under §7 a WAV is an **asset**, content a patch names in its `assets` map
    (DDR-037), so documents are the items. But a box that declares a `sample`
    asset is one a bare WAV is a whole sound for: that is how the library
    browsed before, and how a fresh mic capture appears on SYNTH with no save
    step (DDR-036). Those folders list their WAVs too.
    """

    @property
    def takes_sample(self) -> bool:
        desc = self.manifest.get("desc") or {}
        return "sample" in (desc.get("assets") or [])

    def rescan_items(self):
        if not os.path.isdir(self.path):
            return
        if self.manifest.get("item_kind") == "folder":
            # kits are folders, but a promoted patch is always a file: a
            # kit-based engine still has to show what was saved from it
            self.items = sorted(
                f for f in os.listdir(self.path)
                if (os.path.isdir(os.path.join(self.path, f))
                    or f.endswith(PATCH_SUFFIX)) and not f.startswith("."))
            return
        keep = [PATCH_SUFFIX, RACK_SUFFIX, SEQ_SUFFIX] \
            + ([".wav"] if self.takes_sample else [])
        # A dot-prefixed *file* is not content either: a Mac writing to the
        # exported `/data` (device-os-spec § 11) leaves `._name.patch`
        # AppleDouble files beside every document, and one listed here is a
        # row that does nothing when picked (Q3 audit, #31).
        self.items = sorted(f for f in os.listdir(self.path)
                            if not f.startswith(".")
                            and any(f.lower().endswith(k) for k in keep))


def _caps(box: dict) -> set:
    """Caps come from the box's own manifest (§3.8); `library` lists ids."""
    return set(((box.get("desc") or {}).get("caps") or []))


class NativeLibrary(Library):
    def __init__(self, native, root: str = NATIVE_ROOT):
        self.native = native
        self.root = root
        self.folders: Dict[str, List[LibraryFolder]] = {c: [] for c in CATEGORIES}
        self.boxes: Dict[str, dict] = {}
        #: the host's words for the last `instantiate` it refused, or None
        self.last_error: Optional[str] = None
        self.rescan()

    # -- discovery --------------------------------------------------------
    def rescan(self):
        self.boxes = {}
        for b in self.native.library():
            box = dict(b)
            box["desc"] = _box_manifest(b.get("dir", ""))
            self.boxes[b["id"]] = box
        self.folders = {c: [] for c in CATEGORIES}
        for cat in ("synth", "drum"):
            self.folders[cat] = self._from_tree(cat)
        self.folders["fx"] = self._racks() + self._from_boxes(
            "fx", lambda b: {"audio_in", "audio_out"} <= _caps(b)
            and "sequencer" not in _caps(b))
        self.folders["seq"] = self._from_boxes(
            "seq", lambda b: "sequencer" in _caps(b))
        self.folders["mod"] = self._racks_mod() + self._from_boxes(
            "mod", lambda b: "modulator" in _caps(b))

    def _from_tree(self, cat: str) -> List[LibraryFolder]:
        out = []
        cdir = os.path.join(self.root, cat)
        if not os.path.isdir(cdir):
            return out
        for entry in sorted(os.listdir(cdir)):
            d = os.path.join(cdir, entry)
            if not os.path.isdir(d) or entry.startswith("."):
                continue
            box_id = "arbutus/%s" % entry
            box = self.boxes.get(box_id)
            kind = "folder" if cat == "drum" else "file"
            manifest = {"name": entry.upper(), "id": box_id,
                        "item_kind": kind,
                        "desc": (box or {}).get("desc") or {}}
            if box is None:
                # not installed: shown, marked, and refused by the host
                manifest["needs"] = box_id
            lf = NativeFolder(category=cat, folder=entry, name=entry.upper(),
                              path=d, manifest=manifest)
            lf.rescan_items()
            out.append(lf)
        return out

    def _from_boxes(self, cat: str, keep) -> List[LibraryFolder]:
        out = []
        for box_id in sorted(self.boxes):
            box = self.boxes[box_id]
            if box_id.split("/")[0] in HIDDEN_AUTHORS or not keep(box):
                continue
            name = box_id.split("/")[-1]
            path = os.path.join(self.root, cat, name)
            lf = NativeFolder(category=cat, folder=name, name=name.upper(),
                              path=path,
                              manifest={"name": name.upper(), "id": box_id,
                                        "desc": box.get("desc") or {}})
            lf.rescan_items()
            if not lf.items:
                # every folder needs one selectable row; "use" is the box
                # itself, which for an effect means "put this on the tab"
                lf.items = ["use"]
            out.append(lf)
        return out

    def _racks(self) -> List[LibraryFolder]:
        path = os.path.join(self.root, "fx", "racks")
        if not os.path.isdir(path):
            return []
        lf = NativeFolder(category="fx", folder="racks", name="RACKS",
                          path=path, manifest={"name": "RACKS"})
        lf.items = sorted(f for f in os.listdir(path)
                          if f.endswith(RACK_SUFFIX) and not f.startswith("."))
        out = [lf]
        # SAVED RACKS sits under the stock racks, and only once it holds one:
        # an empty folder would be a row that loads nothing
        saved = os.path.join(path, SAVED_RACKS)
        if os.path.isdir(saved):
            sf = NativeFolder(category="fx", folder="saved-racks",
                              name="SAVED RACKS", path=saved,
                              manifest={"name": "SAVED RACKS"})
            sf.items = sorted(f for f in os.listdir(saved)
                              if f.endswith(RACK_SUFFIX)
                              and not f.startswith("."))
            if sf.items:
                out.append(sf)
        return out

    def rack_names(self) -> List[str]:
        """Every rack name the card holds, stock and saved, for the name
        menu's `taken` (a saved rack never shadows a stock one's name)."""
        root = os.path.join(self.root, "fx", "racks")
        out = []
        for d in (root, os.path.join(root, SAVED_RACKS)):
            if os.path.isdir(d):
                out += [os.path.splitext(f)[0].upper() for f in os.listdir(d)
                        if f.endswith(RACK_SUFFIX)]
        return out

    def write_rack(self, name: str, doc: dict) -> str:
        """SAVE THIS RACK's commit: `doc` (the host's §6.7 rack) as `name`
        in `fx/racks/saved/`; the rack folders are re-read so SAVED RACKS lists
        it.
        Returns the file name."""
        from . import patch as P

        folder = os.path.join(self.root, "fx", "racks", SAVED_RACKS)
        os.makedirs(folder, exist_ok=True)
        fname = P.slug(name) + RACK_SUFFIX
        doc = dict(doc)
        doc["name"] = name
        with open(os.path.join(folder, fname), "w") as f:
            json.dump(doc, f, indent=1)
            f.write("\n")
        # only the rack folders are re-read: a whole rescan would hand every
        # page a new engine folder object under the one it holds
        self.folders["fx"] = self._racks() + [
            f for f in self.folders["fx"] if not is_rack_folder(f)]
        return fname

    def _racks_mod(self) -> List[LibraryFolder]:
        path = os.path.join(self.root, "mod", "racks")
        if not os.path.isdir(path):
            return []
        lf = NativeFolder(category="mod", folder="racks", name="RACKS",
                          path=path, manifest={"name": "RACKS"})
        lf.items = sorted(f for f in os.listdir(path)
                          if (f.endswith(RACK_SUFFIX) or f.endswith(PATCH_SUFFIX))
                          and not f.startswith("."))
        return [lf]

    def box_dir(self, lf: LibraryFolder) -> str:
        """Where the host found this folder's box (`library`'s `dir`), or ""."""
        box = self.boxes.get(lf.manifest.get("id") or "")
        return str((box or {}).get("dir") or "")

    # -- instantiation ----------------------------------------------------
    def instantiate(self, lf: LibraryFolder, sample_rate: int = 48000,
                    inst_id: str = "SYNTH"):
        """Load the box into its slot and hand back the proxy (§8.2).

        `inst_id` only reaches a sequencer: there is one per instrument (S13)
        and it drives that instrument's SEQ slot for its whole life. An
        instrument engine takes its instrument from the folder's category.
        """
        from .native_engine import NativeEngine
        from .native_sequencer import NativeSequencer

        box_id = lf.manifest.get("id")
        if not box_id:
            return None
        if lf.category == "seq":
            return NativeSequencer(self.native, box_id,
                                   lf.manifest.get("desc") or {}, sample_rate,
                                   inst_id=inst_id)
        inst = CATEGORY_INSTRUMENT.get(lf.category, "SYNTH")
        reply = self.native.load_engine(inst, "engine", box_id)
        self.last_error = None
        if reply.get("type") == "error":
            self.last_error = reply.get("message") or "ENGINE NOT LOADED"
            return None
        return NativeEngine(self.native, inst, box_id, lf.name,
                            sample_rate=sample_rate)

    # -- items ------------------------------------------------------------
    def apply_item(self, engine, lf: LibraryFolder, item: str) -> bool:
        if engine is None or not item:
            return False
        inst = CATEGORY_INSTRUMENT.get(lf.category, "SYNTH")
        path = lf.item_path(item)

        if lf.category == "drum" and os.path.isdir(path):
            return self.load_kit_folder(inst, path)
        if item.lower().endswith(".wav"):
            return self.load_sample(inst, lf, path)
        if item.endswith(PATCH_SUFFIX):
            doc = read_doc(path)
            if doc is None:
                return False
            # SHIFT+1 browses *engines*: a patch taken here gives up only its
            # own module (control-inventory § 1-4). Everything else on the
            # instrument is read back out of the host and sent down again.
            return apply_engine_block(self.native, inst, doc)
        if item.endswith(RACK_SUFFIX):
            doc = read_doc(path)
            if doc is None:
                return False
            return self.native.load_rack(inst, doc).get("type") != "error"
        if item.endswith(SEQ_SUFFIX):
            # a `.seq` **is** the asset (DDR-037): the file goes down the wire
            # as a path and the box opens it, so the browser hands over the
            # library's own document rather than a copy of its contents
            doc = read_doc(path)
            if doc is None:
                return False
            if "pattern" in doc and hasattr(engine, "load_pattern"):
                return bool(engine.load_pattern(doc, os.path.abspath(path)))
            return True
        return True

    def load_sample(self, inst: str, lf: LibraryFolder, path: str) -> bool:
        """A WAV as the whole item: this box, this sample, the same knobs.

        The engine's own parameters are carried over when it is already
        loaded, so picking the next sample in the browser changes the sound
        and not the settings — what `engine.load(path)` did in Python.
        """
        box_id = lf.manifest.get("id")
        if not box_id:
            return False
        cur = self.native.slot(inst, "engine")
        params = dict(cur.get("params") or {}) if cur.get("id") == box_id else {}
        doc = {"format": 1, "kind": "patch",
               "name": os.path.splitext(os.path.basename(path))[0].upper()[:12],
               "engine": {"id": box_id, "params": params,
                          "assets": {"sample": os.path.abspath(path)}}}
        return apply_engine_block(self.native, inst, doc)

    def load_kit_folder(self, inst: str, path: str) -> bool:
        doc = read_doc(os.path.join(path, KIT_FILE))
        if doc is None:
            return False
        doc = dict(doc)
        doc["dir"] = os.path.abspath(path)      # pads are named relative to it
        return self.native.load_kit(inst, doc).get("type") != "error"

    def load_rack_item(self, inst: str, lf: LibraryFolder, item: str) -> bool:
        """SHIFT+3 › RACKS: a whole §6.7 document onto the FX slot."""
        doc = read_doc(lf.item_path(item))
        if doc is None:
            return False
        return self.native.load_rack(inst, doc).get("type") != "error"

    def load_mod_rack_item(self, inst: str, lf: LibraryFolder, item: str) -> bool:
        """SHIFT+4 › RACKS: a whole §11 MOD rack document."""
        doc = read_doc(lf.item_path(item))
        if doc is None:
            return False
        # The file is either a bare mod block or a patch carrying one
        mod_doc = doc.get("mod", doc)
        patch = patch_from_state(self.native, inst)
        patch["mod"] = mod_doc
        patch["dir"] = lf.path
        return self.native.load_patch(inst, patch).get("type") != "error"

    def load_full_patch(self, lf: LibraryFolder, item: str) -> bool:
        """SHIFT + slot key: the whole sound, exactly as the file has it."""
        inst = CATEGORY_INSTRUMENT.get(lf.category, "SYNTH")
        if lf.category == "drum" and os.path.isdir(lf.item_path(item)):
            return self.load_kit_folder(inst, lf.item_path(item))
        if item.lower().endswith(".wav"):
            return self.load_sample(inst, lf, lf.item_path(item))
        doc = read_doc(lf.item_path(item))
        if doc is None:
            return False
        return self.native.load_patch(inst, doc).get("type") != "error"


# ---------------------------------------------------------------------------
# format-1 documents (§7)
# ---------------------------------------------------------------------------

def _box_manifest(box_dir: str) -> dict:
    """The §3.8 snapshot, read once — the UI draws slots from it (§3.5)."""
    try:
        with open(os.path.join(box_dir, "engine.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_doc(path: str) -> Optional[dict]:
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("format") == 1 else None


def patch_from_state(native, inst_id: str, name: str = "",
                     octave: Optional[int] = None) -> dict:
    """The instrument, right now, as a §7.2 patch.

    This is what a slot records (DDR-029) and what SHIFT+SYNTH promotes, so
    the on-disk shape and the autosaved shape are the same document — the same
    property `app/patch.py` already relies on, one format up.
    """
    inst = native.instrument(inst_id)
    env = inst.get("env") or {}
    doc = {"format": 1, "kind": "patch",
           "name": (name or inst.get("name") or "INIT").upper()[:12],
           # the `‹ ›` octave is the UI's transposition, so the caller is the
           # one that knows it; §7.2 keeps it in the patch
           "octave": int(inst.get("octave", 0) if octave is None else octave),
           # DDR-052's three ride in `env` beside the ADSR, defaults as
           # PROTOCOL.md § env gives them: 0 s / 12 ct / 2 st, which is how
           # every patch written before they existed sounded
           "env": {"a": env.get("a", 0.012), "d": env.get("d", 0.2),
                   "s": env.get("s", 0.8), "r": env.get("r", 0.34),
                   "mode": env.get("mode", "poly"),
                   "glide": env.get("glide", 0.0),
                   "detune": env.get("detune", 12.0),
                   "bend_range": env.get("bend_range", 2.0)}}
    for key, slot in (("engine", inst.get("engine")), ("seq", inst.get("seq"))):
        if slot:
            block = {"id": slot.get("id", ""),
                     "min_version": slot.get("min_version", ""),
                     "params": dict(slot.get("params") or {})}
            if slot.get("assets"):
                block["assets"] = dict(slot["assets"])
            doc[key] = block
    rack = inst.get("fx")
    if rack:
        doc["fx"] = rack
    if inst.get("mod"):
        from copy import deepcopy
        doc["mod"] = deepcopy(inst["mod"])
    if inst.get("lfo"):
        doc["lfo"] = inst["lfo"]
    return doc


def apply_engine_block(native, inst_id: str, doc: dict) -> bool:
    """Take only `engine` out of `doc` and leave the rest of the sound alone."""
    block = doc.get("engine")
    if not isinstance(block, dict):
        return False
    current = patch_from_state(native, inst_id)
    current["engine"] = block
    current["name"] = doc.get("name", current.get("name", ""))
    if doc.get("octave") is not None:
        current["octave"] = doc["octave"]
    return native.load_patch(inst_id, current).get("type") != "error"


def write_patch(folder: LibraryFolder, name: str, doc: dict) -> str:
    """Promote a slot into the library as a format-1 file (§7.1 naming)."""
    from . import patch as P

    fname = P.slug(name) + PATCH_SUFFIX
    doc = dict(doc)
    doc["name"] = name
    with open(os.path.join(folder.path, fname), "w") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    folder.rescan_items()
    return fname
