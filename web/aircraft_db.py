"""Registration to ICAO address, from a Virtual Radar Server style aircraft database.

Plain ACARS (acarsdec) gives a message's registration but not the aircraft's 24-bit ICAO
address, so it cannot be paired with ADS-B the way VDL2 messages are. A quarter of the
archive was in that position. The live VRS aircraft list resolves some registrations, but
only for aircraft VRS itself has a registration for; this table covers the rest - 99.4%
of the registrations plain ACARS gave over the first 20 days of the archive.

The file (AircraftDatabase.sqb, an AircraftDetail table of Icao and Registration) has no
index on Registration and writes it with punctuation ("G-STBG"), so a direct query is a
full scan. Instead the registrations are copied once, keyed the way server.reg_key keys
them, into an indexed in-memory table: about a second and 30 MB for 560,000 rows, then a
lookup in microseconds. Where one registration appears on more than one row (re-registered
airframes, 2,658 of them), the most recently updated row wins.

A missing or unreadable file is not an error: lookups return None and the server carries
on as it did before this existed.
"""
import logging
import re
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger("acars.aircraft_db")


def reg_key(reg):
    """The same key server.reg_key makes: upper case, letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", reg.upper()) if reg else None


class AircraftDB:
    def __init__(self, path):
        self.path = Path(path)
        self._db = None
        self._lock = threading.Lock()
        self.rows = 0

    def load(self):
        """Build the lookup table. Safe to call from a worker thread; returns the row count."""
        if not self.path.is_file():
            log.info("no aircraft database at %s; registrations resolve only through VRS", self.path)
            return 0
        try:
            db = sqlite3.connect(":memory:", check_same_thread=False)
            db.execute("ATTACH DATABASE ? AS src", (f"file:{self.path}?mode=ro",))
            db.execute("CREATE TABLE reg (k TEXT PRIMARY KEY, icao TEXT NOT NULL) WITHOUT ROWID")
            # Later rows replace earlier ones, so order by UpdatedUtc: the newest owner wins.
            db.execute("""INSERT OR REPLACE INTO reg
                          SELECT upper(replace(replace(replace(Registration, '-', ''), ' ', ''), '.', '')),
                                 upper(Icao)
                          FROM src.AircraftDetail
                          WHERE Registration IS NOT NULL AND Registration <> '' AND length(Icao) = 6
                          ORDER BY UpdatedUtc, AircraftDetailID""")
            db.commit()
            db.execute("DETACH DATABASE src")
            rows = db.execute("SELECT count(*) FROM reg").fetchone()[0]
        except sqlite3.Error as exc:
            log.warning("could not read aircraft database %s: %s", self.path, exc)
            return 0
        with self._lock:
            self._db, self.rows = db, rows
        log.info("aircraft database: %d registrations from %s", rows, self.path)
        return rows

    def icao(self, reg):
        """ICAO address (upper-case hex) for a registration, or None."""
        key = reg_key(reg)
        if not key or self._db is None:
            return None
        with self._lock:
            row = self._db.execute("SELECT icao FROM reg WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None
