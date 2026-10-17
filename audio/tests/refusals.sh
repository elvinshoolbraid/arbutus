#!/bin/sh
# §7.4: a box that cannot load says exactly why, and loads nothing. Each case
# breaks one thing in a copy of a good box and checks the message.
set -u
RUN="$1"; BOX="$2"; TMP="$3"; ARCH="${4:-}"
fails=0

# The arch key is <os>-<cpu> and only the build machine knows it (sdk/CMakeLists
# ARB_ARCH). Hard-coding it here made the last case a no-op off darwin-arm64 and
# linux-aarch64 — the manifest kept a build for this device and loaded.
[ -n "$ARCH" ] || { echo "refusals.sh: no arch key passed (arg 4)"; exit 1; }

expect() {                       # expect <message> <sed script>
  msg="$1"; edit="$2"
  rm -rf "$TMP/box"; mkdir -p "$TMP"; cp -R "$BOX" "$TMP/box"
  sed "$edit" "$BOX/engine.json" > "$TMP/box/engine.json"
  out=$("$RUN" "$TMP/box" --describe 2>&1); rc=$?
  if [ $rc -eq 0 ]; then
    echo "FAIL [$msg] loaded a broken box"; fails=$((fails+1)); return
  fi
  case "$out" in
    *"$msg"*) echo "ok   [$msg]" ;;
    *) echo "FAIL [$msg] got: $out"; fails=$((fails+1)) ;;
  esac
}

# A good box must load, or the rest proves nothing.
if "$RUN" "$BOX" --describe > /dev/null 2>&1; then
  echo "ok   [loads]"
else
  echo "FAIL [loads] the unmodified box was refused"; fails=$((fails+1))
fi

expect "ENGINE BROKEN"         's/"id": "q"/"id": "qq"/'
expect "ENGINE BROKEN"         's/"def": 8000.0/"def": 9000.0/'
expect "ENGINE BROKEN"         's/"kind": "engine"/"kind": "rack"/'
expect "NEEDS FIRMWARE UPDATE" 's/"abi": 2/"abi": 3/'
expect "NEEDS FIRMWARE UPDATE" 's/"format": 1/"format": 2/'
expect "NO BUILD FOR THIS DEVICE" "s/\"$ARCH\"/\"sunos-vax\"/"
# #673: a wrongly typed field is a refusal, not an exception out of the loader.
expect "ENGINE BROKEN"         's/"version": "1.0.0"/"version": 1/'
expect "ENGINE BROKEN"         's/"format": 1/"format": "1"/'

[ $fails -eq 0 ] || { echo "refusals: $fails FAILED"; exit 1; }
echo "refusals: all checks passed"
