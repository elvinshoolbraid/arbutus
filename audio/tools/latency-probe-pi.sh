#!/bin/bash
# P7 (#73) bench, run ON the CM4 from ~/Arbutus-1 (built): arbutus-audio as a
# transient unit with arbutus-audio.service's realtime properties (CPUAffinity 3,
# LimitRTPRIO 95, LimitMEMLOCK infinity, Nice -10), one per block size, each
# probed idle and under load by latency-probe.py. Stops arbutus.target for the
# run (snd-dummy, core 3) and starts it again on exit. PRESSES=40 for a smoke run.
set -u
R=$HOME/Arbutus-1
OUT=$HOME/p7-$(date -u +%Y%m%dT%H%M)
mkdir -p "$OUT"
sudo systemctl stop arbutus.target
trap 'sudo systemctl stop p7-audio 2>/dev/null; sudo systemctl start arbutus.target' EXIT
for B in 64 128 256; do
  S=/tmp/p7-$B.sock; rm -f $S
  sudo systemd-run --unit=p7-audio --collect -p User=$USER -p Group=audio \
    -p CPUAffinity=3 -p LimitRTPRIO=95 -p LimitMEMLOCK=infinity -p Nice=-10 \
    $R/audio/build/host/arbutus-audio --socket $S --block $B \
    --library $R/audio/build/boxes --library $R/audio/library >/dev/null
  for i in $(seq 50); do [ -S $S ] && break; sleep 0.1; done
  sleep 1
  for L in idle load; do
    F=""; [ $L = load ] && F=--load
    python3 $R/audio/tools/latency-probe.py --socket $S $F --presses ${PRESSES:-400} \
      --json $OUT/b$B-$L.json 2>&1 | tee -a $OUT/summary.txt
  done
  sudo journalctl -u p7-audio --no-pager -o cat | grep -iE 'realtime|xrun|error' | tail -3 | tee -a $OUT/summary.txt
  sudo systemctl stop p7-audio; sleep 1
done
echo "out: $OUT"
