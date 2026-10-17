#!/usr/bin/env python3
"""#646: a MONO or LEGATO note change must not click.

Every PolyHost synth box plays one note, then a second one pressed over it, in
MONO and in LEGATO. The largest sample-to-sample step in the few milliseconds
after the change must stay within the largest step either note makes on its
own just before and just after. Before #646 each box's start() reset its oscillator
phase at the change, and the step there was 1.6-36x the steady one.

#828 adds three things the equal-velocity renders could not see:
- MONO at a new velocity, the second note held over the first, and a note at a
  new velocity landing in the first one's release (MONO and LEGATO both take
  the new velocity there: from a released note the change is a retrigger).
  Before #828 retarget() set the velocity at once and the gain stepped:
  osc 4.3x, fm 1.6x, wavetable 20x the steady step.
- the pitch after the change, and after letting go of the top note so the
  voice returns to the one still held: the click renders pass just as well
  when retarget() stops changing the note.

Standard library only (hostcase.read_wav); arbutus-run renders, no host.
"""

import argparse
import os
import subprocess
import sys
import tempfile

from hostcase import read_wav

SR = 48000
CHANGE = 24100                 # the second note lands here, off any whole cycle of A3
LEN = 48000
WINDOW = 480                   # 10 ms after the change is where a click would be
# "Steady" is local: the 50 ms before the change and the 50 ms after the window.
# A whole note will not do: wavetable's DRIFT moves POS, so its step size
# wanders over seconds, and va's two saws line up now and then into a step
# ~1.8x its usual one, which a max over the whole note would let hide a click.
NEAR = 2400
# Sustain 1.0, so MONO's re-attack (from the level it is at, up to 1.0) is
# flat and the only thing that can move at the change is the voice itself.
# [Not a sound call: a margin for the ladder and filter moving to the new
# note's cutoff, which is the box behaving, not a reset. The clicks this
# catches were 1.6-36x over.]
MARGIN = 1.10

# (name, modes, notes). Equal velocities, the #646 case; then #828's.
CASES = [
    ("same-vel", ("legato", "mono"), """\
0      on  57 0.80
%d  on  64 0.80
%d  off 57
46000  off 64
""" % (CHANGE, CHANGE)),
    # Held over, louder: MONO re-attacks at the new velocity.
    ("vel-up", ("mono",), """\
0      on  57 0.20
%d  on  64 0.80
%d  off 57
46000  off 64
""" % (CHANGE, CHANGE)),
    # Into the release: the first note is let go 20 ms before the second, so the
    # voice is still sounding (release 0.2 s) and the second note retriggers it.
    ("in-release", ("legato", "mono"), """\
0      on  57 0.20
%d  off 57
%d  on  64 0.80
46000  off 64
""" % (CHANGE - 960, CHANGE)),
]

# The pitch check: 57 held, 61 pressed over it at CHANGE and let go at BACK, so
# the voice returns to 57. Each window must sit at its own note's period. A
# third, not the fifth above: 64 is 3:2 over 57, so three of its cycles are two
# of 57's and the check could not tell them apart.
BACK = 36000
RETURN = """\
0      on  57 0.80
%d  on  61 0.80
%d  off 61
46000  off 57
""" % (CHANGE, BACK)
PITCH_LEN = 2048


def hz(note):
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


def mismatch(x, a, n, period):
    """How unlike itself one period later x[a:a+n] is: 0 for a perfect cycle.
    The lag is fractional, read by linear interpolation."""
    k = int(period)
    fr = period - k
    num = den = 0.0
    for i in range(a, a + n):
        y = x[i + k] + fr * (x[i + k + 1] - x[i + k])
        num += (x[i] - y) ** 2
        den += x[i] * x[i]
    return num / den if den else 1.0


def pitch_ok(x, a, note, other):
    """True when x at a repeats after two of note's periods and not after two of
    other's. Two, because va's SUB sounds an octave down: its cycle is two of
    the note's."""
    want = mismatch(x, a, PITCH_LEN, 2 * SR / hz(note))
    wrong = mismatch(x, a, PITCH_LEN, 2 * SR / hz(other))
    return want < 0.25 * wrong, want, wrong


def largest_step(x, a, b):
    return max(abs(x[i] - x[i - 1]) for i in range(max(a, 1), b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="arbutus-run")
    ap.add_argument("--boxes", required=True, help="the box library root")
    ap.add_argument("--audio-root", required=True)
    o = ap.parse_args()

    table = os.path.join(o.audio_root, "library", "synth", "wavetable", "harmonics.wav")
    boxes = [("osc", []), ("fm", []), ("va", []),
             ("wavetable", ["--asset", "wavetable=" + table])]
    fails = 0
    with tempfile.TemporaryDirectory() as tmp:
        def render(box, extra, mode, name, text):
            notes = os.path.join(tmp, name + ".txt")
            with open(notes, "w") as f:
                f.write(text)
            out = os.path.join(tmp, "%s-%s-%s.wav" % (box, mode, name))
            subprocess.run([o.run, os.path.join(o.boxes, "arbutus", box),
                            "--notes", notes, "--len", str(LEN), "--sr", str(SR),
                            "--frames", "64", "--env", "0.005", "0.05", "1.0", "0.2",
                            "--env-mode", mode, "--wav-bits", "32", "--out", out] + extra,
                           check=True, stdout=subprocess.DEVNULL)
            return read_wav(out)[1][0]

        for box, extra in boxes:
            for name, modes, text in CASES:
                for mode in modes:
                    x = render(box, extra, mode, name, text)
                    steady = max(largest_step(x, CHANGE - NEAR, CHANGE),
                                 largest_step(x, CHANGE + WINDOW, CHANGE + WINDOW + NEAR))
                    at = largest_step(x, CHANGE, CHANGE + WINDOW)
                    good = at <= steady * MARGIN
                    print("%s %-9s %-6s %-10s step at the change %.4f, steady %.4f (x%.2f)" % (
                        "ok -" if good else "FAIL", box, mode, name, at, steady, at / steady))
                    fails += not good
            for mode in ("legato", "mono"):
                x = render(box, extra, mode, "return", RETURN)
                # 50 ms after each change: past the 5 ms attack, inside the note.
                for where, at, note, other in (("after the change", CHANGE, 61, 57),
                                               ("back on the held note", BACK, 57, 61)):
                    good, want, wrong = pitch_ok(x, at + NEAR, note, other)
                    print("%s %-9s %-6s pitch %s: note %d (mismatch %.3f, at note %d %.3f)" % (
                        "ok -" if good else "FAIL", box, mode, where, note, want, other, wrong))
                    fails += not good
    if fails:
        print("%d check(s) failed: a click at the note change, or the wrong pitch" % fails)
        sys.exit(1)


if __name__ == "__main__":
    main()
