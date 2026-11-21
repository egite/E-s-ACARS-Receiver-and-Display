"""The archive query service: one aircraft's archived messages over a time range, over TCP,
in the shape docs/query-protocol.md publishes."""
import json
import socket
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

import query_server as qs

NOV16 = datetime(2026, 11, 16, tzinfo=UTC).timestamp()


def line(ts, icao="A524E1", **extra):
    rec = {"ts": ts, "source": "VDL2", "freq": 136.8, "level": -40.0, "noise": -55.0, "errors": 0, "icao": icao,
           "direction": "down", "gs": "2215C5", "label": "H1", "text": "/HDQDLUA.POSN38117W101267,KIISS,005218,360",
           "kind": "acars", "type": "VDL2:position",
           "adsb": {"lat": 38.2, "lon": -101.4, "alt": 36000, "gs": 450.0, "track": 270.0, "postime": ts * 1000 - 2000}}
    rec.update(extra)
    return json.dumps(rec, separators=(",", ":"))


@pytest.fixture
def archive():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "2026-11-15.jsonl").write_text("\n".join([
            line(NOV16 - 60),
            line(NOV16 - 30, icao="ABCDEF"),
        ]) + "\n")
        (d / "2026-11-16.jsonl").write_text("\n".join([
            line(NOV16),
            # The address inside another field must not count as this aircraft's message.
            line(NOV16 + 10, icao="ABCDEF", text='"icao":"A524E1"'),
            line(NOV16 + 20, kind="link", text="Receive Ready", label=None),
            line(NOV16 + 86399),
        ]) + "\n" + line(NOV16 + 86399.5)[:40])  # a line still being written
        yield d


def test_scan_finds_only_that_aircraft_in_range(archive):
    got = [r["ts"] - NOV16 for r in qs.scan(archive, "A524E1", NOV16 - 3600, NOV16 + 86400)]
    assert got == [-60, 0, 20, 86399]
    assert [r["ts"] - NOV16 for r in qs.scan(archive, "A524E1", NOV16, NOV16 + 20)] == [0]  # "to" is exclusive


def test_day_files_clamps_to_what_exists(archive):
    assert [p.stem for p in qs.day_files(archive, 0, 4e9)] == ["2026-11-15", "2026-11-16"]
    assert [p.stem for p in qs.day_files(archive, NOV16, NOV16 + 86400)] == ["2026-11-16"]
    assert qs.day_files(archive / "missing", 0, 4e9) == []


@pytest.mark.parametrize("request_line, expect", [
    (b'{"icao":"a524e1","from":"2026-11-16","to":"2026-11-17"}', ("A524E1", NOV16, NOV16 + 86400, None)),
    (b'{"icao":"A524E1","from":1794787200,"to":"1794873600"}', ("A524E1", NOV16, NOV16 + 86400, None)),
    (b'{"icao":"A524E1","from":"2026-11-15T17:00:00-07:00","to":"2026-11-16T01:00:00Z"}',
     ("A524E1", NOV16, NOV16 + 3600, None)),
    (b'A524E1 2026-11-16 2026-11-17\n', ("A524E1", NOV16, NOV16 + 86400, None)),
    (b'{"icao":"A524E1","from":"2026-11-16","to":"2026-11-17","kinds":["acars"]}',
     ("A524E1", NOV16, NOV16 + 86400, {"acars"})),
])
def test_parse_request(request_line, expect):
    assert qs.parse_request(request_line) == expect


@pytest.mark.parametrize("request_line", [
    b"", b"A524E", b"A524E1 2026-11-17 2026-11-16", b'{"icao":"A524E1","colour":"red"}',
    b'{"icao":"A524E1","from":"soon"}', b'{"icao":"A524E1","kinds":["fax"]}', b'{"icao":"A524E1","from":true}',
    b"[]", b"{nope",
])
def test_bad_requests_are_refused(request_line):
    with pytest.raises(qs.RequestError):
        qs.parse_request(request_line)


def test_outward_names_every_field():
    rec = json.loads(line(NOV16))
    out = qs.outward(rec)
    assert out["record"] == "message" and out["time"] == "2026-11-16T00:00:00.000Z"
    assert out["ground_station"] == "2215C5" and out["freq_mhz"] == 136.8
    assert out["adsb"] == {"lat": 38.2, "lon": -101.4, "alt_ft": 36000, "speed_kt": 450.0, "track_deg": 270.0,
                           "position_ts": NOV16 - 2}
    assert set(out["english"]) >= {"category", "summary", "details"}  # today's translation, made on the way out
    assert out["registration"] is None and out["decoded"] is None  # absent means null, never missing
    assert set(out) == {"record", "ts", "time", "source", "kind", "message_type", "direction", "icao", "icao_source",
                        "registration", "flight", "freq_mhz", "level_db", "noise_db", "errors", "ground_station",
                        "frame", "label", "sublabel", "msgno", "text", "decoded", "position", "english", "adsb"}


def ask(port, request):
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(request + b"\n")
        with sock.makefile("r") as fh:
            return [json.loads(x) for x in fh]


def test_over_tcp(archive):
    srv = qs.QueryServer(("127.0.0.1", 0), archive, Path("no-such-database.sqb"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        reply = ask(port, b'{"icao":"A524E1","from":"2026-11-16","to":"2026-11-17","kinds":["acars"]}')
        assert [r["record"] for r in reply] == ["header", "message", "message", "end"]
        header, end = reply[0], reply[-1]
        assert header["protocol"] == 1 and header["aircraft"] is None
        assert header["archive_first_day"] == "2026-11-15" and header["archive_last_day"] == "2026-11-16"
        assert end["count"] == 2

        assert ask(port, b"ZZZZZZ") == [{"record": "error", "error": "bad_request",
                                         "message": 'icao must be six hexadecimal digits, e.g. "A1B2C3"'}]
    finally:
        srv.shutdown()
        srv.server_close()
