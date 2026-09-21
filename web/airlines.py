"""Airline names for ACARS flight ids.

web/airlines.json is generated from Wikipedia by tools/update_airlines.py: rows of
[iata, icao, name, defunct], current airlines first.
"""
import json
from collections import Counter
from pathlib import Path

# IATA -> ICAO airline designators, used to turn ACARS flight ids (UA1220)
# into ADS-B callsigns (UAL1220) for VRS matching.
IATA_TO_ICAO = {
    "AA": "AAL", "AC": "ACA", "AF": "AFR", "AM": "AMX", "AS": "ASA", "AV": "AVA",
    "B6": "JBU", "BA": "BAW", "CM": "CMP", "CV": "CLX", "DL": "DAL", "EI": "EIN",
    "EK": "UAE", "F9": "FFT", "FX": "FDX", "G4": "AAY", "HA": "HAL", "KL": "KLM",
    "LH": "DLH", "MQ": "ENY", "NK": "NKS", "OH": "JIA", "OO": "SKW", "PT": "SWQ",
    "QX": "QXE", "SY": "SCX", "UA": "UAL", "WN": "SWA", "WS": "WJA", "YV": "ASH",
    "YX": "RPA", "ZW": "AWI", "5X": "UPS", "9E": "EDV", "K4": "CKS", "5Y": "GTI",
    "XP": "CXP", "MX": "MXY", "2Q": "ACN", "LX": "SWR", "NH": "ANA", "JL": "JAL",
    "NW": "DAL",  # Delta's ex-Northwest fleet still reports NW flight ids (NW0892 flies as DAL892)
}

# Prefixes that aren't the airline's IATA code, for naming only (callsign unverified).
PREFIX_OPERATOR = {**IATA_TO_ICAO, "UP": "UPS"}  # UPS is IATA 5X; its ACARS ids read UP0056

# Flight-id prefixes that aren't airlines at all: business jets using a datalink
# service provider send these with placeholder flight numbers (XA0001, GS0000, ZD0001).
# The IATA table would otherwise call them Tianjin Airlines and Ewa Air.
NOT_AIRLINES = {"XA", "GS", "ZD"}

_rows = json.loads((Path(__file__).resolve().parent / "airlines.json").read_text())
BY_ICAO, BY_IATA = {}, {}
for _iata, _icao, _name, _defunct in _rows:
    if _icao:
        BY_ICAO.setdefault(_icao, _name)
    if _iata:
        BY_IATA.setdefault(_iata, []).append((_icao, _name, _defunct))


def airline_name(prefix, operators=()):
    """Name for a two-character flight-id prefix.

    `operators` is a Counter of (OpIcao, Op) from VRS for aircraft seen flying under the
    prefix. When most of them agree on an ICAO operator that decides it: it's right even
    when the registered owner (Op) is a leasing trustee, and it follows airlines whose
    ACARS prefix isn't their IATA code. Otherwise the IATA table.
    The curated PREFIX_OPERATOR beats both - that also settles codes that IATA has
    issued more than once (DL was Deutsche Luft Hansa before it was Delta).
    """
    if prefix in NOT_AIRLINES:
        return "Business aviation"
    if BY_ICAO.get(PREFIX_OPERATOR.get(prefix)):
        return BY_ICAO[PREFIX_OPERATOR[prefix]]
    operators = operators or {}
    by_icao = {}
    for (icao, op), n in operators.items():
        if icao:
            by_icao.setdefault(icao, Counter())[op] += n
    if by_icao:
        icao, ops = max(by_icao.items(), key=lambda kv: kv[1].total())
        if ops.total() * 2 > sum(operators.values()):
            return BY_ICAO.get(icao) or ops.most_common(1)[0][0]
    candidates = BY_IATA.get(prefix, [])
    current = [name for _icao, name, defunct in candidates if not defunct]
    if current:
        return current[0]
    if operators:
        return max(operators.items(), key=lambda kv: kv[1])[0][1]
    return candidates[0][1] if candidates else None

