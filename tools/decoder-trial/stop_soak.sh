#!/bin/bash
# Abort the soak and restore production ACARS. Safe to run any time.
# NOTE: the match must exclude this script (and its parent shell), or it kills itself -
# "bash .*soak\.sh" happily matches "stop_soak.sh".
set -u
SELF=$$; PAR=$PPID
echo "stopping soak.sh..."
for pid in $(pgrep -f "soak\.sh" 2>/dev/null); do
  [ "$pid" = "$SELF" ] || [ "$pid" = "$PAR" ] && continue
  tr "\0" " " < /proc/$pid/cmdline 2>/dev/null | grep -q "stop_soak" && continue
  echo "  killing soak.sh pid $pid"; kill "$pid" 2>/dev/null
done
sleep 3
echo "stopping xng and the udp counter..."
pkill -f "decoder-trial/run/xng" 2>/dev/null
pkill -f "decoder-trial/udp_count.py" 2>/dev/null
sleep 3
pkill -9 -f "decoder-trial/run/xng" 2>/dev/null
sleep 2
echo "restarting acars-decoder@acars..."
sudo systemctl start acars-decoder@acars
sleep 4
echo "service: $(systemctl is-active acars-decoder@acars)"
ps -eo args | grep -q "[b]uild/acarsdec" && echo "acarsdec running: yes" \
  || echo "acarsdec running: NO - check journalctl -u acars-decoder@acars -n 20"
