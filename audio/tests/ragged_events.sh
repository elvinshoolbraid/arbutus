#!/bin/sh
# ragged.sh for a sequencer (#631, test 10). A sequencer renders silence, so
# ragged.sh's audio comparison cannot see its timing; what it emits is the
# thing that must not depend on the block size (engine-spec §15.7 rule 8). The
# event list, rebased onto the run's timeline by --events-out, must come out
# byte for byte the same at 512, 128, 64 (the device's), 97, 2048 and 1.
#
# #56's lesson holds here too: the status is captured before any output is
# filtered, and `ragged_events_catches_block_dependence` proves this can fail.
set -u
RUN="$1"; BOX="$2"; TMP="$3"; shift 3
mkdir -p "$TMP"
REF="$TMP/ref512.json"
"$RUN" "$BOX" --frames 512 --events-out "$REF" "$@" > /dev/null || exit 1
fails=0
for N in 128 64 97 2048 1; do
  out="$TMP/out$N.json"
  if "$RUN" "$BOX" --frames "$N" --events-out "$out" "$@" > "$TMP/log$N.txt" 2>&1 &&
     cmp -s "$REF" "$out"; then
    echo "  $N: same events"
  else
    sed "s/^/  $N: /" "$TMP/log$N.txt"
    echo "FAIL at --frames $N"
    fails=$((fails+1))
  fi
done
[ $fails -eq 0 ] || exit 1
echo "ragged: all block sizes emit the same events"
