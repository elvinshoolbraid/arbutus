#!/usr/bin/env python3
"""#597: a full control-to-audio ring refuses the message; it never says ok.

The m-audio-host-core review (finding 2): the ring the control thread hands
commands to the audio thread over is 1,024 deep, and `push()` only printed when
it was full. In --offline nothing drains it until a `render`, so 1,050 tempo
edits and then a metronome edit dropped 28 commands, the metronome's among
them -- and still replied ok and published level 1.0 while the render stayed
silent. The state document said one thing and the speaker another.

Pinned here: the host refuses (`busy`) before the mirror changes, so what
the state document says is what the audio does; a refused edit changes
nothing; a `render` (which drains the ring) is never refused; and once the
ring has drained the same edit lands and sounds. The host never logs a
dropped command.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hostcase import Host, args, ok, peak, read_wav                # noqa: E402


def main():
    a = args()
    with Host(a.exe, [a.boxes], block=128) as h:
        assert h.c.state()["metronome"]["level"] == 0.0

        # Fill the ring with nothing draining it. Every edit either lands
        # (ok, and the state follows it) or is refused (and nothing moves).
        last_ok, refused = None, 0
        for i in range(1050):
            bpm = 60.0 + (i % 100)
            r = h.req({"type": "tempo", "bpm": bpm})
            if r["type"] == "ok":
                assert refused == 0, "an edit landed after one was refused: %r" % r
                last_ok = bpm
            else:
                assert r["type"] == "error" and r["code"] == "busy", r
                refused += 1
        assert refused > 0, "1,050 edits with no render: none refused"
        assert last_ok is not None
        st = h.c.state()
        assert abs(st["tempo"] - last_ok) < 1e-6, (st["tempo"], last_ok)
        ok("a full ring: %d edits landed, %d refused busy, the state is the last that landed"
           % (1050 - refused, refused))

        # The review's case: the metronome edit is refused, and the state
        # does not claim it.
        r = h.req({"type": "metronome", "level": 1.0, "pitch": 1000.0})
        assert r["type"] == "error" and r["code"] == "busy", r
        assert h.c.state()["metronome"]["level"] == 0.0, h.c.state()["metronome"]
        ok("a refused metronome edit leaves the state at level 0")

        # `render` drains the ring and is never refused; the same edit then
        # lands, and the state and the audio agree.
        path = h.path("click.wav")
        h.render(512)
        h.ok({"type": "metronome", "level": 1.0, "pitch": 1000.0})
        assert h.c.state()["metronome"]["level"] == 1.0
        h.ok({"type": "transport", "op": "locate", "position": 0})
        h.ok({"type": "transport", "op": "start"})
        h.render(4800, path)
        pk = peak(read_wav(path)[1])
        assert pk > 0.1, pk
        ok("after a render the edit lands and the click sounds (peak %.2f)" % pk)

    with open(os.path.join(h.dir, "host.log")) as f:
        log = f.read()
    assert "ring full" not in log, log
    ok("no command was dropped")


if __name__ == "__main__":
    main()
