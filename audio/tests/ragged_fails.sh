#!/bin/sh
# #56: prove `ragged.sh` can go red.
#
# For as long as they existed, all 24 ragged_* tests passed unconditionally --
# the script's condition was `"$RUN" ... | sed ...` and a pipeline's status is
# its last command's. Twenty-four tests that cannot fail are indistinguishable
# from twenty-four that pass, and two stock boxes sat 16 and 60 dBFS outside the
# tolerance behind them. So the harness now has a negative control of its own,
# the `rt_guard_catches_allocation` pattern: `arbutus-test/blockbox` reads one
# value per process() call instead of per frame, so its output really does
# depend on the block size, and ragged.sh must refuse it.
set -u
RAGGED="$1"; RUN="$2"; BOX="$3"; TMP="$4"

out=$("$RAGGED" "$RUN" "$BOX" "$TMP" --len 8192 --sr 48000 2>&1); rc=$?
echo "$out"

[ "$rc" -ne 0 ] || {
  echo "FAIL: ragged.sh passed a box whose output depends on the block size (#56)"
  exit 1
}
case "$out" in
  *"FAIL at --frames"*) ;;
  *) echo "FAIL: ragged.sh exited $rc but never said which block size differed"; exit 1 ;;
esac
case "$out" in
  *"all block sizes agree"*)
    echo "FAIL: ragged.sh printed its pass line on a failing box"; exit 1 ;;
  *) ;;
esac
echo "ragged.sh refused the block-size-dependent box"
