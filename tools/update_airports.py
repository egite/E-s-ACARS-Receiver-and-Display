#!/usr/bin/env python3
"""Rebuild web/airports.json from the OurAirports dataset (public domain).

Several airline formats carry the origin and destination as four-letter ICAO codes, and
in the obfuscated ones those fields are the hardest part to recover: the digits fall out
of the format's own structure, but the letters only give way to a dictionary attack, and
an attack is only as good as the dictionary. Scoring against codes harvested from our own
plaintext was not good enough - it cannot tell a wrong letter from an airport this
receiver has simply never heard from, so a Southwest flight decoded happily to Flying
Cloud and Hays Regional and the score went up.

Each row is [icao, iata, name, city, lat, lon, rank]. The city is for display - a decoded
route reads better as "KSLC Salt Lake City -> KJFK New York" than as two codes - and comes
from the dataset's municipality, cut at any parenthetical so Paris does not arrive as
"Paris (Roissy-en-France, Val-d'Oise)". The rank is what makes the dictionary
sharp rather than merely large: 1-3 for small, medium and large, plus 4 if the airport has
scheduled airline service. KDTW outranks KDET 7 to 2, which is the difference between
decoding a Southwest flight into Detroit Metropolitan and into Coleman A. Young Municipal.
The coordinates matter just as much - they turn "is this a real code" into "does this
route match where the aircraft actually flew", which is a check the message cannot fake.

Heliports, seaplane bases, balloonports and closed fields are dropped, as is anything
without a four-character ICAO or GPS code, which takes 86,000 rows down to about 32,000.

    tools/update_airports.py            fetch and rewrite web/airports.json

Run it occasionally; the committed file is what the web server uses, so the server never
needs network access for this.
"""
import csv
import io
import json
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "airports.json"
URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
UA = "acars-monitor airport table builder (https://github.com/egite/E-s-ACARS-Receiver-and-Display)"
SIZE = {"small_airport": 1, "medium_airport": 2, "large_airport": 3}


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8")


def main():
    airports = []
    for row in csv.DictReader(io.StringIO(fetch())):
        code = (row["icao_code"] or row["gps_code"] or "").strip().upper()
        if row["type"] not in SIZE or len(code) != 4 or not code.isalnum() or not row["latitude_deg"]:
            continue
        rank = SIZE[row["type"]] + (4 if row["scheduled_service"] == "yes" else 0)
        city = (row["municipality"] or "").split(" (")[0].strip()[:28]
        airports.append([code, (row["iata_code"] or "").strip().upper() or None, row["name"][:48],
                         city or None, round(float(row["latitude_deg"]), 3),
                         round(float(row["longitude_deg"]), 3), rank])
    airports.sort()
    OUT.write_text("[\n" + ",\n".join(json.dumps(a, ensure_ascii=False) for a in airports) + "\n]\n")
    print(f"{len(airports)} airports -> {OUT}")


if __name__ == "__main__":
    main()
