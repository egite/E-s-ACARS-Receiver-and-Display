# Message decoding: state, method, and what not to repeat

Written 19 October 2026, for whoever picks this up next. The receiver at KFNL has been
running since 23 September; `archive/messages/` holds 20 days of traffic at the time of
writing and keeps 120.

This is about reading message *contents*. The radio side, the decoders and the web app are
in the README; `docs/decoder-evaluation.md` covers the acarsdec/xng question, and
`docs/handoff-cn62.html` (18 September) is the earlier handoff from the move to this machine.

## What reads and what doesn't

About 96% of content messages now read. The rest is label MA, which is closed, and a thin
tail of rare labels.

| family | state |
|---|---|
| Standard ACARS/VDL2 formats | handled by `web/translate.py` |
| CPDLC and ADS-C (label BA, H1/M1) | **libacars**, already linked. AT1, DR1 and CC1 all decode |
| Delta/Northwest label 17 | waypoint progress reports, decoded |
| Southwest label 37 | positions, altitude, Mach, airspeed, track, wind, temperature, fuel, weight, and (20 October) origin and destination |
| Business aviation labels 41/42/44 | fully read, cipher broken: position, altitude, times, fuel, origin, destination, plus OUT/OFF/ETA/TWX/MSG events |
| Business aviation on labels 17/42/48 (20 October) | the same cipher off label 44: POA position reports on 17 (position 0.35 km from ADS-B, wind turns 413-523 kt ground speeds into 462-473 kt true airspeed), SPN avionics software reports and TST on 48, TWX on 42. Space, point and minus added as `BIZJET_PUNCT`; groups 03 and 05 had Q where the space is |
| FedEx label 16 AUTPOS | decoded |
| Label 15 position reports | decoded (20 October): general-aviation `(2N…` reports in decimal degrees with ground speed and temperature; British Airways `FST01` with route, FL, fuel, wind, heading, ground speed (within 2 kt of ADS-B) |
| UPS `*POS` (H1) | decoded (20 October): position, FL, wind, Mach, temperature, fuel. Mach, temperature and wind reproduce the ADS-B ground speed through the wind triangle; fuel falls at a 757's burn |
| FedEx `PO6` (H1) | decoded (20 October): route, time, FL, Mach, air temperatures, phase, position (2 km from ADS-B). A field that looked like heading matched the ADS-B track a third of the time and is withheld |
| Weather profile rows: American `D3B/D3M`, Alaska `E330`, Northwest `*WXR`, United `EB0032`/`EB0116`, Allegiant `ABS…BA_`, Southwest `#DFB764xx` | decoded (20 October) as weather observations: same row layouts as the formats already read, reached by matching their headers |
| JetBlue/Air Canada H1/T8 `R721V` | report name, route, time, position and ground speed (exact against ADS-B); engine values unread |
| United 5Z short forms (`/B6 DENDSM DSM R13`) | decoded (20 October) |
| United `HYDN` (H1, 737 hydraulic report) | decoded (20 October): altitude, Mach, static and total temperature, N1/N2 both engines, hydraulic pressures, weight. No ADS-B pairs (plain ACARS, no ICAO address), so checked physically: total temperature = static x (1 + 0.2 M^2) to 0.2 C over 26,286 samples |
| United `PNM4` (H1, performance report) | decoded (20 October): altitude, Mach, static and total temperature, N1; same temperature check holds over 25,713 samples. `PNMN`, `APM` and `CAB` are now at least named with route and phase on reassembled messages, which start `#DFB` |
| Delta `APM` (H1/DF, cruise performance) | decoded (20 October): position, Mach, true and calibrated airspeed, temperatures, ground speed, track, N1, fuel flow, weight. Ground speed 0.5 kt from ADS-B, position 3.5 km; CAS equals what Mach and altitude give to 0.14 kt |
| United `A86BB` (H1, 737 sample report) | decoded (20 October): altitude, Mach and static temperature per sample, plus tail, phase, route. True airspeed from Mach and temperature is 17 kt from ADS-B ground speed at the median (64 shuffled) |
| United `TKON`/`TKO` (H1, takeoff engine reports) | route and takeoff time only (20 October): the time is 0-10 min before receipt in 98% of 878. The engine data is not read |
| Delta `FD` (H1/DF) | named (20 October): route, tail, phase, and each row's code with its time interval. The codes' meaning is unknown |
| United `CRZN` (H1, cruise report) | decoded (20 October): position, altitude, Mach, static temperature, weight. Altitude 12 ft from ADS-B, position 10 km (the minute between report and fix) |
| Report 239 (Airbus ACMS `REP239` and Delta's Boeing layout) | snapshot decoded (20 October): position, FL, total and static temperature, wind on 4,839 reports. Position 1.3 km (2.6 km in the two-decimal layout); the wind shrinks the cruise ground-speed spread from 38 to 7 kt; Mach from the two temperatures equals Mach from true airspeed to 0.000 |
| ATS Facilities Notification (B0, and H1 from the flight computer) | decoded (21 October) from ARINC 622: centre, flight, registration, ICAO address, position, offered ADS-C/CPDLC versions. Position 0.43 km from ADS-B, ICAO matches in 66 of 66 |
| Delta/Northwest label 48 | connection requests (20 October): a flight number or city at the destination. 9 of 10 requested flights the receiver also heard leave the asker's destination, against 12% by chance |
| label MA | **closed**. 6.41 bits/char against a 6.44 ceiling over the 442 messages held at the time of the test (the archive now has 1,474, all with text): encrypted or compressed, no structure to attack |

Two ciphers were broken here and neither is in any public decoder. The business-aviation one
is documented in the literature (see Prior art); the Southwest one is not.

## The evidence bar

This is the part that matters most, because nearly every wrong turn below came from
measuring badly rather than decoding badly.

A field is only believed when something **outside the message** agrees with it:

- positions against the aircraft's own ADS-B fix — label 44 lands 0.43 km out at the median
- altitude, Mach and airspeed against each other through the atmosphere — true airspeed
  tracks Mach times the speed of sound at the reported altitude with r=+0.999
- wind direction and speed through the wind triangle — predicts ADS-B ground speed to
  2.4 kt against 19.5 kt for airspeed alone, an eightfold reduction
- temperature against altitude, r=-0.976
- route fields against independence: nine cipher alphabets were derived separately, and
  where two of them read the same aircraft's flight within the same hour they name the
  same airport pair 95% of the time, using no geometry and no dictionary

Correlation with a physical quantity is worth far more than a plausible-looking output. A
wrong alphabet produces real-looking airport codes; it does not produce r=0.999.

## Infrastructure

**`archive/messages/*.jsonl`** — every frame received, one file per UTC day, each line the
message plus `adsb`: the aircraft's position, altitude, ground speed and track at that
moment. The ADS-B pairing is what makes an unfamiliar format solvable. The archive is
gitignored, so it exists only on this machine; a clone of the repo has none of it.

One caveat when replaying it. `decoded` - what libacars made of an ARINC 622 payload - was
only added to the archived fields with the restart late on 19 October local time, so it
first appears in the `2026-10-20` UTC file, and it cannot be recovered from `text`
afterwards because reading it needs the decoder's raw JSON the archive drops. Every file
up to and including `2026-10-19` replays every CPDLC message as unread. Measuring the replay rather than the
live pipeline is how label BA came to be called the largest unsolved family at 580 messages
a day, when it had been decoding to WILCO all along.

**`web/airports.json`** — 32,000 airports with coordinates and a rank, built by
`tools/update_airports.py`. The rank matters: a dictionary of merely *real* airports will
happily accept Flying Cloud for a Southwest flight. The coordinates matter more, because
they turn "is this a real code" into "does this route pass near where the aircraft was".

**`tools/crack_routes.py`** — the attack on the obfuscated route fields and, more
importantly, the honest way to score it. Read its docstring before changing anything.

**`tools/sweep_unread.py`** - replays the archive through the translator and buckets what
comes back unread, extracts one family with its ADS-B pairing, and scores a parser against
the fixes. The 20 October formats were found and checked with it. `--crib` looks for known
plaintext: it gathers what each message's flight said in clear elsewhere (route) and what
ADS-B says (flight level, speed, track, time), then scores every column for that fact in
clear or under a consistent substitution, against a shuffle. Run blind on label 37 it finds
the route fields at 1.00 against 0.38 shuffled; on label 48 it found the route in the header.

Analysis is moving to a faster machine; `docs/analysis-handoff.md` says what to copy and
what stays on the receiver.

## Pitfalls that cost real time

**Know what success looks like before calling a number a failure.** The route attack was
judged for days against "71% of decoded routes pass within 100 km of the aircraft" and
treated as failing. Routes *known true* from the clear-text reports score 78% on the same
test, because real flights deviate from a great circle. 71 against a ceiling of 78 was
nearly solved, not broken. Every proxy metric needs its ceiling and its chance level
measured before it is used: here, 78% and 41%.

**A crib is an assertion about the data.** `crib()` assumed field 0 always reads `POS`. It
doesn't — there are TOC and TOD reports too — so a group whose first training record was a
TOC asserted that T is P and C is S. Three of five letters wrong, failing silently because
POS and TOC share their middle O. That one line produced 4x swings in objective between
runs, which I diagnosed as local minima, then a bad prior, then overfitting, then a
misaligned objective, running an experiment for each. All four were the crib.
`translate_label44` had matched `ETA|POS|OFF|ON|IN` in this repo the whole time.

**Compare runs, not stories.** When results swing between runs, measure the spread before
theorising. `crack_routes.py` prints it for this reason. One controlled comparison - same
rows, same scorer, two candidate alphabets side by side - answered in a single run what four
experiments hadn't.

**The archive grows under you.** Two runs minutes apart see different data and reshuffle
differently, so a fixed seed guarantees nothing; one record's difference moved a group by 14
points. Freeze a snapshot before comparing anything.

**Check independently-derived things against each other when you combine them.** The digit
and letter halves of each key were recovered separately and assumed disjoint. Three
characters clashed, and the letter half - a hill-climb - was overriding the digit half,
which had been checked against ADS-B to 0.43 km. Caught only because decrypting a different
message type surfaced a stray Z.

**Look for known plaintext before attacking.** Label 37's route fields sat at chance under
a dictionary attack for days. Southwest's own H1 position reports name the route in clear
(`...,KDEN,KSAN,...`), so pairing each label 37 report with the same flight's H1 report
gave 1,917 cribbed records and every letter in nine alphabets in one pass, 1,071 of 1,074
exact on days held out. Before attacking an obfuscated field, look for the same fact sent in
clear by the same flight on another label; the archive pairs them by aircraft and time.

**Look for prior art first.** The business-aviation cipher is published. Searching for it at
the outset would have supplied the key identifier, the nine keys and the POS crib directly.

## Prior art

Smith, Moser, Strohmeier, Lenders and Martinovic, *Economy Class Crypto: Exploring Weak
Cipher Usage in Avionic Communications via ACARS*, Financial Cryptography 2017
(<http://www.cs.ox.ac.uk/files/9693/fc-paper.pdf>). Independently derives the same
structure found here: labels 41/42/44, a two-digit key identifier 01-09, nine keys shared
across all aircraft using the cipher, and the three-letter-plus-two-digit message prefix as
the way in. They recovered 98.44% of key characters from 2,690 messages.

Two things from it are untested here. They say the keys are **shared across all aircraft**,
which would mean our table decodes this traffic anywhere and that thin key groups could be
filled from someone else's capture. And they recovered their remaining characters from
**SATCOM weather uplinks** - METAR, TAF, NOTAM - which have fixed structure and abundant
known plaintext. That crib is unavailable here: all 1,554 ciphered messages this receiver
has are downlinks, and VHF cannot hear SATCOM.

ARINC 620-6 (in `ARINC/`, gitignored, with 618-6, 622-4 and 702A-3) is not the missing key it looks like: it
assigns most numeric labels (10, 15-17, 22, 31, 33, 37, 41, 42, 44, 48, 1L ...) as
user-defined, so the airline formats that remain unread are defined nowhere public. What it
does fix is the standard downlinks - Q3, QF, Q2, Q7, HX, 5D are now read from it - and it
shows label MA is not one of its labels (MA is only a TEI there; ACARS Message Security,
ARINC 823, uses labels P0-PC), so MA is an operator's own encrypted channel.

ARINC 702A-3 (also in `ARINC/`) defines the flight-computer messages: it
confirmed `WQ`/`DQ`/`WR` in wind requests, gave fuel remaining after the wind in POSN
reports (hundreds of pounds here, by burn rate), and showed that `RES.../AK` is an
acknowledgement where only `AC` is acceptance - the translator had called both accepted.
Only codes the spec defines and this traffic uses are named in the code; the tables are not
copied in. ARINC 622-4 (in `ARINC/`, gitignored - ARINC sells these) specifies the
bit-oriented ATS format libacars already reads: a three-character IMI, a seven-character
dot-padded registration, hex application data, CRC. Character-oriented ATS is ARINC 623,
which we do not have. Our label 44 has no slash after its identifier so it is neither, which
fits it being an airline's own format.

## What remains, and what it would take

Measured 21 October 2026 with `tools/sweep_unread.py`, which replays the archive through
the translator joined the way the screen joins it (ARINC 622 traffic excluded: libacars
decodes it live). Of 782,188 messages, **19,669 (2.5%) had no specific reading**; after the
21 October formats below, **17,235 (2.2%)**. A
further **~23,000 are monitoring reports identified only by name and route**, their values
unread. The groups, and what would open each:

| group | messages | what it is | what would open it |
|---|---|---|---|
| Headless monitoring blocks | ~8,800 | Middle blocks of multi-block H1 reports whose first block was never received, so they cannot be joined | Nothing recovers the missed ones. Fewer in future with better reception (antenna, second receiver) and the decoders' end-of-message flag (TODO) |
| H1 monitoring, layout unknown | ~5,300 | Airline-configured engine and aircraft monitoring reports (United, Southwest, Alaska, Frontier) | The airlines' report definitions, from the aircraft maker or monitoring vendor (Boeing AHM, Airbus, Teledyne Controls, Honeywell), or someone at an operator. No ARINC specification covers them |
| Named, values unread | ~23,000 | Airbus ACMS report numbers (8,800), Boeing OHMA (5,100), United `A`/`B`-numbered, PNM, APM, CAB, takeoff engine data, avionics configuration | Same as above. Air-data fields can be read without definitions - temperature, Mach, altitude and wind check against physics and ADS-B, as HYDN, PNM4, A86, CRZN and report 239 were - but engine values (EGT, vibration, oil, bleed) have nothing outside the message to check them by |
| Label MA | 1,334 | Encrypted: 6.41 bits/char against a 6.44 ceiling. Not an ARINC 620 label (MA there is only a TEI) and not ARINC 823 AMS, which uses P0-PC | Nothing. Closed |
| Lufthansa 1L | 227 | Structured (low-entropy columns, repeated runs) but no known plaintext attaches | A capture where these flights send the same facts in clear, or Lufthansa's own documentation |
| Business-aviation residue | ~20 | Messages missing rare key characters, plus one clear key-00 `AMI02` event (label 88) the reader doesn't know | Months more data, a capture from another receiver (the keys are shared), or SATCOM uplinks per the paper (needs an L-band receiver) |
| Southwest 37 residue | 351 | Key groups 14, 17-19 not in the table, and odd variants | More data per group; the H1 crib method would work once enough pairs exist |
| Not yet attempted | ~100 | Done 21 October: label 83, Porter 19, Turkish 16 (both layouts), Cathay 32 (route and altitude - its position is stale), Air Canada 4R and 4T (AGFSR with its own position, 1.2 km from ADS-B; ETA), label 49 as flight-phase reports (ICCL/CLER/ERDC/DCAP), runway data on 33/4N (Frontier, Alaska, Sun Country, SkyWest `Q,H`) and the 4P request for it, Frontier 14 ETAs, FedEx and BA 10, 3J/3S as route and time, Northwest's report-239 copies. Left: the label 14/16 `(2` layout (30, no ADS-B to check against) and stragglers of a few each | - |
| Not worth chasing | small | Air Canada 4s `DFR` (flight and day only), RB, free-form 5Z | - |

Two families are held back on purpose. American's A39 `C1TRP` trip reports (460) carry a
position and flight level that look clean - altitude matches ADS-B to the foot - but the
position is either ~5 km from the aircraft or ~150 km behind it along its track, with the
report only seconds old: a sample from some event the message doesn't name, so it stays off
the map until that trigger is found (an archived ADS-B track would show it). Label 15
`(1N...` (nine messages) has one field fewer than `(2N...` and is not read.

**Resources.** In `ARINC/` (gitignored): 618-6, 620-6, 622-4 and 702A-3 are used; 429
(the avionics bus) and 653 (avionics partitioning) don't bear on message text. ARINC 623 is
not worth buying for this receiver - US departure clearances are 620 free text and only ~5
messages here are 623 territory - and no ARINC document defines the user-defined labels
most of the remainder sits on. What would help, most first:

1. Receiver changes, all cheap and forward-looking: the registration-to-ICAO lookup (built,
   `3aa3e8e`, to verify on the receiver), a few minutes of ADS-B track archived around each
   message, and keeping the decoders' end-of-message flag. See `docs/TODO.md`.
2. Airline or vendor report definitions for the monitoring reports, if they can be had.
3. Time: months of collection fill rare key characters and rare formats.
4. Better reception or a second receiver, for fewer orphaned blocks.

Sources already searched and what they gave: Smith et al. (FC 2017, PoPETs 2018) for the
business-aviation cipher; airframesio/acars-message-documentation and the acarsonline label
list for community layouts; ibosoftnet/acars-decoding-library (Elastic License - read, not
copied) for the ARINC 620-7 label list showing the numeric labels as user-defined. No
academic source outlines ARINC 620 or 623, and the Honeywell ACARS-over-IP patents cite 620
without reproducing it.
