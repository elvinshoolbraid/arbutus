"""The `arbutus-audio` the suite runs is not older than its sources (#1002).

Every native test spawns whatever `audio/build/host/arbutus-audio` is on disk. After a pull
that changed `audio/host/`, an unbuilt binary turned two tests red on unchanged code and the
run spent its time finding out why (retro 2026-10-13-02-22 #2). This test says it in one
line instead. The gate always builds first, so there it is green; it speaks when the suite is
run alone. No binary at all is a skip, as `native_or_skip` does.
"""

import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # emulator/
AUDIO = os.path.join(os.path.dirname(HERE), "audio")
EXE = os.path.join(AUDIO, "build", "host", "arbutus-audio")
# What the host binary is built from (audio/CMakeLists.txt: sdk, hostlib, host).
SOURCES = [os.path.join(AUDIO, d) for d in ("host", "hostlib", "sdk")]
SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hpp", ".txt", ".cmake")


def newer_sources(exe, roots):
    """[(path, mtime)] of the sources under `roots` modified after `exe`, newest first."""
    built = os.path.getmtime(exe)
    out = []
    for root in roots:
        for d, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith(SUFFIXES):
                    p = os.path.join(d, name)
                    t = os.path.getmtime(p)
                    if t > built:
                        out.append((p, t))
    return sorted(out, key=lambda pt: -pt[1])


def stale_message(exe, roots):
    """None when `exe` is at least as new as every source, else the one line to show."""
    newer = newer_sources(exe, roots)
    if not newer:
        return None
    return ("arbutus-audio is older than %d of its sources (newest: %s): rebuild it, "
            "cmake --build audio/build" % (len(newer), os.path.relpath(newer[0][0], AUDIO)))


class HostFresh(unittest.TestCase):
    def test_the_host_is_not_older_than_its_sources(self):
        if not os.path.exists(EXE):
            self.skipTest("no arbutus-audio at %s" % EXE)
        msg = stale_message(EXE, SOURCES)
        self.assertIsNone(msg, msg)


class Detects(unittest.TestCase):
    """`stale_message` on a scratch tree: a binary, then a source touched after it."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="host-fresh.")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "host")
        os.makedirs(self.src)
        self.exe = os.path.join(self.tmp, "arbutus-audio")
        for p in (os.path.join(self.src, "audio_host.cpp"), os.path.join(self.src, "notes.md"),
                  self.exe):
            with open(p, "w") as f:
                f.write("x")
        os.utime(self.exe, (2000, 2000))
        os.utime(os.path.join(self.src, "audio_host.cpp"), (1000, 1000))
        os.utime(os.path.join(self.src, "notes.md"), (3000, 3000))     # not a source

    def test_fresh_binary_says_nothing(self):
        self.assertIsNone(stale_message(self.exe, [self.src]))

    def test_a_source_newer_than_the_binary_is_named(self):
        os.utime(os.path.join(self.src, "audio_host.cpp"), (3000, 3000))
        msg = stale_message(self.exe, [self.src])
        self.assertIsNotNone(msg)
        self.assertIn("older than 1 of its sources", msg)
        self.assertIn("audio_host.cpp", msg)
        self.assertIn("cmake --build audio/build", msg)


if __name__ == "__main__":
    unittest.main()
