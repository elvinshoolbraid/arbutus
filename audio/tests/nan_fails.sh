#!/bin/sh
# #626: prove arbutus-run refuses NaN, golden or not.
#
# The golden comparison was `peak = std::max(peak, |out - ref|)`, and
# `std::max(peak, NaN)` returns `peak`: an all-NaN output passed `biquad_golden`
# at -inf dBFS, and every ragged_* test with it (ragged.sh compares through the
# same --golden path). `arbutus-test/nanbox` is silence, or with nan=1 silence
# with one NaN in it. Four checks, each one a way the hole could come back:
#   1. a NaN output against a clean golden fails, and says why;
#   2. a clean output against a golden holding a NaN fails: the comparator
#      alone, with the output check (3) silent;
#   3. a NaN output with no golden at all fails;
#   4. ragged.sh refuses the NaN box (its reference is the box's own render).
# Status is captured before any filtering: never the thing tested on the left
# of a pipe (#56).
set -u
RUN="$1"; RAGGED="$2"; BOX="$3"; TMP="$4"
mkdir -p "$TMP"
LEN="--len 4096 --sr 48000"
bad=0
no() { echo "FAIL: $*"; bad=$((bad+1)); }

# shellcheck disable=SC2086  # LEN is two flags on purpose
"$RUN" "$BOX" $LEN --set nan=0 --out "$TMP/clean.npy" > "$TMP/clean.txt" 2>&1 \
  || { cat "$TMP/clean.txt"; echo "FAIL: the clean render itself failed"; exit 1; }

# 1.
out=$("$RUN" "$BOX" $LEN --set nan=1 --golden "$TMP/clean.npy" 2>&1); rc=$?
echo "1: $out"
[ "$rc" -ne 0 ] || no "a NaN output passed a clean golden (exit 0)"
case "$out" in *"non-finite differences"*FAIL*) ;; *) no "the golden line did not name the NaN" ;; esac
case "$out" in *PASS*) no "a PASS line was printed for a NaN output" ;; *) ;; esac

# 2. The NaN golden is written by a run that itself fails (check 3), so its
# status is not asked here; the file is.
"$RUN" "$BOX" $LEN --set nan=1 --out "$TMP/nan.npy" > /dev/null 2>&1
[ -s "$TMP/nan.npy" ] || { echo "FAIL: no NaN golden was written"; exit 1; }
out=$("$RUN" "$BOX" $LEN --set nan=0 --golden "$TMP/nan.npy" 2>&1); rc=$?
echo "2: $out"
[ "$rc" -ne 0 ] || no "a golden holding a NaN passed a clean output (exit 0)"
case "$out" in *"non-finite differences"*FAIL*) ;; *) no "the comparator did not see the golden's NaN" ;; esac
case "$out" in *"non-finite samples"*) no "check 2 is meant to be the comparator alone" ;; *) ;; esac

# 3.
out=$("$RUN" "$BOX" $LEN --set nan=1 2>&1); rc=$?
echo "3: $out"
[ "$rc" -ne 0 ] || no "a NaN output with no golden exited 0"
case "$out" in *"output: 1 non-finite samples FAIL"*) ;; *) no "the output check did not count the one NaN" ;; esac

# 4.
out=$("$RAGGED" "$RUN" "$BOX" "$TMP/ragged" $LEN --set nan=1 2>&1); rc=$?
echo "4: $out"
[ "$rc" -ne 0 ] || no "ragged.sh passed the NaN box"
case "$out" in *"all block sizes agree"*) no "ragged.sh printed its pass line" ;; *) ;; esac

[ "$bad" -eq 0 ] || exit 1
echo "arbutus-run refused NaN four ways"
