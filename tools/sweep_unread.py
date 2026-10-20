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

With --crib FILE it looks for known plaintext. For each message in such a file it gathers
what is already known about that flight at that moment - the route the same aircraft sent
in clear on another label within --window seconds, and from the message's own ADS-B fix
the flight level, ground speed, track and the UTC time - and then, column by column, asks
whether a fact sits there. Two tests per fact and column:

  clear    the fact's characters appear literally at that column
  cipher   the characters at that column map to the fact's characters consistently, as a
           substitution would - per key group, taken from the first --group characters,
           since these ciphers pick an alphabet by a leading key

Each is scored against the same test with the facts shuffled among the messages, which is
what chance looks like on that column: a constant column or a narrow field "agrees" with
anything. Only a column well above its own shuffle is worth reading. This is how
Southwest's label 37 route fields were read on 20 October, after a dictionary attack had
sat at chance for days. ADS-B numbers are rounded and a moment apart from the report, so
a numeric field shows as partly consistent rather than exact.

Examples:
    tools/sweep_unread.py                         # the buckets, biggest first
    tools/sweep_unread.py --family '^\\*POS'       # extract one family to a file
    tools/sweep_unread.py --family . --label 88   # or every message with one label
    tools/sweep_unread.py --check family.jsonl    # score a parser against ADS-B
    tools/sweep_unread.py --crib family.jsonl --group 2   # find known plaintext in it
"""
import argparse
import collections
import datetime as dt
import glob
import json
import math
import random
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
            if text and pattern.search(text) and (not args.label or msg.get("label") == args.label):
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


ROUTE = re.compile(r"Flight ([A-Z]{4})\b.*→ ([A-Z]{4})\b")


def body(msg):
    return (msg.get("text") or "").replace("\r", "").replace("\n", "")


def clear_routes(archive, aircraft, skip_labels):
    """aircraft -> sorted [(ts, flight, origin, destination)] from other labels' clear text."""
    out = collections.defaultdict(list)
    for msg in records(archive):
        who = msg.get("icao") or msg.get("reg")
        if who not in aircraft or msg.get("label") in skip_labels or not msg.get("text"):
            continue
        r = translate.translate(dict(msg, english=None))
        if r.get("category") == "encoded":
            continue
        m = next((m for d in r.get("details") or [] if d and (m := ROUTE.search(d))), None)
        ends = m.groups() if m else translate.route_in(msg["text"])
        if ends:
            out[who].append((msg["ts"], msg.get("flight"), *ends))
    for v in out.values():
        v.sort()
    return out


def facts(msg, routes, window):
    """What is known about this flight now, as strings that might appear in the message."""
    out = {}
    who, ts = msg.get("icao") or msg.get("reg"), msg["ts"]
    near = [r for r in routes.get(who, []) if abs(r[0] - ts) < window
            and (not r[1] or not msg.get("flight") or r[1] == msg["flight"])]
    if near:
        _, _, orig, dest = min(near, key=lambda r: abs(r[0] - ts))
        out.update({"origin": orig, "destination": dest, "origin IATA": orig[1:], "destination IATA": dest[1:]})
    out["time HHMM"] = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%H%M")
    fix = msg.get("adsb") or {}
    if fix.get("alt"):
        out["flight level"] = f"{round(fix['alt'] / 100):03d}"
    if fix.get("gs"):
        out["ground speed"] = f"{round(fix['gs']):03d}"
    if fix.get("track") is not None:
        out["track"] = f"{round(fix['track']) % 360:03d}"
    return out


def crib_score(rows, kind, col):
    """Messages tested, the share with the fact literally at col, and how consistently the
    characters there map to the fact's characters within each key group."""
    hits = n = pairs = 0
    maps = collections.defaultdict(collections.Counter)
    for text, group, f in rows:
        fact = f.get(kind)
        if not fact or col + len(fact) > len(text):
            continue
        seen = text[col:col + len(fact)]
        n += 1
        hits += seen == fact
        for c, p in zip(seen, fact):
            maps[group, p][c] += 1
            pairs += 1
    if not n:
        return 0, 0.0, 0.0
    return n, hits / n, sum(x.most_common(1)[0][1] for x in maps.values()) / pairs


def crib(args):
    msgs = [json.loads(l) for l in open(args.crib)]
    labels = {m.get("label") for m in msgs}
    routes = clear_routes(args.archive, {m.get("icao") or m.get("reg") for m in msgs}, labels)
    rows = [(body(m), m["text"][:args.group], facts(m, routes, args.window)) for m in msgs]
    kinds = collections.Counter(k for *_, f in rows for k in f)
    print(f"{len(rows)} messages; facts known: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
    random.seed(1)
    found = []
    for kind in kinds:
        have = [r for r in rows if kind in r[2]]
        if len(have) < args.min_rows:
            continue
        others = [r[2] for r in have]
        random.shuffle(others)
        shuffled = [(t, g, f) for (t, g, _), f in zip(have, others)]
        for col in range(min(max(len(t) for t, *_ in have), args.columns)):
            n, clear, cipher = crib_score(have, kind, col)
            if n < args.min_rows:
                continue
            _, clear0, cipher0 = crib_score(shuffled, kind, col)
            found.append((max(clear - clear0, cipher - cipher0), kind, col, n, clear, clear0, cipher, cipher0))
    found.sort(reverse=True)
    print(f"{'fact':18} {'col':>4} {'n':>6}   clear (shuffled)   cipher (shuffled)")
    for lift, kind, col, n, clear, clear0, cipher, cipher0 in found[:args.top]:
        if lift < args.min_lift:
            break
        print(f"{kind:18} {col:4d} {n:6d}   {clear:5.2f} ({clear0:.2f})       {cipher:5.2f} ({cipher0:.2f})")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--archive", default=str(ROOT / "archive" / "messages"))
    parser.add_argument("--with-arinc622", action="store_true")
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--examples", type=int, default=2)
    parser.add_argument("--width", type=int, default=200)
    parser.add_argument("--family", help="regex over message text: extract matching messages instead of sweeping")
    parser.add_argument("--out", default="family.jsonl")
    parser.add_argument("--label", help="--family: only messages with this label")
    parser.add_argument("--check", help="JSONL from --family: run the translator and compare with ADS-B")
    parser.add_argument("--crib", help="JSONL from --family: look for known plaintext in it, column by column")
    parser.add_argument("--group", type=int, default=0, help="--crib: leading characters that pick a cipher alphabet")
    parser.add_argument("--window", type=float, default=1800, help="--crib: seconds to look for the flight's clear route")
    parser.add_argument("--columns", type=int, default=120, help="--crib: how many columns to test")
    parser.add_argument("--min-rows", type=int, default=20, help="--crib: fewest messages a fact must be tested on")
    parser.add_argument("--min-lift", type=float, default=0.15, help="--crib: smallest margin over the shuffle to list")
    args = parser.parse_args()
    if args.crib:
        crib(args)
    elif args.check:
        check(args)
    elif args.family:
        extract(args)
    else:
        sweep(args)


if __name__ == "__main__":
    main()
