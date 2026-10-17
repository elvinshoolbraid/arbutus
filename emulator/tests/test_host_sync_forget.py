"""A project load forgets everything the audio process was told (#1017).

`App._native_sync` sends a value only when it differs from a fingerprint, so
`project_file.resync` has to forget every fingerprint or a value that lands
back on a remembered one is never sent. Until #1017 `resync` cleared App's six
fingerprints by name from another file, and a seventh added to `_native_sync`
would have been missed with no test failing. Now `App.forget_host_sync` owns
the forgetting; these tests hold it to every `_*_fingerprint`, including one
that does not exist yet.

The negative control (#56) is the seventh: a `forget_host_sync` that lists the
six by name passes the first test and fails the second.
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import project_file                                # noqa: E402
from app.app import App                                     # noqa: E402

SEEN = object()      # a remembered value: anything but None


def bare_app():
    """An `App` with no `__init__` run: no audio process, no pages."""
    return App.__new__(App)    # __new__ alone: the object, its __init__ skipped


def fingerprints_in_app_py():
    with open(os.path.join(HERE, "app", "app.py")) as f:
        return sorted(set(re.findall(r"self\.(_\w+_fingerprint)\b", f.read())))


class ForgetHostSync(unittest.TestCase):

    def test_every_fingerprint_app_py_keeps_is_forgotten(self):
        names = fingerprints_in_app_py()
        self.assertGreaterEqual(len(names), 6, names)    # the six of 10-13
        app = bare_app()
        for name in names + ["_playhead_seen"]:
            setattr(app, name, SEEN)
        app.forget_host_sync()
        for name in names + ["_playhead_seen"]:
            self.assertIsNone(getattr(app, name), name)

    def test_a_seventh_fingerprint_is_forgotten_too(self):
        app = bare_app()
        app._seventh_fingerprint = SEEN
        app._playhead_seen = SEEN
        app.forget_host_sync()
        self.assertIsNone(app._seventh_fingerprint)


class Resync(unittest.TestCase):

    def test_resync_forgets_then_pushes(self):
        calls = []
        app = bare_app()
        app.project = None
        # instance attributes shadow the methods `App.resync_host` calls
        app.forget_host_sync = lambda: calls.append("forget")
        app._native_sync = lambda: calls.append("sync")
        project_file.resync(app)
        self.assertEqual(calls, ["forget", "sync"])


if __name__ == "__main__":
    unittest.main()
