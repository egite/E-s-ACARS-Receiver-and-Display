"""Where an ICAO code is, for the routes that appear in decoded messages.

web/airports.json is generated from the public-domain OurAirports dataset by
tools/update_airports.py: rows of [icao, iata, name, city, lat, lon, rank].

Only the city is loaded, and only when something first asks for one, so a receiver that
never decodes a route never pays for the table: 2.4 MB of JSON becomes a 30,000-entry
mapping costing about 17 MB of RSS, which is why it is not loaded at import.
"""
import json
from pathlib import Path

PATH = Path(__file__).resolve().parent / "airports.json"
_city = _iata = None


def _load():
    global _city, _iata
    if _city is None:
        rows = json.loads(PATH.read_text())
        _city = {a[0]: a[3] for a in rows if a[3]}
        # IATA codes are reused by small fields, so the busiest airport wins the code.
        _iata = {}
        for a in sorted((a for a in rows if a[1] and a[3]), key=lambda a: a[6]):
            _iata[a[1]] = a[3]


def city(code):
    """The town an ICAO code serves, or None if it isn't an airport we know."""
    _load()
    return _city.get(code)


def city_iata(code):
    """The same for a three-letter IATA code, which some airlines use for routes."""
    _load()
    return _iata.get(code)
