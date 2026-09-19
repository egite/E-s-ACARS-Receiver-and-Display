#!/bin/bash
# Alternating A/B trial: acarsdec vs xng on the ACARS1 dongle.
#
# The dongle is exclusive, so the two decoders cannot run at once. Instead we
# alternate them in equal blocks and compare messages-per-minute, which averages
# out traffic variation across the run.
#
# Both decoders feed 127.0.0.1:5550, so the live web display keeps working
# throughout; each also writes its own JSONL so blocks can be scored separately.
#
# The production acars-decoder@acars service is stopped for the duration and
# restarted on exit (including Ctrl-C / kill).
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
RUN="$HERE/run"                    # all output lives here; gitignored
mkdir -p "$RUN"
LOGS=$RUN/logs; mkdir -p "$LOGS"
XNG=${XNG:-$RUN/xng}
ACARSDEC=${ACARSDEC:-$REPO/third_party/acarsdec/build/acarsdec}
# Mirror receiver.py: take librtlsdr_preload from config.json and only apply it if it
# exists. It is a workaround for a /usr/local librtlsdr that fails to detach the DVB
# driver; where there is no such build (and on distros shipping librtlsdr.so.2) it is
# empty, and LD_PRELOAD= is a harmless no-op.
PRELOAD=${PRELOAD:-$(python3 -c "import json;print(json.load(open('$REPO/config.json')).get('librtlsdr_preload') or '')" 2>/dev/null)}
[[ -n ${PRELOAD:-} && -e ${PRELOAD:-} ]] || PRELOAD=

BLOCK_SECS=${BLOCK_SECS:-1200}     # 20 minutes per block
CYCLES=${CYCLES:-6}                # 6 cycles => 4 h total, 2 h per decoder

# Identical front end for both: same dongle, gain, channels, centre and rate.
DEV=ACARS1
GAIN=40
CENTER_MHZ=130.2375
RATE=960000                        # 40 * 24 kHz (xng) and 80 * 12 kHz (acarsdec)
CHANS_CSV=130.025,130.450
CHANS_SPACE="130.025 130.450"
UDP=127.0.0.1:5550

CLK=$(getconf CLK_TCK)
RESULTS=$RUN/results.csv

cleanup() {
  echo "[$(date +%T)] restoring production service..."
  pkill -f "decoder-trial/run" 2>/dev/null   # matches acarsdec via its log path
  sleep 2
  sudo systemctl start acars-decoder@acars
  echo "[$(date +%T)] acars-decoder@acars: $(systemctl is-active acars-decoder@acars)"
}
trap cleanup EXIT INT TERM

cpu_secs() {  # utime+stime of $1 in seconds
  local s; s=$(cat /proc/"$1"/stat 2>/dev/null) || { echo 0; return; }
  # strip comm field (may contain spaces) before positional fields
  s=${s#*) }
  awk -v c="$CLK" '{printf "%.2f", ($12 + $13)/c}' <<<"$s"
}

run_block() {  # $1=decoder  $2=block index
  local who=$1 idx=$2 log="$LOGS/${2}_${1}.jsonl" pid t0 t1 c0 c1 secs cpu n
  echo "[$(date +%T)] block $idx: $who for ${BLOCK_SECS}s"
  if [[ $who == acarsdec ]]; then
    LD_PRELOAD=$PRELOAD "$ACARSDEC" \
      --output "json:file:path=$log" \
      --output "json:udp:host=127.0.0.1,port=5550" \
      --rtlsdr $DEV -p 2 -g $GAIN -c $CENTER_MHZ -m 80 $CHANS_SPACE \
      >"$LOGS/${idx}_${who}.err" 2>&1 &
  else
    "$XNG" listen --sdr "driver=rtlsdr,serial=$DEV" --mode acars \
      -g $GAIN -r $RATE -c ${CENTER_MHZ}M --channels "$CHANS_CSV" \
      --demod-effort live --jsonl "$log" --udp "$UDP" \
      >"$LOGS/${idx}_${who}.err" 2>&1 &
  fi
  pid=$!
  sleep 5
  if ! kill -0 $pid 2>/dev/null; then
    echo "  !! $who failed to start; see $LOGS/${idx}_${who}.err"
    echo "$idx,$who,0,0,0,FAILED,0" >>"$RESULTS"; return
  fi
  t0=$(date +%s); c0=$(cpu_secs $pid)
  sleep "$BLOCK_SECS"
  c1=$(cpu_secs $pid); t1=$(date +%s)
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
  secs=$((t1-t0))
  cpu=$(awk -v a="$c0" -v b="$c1" 'BEGIN{printf "%.1f", b-a}')
  n=$(wc -l <"$log" 2>/dev/null || echo 0)
  echo "  -> $n messages, ${cpu}s CPU over ${secs}s"
  local la; la=$(cut -d" " -f1 /proc/loadavg)
  echo "$idx,$who,$n,$secs,$cpu,OK,$la" >>"$RESULTS"
  sleep 3   # let the dongle settle before the other decoder claims it
}

echo "=== A/B trial: $CYCLES cycles x ${BLOCK_SECS}s per decoder ==="
echo "=== total run time: $((CYCLES*BLOCK_SECS*2/60)) minutes ==="
echo "block,decoder,messages,seconds,cpu_secs,status,load" >"$RESULTS"
sudo systemctl stop acars-decoder@acars
sleep 2
for ((i=1;i<=CYCLES;i++)); do
  run_block acarsdec "$i"
  run_block xng      "$i"
done
echo "=== trial complete ==="
