#!/bin/sh
# §3.9: a box's id is the folder it lands in (#675, review m-audio-sdk row 11).
# arb-manifest is handed the folder's <author>/<name> by add_arbutus_engine()
# and must refuse a binary whose arb_describe() id says anything else -- the
# copied template whose id nobody renamed. The control: the right name passes.
set -u
TOOL="$1"; BIN="$2"; SRC="$3"; TMP="$4"; ARCH="$5"
fails=0
mkdir -p "$TMP"

out=$("$TOOL" "$BIN" "$SRC" "$TMP/good.json" "$ARCH" "$ARCH/example.so" arbutus/example 2>&1); rc=$?
if [ $rc -eq 0 ]; then echo "ok   [the right folder passes]"
else echo "FAIL [the right folder passes] rc=$rc: $out"; fails=$((fails+1)); fi

out=$("$TOOL" "$BIN" "$SRC" "$TMP/bad.json" "$ARCH" "$ARCH/example.so" arbutus/mysynth 2>&1); rc=$?
if [ $rc -eq 0 ]; then
  echo "FAIL [a copied id is refused] arb-manifest accepted arbutus/example as arbutus/mysynth"
  fails=$((fails+1))
else
  case "$out" in
    *"is not its folder"*) echo "ok   [a copied id is refused]" ;;
    *) echo "FAIL [a copied id is refused] got: $out"; fails=$((fails+1)) ;;
  esac
fi

[ $fails -eq 0 ] || { echo "manifest_id: $fails FAILED"; exit 1; }
echo "manifest_id: all checks passed"
