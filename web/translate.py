"""Plain-English translation of normalized ACARS/VDL2 messages.

translate(msg, raw) returns {"category", "summary", "details"}:
  category: atc | position | flight | request | crew | maintenance | data | encoded | link | other
  summary:  one English sentence
  details:  list of short "what we could read" strings (may be empty)

Only fields whose meaning is well established are interpreted; anything else is
described by what kind of message it is, and the original text stays available.

message_type(msg) files each message under one entry of MESSAGE_TYPES (per source), which is
what the "Message types" dialog lets people pick from.
"""
import airports
import re

# (type, label, description, shown by default, sources it can occur on)
MESSAGE_TYPE_DEFS = [
    ("atc", "ATC datalink (CPDLC)", "Pilot–controller messages: requests, WILCO/ROGER, connect/disconnect", True, "AV"),
    ("position", "Position reports", "Automated and flight-computer position/progress reports", True, "AV"),
    ("flight", "Flight events", "Takeoff/landing, ETA, flight-computer reports and replies", True, "AV"),
    ("request", "Crew requests", "ATIS, weather and landing-data requests", True, "AV"),
    ("crew", "Crew & dispatch text", "Free-text messages between crew, dispatch and maintenance", True, "AV"),
    ("maintenance", "Maintenance alerts", "Fault reports and maintenance messages", True, "AV"),
    ("data", "Engine & aircraft data", "Condition monitoring, weather observations, health reports", True, "AV"),
    ("encoded", "Encoded airline data", "Proprietary encoded or compressed airline messages", True, "AV"),
    ("linktest", "Link tests & acknowledgements", "ACARS messages without text (_d, Q0, …)", False, "AV"),
    ("status", "Ground station & link status", "Ground station squitters and datalink status advisories", False, "AV"),
    ("partial", "Partial message blocks", "Pieces of multi-block messages before reassembly", False, "AV"),
    ("ack", "Link acknowledgements", "VDL2 supervisory frames (Receive Ready etc.)", False, "V"),
    ("handoff", "Ground station handoffs", "VDL2 XID frames, often carrying the aircraft's position", False, "V"),
    ("connection", "Connection control", "VDL2 connect/disconnect frames (DISC, DM, SABM, UA…)", False, "V"),
    ("network", "ATN network traffic", "X.25 / CLNP network frames carried over VDL2", False, "V"),
    ("other", "Unclassified", "Anything that doesn't fit the types above", True, "AV"),
]
SOURCE_CODES = {"ACARS": "A", "VDL2": "V"}
MESSAGE_TYPES = [
    {"key": f"{source}:{t}", "source": source, "type": t, "label": label, "description": desc, "default": default}
    for source, code in SOURCE_CODES.items()
    for t, label, desc, default, sources in MESSAGE_TYPE_DEFS if code in sources
]
CONTENT_TYPES = {"atc", "position", "flight", "request", "crew", "maintenance", "data", "encoded"}
STATUS_LABELS = {"SQ", "SA", "5V"}


def message_type(msg):
    """Type key such as 'ACARS:position' or 'VDL2:handoff'; 'other' is the catch-all bucket."""
    kind, label = msg.get("kind"), msg.get("label")
    category = (msg.get("english") or {}).get("category")
    if kind == "partial":
        t = "partial"
    elif kind == "xid":
        t = "handoff"
    elif kind == "atn":
        t = "network"
    elif label in STATUS_LABELS:
        t = "status"
    elif kind == "link" and msg.get("source") == "VDL2" and label is None:
        t = {"S": "ack", "U": "connection"}.get(msg.get("frame"), "other")
    elif kind == "link" or category == "link":
        t = "linktest"
    else:
        t = category if category in CONTENT_TYPES else "other"
    return f"{msg.get('source')}:{t}"

# ------------------------------------------------------------------ helpers

def hhmmss(s):
    s = s.strip()
    if re.fullmatch(r"\d{6}", s):
        return f"{s[0:2]}:{s[2:4]}:{s[4:6]} UTC"
    if re.fullmatch(r"\d{4}", s):
        return f"{s[0:2]}:{s[2:4]} UTC"
    return s


def latlon(lat, lon):
    return f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def feet(v):
    try:
        return f"{int(v):,} ft"
    except (TypeError, ValueError):
        return None


def fl(v):
    v = v.strip()
    return f"FL{int(v)}" if v.isdigit() else v


def sentence(s):
    s = re.sub(r"\s+", " ", s.strip())
    return s[:1].upper() + s[1:].lower() if s else s


def pos_detail(msg):
    p = msg.get("position")
    return latlon(p["lat"], p["lon"]) if p else None


ICAO_AP = r"[A-Z]{4}"


def route_in(text):
    m = re.search(rf"(?<![A-Z])(K[A-Z]{{3}}|C[A-Z]{{3}}|P[A-Z]{{3}}|M[A-Z]{{3}})[ ,/]?(K[A-Z]{{3}}|C[A-Z]{{3}}|P[A-Z]{{3}}|M[A-Z]{{3}})(?![A-Z])", text)
    return (m.group(1), m.group(2)) if m and m.group(1) != m.group(2) else None


def wordlike(word):
    """Pronounceable-ish: has a vowel and no long consonant runs (rejects encoded gibberish)."""
    w = word.upper()
    if len(w) <= 4:  # abbreviations (MNR, PDX, WX) are normal in crew text
        return True
    return len(w) <= 14 and re.search(r"[AEIOUY]", w) and not re.search(r"[^AEIOUY\d]{5,}", w)


def free_text_lines(text):
    """Lines (or comma-separated fields) that read like human-written words rather than data.

    Returned with their original spacing: reflow() reads the line length to tell a
    hard wrap from a break the crew typed, so stripping here would lose that."""
    out = []
    for line in re.split(r"[\r\n]+", text):
        for raw in (line.split(",") if "," in line else [line]):
            chunk = raw.strip()
            words = re.findall(r"[A-Za-z]{2,}", chunk)
            letters = sum(len(w) for w in words)
            if len(words) >= 2 and letters >= 8 and letters / max(len(chunk.replace(" ", "")), 1) >= 0.75 \
                    and not re.search(r"[a-z]", chunk) and sum(map(bool, map(wordlike, words))) >= 0.8 * len(words):
                out.append(raw)
    return out


MCDU_COLUMNS = 24  # width of the free-text box crews type into on these fleets

# Everyday words and the shorthand crews lean on, used only to tell a wrapped word from
# two whole ones when a line happens to end flush with the last column. Deliberately
# excludes single letters and word endings (S, T, ED, ING), which are exactly what the far
# half of a wrap looks like, and anything that is also a prefix of a longer word a crew
# might type out (MIN of MINUTES, CONT of CONTINUE).
COMMON_WORDS = set("""
ABOUT AFTER ALL ALSO AN AND ANY ARE AS AT BACK BE BEEN BEST BETTER BUT BY CAN DO DOWN
EVEN FEW FOR FROM GET GO GOOD GOT GREAT HAS HAVE HERE HOW IF IN INTO IS IT ITS JUST KEEP
KNOW LAST LET LIKE LOOK MAKE ME MORE MOST MUCH MY NEAR NEED NEW NEXT NICE NO NOT NOW OF
OFF OKAY ON ONE ONLY OR OUR OUT OVER PLEASE PLS REALLY RIGHT SAME SEE SEEMS SEND SHOULD
SINCE SO SOME SOON STILL SURE TAKE THAN THANK THANKS THAT THE THEM THEN THERE THEY THIS
THRU TIME TO TOO UP US VERY VIA WANT WAS WAY WE WELL WERE WHAT WHEN WHERE WHICH WHILE WHY
WILL WITH WOULD YES YOU YOUR
ATC CHOP LGT LT MOD OCNL THX TURB USE WX
""".split())


def reflow(lines):
    """Re-join ACARS free text that the aircraft hard-wrapped at the field width.

    The break falls wherever the character count runs out, mid-word as often as not,
    so CONSIOSNESS arrives as "CON" then "SIOSNESS". A line filled to the last column
    is one of those wraps and glues straight onto the next; a short line is where the
    crew stopped typing, and gets a space. Lines must arrive unstripped - their length
    is the whole signal - and a block that never reaches the last column is left alone,
    which keeps us from welding words together in text we only think was wrapped.
    """
    lines = [l for l in lines if l.strip()]
    if not lines:
        return ""
    wrapped = max(len(l) for l in lines) == MCDU_COLUMNS
    out = lines[0]
    for prev, line in zip(lines, lines[1:]):
        # A line can also end flush with the last column by chance, right on a word
        # boundary. Punctuation there settles it; otherwise an everyday word on either
        # side of the seam means we would be welding two whole words together.
        tail = re.findall(r"[A-Z0-9]+", prev)
        head = re.findall(r"[A-Z0-9]+", line)
        glue = (wrapped and len(prev) == MCDU_COLUMNS and prev[-1] not in ".,!?;:"
                and not ({tail[-1] if tail else ""} | {head[0] if head else ""}) & COMMON_WORDS)
        out += ("" if glue else " ") + line
    return re.sub(r" {2,}", " ", out).strip()


def looks_encoded(text):
    t = text.replace("\r", "").replace("\n", "")
    if len(t) < 16:
        return False
    odd = sum(1 for c in t if c in "()|&;$%'`<>{}[]^~*+=\\!\"#@:")
    mixed_case = re.search(r"[a-z]", t) and re.search(r"[A-Z]", t)
    base64ish = re.fullmatch(r"[A-Za-z0-9+/=]{40,}", t) and mixed_case
    tokens = re.findall(r"[A-Za-z]{4,}", t)
    gibberish = tokens and sum(not wordlike(x) for x in tokens) / len(tokens) > 0.4 and not free_text_lines(text)
    if digit_ratio(t) > 0.25:  # numeric telemetry, not an encoded blob
        return odd / len(t) > 0.15
    return odd / len(t) > 0.08 or bool(base64ish) or bool(gibberish) or \
        (mixed_case and not free_text_lines(text) and len(t) > 30)


def digit_ratio(text):
    t = re.sub(r"\s", "", text)
    return sum(c.isdigit() for c in t) / len(t) if t else 0


# A four-letter token in text we generated ourselves is an ICAO code; one inside the
# quotes of a crew message is whatever the crew typed, and is left alone.
ICAO_TOKEN = re.compile(r"(?<![A-Z0-9])([A-Z]{4})(?![A-Z0-9])")
# Three letters are too common in aviation shorthand (ETA, FOB, TAS) to name on sight, so
# IATA codes are only recognised either side of a route arrow, where nothing else appears.
IATA_ROUTE = re.compile(r"(?<![A-Z0-9])([A-Z]{3}) → ([A-Z]{3})(?![A-Z0-9])")
QUOTED = re.compile(r"“[^”]*”")


def name_airports(text):
    """Put the town after an ICAO code, so a route reads as places rather than codes."""
    if not text:
        return text
    out, at = [], 0
    for quote in QUOTED.finditer(text):           # step over the crew's own words
        out.append(_name(text[at:quote.start()]))
        out.append(quote.group(0))
        at = quote.end()
    out.append(_name(text[at:]))
    return "".join(out)


def _name(part):
    return IATA_ROUTE.sub(_route, ICAO_TOKEN.sub(_city, part))


def _city(match):
    town = airports.city(match.group(1))
    return f"{match.group(1)} {town}" if town else match.group(1)


def _route(match):
    def one(code):
        town = airports.city_iata(code)
        return f"{code} {town}" if town else code
    return f"{one(match.group(1))} → {one(match.group(2))}"


def result(category, summary, details=None, generic=False, position=None):
    """generic=True marks fallback descriptions that didn't recognise the message format.

    position is for formats whose coordinates only appear once decoded, so parse_position
    can't see them in the raw text; the server range-checks it before using it."""
    out = {"category": category, "summary": name_airports(summary),
           "details": [name_airports(d) for d in (details or []) if d]}
    if position:
        out["position"] = position
    if generic:
        out["generic"] = True
    return out


# --------------------------------------------------------------------- CPDLC

CPDLC_PHRASES = {
    "WILCO": "Crew will comply with the ATC instruction (WILCO)",
    "ROGER": "Crew acknowledged the ATC message (ROGER)",
    "UNABLE": "Crew cannot comply with the ATC instruction (UNABLE)",
    "STANDBY": "Crew asked ATC to stand by",
    "AFFIRM": "Crew answered yes (AFFIRM)",
    "NEGATIVE": "Crew answered no (NEGATIVE)",
    "LOGICAL ACKNOWLEDGEMENT": "Avionics acknowledged receipt of a CPDLC message",
}


def walk(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, path + (i,))
    else:
        yield path, obj


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def cpdlc_value(path, value):
    key = str(path[-1]).lower()
    if "flight_level" in key or key == "fl":
        return f"FL{value}"
    if "alt" in key and isinstance(value, (int, float)):
        return f"{value:,} ft"
    if "speed" in key or key in ("mach",):
        return f"{key.replace('_', ' ')} {value}"
    return str(value)


def translate_cpdlc(raw):
    if raw is None:
        return None
    for direction, who in (("atc_downlink_msg", "Crew"), ("atc_uplink_msg", "ATC")):
        m = find_key(raw, direction)
        if not isinstance(m, dict):
            continue
        phrases = []
        for elem_key, elem in m.items():
            if not elem_key.startswith("atc_") or not isinstance(elem, dict):
                continue
            label = elem.get("choice_label")
            if not label:
                continue
            values = [cpdlc_value(p, v) for p, v in walk(elem.get("data") or {})
                      if str(p[-1]) not in ("choice", "choice_label")]
            if label.upper() in CPDLC_PHRASES and not values:
                phrases.append(CPDLC_PHRASES[label.upper()])
                continue
            if label.lower() == "[versionnumber]":
                phrases.append("Avionics reported their CPDLC version while connecting to ATC")
                continue
            text = sentence(label)
            for v in values:
                if "[" in text:
                    text = re.sub(r"\[[^\]]+\]", lambda _: v, text, count=1)
            phrases.append(f"{who}: “{text}”" if who == "ATC" else f"Crew request/report to ATC: “{text}”")
        if phrases:
            return result("atc", "; ".join(phrases), ["Controller–pilot datalink (CPDLC)"])
    msg_type = find_key(raw, "msg_type") or ""
    if "disconnect" in msg_type:
        return result("atc", "CPDLC session with ATC closed by the aircraft (disconnect)")
    if "cpdlc" in msg_type:
        return result("atc", "CPDLC message (contents could not be decoded)")
    if "adsc" in msg_type:
        lat, lon = find_key(raw, "lat"), find_key(raw, "lon")
        alt = find_key(raw, "alt")
        details = [latlon(lat, lon) if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else None,
                   feet(alt) if alt is not None else None]
        return result("position", "ADS-C automatic position report to ATC", details)
    return None


# ------------------------------------------------------------- FMC reports

FMC_CODES = {
    "PER": "Flight computer performance report",
    "PRG": "Flight progress report",
    "RESREQ": "Flight computer datalink acknowledgement",
    "REQPWI": "Crew requested predicted winds for the route",
    "PWI": "Predicted winds uplink",
    "REQFPN": "Crew requested a flight plan uplink",
    "FPN": "Flight plan",
    "INR": "Flight computer initialization request",
    "REQPOS": "Position report requested",
}


# Replies and requests between the flight computer and the airline, as ARINC 702A lays them
# out. A reply is RES (response) or REJ (rejection) followed by the IMI of the uplink it
# answers. A response carries AK (acknowledged: received) or AC (accepted: loaded) and a
# stimulus code; a rejection carries the time and an error type code. Only codes defined in
# the specification that occur in this receiver's traffic are named; the others (RESREQ's
# 470, RESPWI's 478 and 716 among them) are implementation-specific and shown as numbers.
FMC_UPLINKS = {"PWI": "predicted winds", "PWM": "wind update", "FPN": "flight plan", "POS": "position request",
               "PER": "performance data", "PRG": "progress request", "INR": "initialization data",
               "REQ": "request", "TWX": "text message", "MSG": "message", "LDI": "load data"}
FMC_STIMULUS = {"071": "company uplink acknowledged", "073": "company route data accepted",
                "085": "company wind data accepted", "091": "descent forecast accepted",
                "115": "uplink asking for a downlink", "301": "multi-level wind data accepted"}
FMC_REJECTION = {"003": "syntax error", "023": "unsolicited uplink", "027": "not allowed in descent",
                 "053": "not allowed in this flight phase", "101": "buffer full",
                 "109": "invalid format or range", "130": "no matching fix in the route",
                 "203": "not allowed without a valid position"}


def translate_fmc_exchange(body):
    m = re.match(r"(RES|REJ)([A-Z]{3})(?:/(AK|AC|RJ),(\d{2,3})|,\d{6},(\d{3}))?(?=[/,]|[0-9A-F]{4}$|$)", body)
    if m and (m[3] or m[5] or m[2] in FMC_UPLINKS):
        what = FMC_UPLINKS.get(m[2], f"{m[2]} uplink")
        if m[1] == "REJ" or m[3] == "RJ":
            reason = FMC_REJECTION.get(m[5] or "")
            code = m[5] or m[4]
            return result("flight", f"Flight computer rejected the {what} sent from the ground",
                          [f"Reason: {reason}" if reason else f"Error code {code}" if code else None])
        verb = "accepted" if m[3] == "AC" or not m[3] else "acknowledged"
        # Two-digit codes may be the hexadecimal form the specification also allows; not guessed at.
        meaning = FMC_STIMULUS.get(m[4]) if m[4] and len(m[4]) == 3 else None
        return result("flight", f"Flight computer {verb} the {what} sent from the ground",
                      [meaning[0].upper() + meaning[1:] if meaning else f"Code {m[4]}" if m[4] else None])
    m = re.match(r"REQPWI/", body)
    if m:
        fields = dict((f[:2], f[2:]) for f in body.split("/")[1:] if len(f) > 2)
        levels, _, points = fields.get("WQ", "").partition(":")
        details = [f"Winds at FL{', FL'.join(levels.split('.'))}" if levels else None,
                   f"Along {plural(len(points.split('.')), 'waypoint')}: {points.replace('.', ' ')}" if points else None,
                   f"Descent forecast from FL{fields['DQ']}" if re.fullmatch(r"\d{3}", fields.get("DQ", "")) else None,
                   f"Weather for {fields['WR'].replace('.', ', ')}" if fields.get("WR") else None]
        return result("flight", "Crew requested predicted winds for the route", details)
    return None


# ATS Facilities Notification, ARINC 622 chapter 3: the logon that tells an ATC centre which
# datalink services the aircraft has (ADS-C, CPDLC) before either is used. Sent under B0, or
# under H1 when the flight computer originates it:
#
#   /KUSA.AFN/FMHEJA18,..N18QS,A13FC2,131209/FPON40033W105048,1/FCOADS,01/FCOATC,01AEEC
#
# Header: centre address, FMH, flight, registration, ICAO address, time. FN_CON (contact)
# adds FPO, the position in degrees and tenths of minutes and the active-centre flag, and an
# FCO per application and version. Over the archive the position sits 0.43 km from ADS-B at
# the median (90% within 1.2 km), and the ICAO address matches the message's own in all 66
# that carry both.
AFN_APPS = {"ADS": "ADS-C", "ATC": "CPDLC", "AIF": "application interface"}


def translate_afn(text):
    # "/KUSA.AFN/", "KUSA.AFN/" or, from a peripheral, "#M1B/B0 KUSA.AFN/" (MFI before the centre)
    m = re.search(r"(?:^|[/ ])(\w{4,7})\.AFN/FMH([^,/]*),\.*([^,/]*)(?:,([0-9A-F]{6})?)?(?:,(\d{6}))?", text)
    if not m:
        return None
    centre, flight, reg, icao, at = m.groups()
    details = [f"Flight {flight.strip()}" if flight.strip() else None, reg.replace(" ", "") or None,
               f"ICAO {icao}" if icao else None, f"Sent {hhmmss(at)}" if at else None]
    pos = re.search(r"/FPO([NS])(\d\d)(\d\d)(\d)([EW])(\d{3})(\d\d)(\d),(\d)", text)
    position = None
    if pos:
        lat = (int(pos[2]) + (int(pos[3]) + int(pos[4]) / 10) / 60) * (-1 if pos[1] == "S" else 1)
        lon = (int(pos[6]) + (int(pos[7]) + int(pos[8]) / 10) / 60) * (-1 if pos[5] == "W" else 1)
        position = {"lat": lat, "lon": lon, "src": "AFN logon"}
        details.append(latlon(lat, lon))
    apps = [f"{AFN_APPS.get(a, a)} v{int(v)}" for a, v in re.findall(r"/FCO([A-Z]{3}),(\d\d)", text)]
    if apps:
        details.append("Offers " + ", ".join(apps))
    return result("atc", f"Datalink logon to ATC centre {centre}" if pos or apps else f"Datalink logon message ({centre})",
                  details, position=position)


def translate_arinc620(label, text):
    """Standard downlinks whose text ARINC 620 fixes. The decoder has already taken off the
    message number and flight identifier (the specification's characters 1-10), so the text
    starts at its character 11."""
    if label == "Q3":  # Clock Update Advisory: time before reset, space, time after
        m = re.fullmatch(r"(\d\d)(\d\d) (\d\d)(\d\d)", text)
        if m:
            return result("flight", f"Aircraft clock reset from {m[1]}:{m[2]} to {m[3]}:{m[4]} UTC")
    if label == "QF":  # OFF/Destination Report: departure (IATA), OFF time, destination
        m = re.match(r"([A-Z0-9 ]{3})(\d\d)(\d\d)([A-Z0-9 ]{3})", text)
        if m and int(m[2]) < 24 and int(m[3]) < 60:
            dep, dest = m[1].strip(), m[4].strip()
            return result("flight", "Took off" + (f" from {dep}" if dep else "") + f" at {m[2]}:{m[3]} UTC" +
                          (f", bound for {dest}" if dest else ""))
    if label == "Q2":  # ETA Report: destination (IATA), ETA, fuel quantity in airline units
        m = re.match(r"([A-Z0-9 ]{3})(\d\d)(\d\d)([ \d]{4})?(.*)", text)
        if m and int(m[2]) < 24 and int(m[3]) < 60:
            dest = m[1].strip() or (re.search(r"/DS (\w{3,4})", m[5]) or [None, None])[1]
            return result("flight", f"ETA {m[2]}:{m[3]} UTC" + (f" at {dest}" if dest else ""),
                          [f"Fuel {int(m[4])} (airline units)" if m[4] and m[4].strip() else None])
    if label == "Q7":  # Delay Message: free text
        return result("flight", "Delay report" + (f": “{reflow([text])}”" if text.strip() else ""))
    if label == "HX":  # Undelivered Uplink Report: optional /addresses. then free text
        body = re.sub(r"^/[^.]*\.", "", text).strip()
        return result("link", "An uplink reached the aircraft but could not be passed to the system it was for",
                      [f"“{body}”" if body else None])
    if label == "5D":  # ATIS Request: the request is free text
        airport = re.search(r"(?<![A-Z])(" + ICAO_AP + r")", text)
        return result("request", f"Crew requested the ATIS for {airport[1]}" if airport else "Crew requested an ATIS")
    return None


def translate_posn(body):
    """ARINC 702 flight-computer position report: POSNlatlon,wpt,time,FL,next,eta,nextnext,temp,wind,..."""
    m = re.search(r"POSN([NS]?)(\d{2})(\d{2})(\d)([EW])(\d{3})(\d{2})(\d),([^,]*),(\d{6}),(\d{2,3}),([^,]*),(\d{6})?,([^,]*),([MP]\d{1,2}),(\d{4,6})", body)
    if not m:
        return None
    lat = (int(m[2]) + (int(m[3]) + int(m[4]) / 10) / 60) * (-1 if m[1] == "S" else 1)
    lon = (int(m[6]) + (int(m[7]) + int(m[8]) / 10) / 60) * (-1 if m[5] == "W" else 1)
    wpt, t_over, level, nxt, eta, then, temp, wind = m[9], m[10], m[11], m[12], m[13], m[14], m[15], m[16]
    temp_c = int(temp[1:]) * (-1 if temp[0] == "M" else 1)
    wdir, wspd = wind[:3], int(wind[3:])
    if re.fullmatch(r"[NS]\d{5}[EW]\d{6}", wpt):  # waypoint given as coordinates
        wpt = latlon((int(wpt[1:3]) + int(wpt[3:6]) / 600) * (-1 if wpt[0] == "S" else 1),
                     (int(wpt[7:10]) + int(wpt[10:13]) / 600) * (-1 if wpt[6] == "W" else 1))
    summary = f"Position report: over {wpt or latlon(lat, lon)} at {hhmmss(t_over)}, {fl(level)}"
    details = [latlon(lat, lon), (f"Next waypoint {nxt}" + (f" at {hhmmss(eta)}" if eta else "") + (f", then {then}" if then else "")) if nxt else None,
               f"Outside air {temp_c}°C", f"Wind {wdir}° at {wspd} kt"]
    # ARINC 702A puts fuel remaining after the wind, in the airline's units. Here they are
    # hundreds of pounds: over 5,342 consecutive pairs of reports from one flight the value
    # falls at 55-61 an hour for every narrowbody fleet (5,500-6,100 lb/h, an A320's or a
    # 737's burn), twice that for the Hawaiian and FedEx widebodies, and rises in two.
    fuel = re.match(r",(\d{2,4})(?=[0-9A-F]{4}$|[,/])", body[m.end():].strip())
    if fuel:
        details.append(f"Fuel remaining {int(fuel[1]) * 100:,} lb")
    route = re.search(r"/RI:DA:(" + ICAO_AP + r"):AA:(" + ICAO_AP + r")", body)
    if route:
        details.insert(0, f"Flight {route.group(1)} → {route.group(2)}")
    return result("position", summary, details)


# ------------------------------------------------------------ airline formats

UA_CODES = {
    "B6": "Crew requested landing performance data",
    "R3": "Crew requested a Howgozit (fuel and schedule progress) report",
    "C3": "Crew requested a gate assignment",
    "C4": "Crew message to dispatch",
    "ET": "Estimated arrival time report",
    "33": "Avionics system configuration report",
    "14": "Takeoff (OFF) report",
    "13": "Pushback (OUT) report",
    "15": "Landing (ON) report",
    "16": "Gate arrival (IN) report",
    "71": "Message to maintenance control",
}


def translate_united_slash(msg):
    short = re.match(r"/(\w\w) ([A-Z]{3})([A-Z]{3})(?:\s+(.*))?$", msg["text"], re.S)
    if short and short.group(1) in UA_CODES:
        code, orig, dest, rest = short.groups()
        rest = rest or ""
        details = [f"Flight {orig} → {dest}"]
        if code == "C4" and rest.strip():
            return result("crew", f"Crew message to dispatch: “{reflow(rest.splitlines())}”", details)
        summary = UA_CODES[code]
        rwy = re.match(r"([A-Z]{3}) R(\d\d[LRC]?)", rest)
        if code == "B6" and rwy:
            summary += f" for {rwy.group(1)} runway {rwy.group(2)}"
        if code == "C3":
            summary = f"Crew requested a gate assignment at {dest}"
        cat = "request" if code in ("B6", "R3", "C3") else "flight" if code in ("ET", "13", "14", "15", "16") else "data"
        return result(cat, summary, details)
    m = re.match(r"/(\w\w) (.{2,18}?)\s*/ ?(" + ICAO_AP + r")? ?(" + ICAO_AP + r")? ?(\d\d)? ?(\d{6})?(.*)", msg["text"], re.S)
    if not m:
        return None
    code, desc, orig, dest, _day, t, rest = m.groups()
    details = [f"Flight {orig} → {dest}" if orig and dest else None, f"Sent {hhmmss(t)}" if t else None]
    # Free-text messages to dispatch: header line, then the crew's words
    body = re.split(r"[\r\n]+", rest or "")[1:]
    if "DISP" in desc.upper() and any(line.strip() for line in body):
        return result("crew", f"Crew message to dispatch: “{reflow(body)}”", details)
    if code == "C3" or "GATE" in desc.upper():
        return result("request", f"Crew requested a gate assignment at {dest}" if dest else UA_CODES["C3"], details)
    if code == "R3" or "HOWGOZIT" in desc.upper():
        return result("request", UA_CODES["R3"], details)
    summary = UA_CODES.get(code) or sentence(desc)
    rwy = re.search(rf"({ICAO_AP}) R(\d\d[LRC]?)", rest or "")
    if rwy:
        summary += f" for {rwy.group(1)} runway {rwy.group(2)}"
    eon = re.search(r"EON (\d{4})", rest or "")
    if eon:
        summary += f": ETA {hhmmss(eon.group(1))}"
    off = re.search(r"/TIME (\d{4})", rest or "")
    if code == "14" and off:
        summary = f"Took off from {orig} at {hhmmss(off.group(1))}" + (f", bound for {dest}" if dest else "")
    cat = "flight" if code in ("ET", "13", "14", "15", "16") else "request" if code == "B6" else "data"
    return result(cat, summary, details)


def translate_skywest(msg):
    parts = [p.strip() for p in msg["text"].split(",")]
    if len(parts) < 4 or parts[0] != "P":
        return None
    label, text = msg["label"], msg["text"]
    stamp = parts[2]
    t = re.search(r"(\d\d:\d\d:\d\d)", stamp)
    when = f"{t.group(1)} UTC" if t else None
    if "METAR" in parts:
        stations = [p for p in parts[parts.index("METAR") + 1:] if re.fullmatch(ICAO_AP, p)]
        return result("request", f"Crew requested current weather (METAR) for {', '.join(stations) or 'a station'}",
                      [when])
    p = msg.get("position")
    if p:
        alt = None
        m = re.search(r"[NS] ?\d+\.\d+ [EW] ?\d+\.\d+,(\d{3,5}),", text)
        if m:
            alt = feet(m.group(1))
        return result("position", f"Position report: {latlon(p['lat'], p['lon'])}" + (f" at {alt}" if alt else ""),
                      [f"Reported {when}" if when else None])
    route = route_in(",".join(parts))
    words = free_text_lines(" ".join(q for q in parts if re.search(r"[A-Z]{2,} [A-Z]{2,}", q)))
    summary = "Flight status report" + (f", {route[0]} → {route[1]}" if route else "")
    if words:
        summary += f": “{reflow(words)}”"
    return result("crew" if words else "flight", summary, [when])


def translate_frontier(msg):
    text = msg["text"]
    m = re.match(r"POSN ?[\d.]+[EW][\d.]+, *\d+,(\d{6}),[^,]*,[^,]*, *[^,]*,[^,]*,(\d{6}),(" + ICAO_AP + ")", text)
    if m:
        return result("position", f"Position report at {hhmmss(m.group(1))}, heading for {m.group(3)}",
                      [pos_detail(msg), f"ETA {hhmmss(m.group(2))}"])
    m = re.match(r"[NS] ?\d{6}[EW]\d{7},[-\w]*,(\d{6}),", text)
    if m:
        return result("position", f"Position report at {hhmmss(m.group(1))}", [pos_detail(msg)])
    return None


# ------------------------------------------------- automated weather observations (AMDAR)
# Airlines send series of samples (position, time, altitude, temperature, wind). Each airline packs the
# same fields differently; the layouts below were matched against live traffic. Temperatures are whole
# degrees except where noted.

def _signed(sign, digits, tenths=False):
    value = int(digits) / (10 if tenths else 1)
    return -value if sign in "M-" else value


def weather_samples(text):
    """Returns (origin, destination, [sample dicts]) for known weather-observation formats, or None."""
    text = re.sub(r"^#DFB", "", text)  # H1 messages carry the sublabel inline when the decoder didn't split it
    # Southwest ++765xx/++865xx (documented AMDAR layout): lat DDMM.m, lon, DDHHMM, alt ft, temp °C, wind, phase
    # United EB0116AB_ carries the same sample rows after a comma-separated header.
    m = re.match(r"\+\+\d+,[^,]*,[^,]*,\d{6},[^,]*,(" + ICAO_AP + "),(" + ICAO_AP + ")", text) or \
        re.match(r"ABS\d{3}[A-Z]{2}_\S+?,[^,]*,[^,]*,(" + ICAO_AP + "),(" + ICAO_AP + ")", text) or \
        re.match(r"EB\d{4}AB_[^\r\n]*?,(" + ICAO_AP + "),(" + ICAO_AP + "),", text) or \
        re.match(r"(?:[^\n]*\n)?()()[NS]\d{4}\.\d,[EW]\d{5}\.\d,\d{6}, ?\d+,", text)  # continuation block: samples only
    if m:
        rows = re.findall(r"([NS])(\d{2})(\d{2}\.\d),([EW])(\d{3})(\d{2}\.\d),\d\d(\d{4}), ?(\d+), ?(-?\d+\.\d),(\d{3}),(\d{3}),(\w\w)", text)
        samples = [{"lat": (int(a) + float(b) / 60) * (-1 if ns == "S" else 1),
                    "lon": (int(c) + float(d) / 60) * (-1 if ew == "W" else 1), "time": t, "alt": int(alt),
                    "temp": float(temp), "wdir": int(wd), "wspd": int(ws),
                    "phase": {"CL": "climb", "DC": "descent", "ER": "en route"}.get(ph)}
                   for ns, a, b, ew, c, d, t, alt, temp, wd, ws, ph in rows]
        return m.group(1), m.group(2), samples
    # Alaska: 402#.140926.171114….KSEA.KMCI.CR…#  then N42.6001.W103.9351.141711.#35021.-46.5.225.096.CR#
    m = re.search(r"\.\d{6}\.\d+\.(" + ICAO_AP + r")\.(" + ICAO_AP + r")\.[A-Z]{2}\.", text)
    if m and "#" in text:
        rows = re.findall(r"([NS])(\d+\.\d+)\.([EW])(\d+\.\d+)\.\d\d(\d{4})\.#(\d+)\.(-?\d+\.\d)\.(\d{3})\.(\d{3})\.", text)
        samples = [{"lat": float(la) * (-1 if ns == "S" else 1), "lon": float(lo) * (-1 if ew == "W" else 1),
                    "time": t, "alt": int(alt), "temp": float(temp), "wdir": int(wd), "wspd": int(ws)}
                   for ns, la, ew, lo, t, alt, temp, wd, ws in rows]
        return m.group(1), m.group(2), samples
    # United/Southwest ABS0nnDA: " 41350-1016991530038004-51-245053" lat, lon (thousandths), HHMM, alt ft, temp, wind
    m = re.match(r"ABS\d{3}[A-Z]{2}_.{0,14}?(" + ICAO_AP + ")(" + ICAO_AP + r")\s*\d*\s*[\r\n]", text)
    if m:
        rows = re.findall(r"(-?)(\d{5})(-?)(\d{6})(\d{4})(\d{6})([-+ ])(\d\d)([\d-])(\d{3})(\d{3})", text)
        samples = [{"lat": int(la) / 1000 * (-1 if s1 else 1), "lon": int(lo) / 1000 * (-1 if s2 else 1), "time": t,
                    "alt": int(alt), "temp": (int(td) + (int(tt) / 10 if tt.isdigit() else 0)) * (-1 if sg == "-" else 1),
                    "wdir": int(wd), "wspd": int(ws)}
                   for s1, la, s2, lo, t, alt, sg, td, tt, wd, ws in rows]
        return m.group(1), m.group(2), samples
    # Southwest 764xx, Alaska D3M, Delta/Northwest WX02: N39679W104669 1528 1033 P011 287021 (alt in tens of feet)
    # American D3B/D3M: the same rows after IATA codes with a space ("D3M130ORD DEN "), temperature in tenths
    # (M107 at 20,000 ft is -10.7°C; -107 is not a temperature).
    american = re.match(r"D3[BM]\d{3}([A-Z]{3}) ([A-Z]{3}) N\d{5}W", text)
    m = american or re.match(r"\d{5}\r?\n\d\dE\d\d(" + ICAO_AP + ")(" + ICAO_AP + ")", text) or \
        re.match(r"D3M\d{3}(" + ICAO_AP + ")(" + ICAO_AP + r")N\d{5}W", text) or \
        re.search(r"WX\d\dEN\d\d(" + ICAO_AP + ")(" + ICAO_AP + ")", text)
    if m:
        tenths = bool(american) or text.lstrip("/ ").startswith(("WX", "A3", "A2")) or "/WX" in text[:60]  # Delta format uses tenths
        rows = re.findall(r"N(\d{5})W(\d{6})(\d{4})(\d{4})([MP])(\d{3})(\d{3})(\d{3})", text)
        samples = [{"lat": int(la) / 1000, "lon": -int(lo) / 1000, "time": t, "alt": int(alt) * 10,
                    "temp": _signed(sg, tv, tenths), "wdir": int(wd), "wspd": int(ws)}
                   for la, lo, t, alt, sg, tv, wd, ws in rows]
        return m.group(1), m.group(2), samples
    # Alaska E330: E330KSEAKDEN then N40591W106515 0535 3033 M034 248023 G0009; continuation rows carry no time
    m = re.match(r"E\d{3}(" + ICAO_AP + ")(" + ICAO_AP + r")N\d{5}W", text)
    if m:
        samples, t = [], ""
        for la, lo, when, alt, sg, tv, wd, ws in re.findall(r"N(\d{5})W(\d{6})(\d{4})?(\d{4})([MP])(\d{3})(\d{3})(\d{3})G", text):
            t = when or t
            samples.append({"lat": int(la) / 1000, "lon": -int(lo) / 1000, "time": t, "alt": int(alt) * 10,
                            "temp": _signed(sg, tv), "wdir": int(wd), "wspd": int(ws)})
        return m.group(1), m.group(2), samples
    # Northwest *WXR: N40301W107444 0910 371 -505 197 048 00 (flight level, temperature in tenths); no route
    if text.startswith("*WXR"):
        rows = re.findall(r"N(\d{5})W(\d{6})(\d{4})(\d{3})([-+ ]\d{3})(\d{3})(\d{3})", text)
        samples = [{"lat": int(la) / 1000, "lon": -int(lo) / 1000, "time": t, "alt": int(level) * 100,
                    "temp": int(temp) / 10, "wdir": int(wd), "wspd": int(ws)}
                   for la, lo, t, level, temp, wd, ws in rows]
        return None, None, samples
    # United EB0032AA_: reg, route, then " 40370-1053961602 39000-5162000530107": lat, lon, HHMM, alt ft, temp tenths, wind
    m = re.match(r"EB\d{4}AA_ \S+ (" + ICAO_AP + ")(" + ICAO_AP + ")", text)
    if m:
        rows = re.findall(r"(-?\d{5})(-?\d{6})(\d{4}) (\d{5})([-+ ]\d{3})(\d{3})(\d{3})", text)
        samples = [{"lat": int(la) / 1000, "lon": int(lo) / 1000, "time": t, "alt": int(alt), "temp": int(temp) / 10,
                    "wdir": int(wd), "wspd": int(ws)} for la, lo, t, alt, temp, wd, ws in rows]
        return m.group(1), m.group(2), samples
    # Allegiant ABS071BA_: header line with tail and route, rows " 39750-1058341328 38012-513195 54": lat, lon, HHMM,
    # alt ft, temp (tenths, the tenths digit sometimes missing), wind direction, wind speed
    m = re.match(r"ABS\d{3}BA_\s*\S+\s+\d+(" + ICAO_AP + ")(" + ICAO_AP + ")", text)
    if m:
        rows = re.findall(r"(-?\d{5})(-?\d{6})(\d{4}) (\d{5})([-+ ]\d\d)([\d-])(\d{3}) ?(\d{2,3})", text)
        samples = [{"lat": int(la) / 1000, "lon": int(lo) / 1000, "time": t, "alt": int(alt),
                    "temp": int(td) + (int(tt) / 10 if tt.isdigit() else 0) * (-1 if td.startswith("-") else 1),
                    "wdir": int(wd), "wspd": int(ws)} for la, lo, t, alt, td, tt, wd, ws in rows]
        return m.group(1), m.group(2), samples
    return None


def translate_weather(msg):
    found = weather_samples(msg["text"])
    m = re.match(r"A3M\d{7}([A-Z]{3}) ([A-Z]{3})", msg["text"]) if not found else None
    if m:  # American: altitude (tens of ft), temperature (tenths), wind; no positions
        rows = re.findall(r"(\d{4})([MP])(\d{3})(\d{3})(\d{3})[GB]\d{4}", msg["text"])
        if rows:
            alts = [int(r[0]) * 10 for r in rows]
            alt, sign, temp, wdir, wspd = rows[-1]
            return result("data", f"Weather observations ({plural(len(rows), 'sample')}, {alts[0]:,} → {alts[-1]:,} ft)",
                          [f"Flight {m.group(1)} → {m.group(2)}",
                           f"Latest: {int(alt) * 10:,} ft, {_signed(sign, temp, True):.0f}°C, wind {wdir}° at {int(wspd)} kt"])
    if not found:
        return None
    orig, dest, samples = found
    details = [f"Flight {orig} → {dest}" if orig else None]
    if not samples:
        return result("data", "Automated weather observations along the route", details)
    first, last = samples[0], samples[-1]
    trend = "climbing" if last["alt"] > first["alt"] + 500 else "descending" if last["alt"] < first["alt"] - 500 else "level"
    alts = f"{first['alt']:,} → {last['alt']:,} ft" if trend != "level" else f"{last['alt']:,} ft"
    summary = f"Weather observations ({plural(len(samples), 'sample')}, {trend} {alts})".replace("level ", "at ")
    t = last["time"]
    details.append(f"Latest {t[:2]}:{t[2:4]} UTC: {last['alt']:,} ft, {last['temp']:.0f}°C, "
                   f"wind {last['wdir']:03d}° at {last['wspd']} kt, at {latlon(last['lat'], last['lon'])}")
    return result("data", summary, details)


# ------------------------------------------------- aircraft monitoring reports
# Engine/airframe monitoring layouts are airline-configured and unpublished, so only the parts that are
# unambiguous are read: report identity, route, flight phase, generation time, and trip-report samples.

PHASE_CODES = {"TKO": "takeoff", "TKOF": "takeoff", "TO": "takeoff", "CLB": "climb", "CLMB": "climb", "CL": "climb",
               "CRZ": "cruise", "CR": "cruise", "CRS": "cruise", "DES": "descent", "DESC": "descent", "DE": "descent", "DC": "descent",
               "APU": "APU", "LD": "landing", "AP": "approach", "ER": "en route", "IC": "initial climb"}


def translate_trip_report(text):
    hashed = re.findall(r"TRP#(\d{6})#(-?\d+\.\d+)#(-?\d+\.\d+)#(\d{3})#", text) or \
        re.findall(r"TRP (\d{6}) +(-?\d+\.\d+) +(-?\d+\.\d+) +(\d{3}) ", text)  # Alaska / American
    if hashed:
        t, lat, lon, level = hashed[-1]
        return result("data", f"Automated trip report (FL{int(level)} at {hhmmss(t)})",
                      [f"Position {latlon(float(lat), float(lon))}"])
    m = re.search(r"TRP (" + ICAO_AP + ") (" + ICAO_AP + ")", text)
    if not m:
        return None
    rows = re.findall(r"/A\d+ (\d{6}), *(-?\d+\.\d+), *(-?\d+\.\d+), *(\d{3}),", text)
    details = [f"Flight {m.group(1)} → {m.group(2)}"]
    summary = "Automated trip report"
    if rows:
        t, lat, lon, level = rows[-1]
        summary += f" ({plural(len(rows), 'sample')}, latest FL{int(level)} at {hhmmss(t)})"
        details.append(f"Latest position {latlon(float(lat), float(lon))}")
    return result("data", summary, details)


def translate_performance_table(text):
    """Southwest takeoff/climb performance tables: header, then time,phase,?,alt,speed,mach,…"""
    m = re.match(r"\w{5},\d{4},(B7[\w-]+),\d{6},\w+,(" + ICAO_AP + "),(" + ICAO_AP + r")[^\n]*\r?\n"
                 r"(\d\d\.\d\d\.\d\d),([A-Z]{2}),\d+,(\d{4,5}),(\d+\.\d),(\.\d+)", text)
    if not m:
        return None
    ac_type, orig, dest, t, phase, alt, speed, mach = m.groups()
    name = PHASE_CODES.get(phase, phase).capitalize()
    extras = [f"{int(alt):,} ft", f"{float(speed):.0f} kt", f"Mach {float(mach):.2f}"]
    if "FLAPS-UP" in text:
        extras.append("flaps up")
    return result("data", f"{name} performance report: {', '.join(extras)}",
                  [f"Flight {orig} → {dest}", f"{ac_type} · recorded {t.replace('.', ':')} UTC"])


def acms_header(body):
    """CC<tail>,SEP14,124704,KCLT,KDEN,1328 -> (generated time, origin, destination)."""
    m = re.search(r"CC\w+,[A-Z]{3}\d\d,(\d{6}), ?(\w{3,4}),(\w{4})", body)
    return (m.group(1), m.group(2) if len(m.group(2)) == 4 else "K" + m.group(2), m.group(3)) if m else None


def translate_tagged_weather(text):
    if not text.startswith("WEATHER/POSITION REPORT"):
        return None
    tags = dict(re.findall(r"([A-Z]{2,5}):\s*(-?[\w.]+)", text))
    parts = []
    if tags.get("ALT", "").isdigit():
        parts.append(f"{int(tags['ALT']):,} ft")
    if "MACH" in tags:
        parts.append(f"Mach {tags['MACH']}")
    if "SAT" in tags:
        parts.append(f"outside air {float(tags['SAT']):.0f}°C")
    gmt = re.search(r"GMT:\s*(\d\d:\d\d)", text)
    return result("data", "Weather/position report" + (f": {', '.join(parts)}" if parts else ""),
                  [f"Reported {gmt.group(1)} UTC" if gmt else None])


# United's 737 hydraulic report, HYDN: tail, date, route and flight, then up to twelve
# samples a few seconds apart, ~70 characters each with no separators to rely on (two
# fields run together once N1 reaches 100):
#
#   HYDN12221300926KPSPKDEN1010...  -8.0 39.78 39.84 76.69 76.72 -33.5  300310.731142800 56369
#       73.0 83.830603036   0.000   0.000:::AIRFALSE
#
# Read as total air temperature, N1 and N2 for each engine, static air temperature,
# altitude, Mach, gross weight, a sample counter, two hydraulic quantities, system A and B
# pressure, two unknowns, and an air/ground flag. No ADS-B pairs with these - they come
# over plain ACARS with no ICAO address, and hold a recorded window rather than the present
# - so the checks are physical: over 26,286 airborne samples total temperature equals
# static temperature x (1 + 0.2 Mach^2) to within 0.2 C (5th-95th percentile), static
# temperature tracks altitude at r=-0.982, altitude changes under 3,300 ft/min between
# samples at the 99th percentile, the 616 ground samples leaving Denver read 5,131 ft at the
# median, and the pressures stay within 3,008-3,088. Weight is in pounds: 104,000-168,000,
# a 737's range and twice its maximum in kilograms. The engine pairs agree to 0.07% at the
# median and the pairs move together at r=0.983; the first idles near 30% and the second
# near 73%, rising to about 96 and 99 at climb power, which is fan (N1) and core (N2) on a
# CFM56.
HYDN_HEAD = re.compile(r"HYDN(\d{4,5})(\d{6})(" + ICAO_AP + ")(" + ICAO_AP + r")(\d{4})")
HYDN_ROW = re.compile(r"(-?\d+\.\d) *(\d+\.\d\d) *(\d+\.\d\d) *(\d+\.\d\d) *(\d+\.\d\d) *(-?\d+\.\d) *(-?\d+?)(0\.\d{3})"
                      r"(\d{6}) *(\d+) *(\d+\.\d) *(\d+\.\d)(\d{4})(\d{4}) *(-?\d+\.\d{3}) *(-?\d+\.\d{3})...(AIR|GND)(?:TRUE|FALSE)")


def translate_hydn(text):
    text = re.sub(r"#DFB|[\r\n]", "", text)
    head = HYDN_HEAD.match(text)
    if not head:
        return None
    rows = HYDN_ROW.findall(text)
    details = [f"Flight {head[3]} → {head[4]}", f"N{head[1]}"]
    if not rows:
        return result("data", "Hydraulic monitoring report", details)
    first, last = rows[0], rows[-1]
    alt = lambda r: "on the ground" if r[16] == "GND" else f"{int(r[6]):,} ft"
    span = alt(first) if alt(first) == alt(last) else f"{alt(first)} → {alt(last)}"
    details += [f"Mach {float(first[7]):.3f}" if first[16] == "AIR" else None,
                f"Outside air {float(first[5]):.1f}°C (total {float(first[0]):.1f}°C)",
                f"N1 {float(first[1]):.1f}/{float(first[2]):.1f}%, N2 {float(first[3]):.1f}/{float(first[4]):.1f}%",
                f"Hydraulic pressure A {int(first[12]):,} psi, B {int(first[13]):,} psi",
                f"Gross weight {int(first[8]):,} lb"]
    return result("data", f"Hydraulic monitoring report ({plural(len(rows), 'sample')}), {span}", details)


# United's PNM4 performance report: header with tail, route and phase (CRS for cruise),
# then samples five seconds apart, laid out like
#
#   28989 28988    85    854335++++++ 71 -31.5  -5.810.728    93    9329---00001100
#
# two altitudes, N1 for each engine, unknowns, static and total air temperature, a 1, Mach,
# then fields that run together once N2 reaches 100 and are not read. Over 25,713 samples
# total temperature equals static x (1 + 0.2 Mach^2) to within 0.2 C (5th-95th percentile),
# static temperature tracks altitude at r=-0.987, and the two altitudes agree to 20 ft at
# the median. N1 runs 32-98% between the 5th and 95th percentile, idle to climb power.
PNM4_ROW = re.compile(r"(\d{5}) (\d{5}) +(\d+) +(\d+?)\d{4}[+\d-]{6} *\d+ +(-?\d+\.\d) +(-?\d+\.\d)1(0\.\d{3}) ")


def translate_pnm4(text):
    text = re.sub(r"#DFB|[\r\n]", "", text)
    if not text.startswith("PNM4"):
        return None
    route = re.search(r"(" + ICAO_AP + ")(" + ICAO_AP + r")(CRS|CLMB|DESC|CLB|CRZ|DES|TKO)?", text[8:60])
    rows = PNM4_ROW.findall(text)
    details = [f"Flight {route[1]} \u2192 {route[2]}" if route else None]
    name = "Performance monitoring (PNM) report" + (f", {PHASE_CODES[route[3]]}" if route and route[3] else "")
    if not rows:
        return result("data", name, details)
    first, last = rows[0], rows[-1]
    span = f"{int(first[0]):,} ft" + ("" if first[0] == last[0] else f" \u2192 {int(last[0]):,} ft")
    details += [f"Mach {float(first[6]):.3f}",
                f"Outside air {float(first[4]):.1f}\u00b0C (total {float(first[5]):.1f}\u00b0C)",
                f"N1 {int(first[2])}/{int(first[3])}%"]
    return result("data", f"{name} ({plural(len(rows), 'sample')}), {span}", details)


# United's A86 report (header "A86BBSM25-H200-0003"): tail, date and time, flight, phase and
# route, then samples five seconds apart:
#
#   0811-00001100 27000206206CLSDCLSD 41 41 39 381  -260.090.083643550.694 79 79
#
# Altitude, static air temperature and Mach (the 27000, -26 and 0.694 above) are read; the
# rest - including the two CLSD valve states - is not. Over 5,222 samples temperature
# tracks altitude at r=-0.985, and on the 473 reports with an ADS-B fix the true airspeed
# that Mach and temperature give differs from ADS-B ground speed by 17 kt at the median,
# one wind's worth, against 64 kt with the reports shuffled. The last sample's altitude
# sits within 380 ft of ADS-B: the samples are a window recorded shortly before sending.
A86_ROW = re.compile(r"\d{4}[-0]00001100 +(\d{4,5})\d{6}(?:CLSD|OPEN){2} +\d+ +\d+ +\d+ +\d+ +(-?\d+?)\d\.\d\d\d\.\d\d\d{6}(\d\.\d{3}) ")


def translate_a86(text):
    text = re.sub(r"#DFB|[\r\n]", "", text)
    if not re.match(r"A86BB", text):
        return None
    tail = re.search(r"(N\d{1,5}[A-Z]{0,2}) \d{8}", text)
    leg = re.search(r"\d(ER|CL|CR|DC|TO|IC)(" + ICAO_AP + ")(" + ICAO_AP + r")\d", text)
    rows = A86_ROW.findall(text)
    name = "Aircraft monitoring report A86" + (f", {PHASE_CODES[leg[1]]}" if leg else "")
    details = [f"Flight {leg[2]} → {leg[3]}" if leg else None, tail[1] if tail else None]
    if not rows:
        return result("data", name, details)
    first, last = rows[0], rows[-1]
    span = f"{int(first[0]):,} ft" + ("" if first[0] == last[0] else f" → {int(last[0]):,} ft")
    details += [f"Mach {float(first[2]):.3f}", f"Outside air {int(first[1])}°C"]
    return result("data", f"{name} ({plural(len(rows), 'sample')}), {span}", details)


# United's takeoff engine reports, in two layouts:
#
#   TKON67827KDENKLAS30091356 ...      route, then day, month, hour, minute
#   TKO6134972149KDENKSMF093026142204  route, then month, day, year, hour, minute, second
#
# The rest is fixed-width engine data with nothing outside the message to check it by, and
# is not read. The time is the takeoff: in 98% of 878 reports it falls 0-10 minutes before
# the report was received, and every one leaves Denver, the airport this receiver sees
# aircraft climb out of.
TKO_HEADS = (re.compile(r"TKON\d{5}(" + ICAO_AP + ")(" + ICAO_AP + r")(?P<d>\d\d)(?P<mo>\d\d)(?P<h>\d\d)(?P<mi>\d\d)"),
             re.compile(r"TKO\d{10}(" + ICAO_AP + ")(" + ICAO_AP + r")(?P<mo>\d\d)(?P<d>\d\d)\d\d(?P<h>\d\d)(?P<mi>\d\d)(?P<s>\d\d)"))


def translate_takeoff(text):
    text = re.sub(r"#DFB|[\r\n]", "", text)
    m = next((m for p in TKO_HEADS if (m := p.match(text))), None)
    if not m or int(m["h"]) > 23 or int(m["mi"]) > 59 or not (airports.city(m[1]) and airports.city(m[2])):
        return None
    at = f"{m['h']}:{m['mi']}" + (f":{m['s']}" if m.groupdict().get("s") else "")
    return result("data", f"Takeoff engine report, {m[1]} → {m[2]}",
                  [f"Flight {m[1]} → {m[2]}", f"Took off {at} UTC",
                   "Derated takeoff" if "DERATE" in text else None])


# United's CRZN cruise report: tail digits, route, date and time, then a run of fixed-width
# numbers and, further on, the position written twice:
#
#   CRZN76529KHDNKDEN30091327 845  625-138 21004062013568022551SCR1... N403863N403863S1057386S1057386
#
# Some give the route as IATA codes instead ("CRZN343PN1483PHX ORD 30091525").
# Read: static air temperature (-13.8), altitude, Mach, weight, the flight (2255, plus a
# digit) and the position in ten-thousandths of a degree, the west longitude marked S.
# Checked over 483 reports: the flight matches the message's own in 480, altitude is
# ADS-B's to 12 ft at the median, every temperature is within 25 C of the standard
# atmosphere at its altitude, and the position sits 10 km from ADS-B at the median (90%
# within 17 km) - the distance flown in the minute or so between report and fix. Weight
# runs 104,000-235,000 lb across 737s and 757s.
CRZN_HEAD = re.compile(r"CRZN\d{5}(" + ICAO_AP + ")(" + ICAO_AP + r")\d\d\d\d(\d\d)(\d\d)")
CRZN_HEAD_IATA = re.compile(r"CRZN\w{5}[ \d]{4}([A-Z]{3}) ([A-Z]{3}) \d{4}(\d\d)(\d\d)")  # "CRZN343PN1483PHX ORD 30091525"
CRZN_DATA = re.compile(r"-?\d{1,3} *(-?\d{1,3}) +(\d{4,5})(\d{4})(\d{6})\d{4}\dSCR")
CRZN_POS = re.compile(r"([NS])(\d{6})\1\2([EWS])(\d{7})\3\4")


def translate_crzn(text):
    text = re.sub(r"#DFB|[\r\n]", "", text)
    head = CRZN_HEAD.match(text)
    known = airports.city if head else airports.city_iata
    head = head or CRZN_HEAD_IATA.match(text)
    if not head or not known(head[1]) or not known(head[2]):
        return None
    data, pos = CRZN_DATA.search(text[:80]), CRZN_POS.search(text)
    details = [f"Flight {head[1]} → {head[2]}", f"Recorded {head[3]}:{head[4]} UTC"]
    position = None
    if pos:
        lat = int(pos[2]) / 10000 * (-1 if pos[1] == "S" else 1)
        lon = int(pos[4]) / 10000 * (1 if pos[3] == "E" else -1)
        position = {"lat": lat, "lon": lon, "src": "United CRZN report"}
        details.append(latlon(lat, lon))
    summary = "Cruise performance report"
    if data:
        summary += f", {int(data[2]):,} ft, Mach {int(data[3]) / 1000:.3f}"
        details += [f"Outside air {int(data[1]) / 10:.1f}°C", f"Gross weight {int(data[4]):,} lb"]
    return result("data", summary, details, position=position)


# Delta's FD report: tail, date and time, flight (plus one digit), route and phase, then
# rows of an eight-character code and two times:
#
#   FD0A8115N873DN30090901 850155328341KLASKMSPCRS / 2700 / 34350X11081614070985090113
#
# The flight matches the message's own in all 677 reports. In 2,054 rows the first time
# precedes the second 96% of the time and the second is within 15 minutes of receipt in
# 77%, so each row is a code active over an interval. What the codes mean is not known:
# their leading digits (34, 20, 14, 16, 05) do not read as ATA chapters throughout.
FD_HEAD = re.compile(r"FD\w{6}(N\w+?)\d{8}.{6,9}?\d{5}(" + ICAO_AP + ")(" + ICAO_AP + r")(CRS|CLMB|DESC)?")
FD_ROW = re.compile(r"[\r\n](\d{4}[0-9A-Z]{4})(\d{6})\d{6}(\d{6})")


def translate_fd(text):
    head = FD_HEAD.match(text)
    if not head:
        return None
    rows = FD_ROW.findall(text)
    phase = {"CRS": "cruise", "CLMB": "climb", "DESC": "descent"}.get(head[4] or "")
    details = [f"Flight {head[2]} → {head[3]}", head[1]] + \
              [f"{code} {hhmmss(a).removesuffix(' UTC')}–{hhmmss(b)}" for code, a, b in rows]
    return result("data", "Maintenance data report (FD)" + (f", {phase}" if phase else "") +
                  (f", {plural(len(rows), 'code')}" if rows else ""), details)


# Delta's APM cruise performance report: comma-separated, most fields padded with empty
# ones, after a header of tail, flight (plus one digit), route and date/time:
#
#   APM    1N804DN          DA11299 KDENKMSP300926122910,,,.720,,438.4,277.4,,, -4.50,,,28981,...
#
# Read by position in the field list and checked over 319 reports. Against the aircraft's
# own ADS-B: ground speed matches to 0.5 kt (median absolute), track to 2 degrees, and the
# position sits 3.5 km away - about fifteen seconds of flight. Against the atmosphere:
# calibrated airspeed is what Mach and altitude give in the standard atmosphere to 0.14 kt,
# static temperature is what Mach and true airspeed give to 0.4 C, total temperature
# follows from both to 0.4 C, and every position is inside the receiver's radio horizon.
# Fuel flow (2,600-3,300 per engine) and weight (136,000-165,000) are pounds: in
# kilograms both would be twice a narrowbody's.
APM_HEAD = re.compile(r"APM +\d+(N\w+?) +DA(\d{4})\d? *(" + ICAO_AP + ")(" + ICAO_AP + r")\d{6}(\d{6}),")
APM_FIELDS = {"mach": 3, "tas": 5, "cas": 6, "tat": 9, "alt": 12, "weight": 15, "ff1": 18, "ff2": 19,
              "n1a": 22, "n1b": 23, "gs": 27, "lat": 44, "lon": 47, "track": 50, "sat": 127}


def translate_delta_apm(text):
    text = text.replace("\r", "").replace("\n", "")
    head = APM_HEAD.search(text)
    if not head:
        return None
    raw = [f.strip() for f in text[head.end() - 1:].split(",")]
    v = {k: float(raw[i]) if i < len(raw) and re.fullmatch(r"-?\d*\.?\d+", raw[i]) else None
         for k, i in APM_FIELDS.items()}
    details = [f"Flight {head[3]} → {head[4]}", f"Generated {hhmmss(head[5])}"]
    position = None
    if v["lat"] is not None and v["lon"] is not None:
        position = {"lat": v["lat"], "lon": v["lon"], "src": "Delta APM report"}
        details.append(latlon(v["lat"], v["lon"]))
    details += [f"True airspeed {v['tas']:.0f} kt, calibrated {v['cas']:.0f} kt" if v["tas"] and v["cas"] else None,
                f"Ground speed {v['gs']:.0f} kt, track {v['track'] % 360:.0f}°" if v["gs"] and v["track"] is not None else None,
                f"Outside air {v['sat']:.1f}°C (total {v['tat']:.1f}°C)" if v["sat"] is not None and v["tat"] is not None else None,
                f"N1 {v['n1a']:.1f}/{v['n1b']:.1f}%" if v["n1a"] and v["n1b"] else None,
                f"Fuel flow {v['ff1']:,.0f}/{v['ff2']:,.0f} lb/h" if v["ff1"] and v["ff2"] else None,
                f"Gross weight {v['weight']:,.0f} lb" if v["weight"] else None]
    summary = "Cruise performance report" + (f", {feet(int(v['alt']))}" if v["alt"] else "") + \
              (f", Mach {v['mach']:.3f}" if v["mach"] else "")
    return result("data", summary, details, position=position)


# Report 239, a cruise snapshot sent both as an Airbus ACMS report (".../REP239,...") and in
# Delta's Boeing layout, carries one fixed-width block of position, flight level, total and
# static air temperature and wind, in two layouts:
#
#   N38764W105 15340-14-43209 64A      38.764, -105.015, FL340, TAT -14, SAT -43, 209/64
#    4127-10561289 -3-31244 22A        41.27, -105.61 (two decimals), FL289, TAT -3, SAT -31, 244/22
#
# Checked over 5,110 reports. Position is 1.3 km from ADS-B at the median in the first
# layout and 2.6 km in the coarser second, flight level within about 100 ft. The wind is
# right: through the wind triangle it shrinks the spread of cruise ground speeds from 38 kt
# to a true-airspeed spread of 7 kt. And the temperatures are total and static: the Mach
# they imply equals the Mach that true airspeed implies, to 0.000 at the median (+-0.009).
REP239_LONG = re.compile(r"239N\w+?\d{15}([NS])([\d ]{5})([EW])([\d ]{6})(\d{3})([- \d]{3})([- \d]{3})([ \d]{3})([ \d]{3})A")
REP239_SHORT = re.compile(r" (\d{4})(-1\d{4})(\d{3})([- \d]{3})([- \d]{3})([ \d]{3})([ \d]{3})A")


def report239(text):
    """(position, details) from a report 239's snapshot block, or None."""
    at = text.find("239N")
    if at < 0:
        return None
    pad = lambda s: int(s.replace(" ", "0"))  # noqa: E731 - fixed-width fields pad with spaces
    m = REP239_LONG.search(text, at)
    if m:
        lat = pad(m[2]) / 1000 * (-1 if m[1] == "S" else 1)
        lon = pad(m[4]) / 1000 * (-1 if m[3] == "W" else 1)
        rest = m.groups()[4:]
    elif m := REP239_SHORT.search(text, at):
        lat, lon, rest = int(m[1]) / 100, int(m[2]) / 100, m.groups()[2:]
    else:
        return None
    level, tat, sat, wind_dir, wind_kt = rest
    if not (tat.strip(" -") and sat.strip(" -")):
        return None
    details = [latlon(lat, lon), f"FL{int(level)}",
               f"Outside air {int(sat)}\u00b0C (total {int(tat)}\u00b0C)",
               f"Wind {pad(wind_dir)}\u00b0 at {pad(wind_kt)} kt"]
    return {"lat": lat, "lon": lon, "src": "Report 239"}, details


def translate_monitoring(msg):
    report = _translate_monitoring(msg)
    snapshot = report239(re.sub(r"[\r\n]", "", msg["text"])) if report else None
    if snapshot:
        report["position"], extra = snapshot
        report["details"] = [d for d in report["details"] if d] + extra
    return report


def _translate_monitoring(msg):
    # H1 reports can carry their sublabel inline ("#DFB", "#CFB") when the decoder didn't
    # split it off, which kept every pattern below anchored on the report's own first
    # characters from matching.
    text = re.sub(r"^#[A-Z0-9]{2}B", "", msg["text"])
    tagged = translate_tagged_weather(text)
    if tagged:
        return tagged
    for reader in (translate_hydn, translate_pnm4, translate_delta_apm, translate_a86, translate_takeoff, translate_fd, translate_crzn):
        report = reader(text)
        if report:
            return report
    trip = translate_trip_report(text)
    if trip:
        return trip
    # ACMS trip report samples: A38/A31938,1,1/C1TRP,155614,385774,-1026323,315,...
    m = re.search(r"(A\d\d)/A\d{5},\d,\d/C1TRP,(\d{6}),(-?\d{5,7}),(-?\d{6,8}),(\d{3})", text)
    if m:
        lat, lon = int(m.group(3)) / 10000, int(m.group(4)) / 10000
        return result("data", f"Automated trip report (FL{int(m.group(5))} at {hhmmss(m.group(2))})",
                      [f"Position {latlon(lat, lon)}", f"Report {m.group(1)}"])
    perf = translate_performance_table(text)
    if perf:
        return perf
    route = route_in(text)
    route_detail = f"Flight {route[0]} → {route[1]}" if route else None
    phase = re.search(r"[A-Z]{8}(DESC|CLMB|CRZ|TKOF|TKO|CLB)(?![A-Z])|/(CRZ|TKO|CLB|DES)\d", text)
    phase_name = PHASE_CODES.get(phase.group(1) or phase.group(2)) if phase else None

    # Airbus ACMS: A320,145387,1,1,TB000000/REP997,00,00,4/<body>
    m = re.match(r"A[23]\d\d,\d{6},\d,\d,TB\d+/REP(\d{3}),[^/]*/\s*(.*)", text, re.S)
    if m:
        body = m.group(2)
        inner = re.match(r"-?\s*(A\d\d)/A\d{5}", body) or re.match(r"(?!CC)([A-Z]{3,5})\d", body)
        name = f"Airbus aircraft monitoring report {int(m.group(1))}"
        if inner:
            name += f" ({inner.group(1)})"
        header = acms_header(body)
        details = [f"Flight {header[1]} → {header[2]}", f"Generated {hhmmss(header[0])}"] if header else [route_detail]
        return result("data", name + (f", {phase_name}" if phase_name else ""), details)
    if re.match(r"A[23]\d\d,\d{6},\d,\d,TB\d+/", text):
        return result("data", "Airbus aircraft monitoring report", [route_detail])
    m = re.match(r"(\d{3})[A-Z0-9]{0,6}?N\d{1,4}[A-Z]{1,2}(?=\d|\s)", text)  # report no., then tail (N775DE)
    glued = re.findall(r"([KCPM][A-Z]{3})([KCPM][A-Z]{3})(?![A-Z])", text)  # route codes can follow other letters
    if m:
        orig, dest = route or (glued[-1] if glued else (None, None))
        return result("data", f"Aircraft monitoring report {int(m.group(1))}", [f"Flight {orig} → {dest}" if orig else None])
    # ACMS report with header A02/A32002,1,1/CC<tail>,SEP14,150905,KDTW,KDEN
    m = re.match(r"(A\d\d)/A\d{5},\d,\d/C(?:C|1N)\w+,[A-Z]{3}\d\d,(\d{6}),(" + ICAO_AP + "),(" + ICAO_AP + ")", text)
    if m:
        return result("data", f"Aircraft monitoring report {m.group(1)}",
                      [f"Flight {m.group(3)} → {m.group(4)}", f"Generated {hhmmss(m.group(2))}"])
    # Boeing-style header: B07C / 6N37449  4812412KBWIKDENDC140926154743
    m = re.match(r"([AB]\d\d)\w*\s*.{0,40}?(" + ICAO_AP + ")(" + ICAO_AP + r")(DC|CL|CR|TO|ER|IC)(?:\d{4,6}|\s)",
                 text.replace("\r", "").replace("\n", " "))
    if m:
        return result("data", f"Aircraft monitoring report {m.group(1)}, {PHASE_CODES[m.group(4)]}",
                      [f"Flight {m.group(2)} → {m.group(3)}"])
    # Named report codes at the start of the text
    for code, name in (("APM", "Aircraft performance monitoring (APM) report"),
                       ("PNM", "Performance monitoring (PNM) report"),
                       ("HYDN", "Aircraft monitoring report (HYDN)"),
                       ("CAB", "Aircraft monitoring report (CAB)")):
        if re.match(rf"(?:#DFB)?\s*(?:\w{{4}}\s+)?{code}\b|(?:#DFB)?\s*{code}[\dN]", text):
            # No header fields here, so a phase word (CLMB) can pass for a Canadian airport
            # code: name the route only when both ends are airports.
            known = route and all(airports.city(c) for c in route)
            return result("data", name + (f", {phase_name}" if phase_name else ""), [route_detail if known else None])
    return None


# ------------------------------------------------- ARINC 702 flight-computer messages
# IMI/IEIdata/IEIdata…checksum, e.g. PRG/DTKMCI,09O,105,164741/…3D39  (layout from acars-decoder-typescript)

A702_TYPES = {"FPN": "flight plan", "FTX": "free text", "INI": "initialization report", "INR": "in-range report",
              "LDI": "load distribution", "PER": "performance data", "POS": "position report",
              "PRG": "progress report", "PWI": "predicted winds", "WXR": "weather", "SUM": "flight summary",
              "REQ": "request"}


def parse_arinc702(text):
    body = re.sub(r"^(?:- ?#M\dB|#M\dB|/[A-Z0-9]{7}\.)", "", text.strip())
    m = re.match(r"(FPN|FTX|INI|INR|LDI|PER|POS|PRG|PWI|WXR|SUM|REQ|RES|REJ)(FPN|FTX|INI|INR|LDI|PER|POS|PRG|PWI|WXR|SUM|REQ)?"
                 r"(?=[/,]|[0-9A-F]{4}$|$)", body)
    if not m:
        return None
    parts = body.split("/")[1:]
    if parts:
        parts[-1] = re.sub(r"[0-9A-F]{4}$", "", parts[-1])  # trailing CRC
    fields = {}
    for part in parts:
        if len(part) >= 2:
            fields.setdefault(part[:2], part[2:])
    return m.group(1), m.group(2), fields


def translate_arinc702(text):
    parsed = parse_arinc702(text)
    if not parsed:
        return None
    imi, sub, fields = parsed
    details = []
    if "AF" in fields and "," in fields["AF"]:
        dep, arr = fields["AF"].split(",")[:2]
        details.append(f"Flight {dep} → {arr}")
    dest_part = None
    if "DT" in fields:
        dt = fields["DT"].split(",")
        if len(dt) >= 4 and re.fullmatch(ICAO_AP, dt[0]):
            dest_part = f"destination {dt[0]}" + (f" runway {dt[1]}" if dt[1] else "") + \
                        (f", ETA {hhmmss(dt[3][:6])}" if re.fullmatch(r"\d{4,6}", dt[3]) else "")
            if dt[2].isdigit():
                details.append(f"Fuel on board {int(dt[2]):,} (airline units)")
    if "ET" in fields and re.fullmatch(r"\d{5,6}", fields["ET"]):
        details.append(f"ETA {hhmmss(fields['ET'][-4:])}")
    if "FB" in fields and fields["FB"].isdigit():
        details.append(f"Fuel on board {int(fields['FB']):,} (airline units)")
    if "DQ" in fields and fields["DQ"].isdigit():
        details.append(f"Requested FL{int(fields['DQ'])}")
    if "PR" in fields:
        pr = fields["PR"].split(",")
        if len(pr) > 2 and pr[2].isdigit() and int(pr[2]):
            details.append(f"Altitude FL{int(pr[2])}")

    if imi in ("RES", "REJ") and sub:
        verb = "accepted" if imi == "RES" else "rejected"
        return result("flight", f"Flight computer {verb} the {A702_TYPES.get(sub, sub)} sent from the ground", details)
    if imi == "REQ" and sub:
        airports = ", ".join(a for a in fields.get("WQ", "").split(":") if re.fullmatch(ICAO_AP, a))
        what = A702_TYPES.get(sub, sub) + (f" for {airports}" if airports else "")
        return result("request", f"Flight computer requested {what} from the ground", details)
    if imi == "FTX" and "FX" in fields:
        return result("crew", f"Crew free text: “{fields['FX'].strip()}”", details)
    if imi == "WXR":
        airports = ", ".join(a for a in fields.get("WQ", "").split(":") if re.fullmatch(ICAO_AP, a))
        return result("request", f"Crew requested weather{f' for {airports}' if airports else ''}", details)
    name = A702_TYPES[imi].capitalize() + (f" ({A702_TYPES.get(sub, sub)})" if sub else "")
    summary = f"{name}" + (f": {dest_part}" if dest_part else "")
    category = "request" if imi == "REQ" else "position" if imi == "POS" else "flight"
    return result(category, f"Flight computer {summary[0].lower()}{summary[1:]}", details)


# ------------------------------------------------- label 44, 80 and 4A formats (after acars-decoder-typescript)

def translate_label44(msg):
    parts = [p.strip() for p in msg["text"].split(",")]
    # TWX and MSG turned up once the ciphered half of this traffic became readable: a TWX
    # carries a route and little else, a MSG is free text to or from dispatch.
    m = re.match(r"0*(ETA|POS|OUT|OFF|ON|IN|TWX|MSG|TST|SPN)0\d$", parts[0]) if parts else None
    if not m or len(parts) < 5:
        return None
    event = m.group(1)
    pos = pos_detail(msg)
    if event in ("ETA", "POS") and len(parts) >= 8:
        # The clear form gives altitude in hundreds of feet, and fuel in the last field.
        level = "on the ground" if parts[2] in ("GRD", "***") else feet(int(parts[2]) * 100) if parts[2].isdigit() else None
        eta = parts[7][:4] if re.fullmatch(r"\d{4,6}", parts[7]) else None
        # A '?' marks a character the key still lacks, so the route is withheld rather than
        # shown with a hole in it. Clear-text messages never contain one.
        route = "" if any("?" in p for p in parts[3:5]) else f", {parts[3]} → {parts[4]}"
        summary = ("ETA report" if event == "ETA" else "Position report") + route
        fuel = parts[8] if len(parts) > 8 and re.fullmatch(r"[\d.]+", parts[8]) else None
        return result("flight" if event == "ETA" else "position", summary,
                      [level, f"ETA {hhmmss(eta)}" if eta else None,
                       f"Fuel on board {float(fuel) * 1000:,.0f} lb" if fuel else None, pos])
    if event in ("SPN", "TST"):
        # SPN carries part numbers (CMF-2600-130507) after the route: the avionics software
        # loaded, sent once after takeoff. TST is a test message with a route and nothing else.
        level = feet(int(parts[2]) * 100) if parts[2].isdigit() else None
        route = f", {parts[3]} \u2192 {parts[4]}" if all(re.fullmatch(ICAO_AP, p) for p in parts[3:5]) else ""
        return result("data" if event == "SPN" else "flight",
                      ("Avionics software report" if event == "SPN" else "Test message") + route, [level, pos])
    if event in ("TWX", "MSG"):
        route = [p for p in parts[3:5] if re.fullmatch(ICAO_AP, p)]
        words = [p for p in parts[5:] if re.search(r"[A-Z]{3,}", p) and not re.fullmatch(r"[\d.]+", p)]
        return result("crew" if words else "flight",
                      ("Message to dispatch" if event == "MSG" else "Text message")
                      + (f", {route[0]} \u2192 {route[1]}" if len(route) == 2 else ""),
                      [f"\u201c{reflow(words)}\u201d" if words else None, pos])
    names = {"OUT": "Left the gate", "OFF": "Took off", "ON": "Landed", "IN": "Arrived at the gate"}
    time = next((p for p in parts[5:] if re.fullmatch(r"\d{4}", p)), None)
    where = "" if any("?" in p or "*" in p for p in parts[2:4]) else f": {parts[2]} \u2192 {parts[3]}"
    return result("flight", f"{names[event]}{where}" + (f" at {hhmmss(time)}" if time else ""), [pos])


LABEL80_TYPES = {"POSRPT": "Position report", "INRANG": "In-range report", "DSPTCH": "Message to dispatch",
                 "ETARPT": "ETA report", "OUTRPT": "Left the gate", "OFFRPT": "Took off", "ONRPT": "Landed",
                 "INRPT": "Arrived at the gate", "DIVERT": "Diversion report"}


def bizjet_other(msg, group, sep, digits):
    """The events that aren't a position report: OFF, ETA, TWX and free-text MSG.

    With both halves of the key known there is no need to parse these by column - they
    decrypt to the same clear format translate_label44 already reads, so they are turned
    back into plaintext and handed to it. One of them says, in full: "MSG03,N39310W101130,
    450,KBJC,KPDK,2118,DISPATCH,N148B,DISPATCH,PDK ETA 2115".

    Where the two halves of a key disagree the digits win, having been checked against
    ADS-B to 0.43 km at the median; the letter fit only ever had a spare Z to place.
    """
    table = {c: str(i) for i, c in enumerate(digits)}
    for extra in (BIZJET_LETTERS.get(group), BIZJET_PUNCT.get(group)):
        if extra:
            table = {**dict(zip(*extra)), **table}
    plain = "".join(table.get(c, "," if c == sep else "?") for c in msg["text"][2:])
    head = re.match(r"([A-Z]{3}\d\d)(.*)", plain)
    if not head:
        return None
    if head.group(1) == "POA01":
        return bizjet_poa(msg, plain)
    # An ETA runs its event straight into the position where the others put a comma.
    # Normalise it so the clear-text parser only ever sees one shape.
    if head.group(2)[:1] not in ("", ","):
        plain = f"{head.group(1)},{head.group(2)}"
    return translate_label44(dict(msg, text=plain))


# POA, the automatic position report ("AUTORP"), sent on label 17 rather than 44 and laid
# out by column rather than by comma; the character between its fields is unknown (shown ?):
#
#   POA01?ZD0321?03031726KELMKVNY?N39 06.5?W102 18.3?279? 56?430?- 59?AUTORP?10
#
# Flight ID, day twice, time, origin and destination run together, position in degrees and
# decimal minutes, wind direction and speed, flight level, temperature. Checked on the 15
# in the archive with an ADS-B fix: positions sit a median of 0.35 km from it (worst 1.05),
# the flight level matches it, and the wind is right - through the wind triangle it turns
# ground speeds spread over 413-523 kt into true airspeeds of 462-473 kt at cruise, Mach
# 0.81-0.83 at the reported temperature, which runs from -13 at FL196 to -61 at FL450.

def bizjet_poa(msg, plain):
    m = re.match(r"POA01.(\S{4,6}).\d\d(\d\d)(\d{4})([A-Z?]{4})([A-Z?]{4}).([NS])(\d\d) (\d\d\.\d).([EW])(\d{3}) (\d\d\.\d)"
                 r".(\d{3}). *(\d+).(\d{3}).([-+?]) *(\d+).AUTORP", plain)
    if not m:
        return None
    lat = (int(m[7]) + float(m[8]) / 60) * (-1 if m[6] == "S" else 1)
    lon = (int(m[10]) + float(m[11]) / 60) * (-1 if m[9] == "W" else 1)
    route = None if "?" in m[4] + m[5] else f"Flight {m[4]} \u2192 {m[5]}"
    temp = f"Outside air {'' if m[15] == '+' else '-'}{int(m[16])}\u00b0C" if m[15] in "+-" else None
    return result("position", f"Position report at {hhmmss(m[3])}, FL{int(m[14])}",
                  [route, latlon(lat, lon), f"Wind {int(m[12])}\u00b0 at {int(m[13])} kt", temp],
                  position={"lat": lat, "lon": lon, "src": "Business-aviation POA report"})


def translate_label80(msg):
    lines = [l for l in re.split(r"\r?\n", msg["text"].strip())]
    m = re.match(r"\w{4} (\w{3,8}) \w+/\d\d (\w{3,4})/(\w{3,4}) \.?(\S+)", lines[0]) if lines else None
    if not m:
        return None
    code, dep, arr = m.group(1), m.group(2), m.group(3)
    tags, text_lines = {}, []
    for line in lines[1:]:
        found = re.findall(r"/?([A-Z]{2,4}) ([^/]+)", line) if line.lstrip().startswith("/") else []
        if found:
            tags.update({k: v.strip() for k, v in found})
        elif line.strip():
            text_lines.append(line)
    details = [f"Flight {dep} → {arr}"]
    for tag, label in (("ALT", "Altitude"), ("FL", "Flight level"), ("MCH", "Mach"), ("SPD", "Speed"),
                       ("FOB", "Fuel on board"), ("SAT", "Outside air"), ("ETA", "ETA")):
        if tag in tags:
            value = hhmmss(tags[tag]) if tag == "ETA" else tags[tag]
            details.append(f"{label} {value}")
    name = LABEL80_TYPES.get(code, f"{code.capitalize()} report")
    if code == "DSPTCH" and text_lines:
        return result("crew", f"Crew message to dispatch: “{reflow(text_lines)}”", details)
    category = "position" if code == "POSRPT" else "flight"
    return result(category, name, details + ([pos_detail(msg)] if msg.get("position") else []))


def translate_label4a(msg):
    fields = msg["text"].split(",")
    if len(fields) == 11 and re.fullmatch(r"\d{6}", fields[0]):
        return result("flight", f"Flight report {fields[4]} → {fields[5]}",
                      [f"Sent {hhmmss(fields[0])}", f"Callsign {fields[3]}" if fields[3] else None])
    if len(fields) == 6 and fields[0][:1] in ("N", "S") and msg.get("position"):
        return result("position", "Position report", [pos_detail(msg)])
    return None


# Delta/Northwest label 17, in fixed columns: a waypoint padded into eight, then the
# time over it, altitude in hundreds of feet, ETA at the destination, outside air
# temperature and the wind. Altitude tracks temperature at r=-0.92 over the samples we
# have, which is what pins the layout down. The trailing seven digits aren't documented.
WAYPOINT_REPORT = re.compile(r"([A-Z][A-Z0-9]{1,6}) +(\d{4})(\d{3})(\d{4})(-?\d{2})(\d{3})( *\d{1,3})\d{7}")


def translate_waypoint_report(line, dep, arr):
    """Progress report over a named waypoint: no lat/lon, so nothing to put on the map."""
    m = WAYPOINT_REPORT.fullmatch(line)
    if not m:
        return None
    wpt, over, alt, eta, temp, wind_dir, wind_kt = m.groups()
    return result("position", f"Position report over {wpt} at {hhmmss(over)}, {feet(int(alt) * 100)}",
                  [f"Flight {dep} \u2192 {arr}", f"ETA {hhmmss(eta)}", f"Outside air {int(temp)}\u00b0C",
                   f"Wind {int(wind_dir)}\u00b0 at {int(wind_kt)} kt"])


# Delta/Northwest label 48, a form of six four-character slots and a Y/N flag:
#
#   301848 KLAS KDTW7 / /FN 0470 / 1274                    N
#   062048 KSFO KDTW6 / 0SLC0RDU                N
#
# Each filled slot is a flight number or an airport (ICAO, or IATA behind a 0), and nearly
# always only the first. Read as the crew asking about onward connections at the
# destination, on this evidence: of the requested numbers whose flight this receiver also
# heard, 9 of 10 depart the asking flight's destination, against 12% for a heard flight
# number picked at random. The Y/N flag is unidentified.

def translate_connection_request(text, orig, dest):
    m = re.search(r"^([0-9A-Z ]{24})([YN])\s*$", text.replace("\r", ""), re.M)
    if not m:
        return None
    asked = []
    for slot in re.findall(r"....", m.group(1)):
        if slot.isdigit():
            asked.append(f"flight {int(slot)}")
        elif re.fullmatch(r"K[A-Z]{3}", slot):
            asked.append(airports.city(slot) or slot)
        elif re.fullmatch(r"0[A-Z]{3}", slot):
            asked.append(airports.city_iata(slot[1:]) or slot[1:])
        elif slot.strip():
            return None
    summary = "Connection request" + (": " + ", ".join(asked) if asked else " (blank)")
    return result("request", summary, [f"Flight {orig} → {dest}"])


# Southwest label 37, a flight-data report obfuscated with a monoalphabetic substitution.
# The first two digits of the message pick one of nine cipher alphabets; the rest of the
# first line is a header. The body is 15 separator-delimited fields in fixed columns.
# Recovered by aligning the coordinate fields against positions decoded from the same
# aircraft's other messages: the longitude degrees agree 99% of the time, and the result
# checks out against physics - true airspeed tracks Mach x the speed of sound at the
# reported altitude with r=+0.999, and no consecutive pair of reports implies a ground
# speed above 1100 km/h.
SOUTHWEST_KEYS = {
    # group: (field separator, the ten characters standing for digits 0-9)
    "01": ('Z', '3NOP71-S,U'),
    "02": ('H', 'C3YJ:4D0R/'),
    "03": ('K', '5B4L 0CM7D'),
    "04": ('2', '3 8L4Z9M5:'),
    "05": ('A', 'CZD E(F)G,'),
    "06": ('O', 'KJIHGFEDCB'),
    "07": ('Z', ')G,H-I.J/K'),
    "08": ('G', 'R A:W9C4S5'),
    "09": ('K', 'HJI8N: 6LM'),
}
SOUTHWEST_SHAPE = (4, 4, 8, 8, 5, 3, 3, 4, 4, 4, 3, 3, 3, 3, 6)

# The letters, for fields 0 and 1 - origin and destination ICAO codes. Read off directly
# rather than attacked: Southwest's H1 position reports name the route in clear, so pairing
# each label 37 report with the same flight's H1 report within half an hour gives known
# plaintext for 1,917 reports. Every letter maps to one cipher character at every position,
# none collides with a digit or separator, and fitting on the first nine days reproduces the
# clear route exactly on 93% of the later ones (the misses are letters not yet seen; the
# full table gets 1,071 of 1,074). The rarest letters - V, Y, Q, and Z and J in a couple of
# groups - never occurred in a paired report and were each the one unused letter that turns
# the remaining codes into airports Southwest flies to. Checks with no clear text behind
# them: all 36,694 codes these decode across the archive are real airports, and on the
# 16,416 reports with no clear route to pair, the decoded route passes within 100 km of the
# aircraft's ADS-B fix 89% of the time over the same airport pairs where clear routes score
# 93%, against 52% shuffled. Fields 0 and 1 are digits rather than letters in a few dozen
# short variants, which decode to holes and are not shown.
SOUTHWEST_LETTERS = {
    '01': (' ()./045689:ABCDEFGHJKLQTW', 'VIKMRNPZSUYXACEHLOTWBGDQJF'),
    '02': (' (,-.1256789ABFKLMNOPSTUVW', 'HGKZLQYWRVTNJADMPBICUEOFSX'),
    '03': ('(),./12689:EFGHINOPQRSTUVW', 'GXEQUFTPRSJAWHLBMNVCYODIKZ'),
    '04': ('(),-./0167ABCDEFGOPQRSTUVW', 'BEHMPSUYDGJLOQTWZACFIKNRVX'),
    '05': ('-./0123456789IJKLMNOPQRSTU', 'BDFHJLNPRTVXZACEGIKMOQSUWY'),
    '06': (' (),-./0123456789:STUVWXYZ', 'AGFEDCBRQPONMLKJIHZYXWVUTS'),
    '07': ('0123456789:ABLMNOPQRSTUVWX', 'ACEGIKMOQSUWYBDFHJLNPRTVXZ'),
    '08': ('(),./123678HIJKLMNOPQTVXYZ', 'MDSJQOWZLNRAPEFTVIKYBHUCXG'),
    '09': ('(),-.01234ABCDEFOPQRSTUVWX', 'LQMSRUZYVXKPTACBNODGJEFIHW'),
}


def southwest_route(group, fields):
    """Origin and destination, when both decode to an airport we know."""
    key = SOUTHWEST_LETTERS.get(group)
    if not key or len(fields) < 2:
        return None
    letters = dict(zip(*key))
    ends = ["".join(letters.get(c, "?") for c in f) for f in fields[:2]]
    if any(len(e) != 4 or "?" in e or not airports.city(e) for e in ends):
        return None
    return f"Flight {ends[0]} → {ends[1]}"


def translate_southwest(msg):
    """Position, altitude, speed, fuel and weight out of a Southwest label 37 report.

    Only part of the traffic uses the full 15-field record; the rest sends shorter
    variants of the same data under the same cipher. Rather than matching a shape, the
    coordinates are found by their signature - eight characters, degrees, a point that
    is not a digit, then thousandths - which reads every variant."""
    head, _, body = msg["text"].partition("\r\n")
    key = SOUTHWEST_KEYS.get(head[:2])
    if not key or not body:
        return None
    sep, digits = key
    table = {c: str(i) for i, c in enumerate(digits)}
    fields = body.replace("\r", "").replace("\n", "").split(sep)

    def num(field, *positions):
        out = "".join(table.get(field[p], " ") for p in positions)
        return None if " " in out else out

    def coordinate(window, lead):
        """dd.ddd after `lead` non-digit characters, the point being a non-digit too."""
        if len(window) != 8 or window[4] in table:
            return None
        if any(c in table for c in window[:lead]):
            return None
        return num(window, *range(lead, 4), 5, 6, 7)

    lat = lon = point = None
    for f in fields:
        for i in range(len(f) - 7):
            w = f[i:i + 8]
            if lat is None and coordinate(w, 2):
                lat, point = coordinate(w, 2), w[4]
            elif lat is not None and w[4] == point and coordinate(w, 1):
                lon = coordinate(w, 1)
                break
        if lon:
            break
    if not (lat and lon):
        return None
    lat, lon = int(lat) / 1000, -int(lon) / 1000
    details = [southwest_route(head[:2], fields), latlon(lat, lon)]
    summary = "Position report"
    if tuple(len(f) for f in fields) == SOUTHWEST_SHAPE:
        alt, mach = num(fields[4], *range(5)), num(fields[7], 1, 2, 3)
        tas, fob = num(fields[6], 0, 1, 2), num(fields[8], 0, 1, 3)
        eta, weight = num(fields[9], *range(4)), num(fields[14], *range(6))
        # Track, wind and temperature, all confirmed against ADS-B once the archive could
        # pair the two: the track sits 8 degrees off the ADS-B heading at the median, and
        # feeding this wind into the wind triangle predicts ADS-B ground speed to 2.4 kt
        # (r=0.991) where airspeed alone is 19.5 kt out. Temperature tracks altitude at -0.976.
        track, wind_dir, wind_kt = num(fields[10], 0, 1, 2), num(fields[11], 0, 1, 2), num(fields[12], 0, 1, 2)
        sat = num(fields[13], 1, 2) if fields[13][0] not in table else None
        summary += (f", {feet(int(alt))}" if alt else "") + (f", Mach {int(mach) / 1000:.3f}" if mach else "")
        details += [f"True airspeed {int(tas)} kt" if tas else None,
                    f"Track {int(track)}\u00b0" if track else None,
                    f"Wind {int(wind_dir)}\u00b0 at {int(wind_kt)} kt" if wind_dir and wind_kt else None,
                    f"Outside air -{int(sat)}\u00b0C" if sat else None,
                    f"Fuel on board {int(fob) * 100:,} lb" if fob else None,
                    f"ETA {hhmmss(eta)}" if eta and int(eta[:2]) < 24 else None,
                    f"Gross weight {int(weight):,} lb" if weight else None]
    else:
        # The short variants carry a clock time, and sometimes fuel, in 4-character fields.
        times = [t for f in fields if len(f) == 4 and (t := num(f, 0, 1, 2, 3)) and int(t[:2]) < 24]
        fuel = [v for f in fields if len(f) == 4 and f[2] == point and (v := num(f, 0, 1, 3))]
        details += [f"Reported {hhmmss(times[0])}" if len(times) == 1 else None,
                    f"Fuel on board {int(fuel[0]) * 100:,} lb" if len(fuel) == 1 else None]
    return result("position", summary, details,
                  position={"lat": lat, "lon": lon, "src": "Southwest label 37"})


def translate_autpos(msg):
    """FedEx label 16: an automatic position report in plain text, unset fields as '*'."""
    text = msg["text"]
    if "/AUTPOS/" not in text:
        return None
    fields = dict(re.findall(r"/([A-Z]{3})\s+([^\s/\r\n]+)", text))
    alt = fields.get("ALT", "").lstrip("0")
    fob = fields.get("FOB", "").lstrip("0")
    wind = re.fullmatch(r"(\d{3})(\d{3})", fields.get("WND", ""))
    return result("position", "Automatic position report" + (f", {feet(alt)}" if alt.isdigit() else ""),
                  [pos_detail(msg),
                   f"Fuel on board {int(fob):,} lb" if fob.isdigit() else None,
                   f"Wind {int(wind.group(1))}° at {int(wind.group(2))} kt" if wind else None,
                   f"Outside air {int(fields['SAT'])}°C" if fields.get("SAT", "").lstrip("-").isdigit() else None])


# Business-aviation label 44 (the XA/GS/ZD datalink prefixes), obfuscated the same way as
# Southwest's label 37: the first two digits pick one of eight cipher alphabets and the
# body is nine separator-delimited fields in fixed columns. No aligned positions were
# available for these aircraft, so the alphabets came from the format's own constraints -
# the columns holding a minutes-tens digit take exactly six values, which splits the ten
# digits into 0-5 and 6-9, and field 1 ends in a constant "10" for the longitude - leaving
# 576 candidates per group. Scoring those on whether field 6 reads as a clock time near
# when the message arrived picked one: it lands within two minutes for every message in
# every group, field 7 is always ahead of it (an ETA), and the resulting positions sit a
# median of 152 km from the receiver. Cross-checks: altitudes run 11,700-45,000 ft with a
# median of 41,000, no pair of consecutive reports implies a ground speed over 1100 km/h,
# and the characters used as digits never appear in the airport-code fields, which a
# substitution cipher forbids. Those two fields hold ICAO codes but the letter alphabet
# is not solved, so they are left alone.
BIZJET_KEYS = {
    # group: (field separator, the ten characters standing for digits 0-9)
    "01": ('b', "lA~S'ELq(?"),
    "02": ('I', ',3-0mQ9]A)'),
    "03": ('q', 'xt(J-DeP9V'),
    "04": ('V', "'\\!mHFXa:+"),
    "05": (')', 'jx9duCo`eB'),
    "06": ('7', 'g. C9)f(:`'),
    "07": (')', '.;40m<UMDk'),
    "08": ('Z', 'LYe}`vaKct'),
    "09": ('c', '48h6NB,s1Z'),
}
BIZJET_SHAPE = (5, 13, 3, 4, 4, 4, 4, 4, 5)

# The letters, for fields 3 and 4 - the origin and destination ICAO codes. Recovered by
# tools/crack_routes.py, which hill-climbs each alphabet against the airport table and the
# aircraft's own position, cribbed from the event names in field 0: the commonest is POS,
# the one sharing its middle letter is TOC, and the one whose middle letter starts TOC is
# ETA, which between them pin nine letters before the search begins.
#
# What justifies putting these on screen, after a long spell of not doing so: across the
# 1,492 reports in a 20-day archive they decode 2,902 airport codes and every single one is
# a real airport, over 299 distinct fields that are recognisably the business-jet network -
# Teterboro 264 times, then Van Nuys, San Jose, Oakland, Aspen, White Plains, Eagle - with
# reciprocal pairs at matching volumes. And the nine alphabets were derived independently of
# one another, yet where two of them decode the same aircraft's flight within the same hour
# they name the same pair 95% of the time, which uses no geometry and no dictionary at all.
# A wrong alphabet does not do that; the earlier broken ones emitted KJLG and KSVD.
BIZJET_LETTERS = {
    '01': ('#$*124569@CDFGHIMT\\_`cdny', 'CQOPGSHEJMFVRITDUXLNYBKWA'),
    '02': ('*/14:;<ENOXY`ehiklopqrstv|', 'FWRUBOZSPGEJXTHQIVKYCNMDLA'),
    '03': ('!&)+.0128>@HILOSTXdfmovw', 'LDYEKOFJMSHCBNIVTURWPGXA'),
    '04': ('%(-/7=@DGKLNZ^`iknpqrvwz', 'WEKABOCTSIXNHJVRMUPLDYGF'),
    '05': ('"#$&\'/2346FIJLOQSTX]^_gh}', 'FYVWZMHASKCTPRODXIENJBUGL'),
    '06': ('"13456<>?DEILMOS[_acdiosx', 'TDKRSMHQGWOJENXFVCLPYUBIA'),
    '07': (' $(*-3567?EOTXY\\]_lnrsxy{}', 'LARPMQCFXONUWSIJEYKZDVBTGH'),
    '08': (' ,07:=?JPRW\\dfijmqsuxyz|~', 'JPBDAEUYLVCFRHQXTNOSMGKIW'),
    '09': (' !%(+-.0;GHLOPQTW\\dejor|}', 'JRHKNLMDQETSCBXYFOUGAWIPV'),
}


# Space, point and minus, which the letter fit never placed. They come from the fixed
# punctuation of two event types that only became readable with the full key: POA
# position reports ("N39 06.5 W102 18.3", temperature "- 59") and SPN part-number reports
# ("CMF-2600-130507,CMF-ATN-HGI-029,69003236-502   "), whose layout the one clear-text
# copy on key 00 confirms ("69003082-502   "). The minus is the temperature sign at every
# cruise report and a different character on the one report from 10,000 ft, where it reads
# +6. The space settled an error: in groups 03 and 05 the letter fit had parked Q on it,
# so free text read "TOWNEPLACEQSUITES". Neither of those Qs ever decoded a real airport.
# Only characters seen at least twice in their role are listed.
BIZJET_PUNCT = {
    '01': ('0Os', ' .-'),
    '02': ('$T7', ' .-'),
    '03': ('W$,', ' .-'),
    '04': (']#3', ' .-'),
    '05': ('?7N', ' .-'),
    '06': (';,', ' .'),
    '07': ('%+i', ' .-'),
    '08': ('*', '-'),
    '09': ('g5', ' .'),
}


def translate_bizjet(msg):
    """Position, altitude and ETA out of a business-aviation label 44 report."""
    text = msg["text"]
    key = BIZJET_KEYS.get(text[:2])
    if not key:
        return None
    sep, digits = key
    fields = text[2:].split(sep)
    if tuple(len(f) for f in fields) != BIZJET_SHAPE:
        return bizjet_other(msg, text[:2], sep, digits)
    table = {c: str(i) for i, c in enumerate(digits)}

    def num(field, *positions):
        out = "".join(table.get(field[p], " ") for p in positions)
        return None if " " in out else out

    where = fields[1]
    # Degrees then decimal minutes, with the longitude's leading "10" written out in full.
    if num(where, 7, 8) != "10":
        return None
    lat, lon = num(where, 1, 2, 3, 4, 5), num(where, 9, 10, 11, 12)
    if not (lat and lon):
        return None
    lat = int(lat[:2]) + float(f"{lat[2:4]}.{lat[4]}") / 60
    lon = -(100 + int(lon[0]) + float(f"{lon[1:3]}.{lon[3]}") / 60)
    alt, over, eta = num(fields[2], 0, 1, 2), num(fields[6], 0, 1, 2, 3), num(fields[7], 0, 1, 2, 3)
    if over and int(over[:2]) > 23:
        return None
    summary = "Position report" + (f", {feet(int(alt) * 100)}" if alt else "")
    return result("position", summary,
                  [bizjet_route(msg["text"][:2], fields),
                   latlon(lat, lon),
                   f"Reported {hhmmss(over)}" if over else None,
                   f"ETA {hhmmss(eta)}" if eta and int(eta[:2]) < 24 else None],
                  position={"lat": lat, "lon": lon, "src": "Business-aviation label 44"})


def bizjet_route(group, fields):
    """Origin and destination, when both decode to an airport we know.

    A rare cipher character - a bit error rather than alphabet - leaves a hole, and a hole
    means the code is incomplete rather than wrong, so nothing is shown for it. 97% of
    reports decode both ends cleanly."""
    key = BIZJET_LETTERS.get(group)
    if not key:
        return None
    letters = dict(zip(*key))
    ends = ["".join(letters.get(c, "?") for c in fields[i]) for i in (3, 4)]
    if any("?" in e or not airports.city(e) for e in ends):
        return None
    return f"Flight {ends[0]} \u2192 {ends[1]}"


# ------------------------------------------------- label 15 position reports
# Label 15 is the general-aviation position report. Two shapes seen here, both with coordinates in
# decimal degrees (the documented layout; a degrees-and-minutes reading is ruled out by reports whose
# "minutes" would exceed 59).
#
#   (2N41387W104423245 48390-55(Z      lat 41.387 lon -104.423, then 245, 48, 390, -55
#   (2N38251W122421OFF0812190755E1919------- (Z   an OFF event: date, time (the letter-and-digits after it unknown)
#
# In the first shape the 3-digit field before the temperature is ground speed: three consecutive
# reports from one aircraft ten minutes apart were 141 km apart, 457 kt, against fields of 390-409,
# and a documented example with +26°C reads 23. The field after the longitude (245) and the two
# digits before the speed (48) are unidentified and not shown.
#
#   FST01KPHXEGLLN391833W1022224390 510 107 M51C074061058058567  13220530
#
# British Airways' long-haul reports: route, position to four decimals (0.5 km from ADS-B), flight
# level, two fuel figures in hundreds (on board, then used - 510/107 leaving Phoenix for London and
# 129/577 arriving at Denver from London), temperature, wind, heading, track and ground speed (567
# against 567 from ADS-B), then ETA and the time sent.

def translate_label15(msg):
    text = msg["text"].strip()
    m = re.match(r"\(2([NS])(\d{2})(\d{3})([EW]) ?(\d{2,3})(\d{3})(.{3}) (..)(.{3})(.{3})\(Z", text)
    if m:
        lat = (int(m[2]) + int(m[3]) / 1000) * (-1 if m[1] == "S" else 1)
        lon = (int(m[5]) + int(m[6]) / 1000) * (-1 if m[4] == "W" else 1)
        speed, temp = m[9].strip(), m[10].replace(" ", "")
        summary = f"Position report at {latlon(lat, lon)}" + \
                  ((f", {int(speed)} kt" if int(speed) else ", stationary") if speed.isdigit() else "")
        details = [f"Outside air {int(temp)}°C" if re.fullmatch(r"[-+]?\d+", temp) else None]
        return result("position", summary, details, position={"lat": lat, "lon": lon, "src": "Label 15"})
    m = re.match(r"\(2([NS])(\d{2})(\d{3})([EW]) ?(\d{2,3})(\d{3})(OFF|ON|IN|OUT)(\d{6})(\d{4})", text)
    if m:
        lat = (int(m[2]) + int(m[3]) / 1000) * (-1 if m[1] == "S" else 1)
        lon = (int(m[5]) + int(m[6]) / 1000) * (-1 if m[4] == "W" else 1)
        event = {"OFF": "Took off", "ON": "Landed", "IN": "Arrived at the gate", "OUT": "Left the gate"}[m[7]]
        return result("flight", f"{event} at {hhmmss(m[9])}", [latlon(lat, lon)], position={"lat": lat, "lon": lon, "src": "Label 15"})
    m = re.match(r"FST01(" + ICAO_AP + ")(" + ICAO_AP + r")([NS])(\d{2})(\d{4})([EW])(\d{3})(\d{4})(\d{3}) *(\d+) +(\d+) *(M| )? ?(\d+)C(.*)", text)
    if m:
        orig, dest = m[1], m[2]
        lat = (int(m[4]) + int(m[5]) / 10000) * (-1 if m[3] == "S" else 1)
        lon = (int(m[7]) + int(m[8]) / 10000) * (-1 if m[6] == "W" else 1)
        level, fob, used, temp, tail = int(m[9]), int(m[10]), int(m[11]), int(m[13]) * (-1 if m[12] == "M" else 1), m[14]
        t = re.match(r"(\d{3})(\d{3})(\d{3})(\d{3})(\d{3})\s+(\d{4})(\d{4})$", tail.strip()) or \
            re.match(r"\d\d(\d{3})(\d{3})(\d{3})()(\d{3})\d{5}(\d{4})(\d{4})$", tail.strip())
        details = [f"Flight {orig} → {dest}", latlon(lat, lon), f"Outside air {temp}°C",
                   f"Fuel on board {fob * 100:,}, used {used * 100:,} (units as sent)"]
        summary = f"Position report, FL{level}"
        if t:
            wdir, wspd, hdg, _trk, gs, eta, sent = t.groups()
            summary += f", {int(gs)} kt at {hhmmss(sent)}"
            details += [f"Wind {wdir}° at {int(wspd)} kt, heading {hdg}°", f"ETA {dest} {hhmmss(eta)}"]
        return result("position", summary, details, position={"lat": lat, "lon": lon, "src": "Label 15"})
    return None


# ------------------------------------------------- UPS *POS reports (label H1)
#   *POS
#   09300432N4031W10028390260067790M5120185
#   MMDDHHMM, lat DDMM, lon DDDMM, flight level, wind direction, wind speed, Mach (thousandths),
#   temperature (sign, tenths), fuel in hundreds of pounds.
# Checked against ADS-B: Mach .790 at -51.2°C is 459 kt true, plus a 67 kt wind from 260° on a track
# of 078° gives 526 kt over the ground, and ADS-B read 526.0. Fuel fell 800 lb per nine minutes on a
# 757 in cruise, twice, which is that aircraft's burn.

def translate_ups_pos(msg):
    body = msg["text"].strip()
    tail = r"(\d{3})(\d{3})(\d{3})(\d{3})([MP])(\d{3})(\d{4})(?:TOD([A-Z]{3})([A-Z]{3}) ?(\d{3,4}))?.*"
    # Some aircraft give minutes to a tenth (N40571W105448); the body is fixed-width, so try that form first
    m = re.fullmatch(r"\*POS\s+(\d\d)(\d\d)(\d\d)(\d\d)([NS])(\d\d)(\d\d\.?\d)([EW])(\d{3})(\d\d\.?\d)" + tail, body) or \
        re.fullmatch(r"\*POS\s+(\d\d)(\d\d)(\d\d)(\d\d)([NS])(\d\d)(\d\d)([EW])(\d{3})(\d\d)" + tail, body, re.S)
    if not m:
        return None
    minutes = lambda v: int(v) / (10 if len(v) == 3 else 1)
    lat = (int(m[6]) + minutes(m[7]) / 60) * (-1 if m[5] == "S" else 1)
    lon = (int(m[9]) + minutes(m[10]) / 60) * (-1 if m[8] == "W" else 1)
    temp = int(m[16]) / 10 * (-1 if m[15] == "M" else 1)
    return result("position", f"Position report at {m[3]}:{m[4]} UTC, FL{int(m[11])}, Mach {int(m[14]) / 1000:.3f}",
                  [latlon(lat, lon), f"Outside air {temp:.1f}°C", f"Wind {m[12]}° at {int(m[13])} kt",
                   f"Fuel {int(m[17]) * 100:,} lb",
                   (f"Top of descent" + (f" at {hhmmss(m[20].zfill(4))}" if m[20] else "") + f", {m[18]} → {m[19]}") if m[18] else None],
                  position={"lat": lat, "lon": lon, "src": "UPS position report"})


# ------------------------------------------------- FedEx PO6 position reports (label H1)
#   PO6N244FE2370 136KMEMKPDX300909533569 -192 -465 360170775281600141
#   51CRS 37767 707724707725 26N 40452W10234521340 695     0
# Tail, route, DDMM HHMM, then total and static air temperature in tenths, flight level and Mach
# (thousandths); the second line has the flight phase and position in degrees and minutes (2 km
# from ADS-B at the median). The three digits after the Mach looked like a heading but agree with
# the ADS-B track only a third of the time, so they and the other numbers are not shown.

def translate_fedex_pos(msg):
    m = re.match(r"PO\d(N\w+FE).{4} .{3}(" + ICAO_AP + ")(" + ICAO_AP + r")(\d\d)(\d\d)(\d\d)(\d\d)[\d ]{4} *(-?\d+) +(-?\d+) (\d{3})\d{3}(\d{3})(\d{3})\d+\s+\d\d(CRS|CLMB|DESC|TKOF|SCR\d|CR(?= ))", msg["text"])
    if not m:
        return None
    # Degrees then minutes in tenths, each right-aligned in three characters: "N 40452W105 49" is 40°45.2' 105°04.9'
    pos = re.search(r"N([ \d]\d\d)([ \d]\d\d)W([ \d]\d\d)([ \d]\d\d)", msg["text"])
    lat = lon = None
    if pos:
        lat = int(pos[1]) + int(pos[2]) / 600
        lon = -(int(pos[3]) + int(pos[4]) / 600)
    phase = {"CRS": "cruise", "CR": "cruise", "CLMB": "climb", "DESC": "descent", "TKOF": "takeoff"}.get(m[13], f"phase {m[13]}")
    return result("position", f"Position report at {m[6]}:{m[7]} UTC, FL{int(m[10])}, Mach {int(m[11]) / 1000:.3f}, {phase}",
                  [f"Flight {m[2]} → {m[3]}", latlon(lat, lon) if lat else None,
                   f"Static air {int(m[9]) / 10:.1f}°C, total air {int(m[8]) / 10:.1f}°C"],
                  position={"lat": lat, "lon": lon, "src": "FedEx position report"} if lat else None)


# ------------------------------------------------- JetBlue / Air Canada T8 reports (label H1)
#   R721V0201,180302,, N3149J,5318410-07,736205,736509,00012,KONT,KJFK,07:24:37,2026:09:30,JBU454 ,...
#   ...,39.6328,39.6320,-102.0671,-102.0675,533.0,533.0,60.69,60.69,...
# Report name, tail, route, time and date, then paired values; the first paired lat/lon/ground speed
# match ADS-B exactly. The engine values that follow are not identified.

def translate_t8_report(msg):
    m = re.match(r"R(\d{3})V(\d{4}),\d+,, ?(\S+),.*?,(" + ICAO_AP + "),(" + ICAO_AP + r"),(\d\d:\d\d:\d\d),(\d{4}):(\d\d):(\d\d),", msg["text"])
    if not m:
        return None
    pos = re.search(r",(-?\d{1,2}\.\d{4}),-?\d{1,2}\.\d{4},(-?\d{1,3}\.\d{4}),-?\d{1,3}\.\d{4},(\d{1,3}\.\d),\d{1,3}\.\d,", msg["text"])
    details = [f"Flight {m[4]} → {m[5]}", f"Generated {m[6]} UTC"]
    position = None
    if pos:
        lat, lon = float(pos[1]), float(pos[2])
        details.append(f"At {latlon(lat, lon)}, {float(pos[3]):.0f} kt")
        position = {"lat": lat, "lon": lon, "src": "T8 report"}
    return result("data", f"Aircraft monitoring report R{m[1]}", details, position=position)


# ----------------------------------------------------------------- dispatcher

def translate(msg, raw=None):
    kind, label, sub = msg.get("kind"), msg.get("label") or "", msg.get("sublabel") or ""
    text = (msg.get("text") or "").strip()
    flight = msg.get("flight") or ""
    airline = flight[:2]
    # A decoder that joins the blocks of an H1 downlink itself leaves each later block's
    # "#<sublabel>B" header (ARINC 620 5.4.2) in the middle of the text; take those out,
    # keeping the leading one the readers below expect.
    header = re.match(r"#[A-Z0-9]{2}B", text) if label == "H1" else None
    if header and header[0] in text[4:]:
        text = text[:4] + text[4:].replace(header[0], "")
        msg = dict(msg, text=text)

    if kind == "xid":
        p = msg.get("position")
        return result("link", f"VDL2 {msg.get('text') or 'link management'}",
                      [f"Aircraft at {latlon(p['lat'], p['lon'])}" + (f", {feet(p.get('alt'))}" if p.get("alt") else "")
                       if p else None])
    if kind == "partial":
        return result("link", "Part of a multi-block message (waiting for the rest)")
    if kind == "link":
        return result("link", {"_d": "Acknowledgement / keep-alive (no text)", "Q0": "Link test (no text)"}
                      .get(label, f"Link-layer frame ({msg.get('text') or 'no text'})"))
    if kind == "atn":
        return result("link", "ATN network frame")

    cpdlc = translate_cpdlc(raw) if kind == "cpdlc" or (raw and "arinc622" in str(raw)[:4000]) else None
    if cpdlc:
        return cpdlc
    if (msg.get("decoded") or "").startswith("Boeing OHMA") or text.startswith("OHMA") or (label == "H1" and sub == "T1"):
        return result("data", "Boeing OHMA aircraft health report", ["Onboard health-management data"])

    if label == "SQ":
        m = re.match(r"\d\dX([A-Z])([A-Z]{3})(" + ICAO_AP + r")(\d)(\d{4})([NS])(\d{5})([EW])V?(\d{6})?/?(\w+)?", text)
        if m:
            return result("link", f"Ground station broadcast: {m.group(10) or ''} station {m.group(2)} at {m.group(3)}".replace("  ", " "),
                          [f"VDL2 frequency {int(m.group(9)) / 1000:.3f} MHz" if m.group(9) else None])
        return result("link", "Ground station broadcast (squitter)")
    if label == "SA":
        link = find_key(raw, "current_link") if raw else None
        if isinstance(link, dict) and link.get("descr"):
            state = "now connected via" if link.get("established") else "lost connection via"
            return result("link", f"Datalink status: {state} {link['descr']}")
        return result("link", "Datalink status advisory")
    if not text:
        return result("link", f"Message with no text (label {label})")

    afn = translate_afn(text) if ".AFN/" in text[:40] else None
    if afn:
        return afn

    # Flight computer reports (H1 M1/M2, and United's HDQ-addressed copies)
    if label == "H1" or label.startswith("BA"):
        # The sublabel can sit inline ("#M1B/HDQDLUA.RESREQ", "#M1B/.POS"), which hid these from
        # every pattern below.
        text = re.sub(r"^(?:- ?)?#M\dB/?(?=[A-Z0-9]{7}\.|\.)\.?", "", text)
        # "POS/TS065719,300926N41040W105051,,065719,410,CYS,..." is the POSN report behind a
        # timestamp: the same fields in the same order.
        stamped = re.match(r"POS/TS\d{6},\d{6}([NS]\d{5}[EW]\d{6},.*)", text)
        posn = translate_posn("POSN" + stamped[1] if stamped else text)
        if posn:
            return posn
        body = re.sub(r"^/?[A-Z0-9]{7}\.", "", text)
        fmc = translate_fmc_exchange(body)
        if fmc:
            return fmc
        code = re.match(r"([A-Z]{3,6})[/,]", body)
        if code and code.group(1) in FMC_CODES:
            return result("flight", FMC_CODES[code.group(1)])
        a702 = translate_arinc702(text)
        if a702:
            return a702
        if sub in ("M1", "M2", "M3"):
            if re.match(r"[A-Z0-9.]*\.\.[A-Z0-9]+:[A-Z]:", body) or re.search(r":[AF]:[A-Z0-9]", body):
                return result("flight", "Flight plan route data (continued)")
            points = re.findall(r"[NS]\d{5}[EW]\d{6}", body)
            if len(points) >= 2:
                return result("flight", f"Flight computer route prediction ({plural(len(points), 'waypoint')} with predicted times)")
            if body.startswith("/") and ".OK" in body[:14] or "DFDAU" in body:
                return result("data", "Avionics data-loader status message")
            return result("flight", "Flight management computer report" +
                          (f" ({code.group(1)})" if code else ""), generic=True)

    if airline == "UA" and text.startswith("/") and re.match(r"/\w\w (?:[A-Z]{6}(?![A-Z])|.{2,18}?\s*/)", text):
        r = translate_united_slash(msg)
        if r:
            return r
    if airline == "AA" and label == "5Z":
        m = re.match(r"OS (" + ICAO_AP + r")\s*(.*)", text, re.S)
        if m:
            rest = m.group(2)
            parts = [f"departure runway {d}" for d in re.findall(r"/DPR(\d\d[LRC]?)", rest)] + \
                    [f"arrival runway {a}" for a in re.findall(r"/ARR(\d\d[LRC]?)", rest)]
            words = [l for l in re.split(r"[\r\n]+", rest)[1:] if re.search(r"[A-Z]{2,}", l)]
            if words:
                return result("crew", f"Crew message about {m.group(1)}: “{reflow(words)}”")
            return result("flight", f"Operations message for {m.group(1)}" + (f": {', '.join(parts)}" if parts else ""))
    if airline == "UA" and label == "33":  # continuation blocks of the system configuration report
        return result("data", "Avionics system configuration report")
    if airline == "UA" and label == "5Z":
        lines = free_text_lines(text)
        if lines:
            return result("maintenance" if re.search(r"RESET|FAULT|FAIL|INOP|MEL", text) else "crew",
                          f"Message to airline operations: “{reflow(lines)}”")

    if label == "15":
        r = translate_label15(msg)
        if r:
            return r
    if label == "16" or "/AUTPOS/" in text:
        report = translate_autpos(msg)
        if report:
            return report
    if label == "B9":
        m = re.search(r"TI2/\d{3}(" + ICAO_AP + ")", text)
        return result("request", f"Crew requested the digital ATIS for {m.group(1)}" if m else "Crew requested a digital ATIS")
    if label in ("5U",) or re.match(r"\w\w,WX,", text):
        stations = list(dict.fromkeys(re.findall(r"(?<![A-Z])(" + ICAO_AP + r")(?![A-Z])", text)))
        return result("request", f"Crew requested weather for {', '.join(stations)}" if stations else "Crew requested weather")
    if label in ("QQ",):
        m = re.match(r"(" + ICAO_AP + ")(" + ICAO_AP + r")(\d{4})", text)
        if m:
            return result("flight", f"Took off from {m.group(1)} at {hhmmss(m.group(3))}, bound for {m.group(2)}")
    oooi = {"QP": "Left the gate (OUT)", "QQ": "Took off (OFF)", "QR": "Landed (ON)", "QS": "Arrived at the gate (IN)"}
    if label in oooi:
        return result("flight", oooi[label])
    # Fixed-column formats: keep leading blanks, and read NULs - which ARINC 620 allows for
    # fields with no data - as the spaces they stand for.
    spec = translate_arinc620(label, (msg.get("text") or "").replace("\x00", " ").rstrip())
    if spec:
        return spec

    if airline == "WN" and label == "37":
        return translate_southwest(msg) or result("encoded", "Southwest airline data message (encoded)")
    if airline == "OO" and text.startswith("P,"):
        r = translate_skywest(msg)
        if r:
            return r
    if airline == "F9" and label in ("21", "22"):
        r = translate_frontier(msg)
        if r:
            return r
    if label == "83":
        m = re.match(r"\d{3}PR\d\d(\d{6})[NS]\d{4}\.\d[EW]\d{5}\.\d(\d{5})", text)
        if m:
            return result("position", f"Position report at {hhmmss(m.group(1))}, {feet(m.group(2))}", [pos_detail(msg)])
    if label == "16":
        m = re.match(r"(\d{6}),(\d{4,5}),", text)
        if m and msg.get("position"):
            return result("position", f"Position report at {hhmmss(m.group(1))}, {feet(m.group(2))}", [pos_detail(msg)])
    if label == "33" and airline == "G4":
        m = re.search(r"(\d{6}),[NS] ?[\d.]+,[EW] ?[\d.]+,(\d+), *\d+,(" + ICAO_AP + "),(" + ICAO_AP + ")", text)
        if m:
            return result("position", f"Position report at {hhmmss(m.group(1))}, {feet(m.group(2))}",
                          [pos_detail(msg), f"Flight {m.group(3)} → {m.group(4)}"])
    bizjet = label == "44" or (airline in ("XA", "GS", "ZD", "OO") and label in ("17", "41", "42", "48", "88"))
    if bizjet:
        r = translate_label44(msg)
        if r:
            return r
    if label == "80":
        r = translate_label80(msg)
        if r:
            return r
    m = re.match(r"AGFSR (\w+)/\d\d/\d\d/(\w{3})(\w{3})/(\d{4})Z/\d+/(\d{4}\.\d[NS]\d{5}\.\d[EW])/(\d{3})/[^/]*/\d*/\d*/([MP]\d\d)/(\d{3})(\d{3})", text)
    if m:
        temp = int(m.group(7)[1:]) * (-1 if m.group(7)[0] == "M" else 1)
        return result("position", f"Position report at {hhmmss(m.group(4))}, FL{int(m.group(6))}",
                      [f"Flight {m.group(2)} → {m.group(3)}", pos_detail(msg), f"Outside air {temp}°C",
                       f"Wind {m.group(8)}° at {int(m.group(9))} kt"])
    if label == "4A":
        r = translate_label4a(msg)
        if r:
            return r
    # Delta free-text labels: "141530 KPDX KATL6" header, then the message
    m = re.match(r"(\d{6}) (" + ICAO_AP + ") (" + ICAO_AP + r")\d?\s*(.*)", text, re.S) if airline in ("DL", "NW") else None
    if m:
        body = [l for l in re.split(r"[\r\n]+", m.group(4))
                if l.strip() and not l.strip().startswith("/")]
        report = translate_waypoint_report(body[0].strip(), m.group(2), m.group(3)) if body else None
        if report:
            return report
        if label == "48":
            report = translate_connection_request(text, m.group(2), m.group(3))
            if report:
                return report
        # digit_ratio keeps numeric report lines - winds, positions - out of the crew bucket.
        words = [l for l in body
                 if re.search(r"[A-Z]{2,}", l) and not re.search(r"\[\s*\]", l) and len(l.strip()) > 1
                 and digit_ratio(l) < 0.5]
        if words:
            return result("crew", f"Crew message: “{reflow(words)}”", [f"Flight {m.group(2)} → {m.group(3)}"])
        # The six leading digits are the flight's day and hour plus the label, not a clock time.
        if msg.get("position"):
            return result("position", f"Position report, {m.group(2)} → {m.group(3)}", [pos_detail(msg)])
        return result("flight", f"Flight message {m.group(2)} → {m.group(3)}")
    m = re.match(r"WXR\d\d\s+((?:" + ICAO_AP + r",?)+)", text)
    if m:
        airports = ", ".join(re.findall(ICAO_AP, m.group(1)))
        return result("request", f"Crew requested weather for {airports}")
    m = re.match(r"\d+,[A-Z],\d\d,(" + ICAO_AP + "),(" + ICAO_AP + r"),(\d\d[LRC]?)/", text)
    if m:
        return result("flight", f"Runway data for {m.group(2)} runway {m.group(3)}", [f"Flight {m.group(1)} → {m.group(2)}"])
    if label == "10" and text.startswith("/N"):
        m = re.search(r"/(" + ICAO_AP + r")/(\d{4})/", text)
        return result("position", "Position and progress report" + (f", destination {m.group(1)} ETA {hhmmss(m.group(2))}" if m else ""),
                      [pos_detail(msg)])

    for parser in (translate_ups_pos, translate_fedex_pos, translate_t8_report):
        r = parser(msg)
        if r:
            return r
    wx = translate_weather(msg)
    if wx:
        return wx
    report = translate_monitoring(msg)
    if report:
        return report

    if sub == "CF" or re.match(r"WRN/|FLR/|FAULT", text):
        m = re.search(r"/([A-Z][A-Z0-9 ]{5,})$", text)
        if m:
            return result("maintenance", f"Maintenance alert: {m.group(1).strip()}", ["Central fault display system"])
        return result("maintenance", "Maintenance fault data", ["Central fault display system"])

    lines = free_text_lines(text)
    route = route_in(text)
    if lines and label not in ("H1",):
        return result("crew", f"Crew / dispatch message: “{reflow(lines)}”",
                      [f"Flight {route[0]} → {route[1]}" if route else None])

    if bizjet:
        report = translate_bizjet(msg)
        if report:
            return report
    if label != "H1" and looks_encoded(text):
        return result("encoded", "Airline data message (encoded)", [f"Label {label}"])

    if label == "H1" or sub in ("DF", "T1"):
        details = [f"Flight {route[0]} → {route[1]}" if route else None]
        if msg.get("position"):
            details.append(f"Includes position {pos_detail(msg)}")
        return result("data", "Engine/aircraft condition monitoring data (automated)", details, generic=True)

    if msg.get("position"):
        return result("position", "Position report", [pos_detail(msg)])
    if digit_ratio(text) > 0.5:
        return result("data", f"Airline data report (label {label})",
                      [f"Flight {route[0]} → {route[1]}" if route else None], generic=True)
    return result("other", f"Airline-defined message (label {label})",
                  [f"Flight {route[0]} → {route[1]}" if route else None], generic=True)


# ------------------------------------------------- structured data for the map

def _obs_time(hhmm, ref_ts):
    """Epoch seconds for an HHMM (UTC) sample time on the day of the message (handles midnight rollover)."""
    import datetime as dt
    ref = dt.datetime.fromtimestamp(ref_ts, dt.timezone.utc)
    t = ref.replace(hour=int(hhmm[:2]) % 24, minute=int(hhmm[2:4]) % 60, second=0, microsecond=0)
    delta = (t - ref).total_seconds()
    if delta > 43200:
        t -= dt.timedelta(days=1)
    elif delta < -43200:
        t += dt.timedelta(days=1)
    return t.timestamp()


def observations(msg):
    """Aircraft weather observations in a message: [{ts, lat, lon, alt, temp, wdir, wspd}] (may be empty)."""
    text, ts = msg.get("text") or "", msg.get("ts") or 0
    out = []
    found = weather_samples(text) if msg.get("kind") in ("acars", None) else None
    if found:
        for s in found[2]:
            if -90 <= s["lat"] <= 90 and -180 <= s["lon"] <= 180 and s["alt"] > 0:
                out.append({"ts": _obs_time(s["time"], ts), "lat": round(s["lat"], 4), "lon": round(s["lon"], 4),
                            "alt": s["alt"], "temp": s["temp"], "wdir": s["wdir"], "wspd": s["wspd"]})
        return out
    # Flight-computer position report: temperature and wind at the reported flight level
    m = re.search(r"POSN([NS]?)(\d{2})(\d{2})(\d)([EW])(\d{3})(\d{2})(\d),[^,]*,(\d{6}),(\d{2,3}),[^,]*,(?:\d{6})?,[^,]*,([MP]\d{1,2}),(\d{3})(\d{1,3})", text)
    if m:
        lat = (int(m[2]) + (int(m[3]) + int(m[4]) / 10) / 60) * (-1 if m[1] == "S" else 1)
        lon = (int(m[6]) + (int(m[7]) + int(m[8]) / 10) / 60) * (-1 if m[5] == "W" else 1)
        out.append({"ts": _obs_time(m[9][:4], ts), "lat": round(lat, 4), "lon": round(lon, 4), "alt": int(m[10]) * 100,
                    "temp": int(m[11][1:]) * (-1 if m[11][0] == "M" else 1), "wdir": int(m[12]), "wspd": int(m[13])})
        return out
    # Label 4T AGFSR: 4034.0N10232.1W/370/…/M48/243054
    m = re.search(r"AGFSR \w+/\d\d/\d\d/\w{6}/(\d{4})Z/\d+/(\d\d)(\d\d\.\d)([NS])(\d{3})(\d\d\.\d)([EW])/(\d{3})/[^/]*/\d*/\d*/([MP]\d\d)/(\d{3})(\d{3})", text)
    if m:
        lat = (int(m[2]) + float(m[3]) / 60) * (-1 if m[4] == "S" else 1)
        lon = (int(m[5]) + float(m[6]) / 60) * (-1 if m[7] == "W" else 1)
        out.append({"ts": _obs_time(m[1], ts), "lat": round(lat, 4), "lon": round(lon, 4), "alt": int(m[8]) * 100,
                    "temp": int(m[9][1:]) * (-1 if m[9][0] == "M" else 1), "wdir": int(m[10]), "wspd": int(m[11])})
    return out


def parse_squitter(text):
    """Ground station squitter (label SQ), e.g. 02XADENKDEN53951N10440WV136975/ARINC."""
    m = re.match(r"0(\d)X([AS])([A-Z]{3})([A-Z]{4})(\d)(\d{2})(\d{2})([NS])(\d{3})(\d{2})([EW])V?(\d{6})?", text or "")
    if not m:
        return None
    lat = (int(m[6]) + int(m[7]) / 60) * (-1 if m[8] == "S" else 1)
    lon = (int(m[9]) + int(m[10]) / 60) * (-1 if m[11] == "W" else 1)
    return {"id": f"{m[4]}{m[5]}", "network": {"A": "ARINC", "S": "SITA"}[m[2]], "iata": m[3], "icao": m[4],
            "lat": round(lat, 4), "lon": round(lon, 4), "vdl_freq": int(m[12]) / 1000 if m[12] else None}
