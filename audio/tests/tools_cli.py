#!/usr/bin/env python3
"""#626: the bench and client tools in audio/tools/ say what they measured.

The code review of m-audio-tools found four that did not, none of them under
any test (a code review (private)):

  row 2  `arbutus-ctl.py --follow` died with a TimeoutError traceback the first
         time the host was quiet for --timeout seconds;
  row 4  `xrun-watch.py` reported the host's lifetime xrun count (a push's
         `total` counts since the host started, and the device's host runs from
         boot), not the run's;
  row 9  `latency-probe.py` raised IndexError when no probe was answered, the
         very failure it exists to catch, before printing or writing --json;
  spec 5 `bench-all.py` kept each test's own --frames, so six rows of the
         README's 512-frame table were benched at 256 or 128.

`xrun-watch.py` and `latency-probe.py` refuse an --offline host (they measure a
live device), so their arithmetic is imported and fed what an offline host
really pushes; `arbutus-ctl.py` runs whole against one.
"""

import contextlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import Host, args, ok                                # noqa: E402

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")


def tool(name):
    """Import audio/tools/<name>.py (a hyphenated script) as a module."""
    path = os.path.join(TOOLS, name + ".py")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_follow_survives_a_quiet_host(a):
    with Host(a.exe, [a.boxes]) as h:
        p = subprocess.Popen([sys.executable, os.path.join(TOOLS, "arbutus-ctl", "arbutus-ctl.py"),
                              "--socket", h.sock, "--timeout", "0.3", "--compact",
                              "--follow", "state"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            # Several --timeout spans with nothing pushed: the old loop died at
            # the first. This sleep is the thing measured, not a wait for it.
            time.sleep(1.5)
            alive = p.poll() is None
            p.send_signal(signal.SIGINT)
            out, err = p.communicate(timeout=10)
        finally:
            if p.poll() is None:
                p.kill()
        assert alive, "--follow exited on a quiet host (exit %s):\n%s" % (p.returncode, err)
        assert "Traceback" not in err, err
        assert p.returncode == 0, (p.returncode, err)
        assert json.loads(out.splitlines()[0])["type"] == "state", out[:200]
    ok("arbutus-ctl --follow waits through five quiet --timeout spans and ends on ^C, exit 0")
    return 0


def test_xrun_watch_counts_the_run_not_the_host(a):
    watch = tool("xrun-watch")
    with Host(a.exe, [a.boxes]) as h:
        h.ok({"type": "load_engine", "slot": "engine", "id": "arbutus/example"})
        h.ok({"type": "load_rack", "rack": {
            "format": 1, "kind": "rack", "name": "SLOW",
            "tabs": [{"instance": "spin", "engine": "arbtest/spin",
                      "params": {"load": 0.05}}]}})
        h.note(60, True, 0.9)
        h.render(2048)                     # xruns before the run: the host's past
        assert h.c.wait_push("xrun"), "the spin box raised no xrun"
        base = h.c.state()["xrun"].get("total")
        assert isinstance(base, int) and base >= 1, h.c.state()["xrun"]
        h.c.clear_pushes()
        h.render(2048)                     # the run
        assert h.c.wait_push("xrun", where=lambda p: p["total"] > base), "no xrun in the run"
        h.c.drain(0.1)
        pushes = h.c.pushes()
        after = h.c.state()["xrun"]["total"]
    run, lifetime, _ = watch.run_xruns(base, pushes)
    assert lifetime == after, (lifetime, after)
    assert run == after - base and 0 < run < lifetime, (run, base, after)
    ok("xrun-watch: %d xruns in the run, the host's lifetime %d (state total %d before)" %
       (run, lifetime, base))
    return 0


def test_latency_probe_with_no_answer(a):
    probe = tool("latency-probe")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "probe.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = probe.summarise({1: 100, 2: 200}, {}, [], 128, 48000, False, path)
        with open(path) as f:
            doc = json.load(f)
    assert rc == 1, rc
    assert "0/2 probes answered" in buf.getvalue(), buf.getvalue()
    assert doc["missing"] == [1, 2] and doc["rows_ns"] == [], doc
    ok("latency-probe: no probe answered prints 0/2, writes --json, exits 1")
    return 0


def test_bench_all_benches_every_box_at_512(a):
    bench = tool("bench-all")
    build = os.path.dirname(os.path.dirname(os.path.abspath(a.exe)))
    cmds = bench.box_commands(build)
    assert "arbutus/reverb" in cmds and "arbutus/follower" in cmds, sorted(cmds)
    wrong = {box: cmd for box, cmd in cmds.items()
             if cmd.count("--frames") != 1 or cmd[cmd.index("--frames") + 1] != "512"}
    assert not wrong, wrong
    ok("bench-all: %d boxes, every one at --frames 512 (reverb's test pins 256)" % len(cmds))
    return 0


def main():
    a = args()
    for t in (test_follow_survives_a_quiet_host, test_xrun_watch_counts_the_run_not_the_host,
              test_latency_probe_with_no_answer, test_bench_all_benches_every_box_at_512):
        rc = t(a)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
