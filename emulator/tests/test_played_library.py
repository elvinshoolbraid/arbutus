"""#851: a checkout where the mock was played still gates green.

Playing `python3 -m arbutus_os.mock` writes into the checkout's own
`audio/library/`: a `settings.json` (in dark mode, say), a project under
`projects/`, new patches and samples, none of them tracked by git. The tests
used to copy that folder whole, or read it in place, so the player's state
became the test's: 12 emulator tests red on Elfy's Mac clone (2026-10-12) on a
commit a clean worktree passed.

The tests' library is now what the checkout ships: the files git tracks under
`audio/library/` (`testing/stock_library.py`), and a test or a feature script
that names no library gets a per-process copy of that, never the folder
itself. These build a played library and check none of the player's files
reach a test.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from testing import stock_library                            # noqa: E402
from tests import native_support as NS                       # noqa: E402

STOCK = ["README.md", "synth/osc/init.patch", "drum/sampler/kick.wav"]
#: what a player leaves: the four kinds the Mac clone had (#851)
PLAYED = ["settings.json", "projects/2026-10-04/PROJ_0001/project.json",
          "synth/osc/PATCH_0001.patch", "synth/sampler/SMPL_0001.wav"]
DARK = {"format": 1, "kind": "settings", "system": {"appearance": "dark"}}


def _write(root, rel, text="{}"):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _files(root):
    return sorted(os.path.relpath(os.path.join(d, n), root)
                  for d, _, names in os.walk(root) for n in names)


class PlayedCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arbutus-played-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = os.path.join(self.tmp, "repo", "audio", "library")
        for rel in STOCK:
            _write(self.lib, rel)

    def play(self):
        """What a session on the mock leaves behind, after the stock is in."""
        for rel in PLAYED:
            _write(self.lib, rel, json.dumps(DARK))

    def copy(self):
        dest = os.path.join(self.tmp, "copy")
        with mock.patch.object(NS, "LIBRARY", self.lib):
            NS.library_copy(dest)
        return dest


class TestACopyIsWhatTheCheckoutShips(PlayedCase):
    def setUp(self):
        super().setUp()
        if shutil.which("git") is None:
            self.skipTest("no git")
        repo = os.path.dirname(os.path.dirname(self.lib))
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "add", "."], check=True)
        self.play()

    def test_the_players_files_stay_behind(self):
        self.assertEqual(_files(self.copy()), sorted(STOCK))

    def test_a_stock_file_the_player_changed_keeps_its_content(self):
        # the copy is the tracked *paths*: an edit a session is gating (a
        # stock patch it retuned) is what the tests must see
        _write(self.lib, "synth/osc/init.patch", '{"edited": true}')
        with open(os.path.join(self.copy(), "synth/osc/init.patch")) as f:
            self.assertEqual(json.load(f), {"edited": True})


class TestWithoutGitTheSettingsAndProjectsStayBehind(PlayedCase):
    """A tarball, or a machine with no git: the two things the player writes
    at known paths are left out; a new patch cannot be told from stock."""

    def test_settings_and_projects_stay_behind(self):
        self.play()
        with mock.patch.object(stock_library, "tracked", lambda src: None):
            got = _files(self.copy())
        self.assertNotIn("settings.json", got)
        self.assertFalse([p for p in got if p.startswith("projects")], got)
        for rel in STOCK:
            self.assertIn(rel, got)


class TestNoTestReadsTheCheckoutsLibrary(unittest.TestCase):
    """The defaults: a host or an App a test builds without naming a library."""

    def test_the_default_library_is_a_copy(self):
        root = NS.scratch_library()
        self.assertNotEqual(os.path.realpath(root), os.path.realpath(NS.LIBRARY))
        self.assertFalse(os.path.realpath(root).startswith(
            os.path.realpath(NS.REPO) + os.sep), root)
        self.assertTrue(os.path.isfile(os.path.join(root, "README.md")))

    def test_a_host_and_an_app_without_a_library_use_it(self):
        native = NS.host(self)
        self.assertNotIn(NS.LIBRARY, native.libraries)
        self.assertIn(NS.scratch_library(), native.libraries)
        app, audio = NS.app_for(native, NS.state_dir("played-library"))
        self.addCleanup(audio.stop)
        self.assertEqual(app.library.root, NS.scratch_library())
        self.assertNotEqual(os.path.dirname(app.settings.path), NS.LIBRARY)

    def test_a_bare_host_uses_it(self):
        native = NS.native_or_skip(self, offline=True)
        self.addCleanup(native.close)
        self.assertNotIn(NS.LIBRARY, native.libraries)

    def test_a_feature_script_uses_it(self):
        # `tools/verify.py`: a feature with no `temp_library` used to run on
        # the checkout's library; the `file-system` feature read a player's
        # MIDI channel there (#851)
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "verify_851", os.path.join(HERE, "tools", "verify.py"))
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        out = tempfile.mkdtemp(prefix="arbutus-played-verify-")
        self.addCleanup(shutil.rmtree, out, True)
        for isolate in (False, True):
            try:
                rig = verify.Rig("played-library", out_dir=out,
                                 isolate_library=isolate)
            except Exception as exc:          # no host built: skip like the rest
                if type(exc).__name__ == "NativeUnavailable":
                    self.skipTest(str(exc))
                raise
            self.addCleanup(rig.close)
            self.assertNotIn(NS.LIBRARY, rig.libraries)
            self.assertNotEqual(rig.app.library.root, NS.LIBRARY, isolate)
            self.assertNotEqual(os.path.dirname(rig.app.settings.path),
                                NS.LIBRARY, isolate)

    def test_a_chrome_feature_runs_main_on_it(self):
        # #1075: the `chrome` feature runs the real main.py, which booted on
        # the checkout's library and emulator/state/ and saved a project there
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "verify_1075", os.path.join(HERE, "tools", "verify.py"))
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        out = tempfile.mkdtemp(prefix="arbutus-played-chrome-")
        self.addCleanup(shutil.rmtree, out, True)
        calls = []

        def run(args, **kw):
            calls.append(args)
            open(args[args.index("--screenshot") + 1], "wb").close()
            return subprocess.CompletedProcess(args, 0, "", "")

        doc = {"chrome": [{"shot": "a"}, {"shot": "b", "args": ["--headless"]}]}
        with mock.patch.object(verify, "EVIDENCE_ROOT", out), \
                mock.patch.object(verify.subprocess, "run", run):
            failures, shots, _ = verify.run_chrome(doc, "played-chrome",
                                                   verbose=False)
        self.assertEqual(failures, [])
        self.assertEqual(len(calls), 2)
        default_state = os.path.join(HERE, "state")
        for args in calls:
            self.assertIn("--library", args)
            self.assertNotEqual(args[args.index("--library") + 1], NS.LIBRARY)
            self.assertIn("--state-dir", args)
            self.assertNotEqual(args[args.index("--state-dir") + 1],
                                default_state)

    def test_main_hands_its_library_to_the_host(self):
        # #1075: `main.py --library` gave the App that root, the host the
        # checkout's (its browsers)
        import contextlib
        import io
        import main as emulator_main
        from app import native_audio

        seen = []

        def native(**kw):
            seen.append(kw)
            raise native_audio.NativeUnavailable("not needed")

        lib = NS.scratch_library()
        with mock.patch.object(native_audio, "NativeAudio", native), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = emulator_main.main(["--headless", "--no-audio", "--no-midi",
                                     "--library", lib])
        self.assertEqual(rc, 1)
        self.assertIn(lib, seen[0].get("libraries") or [])


if __name__ == "__main__":
    unittest.main()
