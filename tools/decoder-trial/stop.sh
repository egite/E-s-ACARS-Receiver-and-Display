#!/bin/bash
# Abort the A/B trial and restore the production ACARS service.
# Safe to run at any time, including if trial.sh died and left the service down.
set -u
SELF=$$

echo "stopping trial.sh..."
# Match on the script name only: trial.sh may have been launched as "./trial.sh"
# (relative), so an absolute-path pattern misses it - that bug let a previous run
# keep looping, pollute results.csv and re-enable the service mid-trial.
for pid in $(pgrep -f "bash .*trial\.sh" 2>/dev/null); do
  [ "$pid" = "$SELF" ] && continue
  echo "  killing trial.sh pid $pid"; kill "$pid" 2>/dev/null
done
sleep 2
pgrep -f "bash .*trial\.sh" | grep -qv "^$SELF$" && {
  for pid in $(pgrep -f "bash .*trial\.sh"); do [ "$pid" = "$SELF" ] || kill -9 "$pid" 2>/dev/null; done; }

# acarsdec rewrites its own argv with NULs, so match on the log path instead.
echo "stopping trial decoders..."
pkill -f "decoder-trial/run" 2>/dev/null
pkill -f "decoder-trial/run/xng" 2>/dev/null
sleep 3
for i in 1 2 3 4 5; do
  pgrep -f "decoder-trial/run" >/dev/null || break
  echo "  waiting for dongle release ($i)..."; sleep 2
done
pkill -9 -f "decoder-trial/run" 2>/dev/null
sleep 2

echo "restarting acars-decoder@acars..."
sudo systemctl start acars-decoder@acars
sleep 4
echo "service: $(systemctl is-active acars-decoder@acars)"
if pgrep -f "build/acarsdec" >/dev/null; then
  echo "acarsdec running: yes"
else
  echo "acarsdec running: NO - check 'journalctl -u acars-decoder@acars -n 20'"
fi
