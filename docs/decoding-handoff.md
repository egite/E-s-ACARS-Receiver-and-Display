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

ARINC 622-4 (in the project folder, gitignored - ARINC sells these) specifies the
bit-oriented ATS format libacars already reads: a three-character IMI, a seven-character
dot-padded registration, hex application data, CRC. Character-oriented ATS is ARINC 623,
which we do not have. Our label 44 has no slash after its identifier so it is neither, which
fits it being an airline's own format.

## What the unread pile is, measured

Sweeping the archive through the translator on 20 October (ARINC 622 messages excluded, since they
only decode live) left about 95,000 of 1.3 million messages without a specific reading. Nearly all
of that is engine-monitoring continuation blocks (United, Southwest, Frontier H1/DF), which carry
no text structure to read and are correctly left generic. The families with structure and value
were label 15, UPS `*POS`, FedEx `PO6`, the weather-profile layouts above and the United short
forms, and those are now read. Useful references: airframesio/acars-message-documentation on
GitHub (label 15 layout, 5Z command codes) and the acarsonline.pbworks.com label list.

**Measure what the screen shows, not the archive's blocks.** Until 21 October the sweep judged
every archived block alone, and called 95,000 messages unread. VDL2 multi-block reports are
joined by the server in memory, so the archive keeps each block separately and the middle
of a report looks like nothing. Replayed through the server's own `BlockAssembler` (now
`web/blocks.py`, so the tool can import it) the pile is 31,180 of 782,188, and stripping
the inline `#DFB` sublabel that hid 6,701 Airbus and United monitoring reports from their
patterns brings it lower still.

## Open, in rough order of tractability

1. ~~Southwest label 37 origin and destination.~~ Solved 20 October, not by attack: see
   the pitfall on known plaintext above. `SOUTHWEST_LETTERS` in `web/translate.py` says how
   it was checked.
2. **Business aviation**: after the 20 October sweep, what is left unread in this cipher is a
   handful of free-text MSG reports with rare characters, and one POA whose minus sign is
   unseen in its group. The field separator inside POA reports (shown `?`) is unidentified.
   A wider collection range or a shared capture would fill the rest.
3. **Rare labels.** Swept 20 October. Label 48 (Delta/Northwest) is a connection request and
   now reads. The unread label 42 messages are all business-aviation ciphertext (item 2), as
   are label 48's XA/ZD ones. Label 4S is Air Canada `DFR AC1073/30`, a flight number and
   day with nothing to check a meaning against. Label 88's one unread message is a
   clear key-00 business-aviation `AMI02` event the label 44 reader doesn't know. Label 15 `(1N…` (nine messages) has one field fewer than `(2N…`
   and is not read.
4. **Label MA**: don't. It is closed on entropy.
