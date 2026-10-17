#!/bin/sh
# Block size is the host's business, not the engine's: 512, 128 and a prime must
# give the same samples. arbutus-run always ends a run on a ragged block, so
# this also covers the short tail.
#
# **64 is in the list because the device ships it** (L4, #141, 2026-09-23:
# `arbutus-audio.service` passes `--block 64` while the emulator keeps 128).
# Before that it was absent and the gate never rendered a box at the size the
# instrument runs at — bracketed by 97 and by 1, which is the strongest case
# there is, but bracketing is not the same as testing.
#
# #56: this script could not fail. The condition used to be
#
#     if "$RUN" ... | sed "s/^/  $N: /"; then :; else
#
# and the exit status of a pipeline is its *last* command's -- `sed`, which
# always succeeds. `arbutus-run`'s refusal was never seen, `fails` stayed 0, and
# all 24 ragged_* tests printed "all block sizes agree" and passed however far
# apart the runs were. `va` and `wavetable` were 16 and 60 dBFS out the whole
# time. The lesson generalises past this file: **never put the thing being
# tested on the left of a pipe.** `/bin/sh` has no `pipefail`, so the status is
# captured first and the output is filtered afterwards.
set -u
RUN="$1"; BOX="$2"; TMP="$3"; shift 3
mkdir -p "$TMP"
REF="$TMP/ref512.npy"
if ! "$RUN" "$BOX" --frames 512 --out "$REF" "$@" > "$TMP/ref512.txt" 2>&1; then
  sed "s/^/  512: /" "$TMP/ref512.txt"
  echo "FAIL at the --frames 512 reference"
  exit 1
fi
fails=0
for N in 128 64 97 2048 1; do
  out="$TMP/out$N.txt"
  if "$RUN" "$BOX" --frames "$N" --golden "$REF" --tol -140 "$@" > "$out" 2>&1; then
    sed "s/^/  $N: /" "$out"
  else
    sed "s/^/  $N: /" "$out"
    echo "FAIL at --frames $N"
    fails=$((fails+1))
  fi
done
[ $fails -eq 0 ] || exit 1
echo "ragged: all block sizes agree"
