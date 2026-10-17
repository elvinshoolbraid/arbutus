"""The library a test or a feature script runs on: the checkout's stock, copied.

Playing the mock (`python3 -m arbutus_os.mock`) or the emulator writes into the
checkout's own `audio/library/`: a `settings.json` (dark mode, MIDI channel 3),
a project under `projects/`, new patches and samples, none of them tracked.
A test that read that folder, or copied it whole, took the player's state for
its own: 12 emulator tests and the `file-system` feature went red on Elvin's
Mac clone (#851, 2026-10-12) on a commit a clean worktree passed.

So nothing under `emulator/tests/` or `tools/verify.py` reads the folder
itself. `copy_stock` copies what the checkout ships, the files git tracks
there, at their working-tree content (a stock patch a session is retuning is
what its tests see); `scratch` is one such copy per process, in a temp folder
removed at exit, for a test or feature that names no library of its own.
"""

import atexit
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # emulator/
LIBRARY = os.path.join(os.path.dirname(HERE), "audio", "library")

#: what a player writes at the library root, left out when git cannot say
#: which files are stock (a tarball, a machine with no git)
PLAYED_AT_ROOT = ("projects", "settings.json")


def tracked(src: str) -> Optional[List[str]]:
    """-> the paths under `src` git tracks (relative to it), or None when git
    cannot say (no git, not a checkout, nothing tracked there)."""
    try:
        out = subprocess.run(["git", "-C", src, "ls-files", "-z", "--", "."],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    paths = [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]
    return paths or None


def copy_stock(dest: str, src: str = LIBRARY) -> str:
    """`dest` emptied, then filled with `src`'s stock files. -> `dest`.

    Without git, everything but `PLAYED_AT_ROOT`: a player's new patch cannot
    be told from stock then, but the two files every played library has are.
    """
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    files = tracked(src)
    if files is None:
        top = os.path.abspath(src)
        shutil.copytree(src, dest, ignore=lambda d, names: (
            [n for n in names if n in PLAYED_AT_ROOT]
            if os.path.abspath(d) == top else []))
        return dest
    os.makedirs(dest)
    for rel in files:
        path = os.path.join(src, rel)
        if not os.path.isfile(path):          # deleted in the working tree
            continue
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        shutil.copy2(path, os.path.join(dest, rel))
    return dest


_scratch: Optional[str] = None


def scratch() -> str:
    """One stock copy per process, shared the way the checkout's library was
    (a test that needs a clean one still makes its own with `copy_stock`)."""
    global _scratch
    if _scratch is None or not os.path.isdir(_scratch):
        tmp = tempfile.mkdtemp(prefix="arbutus-stock-library-")
        atexit.register(shutil.rmtree, tmp, True)
        _scratch = copy_stock(os.path.join(tmp, "library"))
    return _scratch
