#!/bin/sh
# #52: prove the rt-guard is awake, not merely quiet.
#
# Two assertions, both of which a silently-broken guard fails:
#   1. on a platform that is supposed to count -- macOS (malloc zone) and
#      Linux/glibc (the counting allocator in hostlib/rt_alloc_hooks.c) -- the
#      guard must NOT report "unavailable on this platform";
#   2. a box that allocates inside process() must be refused, with a non-zero
#      exit status, so `ctest` and `tools/gate.sh` go red for it.
#
# Before #52 this whole file would have passed on Linux by taking the
# "unavailable" branch: the Linux gate and CI were blind to an allocating box,
# which is the one platform where an allocation on the audio thread costs an
# xrun (the CM4).
set -u
RUN="$1"; BOX="$2"

OUT=$("$RUN" "$BOX" --rt-guard --len 4096 --sr 48000 2>&1)
RC=$?
echo "$OUT"

if echo "$OUT" | grep -q "unavailable on this platform"; then
  case "$(uname -s)" in
    Linux|Darwin)
      echo "FAIL: rt-guard cannot count on $(uname -s) — live_blocks() regressed (#52)"
      exit 1 ;;
    *)
      echo "SKIP: no heap counter on $(uname -s); the guard says so honestly"
      exit 0 ;;
  esac
fi

echo "$OUT" | grep -q "allocations inside process() FAIL" || {
  echo "FAIL: the guard did not refuse a box that allocates every block"
  exit 1
}
[ "$RC" -ne 0 ] || {
  echo "FAIL: arbutus-run exited 0 for a box that allocates in process()"
  exit 1
}
echo "rt-guard refused the allocating box on $(uname -s)"
