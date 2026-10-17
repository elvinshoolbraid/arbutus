#!/usr/bin/env python3
"""#630: what the FX boxes' goldens cannot see — a knob that moves, a centred source.

The code review of the effect boxes (`docs/logs/findings/2026-10-06-m-engines-fx.md`)
found four bugs that every golden missed, because each golden renders one
static setting from a mono input. This renders each case with arbutus-run
(`--at` moves a knob mid-render) and measures it:

  * delay  PING does something on a centred (L = R) source (rows 1, 2);
  * delay  DRIVE never makes the repeats run away: the tail dies at every
           FDBK below the top (row 3);
  * delay  FDBK's top is a hold: 60 s of repeats keep their level, never
           grow, at any DRIVE, PING and TONE, a loud loop too, and leaving
           it does not click (#779, #1027);
  * reverb a SIZE change keeps the tail; it used to clear the network (row 4);
  * comb, satcomb  TUNE lands on its note at every DAMP (row 5);
  * drive  MIX 0 is the input, 15 frames late, bit for bit (row 6; the
           other four bypass claims are plain `--golden` tests in each
           box's CMakeLists.txt).

Standard library only (hostcase.read_wav_arrays); arbutus-run renders, no
host. Every signal is an array('f') (4 bytes a sample, the WAV's own float32),
never a list of Python floats (~32 bytes a sample): the 62 s renders as lists
held 570 MB, which reset a 512 MB board under ctest (#1125).
"""

import argparse
from array import array
import math
import os
import struct
import subprocess
import sys
import tempfile

from hostcase import read_wav_arrays

SR = 48000


def write_wav(path, left, right):
    # Interleave by extended-slice assignment: buf[0::2] is every even sample.
    buf = array("f", bytes(8 * len(left)))
    buf[0::2] = left if isinstance(left, array) else array("f", left)
    buf[1::2] = right if isinstance(right, array) else array("f", right)
    if sys.byteorder != "little":
        buf.byteswap()
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + 4 * len(buf)) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, SR, SR * 8, 8, 32))
        fh.write(b"data" + struct.pack("<I", 4 * len(buf)))
        fh.write(buf)


def burst(n, seconds=0.1, hz=440.0, amp=0.5):
    """A centred 440 Hz burst: what a synth note looks like to an FX tab."""
    m = int(seconds * SR)
    x = array("f", (amp * math.sin(2 * math.pi * hz * i / SR) for i in range(min(m, n))))
    x.extend(silence(n - len(x)))
    return x, array("f", x)


def silence(n):
    return array("f", bytes(4 * n))


def rms(x, a, b):
    return math.sqrt(sum(v * v for v in x[a:b]) / max(1, b - a))


def db(x):
    return 20 * math.log10(x) if x > 0 else -999.0


def goertzel(x, hz):
    w = 2 * math.pi * hz / SR
    c = 2 * math.cos(w)
    s1 = s2 = 0.0
    for v in x:
        s1, s2 = v + c * s1 - s2, s1
    return s1 * s1 + s2 * s2 - c * s1 * s2


def peak_cents(x, hz, lo=-150.0, hi=60.0):
    """The ring's spectral peak near `hz`, in cents from it (a golden-section search)."""
    g = (math.sqrt(5) - 1) / 2
    f = lambda ct: goertzel(x, hz * 2 ** (ct / 1200.0))
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    while b - a > 0.05:
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
    return (a + b) / 2


class Run:
    def __init__(self, exe, boxes, tmp):
        self.exe, self.boxes, self.tmp, self.n = exe, boxes, tmp, 0

    def __call__(self, box, inp, *opts):
        self.n += 1
        out = os.path.join(self.tmp, "out%d.wav" % self.n)
        cmd = [self.exe, os.path.join(self.boxes, "arbutus", box), "--in", inp,
               "--sr", str(SR), "--frames", "128", "--out", out] + list(opts)
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        lr = read_wav_arrays(out)[1]
        os.remove(out)                  # the 62 s renders are 24 MB each
        return lr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="arbutus-run")
    ap.add_argument("--boxes", required=True, help="the box library root")
    ap.add_argument("--audio-root", required=True)
    o = ap.parse_args()
    fails = 0

    def check(good, msg):
        nonlocal fails
        print(("ok    " if good else "FAIL  ") + msg)
        if not good:
            fails += 1

    with tempfile.TemporaryDirectory() as tmp:
        run = Run(o.run, o.boxes, tmp)

        # ---- delay PING on a centred source (rows 1, 2) -------------------
        n = 3 * SR
        src = os.path.join(tmp, "burst3.wav")
        write_wav(src, *burst(n))
        base = ["--set", "time=-0.6", "--set", "feedback=0.6", "--set", "mix=1"]
        for ping in (0.0, 1.0):
            l, r = run("delay", src, *base, "--set", "ping=%g" % ping)
            side = max(abs(a - b) for a, b in zip(l, r))
            pk = max(max(abs(v) for v in l), max(abs(v) for v in r))
            if ping == 0.0:
                check(side == 0.0, "delay PING 0, centred: L = R (L-R peak %.3g)" % side)
            else:
                # Before #630 this was 0: two identical lines swapped.
                check(side > 0.5 * pk, "delay PING 1, centred: the repeats bounce "
                      "(L-R peak %.3f of %.3f)" % (side, pk))

        # ---- delay DRIVE: the tail dies (row 3) ---------------------------
        # The review's settings, each of which repeated for ever before:
        # (FDBK, DRIVE, TONE). Wet level over the last half second of 8 s.
        # [Not a sound call: -60 dB is "gone"; the runaway cases sat at
        # -13 to -26 dB, a decaying one is far below -100.]
        n = 8 * SR
        src = os.path.join(tmp, "burst8.wav")
        write_wav(src, *burst(n))
        for fb, dr, tone in ((0.35, 0.6, 0.5), (0.35, 1.0, 0.5), (0.2, 1.0, 0.5),
                             (0.6, 0.35, 0.28)):
            l, _ = run("delay", src, "--set", "time=-0.6", "--set", "mix=1",
                       "--set", "feedback=%g" % fb, "--set", "drive=%g" % dr,
                       "--set", "tone=%g" % tone)
            first = max(abs(v) for v in l[: SR])
            tail = rms(l, n - SR // 2, n)
            lim = -60.0
            check(db(tail / first) < lim if first > 0 else False,
                  "delay FDBK %.2f DRIVE %.2f TONE %.2f: tail %.1f dB of the first "
                  "repeat (< %g)" % (fb, dr, tone, db(tail / first) if first else 0, lim))
            # DRIVE still does something: grit, not gain.
            if (fb, dr) == (0.35, 1.0):
                clean, _ = run("delay", src, "--set", "time=-0.6", "--set", "mix=1",
                               "--set", "feedback=0.35", "--set", "drive=0")
                diff = max(abs(a - b) for a, b in zip(l[: 2 * SR], clean[: 2 * SR]))
                check(diff > 0.01, "delay DRIVE 1 still changes the repeats (peak diff %.3f)"
                      % diff)

        # ---- delay HOLD: FDBK's top keeps the repeats for ever (#779) -----
        # Elfy, on D92: "infinite at same volume is a feature". 60 s of
        # repeats of one burst at 1/8, 120 BPM (240 passes), WOW at its
        # default, at DRIVE 0 and 1, PING 0 and 1, TONE on both sides of
        # centre (the hold takes the lowpass and the highpass out of the
        # loop; #1027 row 1). The level, both sides together, over every
        # whole second after the first, against the first repeat (a second
        # holds four): within 0.1 dB, and never above it. [Not a sound call:
        # the ticket asked for 1 dB; 0.1 also catches the linear read's
        # lowpass coming back (-0.56 dB a minute at 440 Hz); the old 0.95
        # ended 50 dB down.]
        def energy(l, r, a, b):
            return sum(v * v for v in l[a:b]) + sum(v * v for v in r[a:b])

        def pass1(l, r):
            # The first repeat: the burst's 4800 frames from ~12002.
            return energy(l, r, SR // 8, 3 * SR // 8)

        n = 62 * SR
        src = os.path.join(tmp, "burst62.wav")
        write_wav(src, *burst(n))
        for dr, ping, tone in ((0.0, 0.0, 0.2), (1.0, 0.0, 0.8), (0.0, 1.0, 0.8),
                               (1.0, 1.0, 0.2)):
            l, r = run("delay", src, "--set", "feedback=0.95", "--set", "mix=1",
                       "--set", "tone=%g" % tone, "--set", "drive=%g" % dr,
                       "--set", "ping=%g" % ping)
            ref = 4 * pass1(l, r)
            lv = [10 * math.log10(energy(l, r, k * SR, (k + 1) * SR) / ref)
                  for k in range(1, 61)]
            lo, hi = min(lv), max(lv)
            check(lo >= -0.1 and hi <= 0.01,
                  "delay HOLD DRIVE %g PING %g TONE %g: 60 s of repeats within %.2f .. "
                  "%+.2f dB of the first repeat (>= -0.1, never above)"
                  % (dr, ping, tone, lo, hi))
            # Frozen as it is: with WOW stopped and a whole-sample read, a
            # repeat is the one before it, sample for sample (PING 0; 1/8 at
            # 120 BPM is 12000 frames, plus where WOW stopped).
            if ping == 0.0:
                a0 = 30 * SR
                lag = [d for d in range(11990, 12011)
                       if all(l[a0 + i + d] == l[a0 + i] for i in range(SR))]
                check(len(lag) == 1, "delay HOLD DRIVE %g: each repeat is the one before, "
                      "sample for sample (lag %s)" % (dr, lag))
        # Below the top, unchanged: one detent down still dies away.
        l, r = run("delay", src, "--set", "feedback=0.94", "--set", "mix=1",
                   "--len", str(40 * SR))
        first = max(abs(v) for v in l[: SR])
        tail = rms(l, 39 * SR, 40 * SR)
        check(db(tail / first) < -20.0, "delay FDBK 0.94 is not a hold: %.1f dB of the "
              "first repeat after 39 s (< -20)" % db(tail / first))
        # Notes played into the hold pile up to a ceiling, not for ever.
        loud = os.path.join(tmp, "loud.wav")
        ll = array("f", (0.9 * math.sin(2 * math.pi * 220 * i / SR) for i in range(10 * SR)))
        write_wav(loud, ll, ll)
        l, r = run("delay", loud, "--set", "feedback=0.95", "--set", "mix=1")
        pk = max(max(abs(v) for v in l), max(abs(v) for v in r))
        check(pk <= 2.0, "delay HOLD under 10 s of a 0.9 note: peak %.2f (<= 2)" % pk)

        # Between PING's ends the hold keeps the level from the first repeat
        # on, a centred note and a one-sided one (#1027 row 3: the matrix
        # [[1-p, p], [p, 1-p]] gives the side-to-side part 1 - 2p a pass, so
        # a left-only note at PING 0.5 lost 3 dB, a centred one 0.46). Each
        # repeat over 10 s against the first, same bounds as above.
        n = 10 * SR
        left = burst(n)[0]
        for name, chans, ping in (("centred", (left, left), 0.25),
                                  ("centred", (left, left), 0.5),
                                  ("centred", (left, left), 0.75),
                                  ("left-only", (left, silence(n)), 0.5)):
            src = os.path.join(tmp, "ping_%s.wav" % name)
            write_wav(src, *chans)
            l, r = run("delay", src, "--set", "feedback=0.95", "--set", "mix=1",
                       "--set", "ping=%g" % ping)
            ref = pass1(l, r)
            lv = [10 * math.log10(energy(l, r, k * 12000 - 6000, k * 12000 + 6000) / ref)
                  for k in range(2, 39)]
            lo, hi = min(lv), max(lv)
            check(lo >= -0.1 and hi <= 0.01,
                  "delay HOLD PING %g, %s: repeats 2..38 within %.2f .. %+.2f dB of the "
                  "first (>= -0.1, never above)" % (ping, name, lo, hi))

        # A loud loop holds too (#1027 row 4): a 0.6 note for 2 s piles up
        # past the ceiling's knee (peak ~1.9); once it stops, every second
        # keeps the level of the first after it. The knee used to run again
        # every pass and took 1.5 dB off in 20 s.
        n = 22 * SR
        ll = array("f", (0.6 * math.sin(2 * math.pi * 220 * i / SR) for i in range(2 * SR)))
        ll.extend(silence(n - 2 * SR))
        src = os.path.join(tmp, "loud2.wav")
        write_wav(src, ll, ll)
        l, r = run("delay", src, "--set", "feedback=0.95", "--set", "mix=1")
        pk = max(max(abs(v) for v in l), max(abs(v) for v in r))
        e3 = energy(l, r, 3 * SR, 4 * SR)
        lv = [10 * math.log10(energy(l, r, k * SR, (k + 1) * SR) / e3) for k in range(4, 22)]
        lo, hi = min(lv), max(lv)
        check(pk > 1.0 and lo >= -0.1 and hi <= 0.01,
              "delay HOLD past the knee (peak %.2f > 1): seconds 4..21 within %.2f .. %+.2f dB "
              "of second 3 (>= -0.1, never above)" % (pk, lo, hi))

        # Leaving the hold does not click (#1027 row 5): FDBK 0.9 at a dark
        # TONE, the hold from 0.6 s, one detent down at 5.0 s. The largest
        # sample-to-sample step in the half second after the move is no
        # larger than anywhere in the same render left held. The loop's
        # lowpass used to resume from where the hold began: 0.192 against
        # 0.057. [Not a sound call: 10 % over the held reference.]
        n = 6 * SR
        src = os.path.join(tmp, "burst6.wav")
        write_wav(src, *burst(n))
        base = ["--set", "feedback=0.9", "--set", "mix=1", "--set", "tone=0.2",
                "--at", "%d:feedback=0.95" % int(0.6 * SR)]
        held, _ = run("delay", src, *base)
        moved, _ = run("delay", src, *base, "--at", "%d:feedback=0.94" % (5 * SR))

        def steepest(x, a, b):
            return max(abs(x[i + 1] - x[i]) for i in range(a, b - 1))

        ref = steepest(held, SR, n)
        got = steepest(moved, 5 * SR, int(5.5 * SR))
        check(got <= 1.1 * ref, "delay leaving the HOLD at TONE 0.2: largest step %.3f "
              "after the move, %.3f held (<= 1.1x)" % (got, ref))

        # ---- reverb SIZE moves without clearing (row 4) -------------------
        n = 3 * SR
        src = os.path.join(tmp, "burst_rv.wav")
        write_wav(src, *burst(n, seconds=0.5))
        win = (int(0.75 * SR), int(1.1 * SR))     # just after the move
        still, _ = run("reverb", src, "--set", "size=0.5", "--set", "mix=1")
        ref = rms(still, *win)
        moved, _ = run("reverb", src, "--set", "size=0.5", "--set", "mix=1",
                       "--at", "%d:size=0.51" % int(0.7 * SR))
        got = rms(moved, *win)
        check(db(got / ref) > -6.0, "reverb one SIZE detent mid-tail keeps it: "
              "%.1f dB against no move (> -6)" % db(got / ref))
        # A route on SIZE: a 0.45..0.55 triangle, a step every 128-frame block.
        sweep = []
        for k, f in enumerate(range(int(0.6 * SR), n, 128)):
            ph = (k % 64) / 64.0
            sweep += ["--at", "%d:size=%.4f" % (f, 0.45 + 0.1 * (1 - abs(2 * ph - 1)))]
        routed, _ = run("reverb", src, "--set", "size=0.5", "--set", "mix=1", *sweep)
        got = rms(routed, *win)
        check(db(got / ref) > -6.0, "reverb SIZE under a route keeps the tail: "
              "%.1f dB against no move (> -6)" % db(got / ref))

        # ---- comb, satcomb: TUNE lands on its note at any DAMP (row 5) ----
        # [Not a sound call: 3 cents is under what an ear tells apart on a
        # held note; the bug was -6 to -88.]
        n = 16384
        imp = os.path.join(tmp, "imp.wav")
        x = [0.0] * n
        x[0] = 0.05
        write_wav(imp, x, list(x))
        for box in ("comb", "satcomb"):
            for tune in (0, 12, 24):
                for damp in (0.0, 0.3, 0.9):
                    extra = ["--set", "drive=1"] if box == "satcomb" else []
                    l, _ = run(box, imp, "--len", str(n), "--set", "tune=%d" % tune,
                               "--set", "feedback=0.9", "--set", "damp=%g" % damp,
                               "--set", "mix=1", *extra)
                    ct = peak_cents(l, 110.0 * 2 ** (tune / 12.0))
                    check(abs(ct) <= 3.0, "%s TUNE %+d DAMP %.1f: ring %+.1f ct (|.| <= 3)"
                          % (box, tune, damp, ct))

        # ---- drive MIX 0 is the input, 15 frames late (row 6) -------------
        fx = os.path.join(o.audio_root, "tests", "golden", "fx_input.npy")
        # Every other knob at its far end, as the README's claim says.
        l, r = run("drive", fx, "--set", "mix=0", "--set", "drive=1", "--set", "mode=3",
                   "--set", "bias=1", "--set", "hp=400", "--set", "sag=1", "--set", "out=12",
                   "--set", "tone=0")
        src = read_npy_mono(fx)
        lat = 15
        want = [0.0] * lat + src[: len(src) - lat]
        bad = sum(1 for a, b in zip(l, want) if a != b) + sum(1 for a, b in zip(r, want) if a != b)
        check(bad == 0, "drive MIX 0: the input %d frames late, bit for bit "
              "(%d samples differ)" % (lat, bad))

    print("fx_moves: %d failed" % fails if fails else "fx_moves: all passed")
    return 1 if fails else 0


def read_npy_mono(path):
    """A 1-D little-endian float32 .npy (the shared fx_input), as a list."""
    with open(path, "rb") as fh:
        raw = fh.read()
    assert raw[:6] == b"\x93NUMPY"
    hlen = struct.unpack("<H", raw[8:10])[0] if raw[6] == 1 else struct.unpack("<I", raw[8:12])[0]
    start = (10 if raw[6] == 1 else 12) + hlen
    header = raw[(10 if raw[6] == 1 else 12):start].decode("latin1")
    assert "'<f4'" in header and "False" in header, header
    count = (len(raw) - start) // 4
    return list(struct.unpack("<%df" % count, raw[start:start + 4 * count]))


if __name__ == "__main__":
    sys.exit(main())
