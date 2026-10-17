#!/usr/bin/env python3
"""Bench every stock box with its own test material (audio/PI.md § 2).

Each box is run with the arguments of its first golden test in the CTest list
(`ctest -N -V`), minus the comparison and output flags, plus `--bench`. So the
number on the CM4 and the number on the Mac use the same notes, input and
settings, and nothing here has to be kept in step with the tests by hand.
Every box is benched at 512 frames, whatever block its test pins.

    audio/tools/bench-all.py [--build audio/build] [--markdown]
"""
import argparse
import re
import shlex
import subprocess
import sys

# Flags that compare or write, each followed by one value: dropped. `--frames`
# goes too, and every box is benched at BLOCK: six tests pin their own block
# (reverb 256, five modulators 128), and a table whose rows are at different
# block sizes cannot be read down a column (#626; the README says 512).
DROP = {"--golden", "--tol", "--out", "--events-out", "--frames"}
BLOCK = 512


def box_commands(build):
    out = subprocess.run(["ctest", "--test-dir", build, "-N", "-V"],
                         capture_output=True, text=True, check=True).stdout
    first = {}  # box name -> argv, first golden-style run only (dict keeps order)
    for line in out.splitlines():
        m = re.search(r"Test command: (.*)$", line)
        if not m:
            continue
        argv = shlex.split(m.group(1))
        if not argv[0].endswith("arbutus-run") or len(argv) < 2:
            continue  # ragged.sh, compare_files, host tests
        if "--rt-guard" in argv or "--describe" in argv:
            continue
        if "--golden" not in argv and "--events-out" not in argv:
            continue  # refusal tests and the like
        box = "/".join(argv[1].rstrip("/").split("/")[-2:])
        if box in first:
            continue
        cmd, i = [], 0
        while i < len(argv):
            if argv[i] in DROP:
                i += 2
                continue
            cmd.append(argv[i])
            i += 1
        first[box] = cmd + ["--frames", str(BLOCK), "--bench"]
    return first


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="audio/build")
    ap.add_argument("--markdown", action="store_true", help="print a | box | us | % | table")
    args = ap.parse_args()
    rows, bad = [], 0
    for box, cmd in box_commands(args.build).items():
        r = subprocess.run(cmd, capture_output=True, text=True)
        m = re.search(r"([0-9.]+) us/block .*= ([0-9.]+)% of a core", r.stdout)
        if r.returncode or not m:
            print(f"{box}: FAILED\n{r.stdout}{r.stderr}", file=sys.stderr)
            bad += 1
            continue
        rows.append((box, float(m.group(1)), float(m.group(2))))
        if not args.markdown:
            print(r.stdout.strip())
    if args.markdown:
        print("| Box | us/block | % core |\n|---|---|---|")
        for box, us, pct in rows:
            print(f"| `{box}` | {us:.2f} | {pct:.2f} |")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
