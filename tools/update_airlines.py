#!/usr/bin/env python3
"""Rebuild web/airlines.json from Wikipedia's "List of airline codes".

The stats page names airlines by the two-character IATA prefix of the ACARS flight id.
Before this table existed the only source was the VRS "Op" field, which is the
registered owner - often a leasing trustee ("Bank of Utah Trustee") rather than the
airline - and is missing for anything VRS hasn't seen on ADS-B.

IATA designators are reused: the same two characters can belong to a current airline
and several defunct ones, and IATA also issues "controlled duplicates" to two small
carriers in different regions. And ACARS flight ids don't always use the airline's
IATA code (UPS flies as "UP", Delta's ex-Northwest fleet as "NW"). So the file is just
the rows - [iata, icao, name, defunct], current airlines first - and web/airlines.py
decides, preferring the ICAO operator code VRS reports for the aircraft.

    tools/update_airlines.py            fetch and rewrite web/airlines.json

Run it occasionally; the committed file is what the web server uses, so the server
never needs network access for this.
"""
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "airlines.json"
PAGES = ["0–9"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]
URL = "https://en.wikipedia.org/w/index.php?action=raw&title="
UA = "acars-monitor airline table builder (https://github.com/egite/E-s-ACARS-Receiver-and-Display)"


def fetch(page):
    req = urllib.request.Request(URL + urllib.parse.quote(f"List of airline codes ({page})"),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()


def plain(cell):
    """Wikitext cell -> text: drop refs, templates and markup, keep link labels."""
    cell = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", cell, flags=re.S)
    cell = re.sub(r"\{\{[^{}]*\}\}", "", cell)
    cell = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", cell)
    cell = re.sub(r"<[^>]+>", " ", cell)
    cell = cell.replace("''", "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", cell).strip()


def rows(text):
    m = re.search(r"<onlyinclude>(.*)</onlyinclude>", text, re.S)
    body = m.group(1) if m else text
    for chunk in body.split("\n|-")[1:]:
        cells = [line[1:] for line in chunk.strip("\n").split("\n") if line.startswith("|")]
        if len(cells) >= 3:
            yield (cells + [""] * 6)[:6]


def main():
    airlines = []
    for page in PAGES:
        for iata, icao, name, _call, _country, comments in rows(fetch(page)):
            iata, icao = plain(iata).rstrip("*").upper(), plain(icao).upper()
            defunct = name.strip().startswith("''") or "defunct" in comments.lower()
            name = plain(name)
            if name and (re.fullmatch(r"[A-Z0-9]{2}", iata) or re.fullmatch(r"[A-Z]{3}", icao)):
                airlines.append([iata if len(iata) == 2 else None, icao if len(icao) == 3 else None, name, defunct])
    airlines.sort(key=lambda a: (a[3], a[0] or "", a[1] or ""))  # current airlines first
    OUT.write_text("[\n" + ",\n".join(json.dumps(a, ensure_ascii=False) for a in airlines) + "\n]\n")
    print(f"{len(airlines)} airlines -> {OUT}")

if __name__ == "__main__":
    main()
