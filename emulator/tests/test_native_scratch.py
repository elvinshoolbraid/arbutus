"""`NativeAudio`'s own scratch tape folder goes with it (#657).

A spawned host with no `--tape-dir` gets a private `arbutus-tape-*` folder.
`close()` removed it, but a host that never answered (a socket path too long
for AF_UNIX, a binary that dies at start) left it behind on every try: the
seven `test_palette_modes` App tests did that on each run where they skipped.
"""

import glob
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app import native_audio as NA                         # noqa: E402


class TheScratchTapeFolder(unittest.TestCase):
    def setUp(self):
        # `tempfile.tempdir` is where mkdtemp puts things; point it at a
        # folder of our own so the count is this test's alone.
        self.tmp = tempfile.mkdtemp(prefix="arbutus-scratch-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch = mock.patch.object(tempfile, "tempdir", self.tmp)
        patch.start()
        self.addCleanup(patch.stop)

    def left(self):
        return glob.glob(os.path.join(self.tmp, "arbutus-tape-*"))

    def test_a_host_that_never_answers_leaves_no_folder(self):
        # `true` is executable and exits at once, so nothing ever listens.
        with mock.patch("arbutus_ctl.time.sleep"):     # 100 retries, no waiting
            with self.assertRaises(NA.NativeUnavailable):
                NA.NativeAudio(offline=True, exe=shutil.which("true"),
                               socket_path=os.path.join(self.tmp, "s"))
        self.assertEqual(self.left(), [])

    def test_a_host_that_cannot_start_leaves_no_folder(self):
        # An executable text file passes the `os.access` check but `Popen`
        # raises `OSError` (ENOEXEC) before any socket exists (#1180).
        bad = os.path.join(self.tmp, "not-a-binary")
        with open(bad, "w") as f:
            f.write("not a program\n")
        os.chmod(bad, 0o755)
        with self.assertRaises(NA.NativeUnavailable):
            NA.NativeAudio(offline=True, exe=bad,
                           socket_path=os.path.join(self.tmp, "s"))
        self.assertEqual(self.left(), [])

    @unittest.skipUnless(os.access(NA.EXE, os.X_OK), "no arbutus-audio built")
    def test_a_closed_host_leaves_no_folder(self):
        n = NA.NativeAudio(offline=True, tape_seconds=1,
                           socket_path=os.path.join(self.tmp, "s"))
        self.assertEqual(len(self.left()), 1)          # it did make one
        n.close()
        self.assertEqual(self.left(), [])


if __name__ == "__main__":
    unittest.main()
