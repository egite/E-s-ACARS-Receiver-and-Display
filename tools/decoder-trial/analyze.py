#!/usr/bin/env python3
"""Score the acarsdec vs xng A/B trial from results.csv and the per-block JSONL logs."""
import csv, json, sys
from collections import defaultdict
from pathlib import Path

TRIAL = Path(__file__).resolve().parent / "run"


def block_detail(path):
    """Return (total, with_text, crc_ok, unique) for one block's JSONL."""
    total = text = crc = 0
    seen = set()
    if not path.exists():
        return 0, 0, 0, 0
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1
        # xng nests under "body"; acarsdec is flat
        body = m.get("body", m)
        txt = (body.get("text") or "").strip()
        if txt:
            text += 1
        dec = m.get("decode") or {}
        if dec.get("crc_ok", True):
            crc += 1
        seen.add((body.get("tail"), body.get("label"), txt[:40]))
    return total, text, crc, len(seen)


def main():
    res = TRIAL / "results.csv"
    if not res.exists():
        sys.exit("no results.csv yet - has the trial run?")
    agg = defaultdict(lambda: {"msgs": 0, "secs": 0, "cpu": 0.0,
                               "text": 0, "uniq": 0, "blocks": 0, "failed": 0})
    for r in csv.DictReader(res.open()):
        a = agg[r["decoder"]]
        if r["status"] != "OK":
            a["failed"] += 1
            continue
        tot, txt, _crc, uniq = block_detail(TRIAL / "logs" / f'{r["block"]}_{r["decoder"]}.jsonl')
        a["msgs"] += int(r["messages"]); a["secs"] += int(r["seconds"])
        a["cpu"] += float(r["cpu_secs"]); a["text"] += txt; a["uniq"] += uniq
        a["blocks"] += 1

    if not agg:
        sys.exit("no completed blocks yet")

    print(f'{"decoder":<10}{"blocks":>7}{"min":>8}{"msgs":>7}{"msg/min":>9}'
          f'{"w/text":>8}{"unique":>8}{"CPU%":>7}')
    print("-" * 64)
    rows = {}
    for who, a in sorted(agg.items()):
        if not a["secs"]:
            continue
        mins = a["secs"] / 60
        rate = a["msgs"] / mins
        cpu_pct = 100 * a["cpu"] / a["secs"]
        rows[who] = (rate, cpu_pct)
        print(f'{who:<10}{a["blocks"]:>7}{mins:>8.0f}{a["msgs"]:>7}{rate:>9.2f}'
              f'{a["text"]:>8}{a["uniq"]:>8}{cpu_pct:>7.1f}')
        if a["failed"]:
            print(f'  ({a["failed"]} block(s) failed to start)')

    if len(rows) == 2:
        (ar, ac), (xr, xc) = rows["acarsdec"], rows["xng"]
        print("\nxng vs acarsdec:")
        print(f'  messages : {xr/ar*100-100:+.1f}%  ({xr:.2f} vs {ar:.2f} per minute)'
              if ar else "  messages : n/a")
        print(f'  CPU      : {xc/ac*100-100:+.1f}%  ({xc:.1f}% vs {ac:.1f}% of one core)'
              if ac else "  CPU      : n/a")
        print("\nNote: blocks alternate, so traffic variation averages out across the run,")
        print("but this is a time-sliced A/B, not a simultaneous comparison.")


if __name__ == "__main__":
    main()
