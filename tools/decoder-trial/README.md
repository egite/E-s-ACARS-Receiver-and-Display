# Decoder trial harness

Tooling used to evaluate [xng](https://github.com/airframesio/xng) as a
replacement for acarsdec. Findings are in [`docs/decoder-evaluation.md`](../../docs/decoder-evaluation.md).

Everything here writes to `run/`, which is gitignored. Put the `xng` binary at
`run/xng`, or point `XNG=` at it.

Both harnesses **stop `acars-decoder@acars` for their duration** (the dongle is
exclusive) and restart it on exit, including on Ctrl-C. Whichever decoder is
running feeds `127.0.0.1:5550`, so the web display keeps working throughout.

## Scripts

| | |
|---|---|
| `trial.sh` | Alternating A/B. Runs two decoders in equal blocks so traffic variation averages out, since they cannot run simultaneously on one dongle. |
| `analyze.py` | Scores a trial: messages/min, unique messages, CPU%. Works mid-run. |
| `soak.sh` | Long single-decoder soak. Samples CPU, overflows, RSS and load every 5 min. |
| `soak_report.py` | Summarises a soak, including the CRC comparison. |
| `udp_count.py` | Counts what actually arrives over UDP, used to test whether CRC-bad frames are forwarded. |
| `stop.sh`, `stop_soak.sh` | Abort and restore production. Safe to run at any time. |

## Running

```bash
# 4-hour A/B, 6 cycles of 20 min per decoder
./trial.sh                      # or: BLOCK_SECS=300 CYCLES=2 ./trial.sh
python3 analyze.py

# 6-hour soak of xng at the 3-channel config
./soak.sh                       # or: HOURS=1 EVERY=60 ./soak.sh
python3 soak_report.py
```

Run them detached (`setsid nohup ./soak.sh > run/soak.log 2>&1 &`) if the run
outlasts your session.

## Two traps these scripts exist to work around

- **acarsdec rewrites its own argv.** It tokenizes `--output json:udp:host=…,port=…`
  destructively with NULs, so `ps` shows six separate arguments and any `pkill -f`
  pattern built from the original string never matches. The stop scripts match on
  the log path instead.
- **`pkill -f` matches the shell running it.** Your own command line contains the
  pattern you passed. The stop scripts resolve PIDs and skip themselves and their
  parent; an earlier version killed its own parent mid-cleanup and left a decoder
  looping against the production service.
