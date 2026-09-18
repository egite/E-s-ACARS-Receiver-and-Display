#!/bin/bash
# Multi-hour soak of xng at the 3-channel config, to confirm the 45% CPU result
# holds and overflows stay at zero over hours rather than minutes.
#
# xng feeds 127.0.0.1:5550 (so the live web display keeps working, on 3 of the 5
# ACARS channels) and 127.0.0.1:5599 (a counter that answers whether CRC-bad
# frames reach the UDP output).
#
# The production acars-decoder@acars service is stopped for the duration and
# restarted on exit, including Ctrl-C / kill.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
RUN="$HERE/run"                    # all output lives here; gitignored
mkdir -p "$RUN"
S=$RUN/soak; mkdir -p "$S"
XNG=${XNG:-$RUN/xng}
HOURS=${HOURS:-6}
EVERY=${EVERY:-300}          # sample every 5 minutes
DUR=$((HOURS*3600))

RATE=1248000
CENTER=130.575
CHANS=130.025,130.450,131.125

CLK=$(getconf CLK_TCK)
CSV=$S/soak.csv
JSONL=$S/xng.jsonl
ERR=$S/xng.err
UDPC=$S/udp_count.txt

XPID=""; UPID=""
cleanup() {
  echo "[$(date +%T)] cleaning up..."
  [ -n "$XPID" ] && kill "$XPID" 2>/dev/null
  [ -n "$UPID" ] && kill "$UPID" 2>/dev/null
  sleep 3
  [ -n "$XPID" ] && kill -9 "$XPID" 2>/dev/null
  [ -n "$UPID" ] && kill -9 "$UPID" 2>/dev/null
  sleep 2
  sudo systemctl start acars-decoder@acars
  sleep 3
  echo "[$(date +%T)] acars-decoder@acars: $(systemctl is-active acars-decoder@acars)"
}
trap cleanup EXIT INT TERM

cpu_secs() { local s; s=$(cat /proc/"$1"/stat 2>/dev/null) || { echo 0; return; }
             s=${s#*) }; awk -v c="$CLK" '{printf "%.2f",($12+$13)/c}' <<<"$s"; }
rss_mb()   { awk '/VmRSS/{printf "%.0f",$2/1024}' /proc/"$1"/status 2>/dev/null || echo 0; }

echo "=== xng soak: ${HOURS}h, 3 ch @ ${RATE} S/s, sample every ${EVERY}s ==="
rm -f "$JSONL" "$ERR" "$UDPC"
echo "elapsed_min,cpu_pct,overflows,msgs_jsonl,msgs_udp,udp_crc_bad,rss_mb,load" >"$CSV"

sudo systemctl stop acars-decoder@acars
sleep 2

python3 "$HERE/udp_count.py" 5599 "$UDPC" >/dev/null 2>&1 &
UPID=$!
sleep 1

"$XNG" listen --sdr "driver=rtlsdr,serial=ACARS1" --mode acars -g 40 \
  -r $RATE -c ${CENTER}M --channels "$CHANS" --demod-effort live \
  --jsonl "$JSONL" --udp 127.0.0.1:5550 --udp 127.0.0.1:5599 >"$ERR" 2>&1 &
XPID=$!
sleep 10
kill -0 $XPID 2>/dev/null || { echo "!! xng failed to start:"; tail -6 "$ERR"; exit 1; }
echo "[$(date +%T)] xng pid $XPID, udp counter pid $UPID"

START=$(date +%s); PREV_C=$(cpu_secs $XPID); PREV_T=$START
while :; do
  sleep "$EVERY"
  kill -0 $XPID 2>/dev/null || { echo "!! xng DIED at $(date +%T)"; tail -6 "$ERR"; break; }
  NOW=$(date +%s); C=$(cpu_secs $XPID)
  PCT=$(awk -v a="$PREV_C" -v b="$C" -v t=$((NOW-PREV_T)) 'BEGIN{printf "%.1f",(b-a)/t*100}')
  OV=$(grep -ac -i overflow "$ERR" 2>/dev/null); OV=${OV:-0}   # grep -c exits 1 on zero matches
  NJ=$(wc -l <"$JSONL" 2>/dev/null || echo 0)
  read -r _ NU NB <<<"$(cat "$UDPC" 2>/dev/null || echo '0 0 0')"
  EL=$(( (NOW-START)/60 ))
  echo "$EL,$PCT,$OV,$NJ,${NU:-0},${NB:-0},$(rss_mb $XPID),$(cut -d' ' -f1 /proc/loadavg)" >>"$CSV"
  printf '[%s] +%4dmin  cpu %5s%%  overflow %-4s msgs %-6s udp %-6s rss %sMB\n' \
    "$(date +%T)" "$EL" "$PCT" "$OV" "$NJ" "${NU:-0}" "$(rss_mb $XPID)"
  PREV_C=$C; PREV_T=$NOW
  [ $((NOW-START)) -ge $DUR ] && { echo "=== soak complete ==="; break; }
done
