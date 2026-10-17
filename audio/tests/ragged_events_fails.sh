#!/bin/sh
# #631: prove `ragged_events.sh` can go red, as `ragged_fails.sh` does for
# ragged.sh (#56). `arbutus-test/seqblockbox` emits a note at frame 0 of every
# block, so its event list really does depend on the block size.
set -u
RAGGED="$1"; RUN="$2"; BOX="$3"; TMP="$4"

out=$("$RAGGED" "$RUN" "$BOX" "$TMP" --len 8192 --sr 48000 2>&1); rc=$?
echo "$out"

[ "$rc" -ne 0 ] || {
  echo "FAIL: ragged_events.sh passed a box whose events depend on the block size"
  exit 1
}
case "$out" in
  *"FAIL at --frames"*) ;;
  *) echo "FAIL: ragged_events.sh exited $rc but never said which block size differed"; exit 1 ;;
esac
case "$out" in
  *"all block sizes emit the same events"*)
    echo "FAIL: ragged_events.sh printed its pass line on a failing box"; exit 1 ;;
  *) ;;
esac
echo "ragged_events.sh refused the block-size-dependent box"
