#!/usr/bin/env python3
"""ACARS/VDL2 live web display.

Ingests JSON from acarsdec (UDP) and dumpvdl2 (UDP), keeps recent messages in memory,
tracks aircraft, enriches them from a Virtual Radar Server aircraft list (optional), and
pushes everything to browsers over a WebSocket.
"""
import asyncio
import json
import logging
import math
import os
import re
import time
from collections import Counter, OrderedDict, deque
from pathlib import Path

import aiohttp
from aiohttp import web

from airlines import IATA_TO_ICAO
from monitor import GroundStations, HealthMonitor, WeatherStore, process_memory_mb, stats_summary
from translate import MESSAGE_TYPES, message_type, observations, parse_squitter, translate

HERE = Path(__file__).resolve().parent
log = logging.getLogger("acarsweb")

# Labels that only carry link housekeeping when their text is empty.
LINK_LABELS = {"_d", "Q0", "SQ", "5V", "_\x7f"}
REPEAT_WINDOW_SECS = 120
VRS_STALE_SECS = 60  # forget the VRS aircraft list if polls have failed for this long
RECENT_PER_TYPE = 3  # English timeline entries kept per message type for each flight card

CONFIG_PATH = Path(os.environ.get("ACARS_CONFIG", HERE.parent / "config.json"))


def load_config():
    """Shared config.json in the project root (or $ACARS_CONFIG), used by the receivers too."""
    return json.loads(CONFIG_PATH.read_text())


# ---------------------------------------------------------------- helpers

def clean_reg(reg):
    reg = (reg or "").strip().lstrip(".").upper()
    return reg or None


def reg_key(reg):
    return re.sub(r"[^A-Z0-9]", "", reg.upper()) if reg else None


def clean_flight(flight):
    flight = (flight or "").strip().upper()
    return flight or None


def flight_to_callsign(flight):
    """UA0655 -> UAL655; returns None if the airline isn't known."""
    m = re.fullmatch(r"([A-Z0-9]{2})0*(\d{1,4}[A-Z]?)", flight or "")
    if not m or m.group(1) not in IATA_TO_ICAO:
        return None
    return IATA_TO_ICAO[m.group(1)] + m.group(2)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def _hemi(value, letter):
    return -value if letter in "SW" else value


POS_PATTERNS = [
    # N 394358W1043932  (degrees, minutes, seconds)
    (re.compile(r"(?<![A-Z0-9])([NS]) ?(\d{2})(\d{2})(\d{2})([EW]) ?(\d{3})(\d{2})(\d{2})(?!\d)"),
     lambda m: (_hemi(int(m[2]) + int(m[3]) / 60 + int(m[4]) / 3600, m[1]),
                _hemi(int(m[6]) + int(m[7]) / 60 + int(m[8]) / 3600, m[5]))),
    # POSN40231W101251 / N40172W101186  (degrees, minutes and tenths)
    (re.compile(r"(?:POSN ?([NS]?)|(?<![A-Z])([NS]) ?)(\d{2})(\d{2})(\d)([EW]) ?(\d{3})(\d{2})(\d)(?!\d)"),
     lambda m: (_hemi(int(m[3]) + (int(m[4]) + int(m[5]) / 10) / 60, m[1] or m[2] or "N"),
                _hemi(int(m[7]) + (int(m[8]) + int(m[9]) / 10) / 60, m[6]))),
    # N3904.4,W10314.9  (degrees and decimal minutes)
    (re.compile(r"(?<![A-Z])([NS]) ?(\d{2})(\d{2}\.\d+)[ ,/]*([EW]) ?(\d{3})(\d{2}\.\d+)"),
     lambda m: (_hemi(int(m[2]) + float(m[3]) / 60, m[1]), _hemi(int(m[5]) + float(m[6]) / 60, m[4]))),
    # N 41.452 W103.044  /  N 40.791,W105.477  (decimal degrees)
    (re.compile(r"(?<![A-Z0-9])([NS]) ?(\d{1,2}\.\d{2,})[ ,/]*([EW]) ?(\d{1,3}\.\d{2,})"),
     lambda m: (_hemi(float(m[2]), m[1]), _hemi(float(m[4]), m[3]))),
    # POSN 39.732W104.659  (decimal, north implied)
    (re.compile(r"POSN ?(-?\d{1,2}\.\d+)([EW])(\d{1,3}\.\d+)"),
     lambda m: (float(m[1]), _hemi(float(m[3]), m[2]))),
]


def parse_position(text, home, max_km):
    if not text:
        return None
    for pattern, convert in POS_PATTERNS:
        for m in pattern.finditer(text):
            try:
                lat, lon = convert(m)
            except (ValueError, ZeroDivisionError):
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180 and \
                    haversine_km(lat, lon, home["lat"], home["lon"]) <= max_km:
                return {"lat": round(lat, 4), "lon": round(lon, 4)}
    return None


def walk_leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_leaves(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_leaves(v, path + (str(i),))
    else:
        yield path, obj


def summarize_decoded(obj):
    """Human summary of a libacars decode (CPDLC, ADS-C, MIAM, ...)."""
    skip = {"choice", "err", "crc_ok", "msg_type", "gs_addr", "air_addr", "msg_id", "msg_ref"}
    labels, details, msg_types = [], [], []
    for path, value in walk_leaves(obj):
        key = path[-1] if path else ""
        if key == "choice_label":
            labels.append(str(value))
        elif key == "msg_type":
            msg_types.append(str(value).replace("fans1a_", "FANS-1/A ").replace("_", " "))
        elif key not in skip and "header" not in path and value is not None:
            details.append(f"{key}: {str(value)[:80]}")
    if not labels:
        labels = msg_types
    parts = []
    if labels:
        parts.append(" / ".join(dict.fromkeys(labels)))
    if details:
        parts.append(", ".join(details[:12]))
    return " — ".join(parts) or None


# ------------------------------------------------------------- normalizers

def normalize_acars(d, cfg):
    if d.get("assstat") == "duplicate":
        return None
    text = d.get("text") or ""
    label = d.get("label")
    block_id = str(d.get("block_id") or "")
    msg = {
        "source": "ACARS",
        "ts": float(d.get("timestamp") or time.time()),
        "freq": d.get("freq"),
        "level": d.get("level"),
        "noise": d.get("noise"),
        "errors": d.get("error", 0),
        "icao": None,
        "reg": clean_reg(d.get("tail")),
        "flight": clean_flight(d.get("flight")),
        "direction": "up" if block_id.isalpha() else "down",
        "gs": None,
        "label": label,
        "sublabel": d.get("sublabel"),
        "msgno": d.get("msgno"),
        "text": text,
        "decoded": None,
        "position": None,
        "kind": "acars",
    }
    lib = d.get("libacars")
    reasm = [val for path, val in walk_leaves(lib) if path[-1] == "reasm_status"] if lib else []
    if isinstance(lib, dict) and "ohma" in lib:
        msg["decoded"] = "Boeing OHMA health-monitoring report"
        msg["text"] = lib["ohma"].get("text") or text
    elif lib:
        msg["decoded"] = summarize_decoded(lib)
        msg["kind"] = "cpdlc" if "cpdlc" in json.dumps(lib) else "acars"
    if d.get("assstat") == "in progress" or "in progress" in reasm:
        msg["kind"] = "partial"
    elif label in LINK_LABELS and not text.strip():
        msg["kind"] = "link"
    if msg["kind"] in ("acars", "cpdlc"):
        pos = parse_position(text, cfg["home"], cfg["max_position_km"])
        if pos:
            msg["position"] = dict(pos, src="ACARS text")
    return msg


def normalize_vdl2(d, cfg):
    v = d.get("vdl2") or {}
    av = v.get("avlc") or {}
    src, dst = av.get("src") or {}, av.get("dst") or {}
    t = v.get("t") or {}
    msg = {
        "source": "VDL2",
        "ts": t.get("sec", time.time()) + t.get("usec", 0) / 1e6,
        "freq": round(v["freq"] / 1e6, 3) if v.get("freq") else None,
        "level": round(v["sig_level"], 1) if v.get("sig_level") is not None else None,
        "noise": round(v["noise_level"], 1) if v.get("noise_level") is not None else None,
        "errors": v.get("octets_corrected_by_fec", 0),
        "icao": None, "reg": None, "flight": None, "direction": None, "gs": None,
        "label": None, "sublabel": None, "msgno": None, "text": "",
        "decoded": None, "position": None, "kind": "link",
    }
    if src.get("type") == "Aircraft":
        msg.update(icao=src.get("addr"), direction="down", gs=dst.get("addr"))
    elif dst.get("type") == "Aircraft":
        msg.update(icao=dst.get("addr"), direction="up", gs=src.get("addr"))
    else:
        msg["gs"] = src.get("addr")

    ac, xid = av.get("acars"), av.get("xid")
    if ac:
        text = ac.get("msg_text") or ""
        msg.update(reg=clean_reg(ac.get("reg")), flight=clean_flight(ac.get("flight")),
                   label=ac.get("label"), sublabel=ac.get("sublabel"),
                   msgno=(ac.get("msg_num") or "") + (ac.get("msg_num_seq") or "") or None,
                   text=text, kind="acars")
        extra = {k: val for k, val in ac.items() if isinstance(val, dict)}
        reasm = [val for path, val in walk_leaves(ac) if path[-1] == "reasm_status"]
        if "ohma" in extra:
            msg["decoded"] = "Boeing OHMA health-monitoring report"
            msg["text"] = extra["ohma"].get("text") or text
        elif extra:
            msg["decoded"] = summarize_decoded(extra)
            if "cpdlc" in json.dumps(extra):
                msg["kind"] = "cpdlc"
        if "in progress" in reasm:
            msg["kind"] = "partial"
        elif msg["label"] in LINK_LABELS and not text.strip():
            msg["kind"] = "link"
        if msg["kind"] in ("acars", "cpdlc"):
            pos = parse_position(text, cfg["home"], cfg["max_position_km"])
            if pos:
                msg["position"] = dict(pos, src="ACARS text")
    elif xid:
        msg["kind"] = "xid"
        msg["text"] = xid.get("type_descr") or xid.get("type") or "XID"
        for p in xid.get("vdl_params") or []:
            val = p.get("value")
            if p.get("name") == "ac_location" and isinstance(val, dict) and val.get("loc"):
                msg["position"] = {"lat": round(val["loc"]["lat"], 4), "lon": round(val["loc"]["lon"], 4),
                                   "alt": val.get("alt"), "src": "VDL2 XID"}
            elif p.get("name") == "dst_airport" and val:
                msg["text"] += f" (destination {val})"
    else:
        payload = {k: val for k, val in av.items() if isinstance(val, dict) and val and k not in ("src", "dst")}
        if payload:
            msg["kind"] = "atn"
            msg["decoded"] = summarize_decoded(payload)
            msg["text"] = ", ".join(payload)
        else:
            msg["text"] = av.get("cmd") or av.get("frame_type") or ""
    msg["frame"] = av.get("frame_type")  # I / S / U, used to tell link frame types apart
    return msg


# --------------------------------------------------------------- storage

class MemoryStore:
    """Messages from the last history_hours, kept in RAM only (nothing survives a restart)."""

    def __init__(self):
        self.messages = OrderedDict()  # id -> (msg, raw JSON string or None), in arrival order
        self.next_id = 1

    def add(self, msg, raw):
        msg_id = self.next_id
        self.next_id += 1
        msg["id"] = msg_id
        # Original JSON is kept compact, and not at all for link-layer frames, to keep memory down.
        raw_json = json.dumps(raw, separators=(",", ":")) if msg["kind"] not in ("link", "partial") else None
        self.messages[msg_id] = (msg, raw_json)
        return msg_id

    def get(self, ids, keep_raw=False):
        out = []
        for msg_id in sorted(ids, reverse=True):
            entry = self.messages.get(msg_id)
            if entry:
                msg = dict(entry[0])
                if keep_raw:
                    msg["raw"] = json.loads(entry[1]) if entry[1] else None
                out.append(msg)
        return out

    def recent(self, limit):
        ids = list(reversed(self.messages))[:limit]
        return list(reversed(self.get(ids)))

    def purge(self, older_than):
        while self.messages:
            msg_id, (msg, _) = next(iter(self.messages.items()))
            if msg["ts"] >= older_than:
                break
            del self.messages[msg_id]


# ------------------------------------------------------ multi-block reports

BLOCK_WINDOW_SECS = 600
MSGNO_BLOCK = re.compile(r"([A-Z]\d{2})([A-Z])$")


def file_as_block(msg, summary):
    """Re-file a message as a partial block (hidden by default) instead of a report in its own right."""
    msg["kind"] = "partial"
    msg["english"] = {"category": "link", "summary": summary, "details": []}
    msg["type"] = f"{msg['source']}:partial"


class BlockAssembler:
    """Long airline reports are sent as several messages sharing a message number with a letter suffix
    (D26A, D26B, …). Often a later one repeats the whole report; otherwise the pieces only make sense
    joined together. Either way the flight card should show one report, not a trail of fragments."""

    def __init__(self):
        self.groups = {}  # (aircraft, source, label, number) -> {"head", "letter", "own", "parts", "ts"}

    def observe(self, msg):
        """Call before the message is stored. May re-file msg itself as a block; returns other messages
        (already stored) whose translation changed and need re-broadcasting."""
        m = MSGNO_BLOCK.match(msg.get("msgno") or "")
        if not m or msg["kind"] != "acars" or not msg["text"]:
            return []
        now, letter = msg["ts"], m.group(2)
        for k in [k for k, g in self.groups.items() if now - g["ts"] > BLOCK_WINDOW_SECS]:
            del self.groups[k]
        key = (msg["reg"] or msg["flight"] or msg["icao"], msg["source"], msg["label"], m.group(1))
        group = self.groups.get(key)
        if group is None:
            self.groups[key] = self._new_group(msg, letter)
            return []
        group["ts"] = now
        head, text = group["head"], msg["text"]

        # A longer copy of the same report replaces everything received so far.
        if text.startswith(group["own"][:40]) and len(text) >= len(group["own"]):
            replaced = [head, *group["parts"].values()]
            for old in replaced:
                file_as_block(old, "Earlier copy of a report that was later received complete")
            self.groups[key] = self._new_group(msg, letter)
            return replaced

        # A fragment without a recognisable header belongs to the report it follows.
        if msg["english"].get("generic") and letter > group["letter"]:
            if letter not in group["parts"]:
                group["parts"][letter] = msg
                file_as_block(msg, f"Block {letter} of: {head['english']['summary']}")
                self._join(group)
                return [head]
            return []

        # The fragment came first and now the report's own first block has arrived.
        if head["english"].get("generic") and letter < group["letter"] and not msg["english"].get("generic"):
            old_head = head
            self.groups[key] = new = self._new_group(msg, letter)
            new["parts"] = {**group["parts"], group["letter"]: old_head}
            for part_letter, part in new["parts"].items():
                file_as_block(part, f"Block {part_letter} of: {msg['english']['summary']}")
            self._join(new)
            return [old_head, *group["parts"].values()]

        # Otherwise it's a different report reusing the number.
        self.groups[key] = self._new_group(msg, letter)
        return []

    @staticmethod
    def _new_group(msg, letter):
        return {"head": msg, "letter": letter, "own": msg["text"], "parts": {}, "ts": msg["ts"]}

    @staticmethod
    def _join(group):
        head = group["head"]
        head["text"] = group["own"] + "".join(group["parts"][k]["text"] for k in sorted(group["parts"]))
        english = translate(head, None)
        english["details"] = english["details"] + [f"Assembled from {len(group['parts']) + 1} blocks"]
        head["english"] = english
        head["type"] = message_type(head)


# ------------------------------------------------------------ VRS client

class VRS:
    VRS_FIELDS = ("Icao", "Reg", "Call", "Lat", "Long", "PosTime", "Alt", "GAlt", "Spd", "Trak", "Vsi", "Sqk",
                  "Gnd", "Type", "Mdl", "Man", "Op", "OpIcao", "From", "To", "Year", "Cou", "Mil", "Mlat", "WTC", "Help",
                  "Species")

    def __init__(self, cfg):
        self.cfg = cfg
        self.by_icao, self.by_reg, self.by_call = {}, {}, {}
        self.ok, self.last_ok, self.error = False, 0, None

    @property
    def configured(self):
        return bool(self.cfg.get("vrs_url"))

    async def poll(self, session):
        if not self.configured:
            return False
        params = {"feed": self.cfg["vrs_feed"]} if self.cfg.get("vrs_feed") else None
        try:
            async with session.get(self.cfg["vrs_url"], params=params,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception as exc:  # network errors, bad JSON
            self.ok, self.error = False, str(exc) or exc.__class__.__name__
            if time.time() - self.last_ok > VRS_STALE_SECS:
                # Don't keep showing frozen ADS-B positions; aircraft fall back to positions from messages.
                self.by_icao, self.by_reg, self.by_call = {}, {}, {}
            return False
        by_icao, by_reg, by_call = {}, {}, {}
        for a in data.get("acList", []):
            icao = (a.get("Icao") or "").upper()
            if not icao:
                continue
            rec = {k: a[k] for k in self.VRS_FIELDS if k in a}
            rec["Icao"] = icao
            by_icao[icao] = rec
            if a.get("Reg"):
                by_reg[reg_key(a["Reg"])] = rec
            if a.get("Call"):
                by_call[a["Call"].strip().upper()] = rec
        self.by_icao, self.by_reg, self.by_call = by_icao, by_reg, by_call
        self.ok, self.last_ok, self.error = True, time.time(), None
        return True

    def traffic(self):
        return [{"icao": r["Icao"], "lat": r["Lat"], "lon": r["Long"], "trak": r.get("Trak"),
                 "alt": r.get("Alt"), "call": r.get("Call"), "type": r.get("Type"), "gnd": r.get("Gnd"),
                 "sqk": r.get("Sqk") or None, "help": bool(r.get("Help"))}
                for r in self.by_icao.values() if "Lat" in r and "Long" in r]


# --------------------------------------------------------------- tracker

class Tracker:
    def __init__(self, cfg, vrs):
        self.cfg, self.vrs = cfg, vrs
        self.aircraft = {}
        self.reg_to_icao = {}

    def resolve_icao(self, msg):
        if msg["icao"]:
            return msg["icao"]
        rk = reg_key(msg["reg"])
        if rk and rk in self.reg_to_icao:
            return self.reg_to_icao[rk]
        if rk and rk in self.vrs.by_reg:
            return self.vrs.by_reg[rk]["Icao"]
        call = flight_to_callsign(msg["flight"])
        if call and call in self.vrs.by_call:
            return self.vrs.by_call[call]["Icao"]
        return None

    def add(self, msg):
        icao = self.resolve_icao(msg)
        rk = reg_key(msg["reg"])
        if icao and rk:
            self.reg_to_icao[rk] = icao
        if icao:
            key = icao
            # Fold in a record started before the ICAO address was known.
            for alias in ([f"R:{rk}"] if rk else []) + ([f"F:{msg['flight']}"] if msg["flight"] else []):
                if alias in self.aircraft and alias != key:
                    self._merge(alias, key)
        elif rk:
            key = f"R:{rk}"
        elif msg["flight"]:
            key = f"F:{msg['flight']}"
        else:
            return None
        msg["icao"] = msg["icao"] or icao
        msg["key"] = key

        a = self.aircraft.get(key)
        if a is None:
            a = self.aircraft[key] = {
                "key": key, "icao": icao, "reg": None, "flight": None, "first_seen": msg["ts"],
                "last_seen": msg["ts"], "last_msg_ts": None, "counts": Counter(), "labels": Counter(),
                "msg_ids": deque(maxlen=300), "position": None, "last_text": None, "last_label": None,
                "last_source": None, "gs": None, "freqs": Counter(),
                "recent": {},      # message type -> short English timeline for the card
                "type_last": {},   # message type -> time of the latest message of that type
            }
        a["icao"] = a["icao"] or icao
        a["reg"] = msg["reg"] or a["reg"]
        a["flight"] = msg["flight"] or a["flight"]
        a["last_seen"] = max(a["last_seen"], msg["ts"])
        a["counts"][msg["source"]] += 1
        a["freqs"][msg["freq"]] += 1
        if msg["gs"]:
            a["gs"] = msg["gs"]
        english = msg.get("english") or {}
        if english.get("category", "link") != "link":
            a["last_msg_ts"] = msg["ts"]
            a["labels"][msg["label"]] += 1
            a["last_label"] = msg["label"]
            a["last_source"] = msg["source"]
            a["last_text"] = english["summary"]
        a["type_last"][msg["type"]] = max(a["type_last"].get(msg["type"], 0), msg["ts"])
        if msg.get("id"):
            a["msg_ids"].append(msg["id"])
        if msg["position"] and (a["position"] is None or msg["ts"] >= a["position"]["ts"]):
            a["position"] = dict(msg["position"], ts=msg["ts"])
        return key

    def refresh(self, msg):
        """Re-file an already tracked message whose translation or type changed."""
        a = self.aircraft.get(msg.get("key"))
        if not a:
            return None
        for entries in a["recent"].values():
            for entry in [e for e in entries if e["id"] == msg["id"]]:
                entries.remove(entry)
        self.remember(a, msg)
        a["type_last"][msg["type"]] = max(a["type_last"].get(msg["type"], 0), msg["ts"])
        return msg["key"]

    @staticmethod
    def remember(a, msg):
        """Keep a short English timeline per message type, so the page can filter cards by type."""
        e = msg["english"]
        timeline = a["recent"].setdefault(msg["type"], deque(maxlen=RECENT_PER_TYPE))
        last = timeline[-1] if timeline else None
        if last and last["summary"] == e["summary"]:
            last.update(id=msg["id"], ts=msg["ts"], details=e["details"], count=last.get("count", 1) + 1)
            return
        timeline.append({"id": msg["id"], "ts": msg["ts"], "source": msg["source"], "type": msg["type"],
                         "label": msg["label"], "category": e["category"], "summary": e["summary"],
                         "details": e["details"]})

    def _merge(self, old_key, new_key):
        old = self.aircraft.pop(old_key)
        new = self.aircraft.get(new_key)
        if new is None:
            old["key"] = new_key
            self.aircraft[new_key] = old
            return
        new["first_seen"] = min(new["first_seen"], old["first_seen"])
        new["last_seen"] = max(new["last_seen"], old["last_seen"])
        new["counts"].update(old["counts"])
        new["labels"].update(old["labels"])
        new["freqs"].update(old["freqs"])
        new["msg_ids"] = deque(sorted(set(new["msg_ids"]) | set(old["msg_ids"])), maxlen=300)
        for t, entries in old["recent"].items():
            new["recent"][t] = deque(sorted([*new["recent"].get(t, []), *entries], key=lambda r: r["id"]),
                                     maxlen=RECENT_PER_TYPE)
        for t, ts in old["type_last"].items():
            new["type_last"][t] = max(new["type_last"].get(t, 0), ts)
        for k in ("reg", "flight", "last_text", "last_label", "last_source", "gs"):
            new[k] = new[k] or old[k]
        if old["last_msg_ts"] and (not new["last_msg_ts"] or old["last_msg_ts"] > new["last_msg_ts"]):
            new["last_msg_ts"] = old["last_msg_ts"]
        if old["position"] and (not new["position"] or old["position"]["ts"] > new["position"]["ts"]):
            new["position"] = old["position"]

    def expire(self, now):
        cutoff = now - self.cfg["history_hours"] * 3600
        for key in [k for k, a in self.aircraft.items() if a["last_seen"] < cutoff]:
            del self.aircraft[key]

    def snapshot(self, key):
        a = self.aircraft.get(key)
        if not a:
            return None
        v = self.vrs.by_icao.get(a["icao"] or "") or {}
        if not v and a["reg"]:
            v = self.vrs.by_reg.get(reg_key(a["reg"])) or {}
        out = {
            "key": key, "icao": a["icao"] or v.get("Icao"), "reg": a["reg"] or v.get("Reg"),
            "flight": a["flight"], "callsign": v.get("Call"), "first_seen": a["first_seen"],
            "last_seen": a["last_seen"], "last_msg_ts": a["last_msg_ts"], "counts": dict(a["counts"]),
            "labels": dict(a["labels"].most_common(6)), "last_text": a["last_text"],
            "last_label": a["last_label"], "last_source": a["last_source"], "gs": a["gs"],
            "recent": sorted((r for entries in a["recent"].values() for r in entries), key=lambda r: -r["id"]),
            "type_last": a["type_last"],
            "vrs": {k: v[k] for k in ("Type", "Mdl", "Man", "Op", "OpIcao", "From", "To", "Year", "Cou",
                                      "Alt", "GAlt", "Spd", "Trak", "Vsi", "Sqk", "Gnd", "Mil", "Mlat", "WTC", "Help")
                    if k in v} or None,
            "position": None,
        }
        if "Lat" in v and "Long" in v:
            out["position"] = {"lat": v["Lat"], "lon": v["Long"], "alt": v.get("Alt"), "trak": v.get("Trak"),
                               "ts": (v.get("PosTime") or 0) / 1000 or time.time(), "src": "ADS-B (VRS)"}
        elif a["position"]:
            out["position"] = a["position"]
        return out

    def snapshots(self):
        return [s for s in (self.snapshot(k) for k in list(self.aircraft)) if s]


# ------------------------------------------------------------------ app

class ACARSWeb:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = MemoryStore()
        self.vrs = VRS(cfg)
        self.tracker = Tracker(cfg, self.vrs)
        self.clients = set()
        self.started = time.time()
        self.recent_ts = deque()  # (ts, source, kind) for rate stats
        self.seen = {}  # message signature -> last time seen, for dropping retransmissions
        self.blocks = BlockAssembler()
        self.health = HealthMonitor(cfg, CONFIG_PATH)
        self.stations = GroundStations()
        self.weather = WeatherStore()

    # -- ingest
    def ingest(self, source, payload):
        try:
            raw = json.loads(payload)
            # Pick the normalizer by payload shape, not by which port it arrived on.
            # dumpvdl2 nests everything under "vdl2"; acarsdec sends a flat object - and so
            # does xng in *any* mode, since its --udp output is acarsdec-compatible. Keying
            # off the port alone would silently yield empty messages the moment a receiver
            # is switched to xng via receivers.<name>.decoder.
            if isinstance(raw, dict) and "vdl2" in raw:
                msg = normalize_vdl2(raw, self.cfg)
            else:
                msg = normalize_acars(raw, self.cfg)
            if msg is not None:
                msg["source"] = source  # the port decides the label, so xng-on-VDL2 counts as VDL2
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("bad %s datagram: %s", source, exc)
            return
        if msg is None or self.is_repeat(msg):
            return
        msg["english"] = translate(msg, raw)
        msg["type"] = message_type(msg)
        squitter = parse_squitter(msg["text"]) if msg["label"] == "SQ" else None
        obs = observations(msg) if msg["kind"] == "acars" else []
        if obs and not msg["position"]:
            # Weather reports carry the aircraft's position; use the latest sample if it's plausible here.
            latest = max(obs, key=lambda o: o["ts"])
            home = self.cfg["home"]
            if haversine_km(latest["lat"], latest["lon"], home["lat"], home["lon"]) <= self.cfg["max_position_km"]:
                msg["position"] = {"lat": latest["lat"], "lon": latest["lon"], "alt": latest["alt"], "src": "Weather report"}
        changed = self.blocks.observe(msg)
        key = self.tracker.add(msg)  # resolves the ICAO address before the message is stored
        self.store.add(msg, raw)
        if key:
            a = self.tracker.aircraft[key]
            a["msg_ids"].append(msg["id"])
            self.tracker.remember(a, msg)
        self.recent_ts.append((time.time(), source, msg["kind"]))
        self.health.note(msg, raw)
        self.stations.note(msg, squitter)
        self.weather.add(msg, obs)
        self.broadcast({"type": "message", "message": msg,
                        "aircraft": self.tracker.snapshot(key) if key else None})
        for old in changed:
            old_key = self.tracker.refresh(old)
            self.broadcast({"type": "update", "message": old,
                            "aircraft": self.tracker.snapshot(old_key) if old_key else None})

    def is_repeat(self, msg):
        """Aircraft retransmit when they miss the ground acknowledgement (which we can't hear)."""
        if msg["kind"] not in ("acars", "cpdlc") or not msg["text"]:
            return False
        now = time.time()
        sig = (msg["source"], msg["icao"] or msg["reg"] or msg["flight"], msg["label"], msg["msgno"], msg["text"])
        if len(self.seen) > 5000:
            self.seen = {k: t for k, t in self.seen.items() if now - t < REPEAT_WINDOW_SECS}
        last = self.seen.get(sig)
        self.seen[sig] = now
        return last is not None and now - last < REPEAT_WINDOW_SECS

    # -- websocket
    def broadcast(self, obj):
        if not self.clients:
            return
        data = json.dumps(obj)
        for ws in list(self.clients):
            if ws.closed:
                self.clients.discard(ws)
            else:
                asyncio.ensure_future(self._send(ws, data))

    async def _send(self, ws, data):
        try:
            await ws.send_str(data)
        except (ConnectionError, RuntimeError):
            self.clients.discard(ws)

    def stats(self):
        now = time.time()
        while self.recent_ts and self.recent_ts[0][0] < now - 3600:
            self.recent_ts.popleft()
        per_source = Counter(s for _, s, _ in self.recent_ts)
        content = Counter(s for _, s, k in self.recent_ts if k not in ("link", "partial"))
        return {"last_hour": dict(per_source), "last_hour_content": dict(content),
                "aircraft": len(self.tracker.aircraft), "vrs_ok": self.vrs.ok, "vrs_error": self.vrs.error, "vrs_configured": self.vrs.configured,
                "vrs_aircraft": len(self.vrs.by_icao), "uptime": int(now - self.started),
                "health_alerts": self.health.report()["alerts"]}

    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        want_messages = request.query.get("messages") == "1"  # only the raw page needs the backlog
        await ws.send_str(json.dumps({
            "type": "hello", "home": self.cfg["home"], "messages": self.store.recent(400) if want_messages else [],
            "history_mins": self.cfg["history_hours"] * 60, "message_types": MESSAGE_TYPES,
            "aircraft": self.tracker.snapshots(), "traffic": self.vrs.traffic(), "stats": self.stats()}))
        self.clients.add(ws)
        try:
            async for _ in ws:
                pass
        finally:
            self.clients.discard(ws)
        return ws

    async def aircraft_messages(self, request):
        a = self.tracker.aircraft.get(request.match_info["key"])
        if not a:
            return web.json_response([])
        msgs = self.store.get(list(a["msg_ids"]))
        return web.json_response(msgs)

    async def type_counts(self, request):
        """How many messages of each type are in memory, for the Message types dialog."""
        counts = Counter(msg["type"] for msg, _ in self.store.messages.values())
        return web.json_response({"types": MESSAGE_TYPES, "counts": counts,
                                  "history_hours": self.cfg["history_hours"]})

    async def full_stats(self, request):
        now = time.time()
        return web.json_response({
            "generated": now,
            "history_hours": self.cfg["history_hours"],
            "server": {"memory_mb": process_memory_mb(), "messages_in_memory": len(self.store.messages),
                       "aircraft_tracked": len(self.tracker.aircraft), "uptime": int(now - self.started),
                       "vrs_configured": self.vrs.configured, "vrs_ok": self.vrs.ok, "vrs_aircraft": len(self.vrs.by_icao)},
            "health": self.health.report(),
            "traffic": stats_summary(self.store, self.tracker, self.cfg, now),
            "ground_stations": self.stations.report(),
        })

    async def winds(self, request):
        minutes = min(max(int(request.query.get("minutes", 60)), 5), self.cfg["history_hours"] * 60)
        return web.json_response(self.weather.recent(minutes * 60))

    async def ground_stations(self, request):
        return web.json_response(self.stations.report())

    async def message(self, request):
        msgs = self.store.get([int(request.match_info["id"])], keep_raw=True)
        return web.json_response(msgs[0] if msgs else None, status=200 if msgs else 404)

    # -- background tasks
    async def vrs_loop(self):
        async with aiohttp.ClientSession(headers={"User-Agent": "acars-web/1.0"}) as session:
            while True:
                ok = await self.vrs.poll(session)
                if not ok and self.vrs.configured:
                    log.warning("VRS poll failed: %s", self.vrs.error)
                now = time.time()
                self.tracker.expire(now)
                self.broadcast({"type": "aircraft", "aircraft": self.tracker.snapshots(),
                                "traffic": self.vrs.traffic(), "stats": self.stats()})
                await asyncio.sleep(self.cfg["vrs_poll_secs"])

    async def health_loop(self):
        while True:
            try:
                self.health.reload_config()
                await self.health.poll_services()
                await self.health.maybe_auto_correct_ppm()
            except Exception:  # never let monitoring take the web app down
                log.exception("health check failed")
            cutoff = time.time() - self.cfg["history_hours"] * 3600
            self.stations.expire(cutoff)
            self.weather.expire(cutoff)
            await asyncio.sleep(30)

    async def purge_loop(self):
        while True:
            self.store.purge(time.time() - self.cfg["history_hours"] * 3600)
            await asyncio.sleep(60)

    async def on_startup(self, app):
        loop = asyncio.get_running_loop()
        for source, port in (("ACARS", self.cfg["acars_udp_port"]), ("VDL2", self.cfg["vdl2_udp_port"])):
            await loop.create_datagram_endpoint(lambda s=source: UDPIngest(self, s), local_addr=("127.0.0.1", port))
            log.info("listening for %s JSON on udp/127.0.0.1:%d", source, port)
        log.info("keeping %s hours of messages in memory", self.cfg["history_hours"])
        app["tasks"] = [asyncio.create_task(self.vrs_loop()), asyncio.create_task(self.purge_loop()),
                        asyncio.create_task(self.health_loop())]

    async def on_cleanup(self, app):
        for task in app["tasks"]:
            task.cancel()

    def make_app(self):
        @web.middleware
        async def revalidate(request, handler):
            # Make browsers re-check page code on every load, so updates show up without a hard refresh.
            response = await handler(request)
            if not request.path.startswith(("/ws", "/api/")):
                response.headers["Cache-Control"] = "no-cache"
            return response

        app = web.Application(middlewares=[revalidate])
        app.router.add_get("/ws", self.ws_handler)
        app.router.add_get("/api/aircraft/{key}/messages", self.aircraft_messages)
        app.router.add_get("/api/message/{id}", self.message)
        app.router.add_get("/api/types", self.type_counts)
        app.router.add_get("/api/stats", lambda r: web.json_response(self.stats()))
        app.router.add_get("/api/stats/full", self.full_stats)
        app.router.add_get("/api/winds", self.winds)
        app.router.add_get("/api/ground-stations", self.ground_stations)
        app.router.add_get("/stats", lambda r: web.FileResponse(HERE / "static" / "stats.html"))
        app.router.add_get("/", lambda r: web.FileResponse(HERE / "static" / "index.html"))
        app.router.add_get("/raw", lambda r: web.FileResponse(HERE / "static" / "raw.html"))
        app.router.add_static("/static/", HERE / "static")
        app.router.add_static("/leaflet/", "/usr/share/javascript/leaflet")
        app.on_startup.append(self.on_startup)
        app.on_cleanup.append(self.on_cleanup)
        return app


class UDPIngest(asyncio.DatagramProtocol):
    def __init__(self, app, source):
        self.app, self.source = app, source

    def datagram_received(self, data, addr):
        # acarsdec/dumpvdl2 send one JSON object per datagram
        for line in data.decode(errors="replace").splitlines():
            if line.strip():
                self.app.ingest(self.source, line)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    web.run_app(ACARSWeb(cfg).make_app(), host=cfg["http_host"], port=cfg["http_port"], print=None, access_log=None)


if __name__ == "__main__":
    main()
