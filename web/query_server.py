#!/usr/bin/env python3
"""Archive query service: everything the archive holds for one aircraft, over TCP.

Another program connects, sends one request line naming an ICAO address and a time range,
and gets back every archived message for that aircraft in the range, one JSON object per
line, then the connection closes. docs/query-protocol.md is the contract for that program;
change the two together, and bump PROTOCOL when a change would break an existing reader.

It reads the archive files the web server writes (archive/messages/YYYY-MM-DD.jsonl) and
never talks to the web server, so it can be restarted, or fail, without touching the
receiver. Lines reach the archive within about ten seconds of arriving (Archive.note
flushes on a 10 s / 50 line bound), so a query sees all but the last few seconds.

Finding one aircraft in a day file is a byte search for its "icao":"XXXXXX" pair over a
memory-mapped file, not a JSON parse of every line: a day is ~45,000 lines and one aircraft
is a few hundred of them at most, so only the matches are parsed. Every match is still
parsed and checked, so a pair that turned up inside some other field could not leak in.

Each message is passed through server.revive, the same step that rebuilds the web page from
the archive, so the English translation is today's translator's, not the one that wrote
the line.
"""
import json
import logging
import mmap
import os
import re
import socketserver
import sqlite3
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from server import load_config, revive  # noqa: E402  (the web app's own modules live beside this file)

log = logging.getLogger("acars.query")

PROTOCOL = 1
DEFAULT_PORT = 8687
REQUEST_MAX_BYTES = 4096
REQUEST_TIMEOUT_SECS = 30
MAX_CONCURRENT_SCANS = 2  # the decoders share this CPU; a third client waits its turn
SCAN_WAIT_SECS = 120      # ...for this long, then gets "busy"
ICAO_RE = re.compile(r"^[0-9A-F]{6}$")
KINDS = ("acars", "cpdlc", "atn", "link", "partial", "xid")

AIRCRAFT_COLUMNS = (("Registration", "registration"), ("Country", "country"), ("Manufacturer", "manufacturer"),
                    ("Model", "model"), ("ModelIcao", "model_icao"), ("Operator", "operator"),
                    ("OperatorIcao", "operator_icao"), ("Serial", "serial"), ("YearBuilt", "year_built"))


class RequestError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ request

def parse_time(value, name):
    """Unix seconds (number or numeric string), or ISO 8601. No zone means UTC; a bare
    date means 00:00 UTC that day."""
    if isinstance(value, bool):
        raise RequestError("bad_request", f"{name} must be a time, not true/false")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", s):
            return float(s)
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            raise RequestError("bad_request", f"{name} is not ISO 8601 or Unix seconds: {value!r}") from None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    raise RequestError("bad_request", f"{name} must be a string or a number")


def parse_request(line):
    """A request line as (icao, from_ts, to_ts, kinds or None). Accepts the JSON form, or
    the plain form `ICAO [FROM [TO]]` for typing at netcat."""
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        raise RequestError("bad_request", "empty request")
    if text.startswith("{"):
        try:
            req = json.loads(text)
        except ValueError as exc:
            raise RequestError("bad_request", f"request is not valid JSON: {exc}") from None
    else:
        parts = text.split()
        if len(parts) > 3:
            raise RequestError("bad_request", "plain request is ICAO [FROM [TO]]")
        req = dict(zip(("icao", "from", "to"), parts))
    if not isinstance(req, dict):
        raise RequestError("bad_request", "request must be a JSON object")
    unknown = set(req) - {"icao", "from", "to", "kinds"}
    if unknown:
        raise RequestError("bad_request", f"unknown request field(s): {', '.join(sorted(unknown))}")

    icao = req.get("icao")
    if not isinstance(icao, str) or not ICAO_RE.match(icao.strip().upper()):
        raise RequestError("bad_request", "icao must be six hexadecimal digits, e.g. \"A1B2C3\"")
    icao = icao.strip().upper()
    t0 = parse_time(req["from"], "from") if req.get("from") is not None else 0.0
    t1 = parse_time(req["to"], "to") if req.get("to") is not None else time.time()
    if t1 <= t0:
        raise RequestError("bad_request", "\"to\" must be later than \"from\"")

    kinds = req.get("kinds")
    if kinds is not None:
        if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
            raise RequestError("bad_request", "kinds must be a non-empty list of strings")
        bad = [k for k in kinds if k not in KINDS]
        if bad:
            raise RequestError("bad_request", f"unknown kind(s) {bad}; known: {list(KINDS)}")
        kinds = set(kinds)
    return icao, t0, t1, kinds


# ------------------------------------------------------------------ archive

def day_files(archive_dir, t0, t1):
    """The day files that can hold messages with t0 <= ts < t1, oldest first."""
    if not archive_dir.is_dir():
        return []
    present = {p.stem: p for p in archive_dir.glob("*.jsonl")}
    if not present:
        return []
    first = max(datetime.fromtimestamp(max(t0, 0), UTC).date(), date.fromisoformat(min(present)))
    last = min(datetime.fromtimestamp(max(t1 - 1e-6, 0), UTC).date(), date.fromisoformat(max(present)))
    out, day = [], first
    while day <= last:
        path = present.get(day.isoformat())
        if path:
            out.append(path)
        day += timedelta(days=1)
    return out


def scan(archive_dir, icao, t0, t1):
    """Archived records for one aircraft with t0 <= ts < t1, oldest first, as written."""
    needle = f'"icao":"{icao}"'.encode()
    for path in day_files(archive_dir, t0, t1):
        try:
            with path.open("rb") as fh:
                if os.fstat(fh.fileno()).st_size == 0:
                    continue
                # The map is a snapshot of the file's length now; the web server appending
                # to today's file meanwhile is harmless, and a line it is half-way through
                # writing fails to parse and is skipped.
                with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    pos = mm.find(needle)
                    while pos != -1:
                        start = mm.rfind(b"\n", 0, pos) + 1
                        end = mm.find(b"\n", pos)
                        if end == -1:
                            end = len(mm)
                        line = mm[start:end]
                        pos = mm.find(needle, end)
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(rec, dict) and rec.get("icao") == icao and t0 <= rec.get("ts", -1) < t1:
                            yield rec
        except OSError as exc:  # deleted by retention between listing and opening, say
            log.warning("couldn't read %s: %s", path, exc)


def archive_span(archive_dir):
    days = sorted(p.stem for p in archive_dir.glob("*.jsonl")) if archive_dir.is_dir() else []
    return (days[0], days[-1]) if days else (None, None)


def aircraft_details(db_path, icao):
    """The aircraft database's row for this address (newest if several), or None."""
    if not db_path.is_file():
        return None
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            cols = ", ".join(c for c, _ in AIRCRAFT_COLUMNS)
            row = db.execute(f"SELECT {cols} FROM AircraftDetail WHERE Icao = ? "
                             "ORDER BY UpdatedUtc DESC, AircraftDetailID DESC LIMIT 1", (icao,)).fetchone()
        finally:
            db.close()
    except sqlite3.Error as exc:
        log.warning("aircraft database lookup failed: %s", exc)
        return None
    if not row:
        return None
    return {name: (value if value != "" else None) for (_, name), value in zip(AIRCRAFT_COLUMNS, row)}


# ------------------------------------------------------------------ output

def iso(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def outward(rec):
    """An archived line in the published message shape (docs/query-protocol.md). Every key
    is always present; what the archive doesn't know is null."""
    msg = revive(rec)
    adsb = rec.get("adsb")
    if adsb:
        postime = adsb.get("postime")
        adsb = {"lat": adsb.get("lat"), "lon": adsb.get("lon"), "alt_ft": adsb.get("alt"),
                "speed_kt": adsb.get("gs"), "track_deg": adsb.get("track"),
                "position_ts": postime / 1000 if isinstance(postime, (int, float)) else None}
    return {
        "record": "message",
        "ts": rec["ts"],
        "time": iso(rec["ts"]),
        "source": msg.get("source"),
        "kind": msg.get("kind"),
        "message_type": msg.get("type"),
        "direction": msg.get("direction"),
        "icao": msg.get("icao"),
        "icao_source": msg.get("icao_src"),
        "registration": msg.get("reg"),
        "flight": msg.get("flight"),
        "freq_mhz": msg.get("freq"),
        "level_db": msg.get("level"),
        "noise_db": msg.get("noise"),
        "errors": msg.get("errors"),
        "ground_station": msg.get("gs"),
        "frame": msg.get("frame"),
        "label": msg.get("label"),
        "sublabel": msg.get("sublabel"),
        "msgno": msg.get("msgno"),
        "text": msg.get("text") or "",
        "decoded": msg.get("decoded"),
        "position": msg.get("position"),
        "english": msg.get("english"),
        "adsb": adsb,
    }


# ------------------------------------------------------------------ server

class QueryHandler(socketserver.StreamRequestHandler):
    timeout = REQUEST_TIMEOUT_SECS
    wbufsize = 65536

    def send(self, obj):
        self.wfile.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")

    def handle(self):
        srv, client, started = self.server, self.client_address[0], time.monotonic()
        try:
            line = self.rfile.readline(REQUEST_MAX_BYTES + 1)
            if len(line) > REQUEST_MAX_BYTES:
                raise RequestError("bad_request", f"request longer than {REQUEST_MAX_BYTES} bytes")
            icao, t0, t1, kinds = parse_request(line)
        except TimeoutError:
            log.info("%s sent no request within %d s", client, REQUEST_TIMEOUT_SECS)
            return self.try_send({"record": "error", "error": "timeout",
                                  "message": f"no request line within {REQUEST_TIMEOUT_SECS} s"})
        except RequestError as exc:
            log.info("%s bad request: %s", client, exc)
            return self.try_send({"record": "error", "error": exc.code, "message": str(exc)})

        if not srv.slots.acquire(timeout=SCAN_WAIT_SECS):
            log.warning("%s turned away: %d queries already running", client, MAX_CONCURRENT_SCANS)
            return self.try_send({"record": "error", "error": "busy",
                                  "message": "other queries are running; try again shortly"})
        count = 0
        try:
            first, last = archive_span(srv.archive_dir)
            self.send({"record": "header", "protocol": PROTOCOL, "icao": icao,
                       "from": iso(t0) if t0 > 0 else None, "to": iso(t1), "from_ts": t0, "to_ts": t1,
                       "kinds": sorted(kinds) if kinds else None, "server_time": iso(time.time()),
                       "archive_first_day": first, "archive_last_day": last,
                       "aircraft": aircraft_details(srv.db_path, icao)})
            for rec in scan(srv.archive_dir, icao, t0, t1):
                if kinds and rec.get("kind") not in kinds:
                    continue
                try:
                    out = outward(rec)
                except Exception:  # one bad line must not cut the answer short
                    log.exception("couldn't render a record at ts=%s", rec.get("ts"))
                    continue
                self.send(out)
                count += 1
            self.send({"record": "end", "count": count, "elapsed_s": round(time.monotonic() - started, 3)})
            self.wfile.flush()
            log.info("%s %s %s..%s kinds=%s: %d messages in %.2f s", client, icao, iso(t0), iso(t1),
                     ",".join(sorted(kinds)) if kinds else "all", count, time.monotonic() - started)
        except (BrokenPipeError, ConnectionResetError, TimeoutError) as exc:
            log.info("%s went away after %d messages (%s)", client, count, type(exc).__name__)
        except Exception:
            log.exception("query from %s failed", client)
            self.try_send({"record": "error", "error": "internal", "message": "server error; see its log"})
        finally:
            srv.slots.release()

    def try_send(self, obj):
        try:
            self.send(obj)
            self.wfile.flush()
        except OSError:
            pass


class QueryServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, archive_dir, db_path):
        super().__init__(addr, QueryHandler)
        self.archive_dir, self.db_path = archive_dir, db_path
        self.slots = threading.BoundedSemaphore(MAX_CONCURRENT_SCANS)


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else HERE.parent / path


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    conf = cfg.get("query") or {}
    host = conf.get("host") or cfg.get("http_host") or "0.0.0.0"
    port = int(conf.get("port") or DEFAULT_PORT)
    archive_dir = resolve((cfg.get("archive") or {}).get("dir") or "archive/messages")
    db_path = resolve(cfg.get("aircraft_db") or "AircraftDatabase.sqb")
    with QueryServer((host, port), archive_dir, db_path) as srv:
        log.info("archive queries on %s:%d (protocol %d), reading %s", host, port, PROTOCOL, archive_dir)
        srv.serve_forever()


if __name__ == "__main__":
    main()
