"""Scratch content files — #105's rule, written down once.

Content the player *entered* rather than loaded (a SEQ pattern since stage H2,
arbutus/stepmod's step values since #60) has to be a **path** before the host
can open it, because an asset crosses as a file and not as protocol fields
(DDR-037). It is not a library item yet, so it rides on a file under
`$TMPDIR`.

Two rules, and they are the reason this is a module rather than a habit:

* **The file outlives every object that names it.** The host re-reads the
  asset whenever it rebuilds that box, so removing the file when the Python
  object goes would pull it out from under the next rebuild. It goes at
  process exit and not before.
* **It does not outlive the process.** On the device `/tmp` is a tmpfs, so a
  file left behind is RAM that never comes back (#105). Every path carries
  the pid, so one instance can never reach another's file, and
  `ARBUTUS_KEEP_TMP=1` keeps the lot for a post-mortem.

`atexit` is Python's "run this when the interpreter shuts down" hook; the
registered function runs once, after `main()` returns.
"""

import atexit
import os
import tempfile

#: every scratch path this process has handed out
_files = set()


def path(stem: str, suffix: str = ".json") -> str:
    """`$TMPDIR/<stem>-<pid><suffix>`, registered for removal at exit."""
    p = os.path.join(tempfile.gettempdir(), "%s-%d%s" % (stem, os.getpid(), suffix))
    _files.add(p)
    return p


@atexit.register
def forget_all():
    if os.environ.get("ARBUTUS_KEEP_TMP"):
        return
    for p in _files:
        try:
            os.remove(p)
        except OSError:          # never written, or already gone
            pass
    _files.clear()
