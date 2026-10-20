#!/usr/bin/env python3
"""Attack the origin and destination fields of the obfuscated business-aviation reports.

Label 44 from the XA/GS/ZD datalink prefixes is a monoalphabetic substitution, one
alphabet per leading two-digit key. web/translate.py already decodes the digits - those
fell out of the format's own structure - but fields 3 and 4 are four-letter ICAO codes and
the letters only give way to a dictionary attack. This is that attack, plus the part that
matters more: an honest way to tell whether it worked.

Three numbers frame every result, and none of them is 100%:

Field 0 names the event - POS mostly, with some TOC and TOD - and the crib has to come
from the common one. Getting that wrong is silent: the search still runs, still reports a
plausible objective, and is simply pinned away from the answer.

  ceiling  Some of this traffic is sent in clear, origin and destination included. Over
           those known-true routes the aircraft sits within 100 km of its own great circle
           78% of the time - real flights deviate - so 78% is what a perfect decode scores.
  null     Shuffling positions against routes gives 41%. Business jets near this receiver
           fly routes that pass nearby whatever you claim, so chance is not zero.
  held out Alphabets are fitted on 80% of a group's records and scored on the other 20%.
           Fitting and scoring on the same records flatters the result by a wide margin -
           an early version of this reported 71% that way and 66% held out.

Where it stands: shipped. All nine alphabets are in web/translate.py as BIZJET_LETTERS and
the routes are on screen. The held-out score is 71% against the 78% ceiling, and it was
checks with no geometric blind spot that settled it, not this objective: every one of the
2,902 codes the alphabets produce over 1,492 reports is a real airport, the fields are
recognisably one fleet's network with reciprocal pairs at matching volumes, and where two
independently fitted alphabets read the same aircraft's flight within the same hour they
name the same pair 95% of the time. A wrong alphabet does not do that. The groups that
score lower here fly routes the great-circle test is blind to, not routes read wrongly.
The reports still unread are missing a rare key character, not an alphabet.

The objective does rank candidates correctly: given two alphabets for the same group, the
one scoring higher is the better answer, checked side by side on identical rows. What was
wrong for a long time was the crib, and the symptom was run-to-run spread that looked for
all the world like a bad optimiser. Worth checking the spread column before believing any
change to this file helped.

A stronger objective is still the open idea - scoring a route against an aircraft's whole
ADS-B trajectory rather than one point's distance from a great circle, which would also
break the symmetry that makes a route and its reverse score identically.

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
    """Five letters for free - but only from the right record.

    Field 0 is a three-letter event and a two-digit number, and the event is not always
    POS: each key group also carries a few TOC and TOD reports. Taking the crib from
    whichever record happened to sort first asserts, for a group whose first record is a
    TOC, that T is P and C is S. Three of the five letters are then wrong and the search is
    pinned inside a region that cannot contain the answer - which is what made the same
    group score 148.5 on one shuffle and 39.7 on another, and what I spent four experiments
    blaming on the optimiser. The middle letter agrees either way, POS and TOC both having
    an O, which is just enough to make it look half right.

    So the crib comes from the event the group uses most, which is POS by a wide margin -
    219 reports against 10 in the group that caught me out.

    The other events are worth more than a warning, though: they can be named from
    structure alone, before any alphabet exists, because the letters overlap. TOC shares its
    middle letter with POS - both have an O - and ETA's middle letter is the T that starts
    TOC. So the second-commonest event matching POS in the middle is TOC, handing over T and
    C, and one whose middle matches TOC's first letter is ETA, handing over E and A. A group with no TOC
    gets neither, because guessing there cost more than it paid. That takes the crib from
    five letters to nine where both events are present, and the free alphabet from
    twenty-one letters to seventeen.
    """
    ranked = collections.Counter(f[0][:3] for f, _, _ in rows).most_common()
    pos = ranked[0][0]
    position = next(f[1] for f, _, _ in rows if f[0][:3] == pos)
    out = {pos[0]: "P", pos[1]: "O", pos[2]: "S", position[0]: "N", position[6]: "W"}
    toc = next((e for e, _ in ranked[1:] if e[1] == pos[1] and e != pos), None)
    if toc:
        out.update({toc[0]: "T", toc[2]: "C"})
    eta = next((e for e, _ in ranked[1:] if toc and e[1] == toc[0]), None)
    if eta:
        out.update({eta[0]: "E", eta[2]: "A"})
    # No guessing beyond that. A group with no TOC to anchor on was briefly given its odd
    # event as ETA, three letters on an assumption, and group 09 fell from 62% to 48% for it
    # - the same way of being wrong that the POS crib was, and worth not repeating.
    # a cipher is an injection; drop anything that would claim a letter twice
    seen = {}
    for cipher, plain in out.items():
        if plain not in seen.values() and cipher not in seen:
            seen[cipher] = plain
    return seen


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

    total_records = sum(len(v) for v in groups.values())
    # The archive grows while the receiver runs, so two runs minutes apart see different
    # data, reshuffle differently and are not comparable however fixed the seed is. One
    # record's difference in a group moved a result by 14 points. Print the count.
    print(f"ceiling {CEILING}% (known-true routes)   chance {NULL}% (positions shuffled)")
    print(f"{total_records} records in this snapshot - runs are only comparable at equal counts\n")
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
