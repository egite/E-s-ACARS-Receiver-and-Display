"""Registration to ICAO address lookup, on a small database shaped like the real one."""
import sqlite3

import aircraft_db

SCHEMA = """CREATE TABLE AircraftDetail (AircraftDetailID INTEGER PRIMARY KEY AUTOINCREMENT,
            Icao VARCHAR(6) NOT NULL, Registration VARCHAR(20) NULL, UpdatedUtc DATETIME)"""


def make(tmp_path, rows):
    path = tmp_path / "AircraftDatabase.sqb"
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    con.executemany("INSERT INTO AircraftDetail (Icao, Registration, UpdatedUtc) VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return path


def test_lookup_ignores_punctuation_and_case(tmp_path):
    db = aircraft_db.AircraftDB(make(tmp_path, [("406947", "G-STBG", "2026-05-15"), ("a05dd8", "N12221", "2026-05-15")]))
    assert db.load() == 2
    assert db.icao("G-STBG") == "406947"
    assert db.icao("gstbg") == "406947"
    assert db.icao(".N12221") == "A05DD8"  # ACARS pads registrations with dots
    assert db.icao("N99999") is None and db.icao(None) is None


def test_newest_owner_of_a_registration_wins(tmp_path):
    db = aircraft_db.AircraftDB(make(tmp_path, [("AAAAAA", "N100XX", "2026-05-15"), ("BBBBBB", "N100XX", "2024-01-01")]))
    db.load()
    assert db.icao("N100XX") == "AAAAAA"


def test_missing_database_is_not_an_error(tmp_path):
    db = aircraft_db.AircraftDB(tmp_path / "absent.sqb")
    assert db.load() == 0
    assert db.icao("N12221") is None
