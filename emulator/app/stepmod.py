"""arbutus/stepmod's step values as content the UI enters (#60, DDR-037).

`modulation-spec.md` §3.2 said the values were "entered like an endless
pattern: hold SHIFT, turn ENC1 for the value, › to step". Nothing in the
emulator ever wrote one, so every stepmod ran the box's default ramp. The
gesture is now **two ordinary encoder parameters** on the tab (Elvin,
2026-09-18): STEP scrolls which step is selected and latches there, VALUE sets
that step. No SHIFT-and-turn mode, no `›`, and no soft-keys — DDR-041 forbids
those on a modulator.

**STEP and VALUE are UI-side pseudo-parameters** (the decision #60 asked for).
The box never sees them: it declares five parameters and the UI adds two of
its own into the empty slots, edits the `pattern` **asset** — the file the box
opens through `load_asset`, holding the step values — and sends the file back.
Making them real box parameters would need a `set_param` path that mutates the
pattern, and DDR-037 says the pattern is content, not parameters; the box
would then hold the same numbers twice.

**The entered pattern is exactly STEPS long.** The box wraps a short list
(`values[i % len]`) and ignores a long one's tail, so a loaded file need not
match; but the moment the player turns VALUE, the UI writes out what the
screen is showing — one value per drawn cell — because a picture that is not
the file is a picture that lies. Materialising a wrapped list this way does
not change what it sounds like.

This module is the document; `pages/rack.py` is the gesture.
"""

import json
import os
from typing import List, Optional

from . import scratch

#: the box's own limit (`audio/engines/arbutus/stepmod/README.md`)
MAX_VALUES = 16

KIND = "stepmod"
#: the asset key `arbutus/stepmod` declares
ASSET = "pattern"
#: promoted into `mod/<box>/`, where no browser lists it: it is content that
#: rides with a patch, not a modulator you can put on a tab
SUFFIX = ".stepmod"


def default_values(steps: int) -> List[float]:
    """The ramp the box runs with no asset at all — `−1 + 2·i/(STEPS−1)`,
    and a single flat 0 at STEPS 1. Seeding entry with it means the first
    turn changes one step rather than everything at once."""
    n = max(1, min(MAX_VALUES, int(steps)))
    if n == 1:
        return [0.0]
    return [-1.0 + 2.0 * i / (n - 1) for i in range(n)]


def clamp(values) -> List[float]:
    """±1, at most 16, numbers only — the box refuses anything else."""
    out = []
    for v in list(values)[:MAX_VALUES]:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return []
        out.append(max(-1.0, min(1.0, float(v))))
    return out


def effective(values: List[float], steps: int) -> List[float]:
    """What the box is actually playing: the list wrapped out to STEPS."""
    src = clamp(values) or default_values(steps)
    n = max(1, min(MAX_VALUES, int(steps)))
    return [src[i % len(src)] for i in range(n)]


def doc(values: List[float], name: str = "") -> dict:
    d = {"format": 1, "kind": KIND}
    if name:
        d["name"] = name
    d["values"] = [round(float(v), 4) for v in clamp(values)]
    return d


def write_values(path: str, values: List[float], name: str = "") -> str:
    with open(path, "w") as f:
        json.dump(doc(values, name), f, indent=1)
        f.write("\n")
    return path


def read_values(path: Optional[str]) -> Optional[List[float]]:
    """The values in a `pattern` asset, or None if it is not one of ours."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("kind") != KIND:
        return None
    return clamp(d.get("values") or []) or None


#: every scratch file this module hands out starts with this
SCRATCH_STEM = "arbutus-stepmod-"


def is_scratch(path: Optional[str]) -> bool:
    """Is this asset an *entered* pattern that nothing has saved yet?

    By the filename, because that is what the host hands back — the mod
    document carries the resolved path and nothing else. Every file this
    module writes under `$TMPDIR` carries the stem, and no library item does.
    """
    return bool(path) and os.path.basename(str(path)).startswith(SCRATCH_STEM)


def scratch_file(inst_id: str, instance: str) -> str:
    """Where an *entered* pattern lives until something saves it.

    One file per MOD tab per process — keyed by the tab's `instance` (its
    name in the rack document, `stepmod`, `stepmod-2`), not by its index, so
    a tab that moves keeps its file. Removed at exit and not before (#105,
    `app/scratch.py`).
    """
    stem = SCRATCH_STEM + "%s-%s" % (str(inst_id).lower(),
                                     str(instance).lower().replace("/", "-"))
    return scratch.path(stem)
