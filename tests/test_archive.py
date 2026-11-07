"""The archive is the durable copy of the store: every message goes to disk, and a start
rebuilds the live view from it, so a restart no longer empties the page."""
import json
import tempfile
import time
from pathlib import Path

import server
from monitor import GroundStations

DAY = 86400


def cfg(archive_dir, hours=168):
    return {"home": {"lat": 40.45, "lon": -105.01, "name": "KFNL"}, "max_position_km": 1000, "history_hours": hours,
            "archive": {"dir": archive_dir, "retention_days": 7}, "vrs_url": "", "vrs_feed": None, "vrs_poll_secs": 5,
            "acars_udp_port": 0, "vdl2_udp_port": 0, "aircraft_db": "no-such-database.sqb",
            "health": {"silence_minutes": {"acars": 30, "vdl2": 5}, "ppm_tolerance": 3, "auto_ppm": False}}


def squitter(ts):
    return {"ts": ts, "source": "ACARS", "freq": 131.55, "level": -51.3, "noise": -66.4, "errors": 0,
            "direction": "up", "label": "SQ", "text": "02XADENKDEN53951N10440WV136975/ARINC", "kind": "acars",
            "type": "ACARS:status"}


def ack(ts, icao="A445F2"):
    # As archived before the frame type was kept: the type written at the time is all that
    # says it was a supervisory frame.
    return {"ts": ts, "source": "VDL2", "freq": 136.65, "level": -40.4, "noise": -55.0, "errors": 0, "icao": icao,
            "direction": "down", "gs": "1150AA", "text": "Receive Ready", "kind": "link", "type": "VDL2:ack",
            "adsb": {"lat": 39.87, "lon": -104.66, "alt": 5400, "gs": 180.0, "track": 90.0}}


def position(ts):
    return {"ts": ts, "source": "VDL2", "freq": 136.65, "level": -38.0, "noise": -55.0, "errors": 0, "icao": "A445F2",
            "reg": "N37468", "flight": "UA2866", "direction": "down", "gs": "1150AA", "label": "H1", "sublabel": "DF",
            "msgno": "D26A", "text": "#DFBPOS N39.500W105.200,,", "kind": "acars", "type": "VDL2:other",
            "adsb": {"lat": 39.5, "lon": -105.2, "alt": 30000, "gs": 420.0, "track": 270.0}}


def write_days(archive_dir, days):
    for day, records in days.items():
        with open(Path(archive_dir) / f"{day}.jsonl", "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")


def test_archive_writes_every_message_and_reads_back_in_order():
    with tempfile.TemporaryDirectory() as d:
        a = server.Archive(cfg(d))
        base = 1793318400.0  # 2026-10-30 00:00 UTC
        a.note(dict(squitter(base - 2 * DAY), id=1), None)
        a.note(dict(ack(base + 10), id=2), {"Lat": 39.87, "Long": -104.66, "Alt": 5400, "Spd": 180.0, "Trak": 90.0})
        a.note(dict(position(base + 20), id=3), None)
        a.close()
        assert sorted(p.name for p in Path(d).glob("*.jsonl")) == ["2026-10-28.jsonl", "2026-10-30.jsonl"]
        with open(Path(d) / "2026-10-30.jsonl", "a") as fh:
            fh.write('{"ts": 1793318500, "source": "ACARS", "ki')  # cut short by a crash
        got = list(a.records(base))
        assert [r["id"] for r in got] == [2, 3]
        assert got[0]["adsb"]["lat"] == 39.87 and "reg" not in got[0]
        assert [r["id"] for r in a.records(base - 3 * DAY)] == [1, 2, 3]


def test_revive_restores_dropped_fields_and_retranslates():
    msg = server.revive(squitter(1793318410.0))
    assert msg["reg"] is None and msg["flight"] is None and msg["frame"] is None and msg["errors"] == 0
    assert msg["english"]["summary"] == "ARINC ground station KDEN5 broadcasting its position"
    assert msg["type"] == "ACARS:status" and "adsb" not in msg
    old = server.revive(ack(1793318410.0))
    assert old["type"] == "VDL2:ack"  # kept from the line, since the frame type wasn't archived
    new = server.revive(dict(ack(1793318410.0), frame="S", type="VDL2:whatever"))
    assert new["type"] == "VDL2:ack"


def test_replay_rebuilds_store_cards_stations_and_queues_live_traffic():
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        yesterday = time.strftime("%Y-%m-%d", time.gmtime(now - DAY))
        write_days(d, {yesterday: [squitter(now - 30 * 3600), ack(now - 20 * 3600)],
                       today: [position(now - 600), squitter(now - 300), *[ack(now - 200 + i, f"A{i:05X}") for i in range(25)]]})
        app = server.ACARSWeb(cfg(d, hours=24))
        app.replay(now)
        assert app.archive.replayed == 28  # the squitter older than history_hours stayed on disk
        msgs = app.store.recent(100)
        assert [m["id"] for m in msgs] == list(range(1, 29))
        assert msgs[0]["english"]["summary"].startswith("Position") or msgs[0]["english"]["category"]
        card = app.tracker.aircraft["A445F2"]
        assert card["reg"] == "N37468" and card["flight"] == "UA2866" and 1 in card["msg_ids"]
        gs = app.stations.report()
        assert [s["id"] for s in gs["located"]] == ["KDEN5"] and gs["located"][0]["count"] == 1
        vdl2 = gs["vdl2"][0]
        assert vdl2["address"] == "1150AA" and vdl2["fixes"] == 26 and vdl2["site"]["airport"] == "KDEN"  # 25 today + the one from yesterday
        # Nothing was written back while reading.
        assert app.archive.written == 0
        # A datagram arriving during the rebuild waits, and is filed once the gate opens.
        app.ingest("ACARS", json.dumps({"timestamp": now, "freq": 131.55, "label": "SQ", "mode": "2",
                                        "text": "02XADENKDEN13951N10440WV136975/ARINC", "level": -50, "noise": -65}))
        assert len(app.pending) == 1 and len(app.store.messages) == 28
        app.ready.set()
        app.ingest(*app.pending.pop())
        assert len(app.store.messages) == 29 and app.archive.written == 1
        app.archive.close()


def test_replay_survives_a_missing_archive_dir():
    with tempfile.TemporaryDirectory() as d:
        app = server.ACARSWeb(cfg(str(Path(d) / "nothing-here")))
        app.replay(time.time())
        assert app.archive.replayed == 0 and len(app.store.messages) == 0


def test_replay_reads_cpdlc_and_adsc_from_the_archived_summary():
    import translate
    wilco = translate.translate({"label": "H1", "kind": "cpdlc", "direction": "down", "decoded": "WILCO",
                                 "text": "/ATLCDYA.AT1.N12345A1B2C3", "source": "VDL2", "flight": "UA1"}, None)
    assert wilco["category"] == "atc" and wilco["summary"] == translate.CPDLC_PHRASES["WILCO"]
    climb = translate.translate({"label": "H1", "kind": "cpdlc", "direction": "down", "text": "/ATLCDYA.AT1.x", "source": "VDL2",
                                 "decoded": "REQUEST CLIMB TO [altitude] — flight_level: 320"}, None)
    assert climb["summary"] == "Crew request/report to ATC: “Request climb to FL320”"
    gone = translate.translate({"label": "H1", "kind": "cpdlc", "text": "/x", "source": "VDL2",
                                "decoded": "FANS-1/A cpdlc disconnect request"}, None)
    assert "closed by the aircraft" in gone["summary"]
    ads = translate.translate({"label": "B6", "kind": "acars", "text": "/QUKAXBA.ADS.G-STBD071CEA0DA1D4880E95C99D9C8D", "source": "ACARS",
                               "decoded": "adsc msg — lat: 40.660572, lon: -106.553307, alt: 33000, ts_sec: 1394.375, pos_accuracy_nm: 0.25"}, None)
    assert ads["category"] == "position" and ads["details"] == ["40.66°N 106.55°W", "33,000 ft"]
    assert ads["position"] == {"lat": 40.6606, "lon": -106.5533, "src": "ADS-C"}
    assert translate.message_type(dict(ads_msg := {"label": "B6", "kind": "acars", "source": "ACARS"}, english=ads)) == "ACARS:position"
