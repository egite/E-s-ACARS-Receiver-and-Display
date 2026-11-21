# Archive query protocol, version 1

This is the contract for programs that fetch archived ACARS/VDL2 data from this receiver.
A client sends an aircraft's ICAO address and a time range; the server returns every message
it has archived for that aircraft in that range, with what is known about the aircraft.

The server is `web/query_server.py` (systemd unit `acars-query`). A working client in
standard-library Python is `tools/query_client.py`; copy it to the other machine as-is or
use it as the example.

## At a glance

```
client                                    server (TCP 8687)
  | ---- connect --------------------------> |
  | ---- one request line (JSON + \n) -----> |
  | <--- header line ----------------------- |
  | <--- message line (0 or more) ---------- |
  | <--- end line -------------------------- |
  | <--- server closes the connection ------ |
```

- **Transport:** plain TCP, no TLS, no authentication. Default port **8687** on the receiver
  (192.168.21.130). One request per connection.
- **Encoding:** UTF-8 throughout. Every line, both directions, is one JSON object followed by a
  single `\n` (newline-delimited JSON, also called JSON Lines). No line contains a raw newline;
  newlines inside text are escaped as `\n` by JSON.
- **Finished means the `end` line arrived.** If the connection closes before it, the reply is
  incomplete and should be discarded or retried.

## The request

Send one line, at most 4096 bytes, within 30 seconds of connecting.

```json
{"icao": "A524E1", "from": "2026-11-16T00:00:00Z", "to": "2026-11-17T00:00:00Z"}
```

| Field   | Required | Meaning |
|---------|----------|---------|
| `icao`  | yes      | The aircraft's 24-bit ICAO address as six hexadecimal digits. Case does not matter. |
| `from`  | no       | Start of the range, **inclusive**. Omitted or `null`: the start of the archive. |
| `to`    | no       | End of the range, **exclusive**. Omitted or `null`: now. Must be later than `from`. |
| `kinds` | no       | List of message kinds to return (see `kind` below), e.g. `["acars", "cpdlc"]`. Omitted: all kinds. |

Any other field is refused, so a misspelt field name cannot silently widen the query.

**Times** may be given as either:

- a Unix timestamp in seconds, as a JSON number or a numeric string: `1794787200`, `1794787200.5`
- an ISO 8601 string: `"2026-11-16T00:00:00Z"`, `"2026-11-15T17:00:00-07:00"`,
  `"2026-11-16T00:00"`, `"2026-11-16"`. A time **without a zone is UTC**. A bare date means
  00:00 UTC that day, so `"from": "2026-11-16", "to": "2026-11-17"` is all of 16 November UTC.

**Plain form.** For typing at netcat, a line that does not start with `{` is read as
`ICAO [FROM [TO]]`, separated by spaces, with the same time formats (no spaces inside a time):

```
A524E1 2026-11-16 2026-11-17
```

## The reply

Every reply line is a JSON object with a `record` field saying what it is:
`header`, `message`, `end`, or `error`. A client should **ignore records of a type it does not
know**, and **ignore fields it does not know** inside known records; both may be added
without changing the protocol version.

A successful reply is exactly one `header`, then zero or more `message` lines, oldest first,
then one `end`. A refused request is a single `error` line.

### header

```json
{"record":"header","protocol":1,"icao":"A524E1",
 "from":"2026-11-16T00:00:00.000Z","to":"2026-11-17T00:00:00.000Z",
 "from_ts":1794787200.0,"to_ts":1794873600.0,"kinds":null,
 "server_time":"2026-11-21T14:54:48.164Z",
 "archive_first_day":"2026-09-30","archive_last_day":"2026-11-21",
 "aircraft":{"registration":"N430UA","country":"United States","manufacturer":"Airbus Industrie",
             "model":"A320-232","model_icao":"A320","operator":"United Airlines Inc","operator_icao":"UAL",
             "serial":"568","year_built":1996}}
```

| Field | Type | Meaning |
|-------|------|---------|
| `protocol` | integer | This document's version, `1`. A client should refuse a version it was not written for. |
| `icao` | string | The address queried, upper case. |
| `from`, `to` | string or null | The range actually used, ISO 8601 UTC. `from` is null when the request gave none (archive start). |
| `from_ts`, `to_ts` | number | The same range as Unix seconds (`from_ts` is `0` when no start was given). |
| `kinds` | list or null | The kinds filter in force; null means all. |
| `server_time` | string | The server's clock when the reply started, ISO 8601 UTC. |
| `archive_first_day`, `archive_last_day` | string or null | The oldest and newest UTC dates (`YYYY-MM-DD`) the archive holds. Data before the first day has been deleted by retention (currently 120 days) and cannot be returned. |
| `aircraft` | object or null | The aircraft database's entry for this address, or null if it has none. See below. |

**aircraft object.** From a Virtual Radar Server–style aircraft database, not from the radio.
Any field may be null.

| Field | Type | Example |
|-------|------|---------|
| `registration` | string | `"N430UA"` |
| `country` | string | `"United States"` (country of registration) |
| `manufacturer` | string | `"Airbus Industrie"` |
| `model` | string | `"A320-232"` |
| `model_icao` | string | `"A320"` (ICAO type designator) |
| `operator` | string | `"United Airlines Inc"` (may be a leasing trustee rather than the airline) |
| `operator_icao` | string | `"UAL"` |
| `serial` | string | `"568"` (manufacturer's serial number) |
| `year_built` | integer | `1996` |

### message

One per archived message, oldest first. **Every field listed is always present**; a value the
receiver did not have is `null` (never a missing key), except `text`, which is `""` when empty.

```json
{"record":"message","ts":1794793942.569,"time":"2026-11-16T00:52:22.569Z",
 "source":"VDL2","kind":"acars","message_type":"VDL2:position","direction":"down",
 "icao":"A524E1","icao_source":null,"registration":"N430UA","flight":"UA1234",
 "freq_mhz":136.8,"level_db":-41.2,"noise_db":-55.3,"errors":0,"ground_station":"2215C5",
 "frame":"I","label":"H1","sublabel":null,"msgno":"F12A",
 "text":"/HDQDLUA.POSN38117W101267,KIISS,005218,360,DYLLN,005809,WAIY...",
 "decoded":null,
 "position":{"lat":38.195,"lon":-101.445,"src":"ACARS text"},
 "english":{"category":"position","summary":"Position report: over KIISS at 00:52:18 UTC, FL360",
            "details":["38.20°N 101.44°W","Next waypoint DYLLN at 00:58:09 UTC, then WAIYN",
                       "Outside air -52°C","Wind 353° at 38 kt","Fuel remaining 10,000 lb"]},
 "adsb":{"lat":38.2011,"lon":-101.4502,"alt_ft":36000,"speed_kt":447.1,"track_deg":271.3,
         "position_ts":1794793940.112}}
```

| Field | Type | Meaning |
|-------|------|---------|
| `ts` | number | When the receiver heard it: Unix seconds, UTC, with fractions. Use this to sort and compare. |
| `time` | string | The same instant as ISO 8601 UTC, millisecond precision. For display. |
| `source` | string | `"ACARS"` (plain VHF ACARS, 129–131 MHz) or `"VDL2"` (VDL Mode 2, 136 MHz). |
| `kind` | string | What sort of frame it is. See *kinds* below. |
| `message_type` | string | The receiver's classification, `"<source>:<type>"`, e.g. `"VDL2:position"`. See *message types* below. |
| `direction` | string or null | `"down"` aircraft to ground, `"up"` ground to aircraft. Almost everything heard is `"down"`. |
| `icao` | string | The aircraft's ICAO address (always the one queried). |
| `icao_source` | string or null | How the address was established. `null`: the radio frame itself carried it (all VDL2). Otherwise it was looked up from a plain-ACARS message: `"registration"` (from a registration seen earlier or in the live ADS-B feed), `"aircraft database"` (registration looked up in the database), `"callsign"` (flight number matched to a live ADS-B callsign, the least certain). |
| `registration` | string or null | Registration as the message gave it, e.g. `"N430UA"`. Many VDL2 frames carry none. |
| `flight` | string or null | Flight number as the message gave it, e.g. `"UA1234"` (airline IATA code + number, as ACARS sends it). |
| `freq_mhz` | number or null | Receive frequency in MHz. |
| `level_db` | number or null | Signal level in dB relative to the receiver's full scale. Comparable only between messages from this receiver; not an absolute power. |
| `noise_db` | number or null | Noise floor at the time, same scale. `level_db - noise_db` is the signal-to-noise ratio. |
| `errors` | integer or null | Bit errors the decoder corrected (0 = clean). |
| `ground_station` | string or null | VDL2 only: the 24-bit address (hex) of the ground station at the other end of the link. **Not ground speed.** |
| `frame` | string or null | VDL2 link frame type: `"I"` information, `"S"` supervisory, `"U"` unnumbered. Null for ACARS and for messages archived before 30 October 2026. |
| `label` | string or null | ACARS message label (two characters, e.g. `"H1"`, `"SA"`, `"_d"`). Defines the message's format per ARINC 618/620. |
| `sublabel` | string or null | ACARS sublabel where the format has one. |
| `msgno` | string or null | ACARS message sequence number, e.g. `"F12A"`. |
| `text` | string | The message text exactly as decoded, `""` if none. This is the raw content; everything else is derived from it or from the frame. |
| `decoded` | string or null | When the decoder library could read a structured payload (CPDLC, ADS-C, ARINC 622 and similar), its plain-text rendering, e.g. `"WILCO"` or a multi-line ADS-C report. Null otherwise. |
| `position` | object or null | A position the receiver read out of this message's own content. See below. |
| `english` | object | A plain-English reading of the message. See below. Always present. |
| `adsb` | object or null | Where ADS-B (the live Virtual Radar Server feed) placed the aircraft when the message arrived. Null if ADS-B had no position for it then. See below. |

**kinds** (the `kind` field, and the values accepted in the request's `kinds` list):

| kind | Meaning |
|------|---------|
| `acars` | An ACARS message with a label and (usually) text: the airline and operational traffic. Over plain ACARS or carried inside VDL2. |
| `cpdlc` | Air traffic control datalink (FANS-1/A CPDLC / ADS-C); see `decoded`. |
| `link` | Link housekeeping with no content of its own: VDL2 "Receive Ready" acknowledgements, ACARS link tests. Often the most numerous; leave it out with `"kinds"` if not wanted. |
| `xid` | VDL2 XID frames: ground-station logon and handoff, often carrying the aircraft's own position in `position`. |
| `atn` | ATN network-layer frames over VDL2. |
| `partial` | One block of a multi-block message heard without the rest. |

**message types.** `message_type` is `source` + `:` + one of: `atc`, `position`, `flight`,
`request`, `crew`, `maintenance`, `data`, `encoded`, `linktest`, `status`, `partial`, `ack`,
`handoff`, `connection`, `network`, `other`. These are the categories the receiver's web page
filters on. Treat the list as open: a new type may appear.

**position object.** A position decoded from the message's content, as opposed to `adsb`.

| Field | Type | Meaning |
|-------|------|---------|
| `lat`, `lon` | number | Decimal degrees, WGS 84; north and east positive. |
| `alt` | number | Altitude in feet. Present only when the message gave one. |
| `src` | string | Which format it was read from, e.g. `"ACARS text"`, `"VDL2 XID"`, `"Weather report"`. Descriptive only. |

**english object.** The receiver's translation of the message, generated at query time by the
current translator (so it improves as the translator does, even for old messages).

| Field | Type | Meaning |
|-------|------|---------|
| `category` | string | Broad class: `position`, `flight`, `atc`, `request`, `crew`, `maintenance`, `data`, `encoded`, `link`, `other`, possibly others. |
| `summary` | string | One line describing the message. |
| `details` | list of strings | Further facts read from it, one per item. May be empty. |
| `position` | object | Present only for formats whose coordinates appear once decoded: `{"lat":..,"lon":..}` and sometimes `alt` (feet). |
| `generic` | boolean | Present and `true` when the translator did not recognise the format and the summary is only a general description. |

The translation is a best effort for human reading. For anything a program must rely on, use
`text`, `label`, `decoded` and the structured fields.

**adsb object.** The aircraft's ADS-B state at the moment the message arrived.

| Field | Type | Meaning |
|-------|------|---------|
| `lat`, `lon` | number | Decimal degrees, WGS 84. |
| `alt_ft` | number or null | Altitude in feet (geometric where ADS-B reported it, else barometric). |
| `speed_kt` | number or null | Ground speed in knots. |
| `track_deg` | number or null | Track over the ground, degrees true. |
| `position_ts` | number or null | When ADS-B last updated that position, Unix seconds. Usually a few seconds before `ts`; a large gap means a stale fix. |

### end

```json
{"record":"end","count":367,"elapsed_s":0.412}
```

| Field | Type | Meaning |
|-------|------|---------|
| `count` | integer | Number of `message` lines sent. A client should check it matches what it received. |
| `elapsed_s` | number | Server time taken, seconds. Informational. |

### error

Sent instead of a header when a request is refused, or (rarely) after messages when the
server fails part-way; either way the reply is not complete. The connection then closes.

```json
{"record":"error","error":"bad_request","message":"icao must be six hexadecimal digits, e.g. \"A1B2C3\""}
```

| `error` | Meaning | What to do |
|---------|---------|------------|
| `bad_request` | The request could not be read or failed validation; `message` says why. | Fix the request. |
| `timeout` | No complete request line arrived within 30 seconds of connecting. | Send the request promptly, ending it with `\n`. |
| `busy` | Two queries were already running and a slot did not free up within 120 seconds. | Retry later. |
| `internal` | The server failed; its log has the details. | Retry; report if persistent. |

## Behaviour to rely on

- **No matches is not an error.** An unknown address, or a range with no traffic, gives a
  header (with `aircraft` filled in if the database knows it) and `{"record":"end","count":0}`.
- **Order.** Messages arrive oldest first by `ts`.
- **Freshness.** The archive is written within about 10 seconds of reception, so a query whose
  range ends at "now" may miss the last few seconds.
- **Duration.** A day of one aircraft takes well under a second; the whole archive about 5 to
  15 seconds. Allow at least 5 minutes for the read timeout on very wide ranges, and longer if
  two other clients may be querying (a third query waits for one to finish).
- **Size.** Expect up to a few thousand messages per busy aircraft per week, roughly 1 kB each.
  Read the reply as a stream, line by line, rather than buffering it whole if memory matters.
- **Coverage.** Only what this receiver heard: aircraft within VHF range of Denver. Over 99.7%
  of archived messages carry an ICAO address. The rest are mostly ground-station squitters and
  empty link tests, plus a handful of plain-ACARS messages whose registration could not be
  turned into an address; those are not reachable by address.
- **Uplinks** (`direction` `"up"`) carry the address of the aircraft they were sent to, when it
  could be established, so they appear in that aircraft's results.

## Examples

Shell, one UTC day:

```bash
echo '{"icao":"A524E1","from":"2026-11-16","to":"2026-11-17"}' | nc -q 300 192.168.21.130 8687
```

Shell, plain form, everything since 1 November:

```bash
echo 'A524E1 2026-11-01' | nc -q 300 192.168.21.130 8687 > a524e1.jsonl
```

Python, standard library only (the complete version, with error handling and a command line,
is `tools/query_client.py`):

```python
import json, socket

def fetch(host, icao, start=None, end=None, port=8687):
    req = {"icao": icao, "from": start, "to": end}
    with socket.create_connection((host, port), timeout=300) as s:
        s.sendall(json.dumps(req).encode() + b"\n")
        header, messages = None, []
        for line in s.makefile("r", encoding="utf-8"):
            rec = json.loads(line)
            if rec["record"] == "header":
                header = rec
            elif rec["record"] == "message":
                messages.append(rec)
            elif rec["record"] == "end":
                assert rec["count"] == len(messages)
                return header, messages
            elif rec["record"] == "error":
                raise RuntimeError(f'{rec["error"]}: {rec["message"]}')
        raise RuntimeError("reply ended without an end record")

header, msgs = fetch("192.168.21.130", "A524E1", "2026-11-16", "2026-11-17")
```

## Versioning

`protocol` in the header is `1`. Within version 1 the server may add record types, fields,
`kind` and `message_type` values, `english.category` values and error codes; clients ignore
what they do not know. Removing or renaming a field, changing a field's type or meaning, or
changing the request format would be version 2.

## Server configuration

Optional, in `config.json` on the receiver; without it the server listens on `http_host`
(normally all interfaces), port 8687:

```json
"query": {"host": "0.0.0.0", "port": 8687}
```

The service reads the archive files directly (`archive.dir` in `config.json`) and does not
depend on the web server; restarting either leaves the other running.
