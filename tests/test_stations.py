"""Ground stations: what they say about themselves (ACARS squitters, VDL2 GSIF frames),
how it is shown, and placing a VDL2 station we only ever hear aircraft talk to."""
import server
import translate
from monitor import GroundStations, station_broadcast, vdl2_network

CFG = {"home": {"lat": 40.45, "lon": -105.01}, "max_position_km": 800}
SQUITTER = "02XADENKDEN53951N10440WV136975/ARINC"


def test_squitter_is_an_uplink_without_a_block_id():
    raw = {"timestamp": 1793365636.5, "freq": 131.55, "level": -51.3, "noise": -66.4, "error": 0,
           "mode": "2", "label": "SQ", "text": SQUITTER, "assstat": "skipped"}
    msg = server.normalize_acars(raw, CFG)
    assert msg["direction"] == "up" and msg["reg"] is None
    s = station_broadcast(dict(msg, gs_position=None))
    assert s["id"] == "KDEN5" and s["network"] == "ARINC" and s["via"] == "squitter"
    assert (s["lat"], s["lon"], s["vdl_freq"]) == (39.85, -104.6667, 136.975)


def test_squitter_english_names_the_station_and_its_place():
    r = translate.translate({"label": "SQ", "kind": "acars", "text": SQUITTER})
    assert r["summary"] == "ARINC ground station KDEN5 broadcasting its position"
    assert r["details"] == ["At KDEN Denver, 39.85°N 104.67°W", "VDL2 on 136.975 MHz"]
    sita = translate.translate({"label": "SQ", "kind": "acars", "text": "02XSDENKDEN03951N10440WV136975/"})
    assert sita["summary"].startswith("SITA ground station KDEN0")
    assert translate.translate({"label": "SQ", "kind": "acars", "text": "00XS"})["summary"] == "Ground station broadcast (squitter)"


def test_gsif_locates_a_vdl2_station():
    raw = {"vdl2": {"t": {"sec": 1793365636, "usec": 0}, "freq": 136975000, "sig_level": -40.0, "noise_level": -55.0,
                    "avlc": {"src": {"addr": "10345A", "type": "Ground station"}, "dst": {"addr": "FFFFFF", "type": "All stations"},
                             "frame_type": "U", "cmd": "XID",
                             "xid": {"type": "GSIF", "type_descr": "Ground Station Information Frame",
                                     "vdl_params": [{"name": "gs_location", "value": {"lat": 39.8601, "lon": -104.7612}},
                                                    {"name": "nearest_airport_id", "value": "KDEN"},
                                                    {"name": "airport_coverage", "value": "KDEN"}]}}}}
    msg = server.normalize_vdl2(raw, CFG)
    assert msg["kind"] == "xid" and msg["gs"] == "10345A" and msg["icao"] is None
    assert msg["gs_position"] == {"lat": 39.8601, "lon": -104.7612} and msg["gs_airport"] == "KDEN"
    s = station_broadcast(msg)
    assert s["id"] == "10345A" and s["network"] == "ARINC" and s["icao"] == "KDEN" and s["via"] == "GSIF"
    assert s["vdl_freq"] == 136.975


def test_two_transmitters_with_one_name_both_stay_listed():
    gs = GroundStations()
    a = {"id": "KDEN0", "network": "SITA", "iata": "DEN", "icao": "KDEN", "lat": 39.85, "lon": -104.6667, "vdl_freq": 136.975, "via": "squitter"}
    b = dict(a, lat=39.8333, lon=-104.65)
    msg = {"ts": 1.0, "source": "ACARS", "gs": None, "icao": None}
    for station in (a, b, a):
        gs.note(msg, station)
    located = gs.report()["located"]
    assert len(located) == 2 and {s["count"] for s in located} == {1, 2}


def test_vdl2_station_site_from_low_flying_aircraft():
    gs = GroundStations()
    assert vdl2_network("1150AA") == "ARINC" and vdl2_network("2215C5") == "SITA" and vdl2_network("3ABCDE") == "Other"
    high = {"Lat": 41.0, "Long": -103.0, "Alt": 35000}
    for i in range(25):  # low fixes scattered around Denver International
        gs.note({"ts": float(i), "source": "VDL2", "gs": "10345A", "icao": f"A{i:05X}"}, None,
                {"Lat": 39.86 + (i % 5 - 2) * 0.01, "Long": -104.67 + (i % 3 - 1) * 0.02, "Alt": 6000 + i})
        gs.note({"ts": float(i), "source": "VDL2", "gs": "10345A", "icao": "ABCDEF"}, None, high)
    g = gs.report()["vdl2"][0]
    assert g["network"] == "ARINC" and g["aircraft"] == 26 and g["fixes"] == 25
    assert g["site"]["from"] == "aircraft" and g["site"]["airport"] == "KDEN"
    assert abs(g["site"]["lat"] - 39.86) < 0.02 and abs(g["site"]["lon"] + 104.67) < 0.03
    # A station that announced itself is placed by its own word, not by its traffic.
    gs.note({"ts": 30.0, "source": "VDL2", "gs": "10345A", "icao": None},
            {"id": "10345A", "network": "ARINC", "iata": None, "icao": "KDEN", "lat": 39.8601, "lon": -104.7612, "vdl_freq": 136.975, "via": "GSIF"})
    g = gs.report()["vdl2"][0]
    assert g["site"] == {"lat": 39.8601, "lon": -104.7612, "airport": "KDEN", "from": "GSIF"}


def test_few_fixes_give_no_site():
    gs = GroundStations()
    for i in range(5):
        gs.note({"ts": 1.0, "source": "VDL2", "gs": "2215C5", "icao": "ABCDEF"}, None, {"Lat": 40.0, "Long": -104.0, "Gnd": True})
    g = gs.report()["vdl2"][0]
    assert g["site"] is None and g["fixes"] == 5
