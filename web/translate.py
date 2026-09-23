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


def result(category, summary, details=None, generic=False, position=None):
    """generic=True marks fallback descriptions that didn't recognise the message format.

    position is for formats whose coordinates only appear once decoded, so parse_position
    can't see them in the raw text; the server range-checks it before using it."""
    out = {"category": category, "summary": summary, "details": [d for d in (details or []) if d]}
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
    route = re.search(r"/RI:DA:(" + ICAO_AP + r"):AA:(" + ICAO_AP + r")", body)
    if route:
        details.insert(0, f"Flight {route.group(1)} → {route.group(2)}")
    return result("position", summary, details)


# ------------------------------------------------------------ airline formats

UA_CODES = {
    "B6": "Crew requested landing performance data",
    "R3": "Crew requested a Howgozit (fuel and schedule progress) report",
    "C3": "Crew requested a gate assignment",
    "ET": "Estimated arrival time report",
    "33": "Avionics system configuration report",
    "14": "Takeoff (OFF) report",
    "13": "Pushback (OUT) report",
    "15": "Landing (ON) report",
    "16": "Gate arrival (IN) report",
    "71": "Message to maintenance control",
}


def translate_united_slash(msg):
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
    # Southwest ++765xx/++865xx (documented AMDAR layout): lat DDMM.m, lon, DDHHMM, alt ft, temp °C, wind, phase
    m = re.match(r"\+\+\d+,[^,]*,[^,]*,\d{6},[^,]*,(" + ICAO_AP + "),(" + ICAO_AP + ")", text) or \
        re.match(r"ABS\d{3}[A-Z]{2}_\S+?,[^,]*,[^,]*,(" + ICAO_AP + "),(" + ICAO_AP + ")", text) or \
        re.match(r"(?:[^\n]*\n)?()()[NS]\d{4}\.\d,[EW]\d{5}\.\d,\d{6},\d+,", text)  # continuation block: samples only
    if m:
        rows = re.findall(r"([NS])(\d{2})(\d{2}\.\d),([EW])(\d{3})(\d{2}\.\d),\d\d(\d{4}),(\d+), ?(-?\d+\.\d),(\d{3}),(\d{3}),(\w\w)", text)
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
    m = re.match(r"\d{5}\r?\n\d\dE\d\d(" + ICAO_AP + ")(" + ICAO_AP + ")", text) or \
        re.match(r"D3M\d{3}(" + ICAO_AP + ")(" + ICAO_AP + r")N\d{5}W", text) or \
        re.search(r"WX\d\dEN\d\d(" + ICAO_AP + ")(" + ICAO_AP + ")", text)
    if m:
        tenths = text.lstrip("/ ").startswith(("WX", "A3", "A2")) or "/WX" in text[:60]  # Delta format uses tenths
        rows = re.findall(r"N(\d{5})W(\d{6})(\d{4})(\d{4})([MP])(\d{3})(\d{3})(\d{3})", text)
        samples = [{"lat": int(la) / 1000, "lon": -int(lo) / 1000, "time": t, "alt": int(alt) * 10,
                    "temp": _signed(sg, tv, tenths), "wdir": int(wd), "wspd": int(ws)}
                   for la, lo, t, alt, sg, tv, wd, ws in rows]
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
               "CRZ": "cruise", "CR": "cruise", "DES": "descent", "DESC": "descent", "DE": "descent", "DC": "descent",
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


def translate_monitoring(msg):
    text = msg["text"]
    tagged = translate_tagged_weather(text)
    if tagged:
        return tagged
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
    m = re.match(r"(A\d\d)/A\d{5},\d,\d/CC\w+,[A-Z]{3}\d\d,(\d{6}),(" + ICAO_AP + "),(" + ICAO_AP + ")", text)
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
        if re.match(rf"\s*(?:\w{{4}}\s+)?{code}\b|\s*{code}\d", text):
            return result("data", name + (f", {phase_name}" if phase_name else ""), [route_detail])
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
    m = re.match(r"0*(ETA|POS|OFF|ON|IN)0\d$", parts[0]) if parts else None
    if not m or len(parts) < 5:
        return None
    event = m.group(1)
    pos = pos_detail(msg)
    if event in ("ETA", "POS") and len(parts) >= 8:
        level = "on the ground" if parts[2] in ("GRD", "***") else f"FL{int(parts[2])}" if parts[2].isdigit() else None
        eta = parts[7][:4] if re.fullmatch(r"\d{4,6}", parts[7]) else None
        summary = ("ETA report" if event == "ETA" else "Position report") + f", {parts[3]} → {parts[4]}"
        return result("flight" if event == "ETA" else "position", summary,
                      [level, f"ETA {hhmmss(eta)}" if eta else None, pos])
    names = {"OFF": "Took off", "ON": "Landed", "IN": "Arrived at the gate"}
    time = next((p for p in parts[5:] if re.fullmatch(r"\d{4}", p)), None)
    return result("flight", f"{names[event]}: {parts[2]} → {parts[3]}" + (f" at {hhmmss(time)}" if time else ""), [pos])


LABEL80_TYPES = {"POSRPT": "Position report", "INRANG": "In-range report", "DSPTCH": "Message to dispatch",
                 "ETARPT": "ETA report", "OUTRPT": "Left the gate", "OFFRPT": "Took off", "ONRPT": "Landed",
                 "INRPT": "Arrived at the gate", "DIVERT": "Diversion report"}


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


def translate_southwest(msg):
    """Position, altitude, speed, fuel and weight out of a Southwest label 37 report."""
    head, _, body = msg["text"].partition("\r\n")
    key = SOUTHWEST_KEYS.get(head[:2])
    if not key or not body:
        return None
    sep, digits = key
    fields = body.split(sep)
    if tuple(len(f) for f in fields) != SOUTHWEST_SHAPE:
        return None
    table = {c: str(i) for i, c in enumerate(digits)}

    def num(field, *positions):
        out = "".join(table.get(field[p], " ") for p in positions)
        return None if " " in out else out

    # The two coordinate fields carry a hemisphere letter, then degrees, a point, thousandths.
    lat, lon = num(fields[2], 2, 3, 5, 6, 7), num(fields[3], 1, 2, 3, 5, 6, 7)
    alt, mach = num(fields[4], *range(5)), num(fields[7], 1, 2, 3)
    if not (lat and lon and alt and fields[2][4] == fields[3][4]):
        return None
    lat, lon = int(lat) / 1000, -int(lon) / 1000
    tas, fob = num(fields[6], 0, 1, 2), num(fields[8], 0, 1, 3)
    eta, weight = num(fields[9], *range(4)), num(fields[14], *range(6))
    summary = f"Position report, {feet(int(alt))}" + (f", Mach {int(mach) / 1000:.3f}" if mach else "")
    return result("position", summary,
                  [latlon(lat, lon),
                   f"True airspeed {int(tas)} kt" if tas else None,
                   f"Fuel on board {int(fob) * 100:,} lb" if fob else None,
                   f"ETA {hhmmss(eta)}" if eta and int(eta[:2]) < 24 else None,
                   f"Gross weight {int(weight):,} lb" if weight else None],
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


# ----------------------------------------------------------------- dispatcher

def translate(msg, raw=None):
    kind, label, sub = msg.get("kind"), msg.get("label") or "", msg.get("sublabel") or ""
    text = (msg.get("text") or "").strip()
    flight = msg.get("flight") or ""
    airline = flight[:2]

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

    # Flight computer reports (H1 M1/M2, and United's HDQ-addressed copies)
    if label == "H1" or label.startswith("BA"):
        posn = translate_posn(text)
        if posn:
            return posn
        body = re.sub(r"^/?[A-Z0-9]{7}\.", "", text)
        code = re.match(r"([A-Z]{3,6})[/,]", body)
        if code and code.group(1) in FMC_CODES:
            return result("flight", FMC_CODES[code.group(1)])
        # RESxxx / REJxxx: flight computer accepted / rejected a ground uplink of type xxx
        reply = re.match(r"(RES|REJ)(PWI|FPN|POS|PER|PRG|INR)$", code.group(1)) if code else None
        if reply:
            what = {"PWI": "predicted winds", "FPN": "flight plan", "POS": "position request",
                    "PER": "performance data", "PRG": "progress request", "INR": "initialization data"}[reply.group(2)]
            verb = "accepted" if reply.group(1) == "RES" else "rejected"
            return result("flight", f"Flight computer {verb} the {what} sent from the ground")
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

    if airline == "UA" and text.startswith("/") and re.match(r"/\w\w .{2,18}?\s*/", text):
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
    if label == "44":
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
