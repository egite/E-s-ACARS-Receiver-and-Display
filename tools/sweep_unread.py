#!/usr/bin/env python3
"""Sweep the archive through the translator and show what still has no specific reading.

Replays every archived message through web/translate.py and buckets the ones that come back
generic (or categorised "encoded"/"other") by label, sublabel, airline and the shape of the
first few characters, with examples. This is how the decodable families were found on
20 October 2026 - the counts say where the messages are, the examples say whether there is
structure worth reading.

Two things to know before trusting the numbers:

  - ARINC 622 messages (CPDLC, ADS-C) decode live through libacars and only show as read on
    replay in files written after 20 October, so they are excluded here. Include them with
    --with-arinc622 and the count roughly doubles for no reason.
  - Most of what remains is engine-monitoring continuation blocks (United, Southwest,
    Frontier H1/DF), which have no text structure and are correctly left generic. Look for
    buckets whose examples carry coordinates, times, airport codes or a repeating layout.

With --family REGEX it instead writes every message whose text matches REGEX to a JSONL
file (ADS-B pairing kept), which is the input for checking a new parser against the
aircraft's own position: see --check.

With --check FILE it runs the translator over such a file and, for each message with an
ADS-B fix, compares the position the translator returned (or the latest weather sample)
with the fix, and a ground speed in the summary with the ADS-B ground speed.

Examples:
    tools/sweep_unread.py                         # the buckets, biggest first
    tools/sweep_unread.py --family '^\\*POS'       # extract one family to a file
    tools/sweep_unread.py --check family.jsonl    # score a parser against ADS-B
"""
import argparse
import collections
import datetime as dt
import glob
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
import translate  # noqa: E402


def records(archive_dir):
    for path in sorted(glob.glob(str(Path(archive_dir) / "*.jsonl"))):
        with open(path) as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def shape(text):
    text = text.lstrip("/").replace("#DFB", "").replace("#CFB", "")
    return re.sub(r"\d", "9", text[:5])


def unread(msg):
    try:
        r = translate.translate(msg)
    except Exception as exc:  # a crash is the most important finding of all
        return {"category": "CRASH", "summary": f"{type(exc).__name__}: {exc}", "generic": True}
    return r if r.get("generic") or r["category"] in ("encoded", "other") else None


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def sweep(args):
    buckets, examples, total = collections.Counter(), {}, 0
    for msg in records(args.archive):
        text = (msg.get("text") or "").strip()
        if len(text) < 6:
            continue
        if not args.with_arinc622 and "USADCXA." in text:
            continue
        total += 1
        r = unread(msg)
        if not r:
            continue
        key = (r["category"], msg.get("label"), msg.get("sublabel") or "", (msg.get("flight") or "")[:2], shape(text))
        buckets[key] += 1
        if len(examples.setdefault(key, [])) < args.examples:
            examples[key].append(text[:args.width])
    print(f"{sum(buckets.values()):,} of {total:,} messages without a specific reading")
    for key, n in buckets.most_common(args.top):
        print(f"{n:6d}  {key}")
        for ex in examples[key]:
            print(f"        {ex!r}")


def extract(args):
    pattern = re.compile(args.family)
    n = 0
    with open(args.out, "w") as out:
        for msg in records(args.archive):
            text = (msg.get("text") or "").strip()
            if text and pattern.search(text):
                out.write(json.dumps(msg) + "\n")
                n += 1
    print(f"{n} messages matching {args.family!r} written to {args.out}")


def check(args):
    msgs = [json.loads(l) for l in open(args.check)]
    generic, dists, speeds, shown = 0, [], [], 0
    for msg in msgs:
        r = translate.translate(msg)
        if r.get("generic") or r["category"] in ("encoded", "other"):
            generic += 1
            continue
        if shown < args.examples:
            print(f"  {r['summary']} | {r['details']}")
            shown += 1
        fix = msg.get("adsb") or {}
        if not fix.get("lat"):
            continue
        pos = r.get("position")
        if not pos:
            wx = translate.weather_samples(re.sub(r"^#DFB", "", msg["text"]))
            if wx and wx[2]:
                pos = wx[2][-1]  # the latest sample; it predates the message, so expect a few km per minute
        if pos:
            dists.append(haversine_km(pos["lat"], pos["lon"], fix["lat"], fix["lon"]))
        gs = re.search(r", (\d+) kt", r["summary"])
        if gs and fix.get("gs"):
            speeds.append(int(gs.group(1)) - fix["gs"])
    print(f"{len(msgs)} messages, {generic} still generic")
    if dists:
        dists.sort()
        print(f"position vs ADS-B: n={len(dists)} median {dists[len(dists) // 2]:.1f} km, 90th {dists[int(len(dists) * .9)]:.1f} km, max {dists[-1]:.1f} km")
    if speeds:
        speeds.sort()
        print(f"ground speed vs ADS-B: n={len(speeds)} median error {speeds[len(speeds) // 2]:+.0f} kt, max |{max(abs(s) for s in speeds):.0f}| kt")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--archive", default=str(ROOT / "archive" / "messages"))
    parser.add_argument("--with-arinc622", action="store_true")
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--examples", type=int, default=2)
    parser.add_argument("--width", type=int, default=200)
    parser.add_argument("--family", help="regex over message text: extract matching messages instead of sweeping")
    parser.add_argument("--out", default="family.jsonl")
    parser.add_argument("--check", help="JSONL from --family: run the translator and compare with ADS-B")
    args = parser.parse_args()
    if args.check:
        check(args)
    elif args.family:
        extract(args)
    else:
        sweep(args)


if __name__ == "__main__":
    main()
