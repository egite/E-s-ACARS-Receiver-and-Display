#!/usr/bin/env python3
"""Attack the origin and destination fields of the obfuscated business-aviation reports.

Label 44 from the XA/GS/ZD datalink prefixes is a monoalphabetic substitution, one
alphabet per leading two-digit key. web/translate.py already decodes the digits - those
fell out of the format's own structure - but fields 3 and 4 are four-letter ICAO codes and
the letters only give way to a dictionary attack. This is that attack, plus the part that
matters more: an honest way to tell whether it worked.

Three numbers frame every result, and none of them is 100%:

  ceiling  Some of this traffic is sent in clear, origin and destination included. Over
           those known-true routes the aircraft sits within 100 km of its own great circle
           78% of the time - real flights deviate - so 78% is what a perfect decode scores.
  null     Shuffling positions against routes gives 41%. Business jets near this receiver
           fly routes that pass nearby whatever you claim, so chance is not zero.
  held out Alphabets are fitted on 80% of a group's records and scored on the other 20%.
           Fitting and scoring on the same records flatters the result by a wide margin -
           an early version of this reported 71% that way and 66% held out.

Run it to reproduce a measurement, or as a starting point for a better objective. As of
writing the honest position is that the objective, not the search, is the limit: for key
group 02 twelve independent searches all converge on the same global optimum and it is
still only 50% correct, and for group 04 the correlation between objective and truth is
negative. More restarts cannot fix that. A stronger objective - scoring a route against an
aircraft's whole trajectory and its first and last sighting rather than one point's
distance from a great circle - is the open idea, not more compute.

    tools/crack_routes.py                     fit every key group and score it
    tools/crack_routes.py --repeats 12        repeat each fit to expose run-to-run spread
    tools/crack_routes.py --group 05          one group only

Needs the archive (config.json "archive"), which is where the aircraft's ADS-B fix at the
time of each message comes from, and web/airports.json for the dictionary.
"""
import argparse
import collections
import glob
import json
import math
import random
import statistics
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
import translate  # noqa: E402  - for the key table and the already-solved digit alphabets

LAT, LON, RANK = 4, 5, 6  # columns of web/airports.json, named so a new one cannot shift them
ROUTE_FIELDS = (3, 4)
CEILING, NULL = 78, 41  # see the module docstring
R = 6371.0


def airports():
    return {a[0]: a for a in json.loads((ROOT / "web" / "airports.json").read_text())}


def _bearing(a, b, c, d):
    y = math.sin(math.radians(d - b)) * math.cos(math.radians(c))
    x = (math.cos(math.radians(a)) * math.sin(math.radians(c))
         - math.sin(math.radians(a)) * math.cos(math.radians(c)) * math.cos(math.radians(d - b)))
    return math.atan2(y, x)


def _dist(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = p2 - p1, math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.asin(math.sqrt(h))


def crosstrack(olat, olon, dlat, dlon, plat, plon):
    """How far the aircraft sits off the great circle from origin to destination."""
    d13 = _dist(olat, olon, plat, plon) / R
    off = math.sin(d13) * math.sin(_bearing(olat, olon, plat, plon) - _bearing(olat, olon, dlat, dlon))
    return abs(math.asin(max(-1.0, min(1.0, off))) * R)


def records(archive_dir):
    """Per key group: (fields, lat, lon) for every well-formed report we can place."""
    out = collections.defaultdict(list)
    for path in sorted(glob.glob(str(Path(archive_dir) / "*.jsonl"))):
        with open(path) as fh:
            for line in fh:
                msg = json.loads(line)
                if msg.get("label") != "44":
                    continue
                group = msg["text"][:2]
                key = translate.BIZJET_KEYS.get(group)
                if not key:
                    continue
                english = translate.translate(dict(msg, english=None))
                where = english.get("position")
                if not where:
                    continue
                fields = msg["text"][2:].split(key[0])
                if tuple(len(f) for f in fields) == translate.BIZJET_SHAPE:
                    out[group].append((fields, where["lat"], where["lon"]))
    return out


def alphabet(rows):
    """The cipher's letters, and the records that use only those.

    Over weeks the route fields pick up characters seen once or twice - bit errors, not
    alphabet - which push a group past the 26 letters an ICAO code can possibly use.
    """
    freq = collections.Counter(c for f, _, _ in rows for i in ROUTE_FIELDS for c in f[i])
    keep = {c for c, n in freq.items() if n >= 3}
    if len(keep) > 26:
        keep = set(sorted(keep, key=lambda c: -freq[c])[:26])
    rows = [r for r in rows if all(c in keep for i in ROUTE_FIELDS for c in r[0][i])]
    return keep, rows


def crib(rows):
    """Five letters for free: every report begins "POSnn", and the position field is
    written "N dd mm.m W ddd mm.m", so the hemispheres sit at known columns."""
    f = rows[0][0]
    return {f[0][0]: "P", f[0][1]: "O", f[0][2]: "S", f[1][0]: "N", f[1][6]: "W"}


def fit(rows, ap, restarts=25, rng=random):
    """Hill-climb an alphabet. Scores a candidate on whether the decoded origin and
    destination are real airports whose great circle passes near where the aircraft was."""
    seed = crib(rows)
    keep, rows = alphabet(rows)
    keep |= set(seed)
    if len(keep) > 26 or len(rows) < 20:
        return None
    letters = sorted(keep)
    free = [c for c in letters if c not in seed]

    grouped = collections.defaultdict(list)
    for f, la, lo in rows:
        grouped[(f[3], f[4])].append((la, lo))
    cases = [(o, d, statistics.fmean(p[0] for p in v), statistics.fmean(p[1] for p in v), len(v))
             for (o, d), v in grouped.items()]  # one point per distinct route keeps scoring cheap

    def score(table):
        total = 0.0
        for o, d, la, lo, n in cases:
            a, b = ap.get("".join(table.get(c, "?") for c in o)), ap.get("".join(table.get(c, "?") for c in d))
            if not a or not b or a is b:
                continue
            total += n * (0.25 * (a[RANK] + b[RANK]) / 14
                          + max(0.0, 1.0 - crosstrack(a[LAT], a[LON], b[LAT], b[LON], la, lo) / 200.0))
        return total

    best = None
    for _ in range(restarts):
        spare = [x for x in string.ascii_uppercase if x not in seed.values()]
        table = dict(seed)
        table.update(dict(zip(free, rng.sample(spare, len(free)))))
        current, improved = score(table), True
        while improved:
            improved = False
            for c in free:
                for new in string.ascii_uppercase:
                    old = table[c]
                    if new == old or new in seed.values():
                        continue
                    clash = [k for k, v in table.items() if v == new and k not in seed]
                    table[c] = new
                    if clash:
                        table[clash[0]] = old
                    trial = score(table)
                    if trial > current:
                        current, improved = trial, True
                    else:
                        table[c] = old
                        if clash:
                            table[clash[0]] = new
        if best is None or current > best[0]:
            best = (current, dict(table))
    return best


def on_route(table, rows, ap, limit=100.0):
    """The share of reports whose decoded route passes within limit of the aircraft."""
    hits = total = 0
    for f, la, lo in rows:
        a, b = ap.get("".join(table.get(c, "?") for c in f[3])), ap.get("".join(table.get(c, "?") for c in f[4]))
        total += 1
        if a and b and crosstrack(a[LAT], a[LON], b[LAT], b[LON], la, lo) < limit:
            hits += 1
    return hits, total


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--archive", default=str(ROOT / "archive" / "messages"))
    parser.add_argument("--group", help="one key group, e.g. 05")
    parser.add_argument("--restarts", type=int, default=25)
    parser.add_argument("--repeats", type=int, default=1, help="refit to show run-to-run spread")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    ap = airports()
    groups = records(args.archive)
    if args.group:
        groups = {args.group: groups.get(args.group, [])}
    rng = random.Random(args.seed)

    print(f"ceiling {CEILING}% (known-true routes)   chance {NULL}% (positions shuffled)\n")
    print(f"{'group':>6s}{'train':>7s}{'held out':>10s}{'on-route':>10s}{'objective':>12s}")
    hits = total = 0
    for group, rows in sorted(groups.items()):
        if len(rows) < 30:
            print(f"{group:>6s}{len(rows):>7d}   too few records")
            continue
        pool = rows[:]
        rng.shuffle(pool)
        cut = int(len(pool) * 0.8)
        train, test = pool[:cut], pool[cut:]
        runs = []
        for _ in range(args.repeats):
            got = fit(train, ap, restarts=args.restarts, rng=rng)
            if got:
                runs.append((got[0], got[1], *on_route(got[1], test, ap)))
        if not runs:
            print(f"{group:>6s}{len(train):>7d}   no alphabet found")
            continue
        runs.sort(key=lambda r: -r[0])          # pick by objective, as you would without truth
        best = runs[0]
        hits += best[2]
        total += best[3]
        spread = "" if len(runs) < 2 else f"   spread {runs[-1][0]:.0f}-{runs[0][0]:.0f} over {len(runs)} runs"
        print(f"{group:>6s}{len(train):>7d}{best[3]:>10d}{best[2] * 100 // max(best[3], 1):>9d}%"
              f"{best[0]:>12.1f}{spread}")
    if total:
        print(f"\noverall held-out {hits * 100 // total}% of {total} unseen records "
              f"(chance {NULL}%, ceiling {CEILING}%)")


if __name__ == "__main__":
    main()
