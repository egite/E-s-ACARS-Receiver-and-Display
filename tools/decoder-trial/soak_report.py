#!/usr/bin/env python3
"""Summarise the xng soak: CPU stability, overflow growth, and whether CRC-bad
frames reach the UDP output."""
import csv, json, pathlib, statistics as st

S = pathlib.Path(__file__).resolve().parent / "run" / "soak"
rows = list(csv.DictReader((S / "soak.csv").open())) if (S / "soak.csv").exists() else []
if not rows:
    raise SystemExit("no samples yet")

cpu = [float(r["cpu_pct"]) for r in rows]
last = rows[-1]
print(f"samples {len(rows)}   elapsed {last['elapsed_min']} min\n")
print(f"{'CPU % of one core':<24}min {min(cpu):5.1f}  median {st.median(cpu):5.1f}  "
      f"max {max(cpu):5.1f}  last {cpu[-1]:5.1f}")
print(f"{'overflows (cumulative)':<24}{last['overflows']}")
print(f"{'messages (jsonl)':<24}{last['msgs_jsonl']}")
print(f"{'messages (udp)':<24}{last['msgs_udp']}")
print(f"{'RSS MB':<24}first {rows[0]['rss_mb']}  last {last['rss_mb']}  (leak check)")
print(f"{'load avg':<24}last {last['load']}")

# CRC question: compare UDP count against jsonl total vs jsonl CRC-ok total
j = S / "xng.jsonl"
if j.exists():
    tot = ok = 0
    for line in j.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        tot += 1
        if (m.get("decode") or {}).get("crc_ok", True):
            ok += 1
    udp = int(last["msgs_udp"])
    print(f"\nCRC check:  jsonl total {tot}, of which CRC-ok {ok} (bad {tot-ok});  udp delivered {udp}")
    if tot and abs(udp - ok) <= max(2, 0.01 * tot) and tot != ok:
        print("  -> UDP appears to carry ONLY CRC-ok frames: safe for web/server.py")
    elif tot and abs(udp - tot) <= max(2, 0.01 * tot) and tot != ok:
        print("  -> UDP carries CRC-BAD frames too: web/server.py would ingest junk")
    elif tot == ok:
        print("  -> no CRC-bad frames seen yet; inconclusive, needs a longer run")
    else:
        print("  -> counts don't line up cleanly; inspect manually")
