# Message decoding: state, method, and what not to repeat

Written 19 October 2026, for whoever picks this up next. The receiver at KFNL has been
running since 23 September; `archive/messages/` holds 20 days of traffic at the time of
writing and keeps 120.

This is about reading message *contents*. The radio side, the decoders and the web app are
in the README; `docs/decoder-evaluation.md` covers the acarsdec/xng question.

## What reads and what doesn't

About 96% of content messages now read. The rest is label MA, which is closed, and a thin
tail of rare labels.

| family | state |
|---|---|
| Standard ACARS/VDL2 formats | handled by `web/translate.py` |
| CPDLC and ADS-C (label BA, H1/M1) | **libacars**, already linked. AT1, DR1 and CC1 all decode |
| Delta/Northwest label 17 | waypoint progress reports, decoded |
| Southwest label 37 | positions, altitude, Mach, airspeed, track, wind, temperature, fuel, weight. Origin and destination **not** read |
| Business aviation labels 41/42/44 | fully read, cipher broken: position, altitude, times, fuel, origin, destination, plus OFF/ETA/TWX/MSG events |
| FedEx label 16 AUTPOS | decoded |
| label MA | **closed**. 6.41 bits/char against a 6.44 ceiling over 442 messages: encrypted or compressed, no structure to attack |

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
moment. The ADS-B pairing is what makes an unfamiliar format solvable.

One caveat when replaying it. `decoded` - what libacars made of an ARINC 622 payload - was
only added to the archived fields on 19 October, and it cannot be recovered from `text`
afterwards because reading it needs the decoder's raw JSON the archive drops. Files written
before that date replay every CPDLC message as unread. Measuring the replay rather than the
live pipeline is how label BA came to be called the largest unsolved family at 580 messages
a day, when it had been decoding to WILCO all along.

**`web/airports.json`** — 32,000 airports with coordinates and a rank, built by
`tools/update_airports.py`. The rank matters: a dictionary of merely *real* airports will
happily accept Flying Cloud for a Southwest flight. The coordinates matter more, because
they turn "is this a real code" into "does this route pass near where the aircraft was".

**`tools/crack_routes.py`** — the attack on the obfuscated route fields and, more
importantly, the honest way to score it. Read its docstring before changing anything.

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

## Open, in rough order of tractability

1. **Southwest label 37 origin and destination.** Fields 0 and 1, four characters each,
   all letters, position 0 constant (a `K`). They hold still within a leg and reverse on a
   turnaround, so they are origin and destination. The attack sits at chance with 548
   records per group, where the same attack on business aviation reached the ceiling with
   170 - so this is method, not data, and nothing published covers it.
2. **The 15 business-aviation messages still unread**, mostly missing one rare key
   character. The paper's 98.44% suggests the rest is reachable; a wider collection range
   or a shared capture would do it.
3. **Rare labels** - 42, 4s, 88, 48 - a few dozen messages each, formats unknown. ARINC 623
   would probably name them.
4. **Label MA**: don't. It is closed on entropy.
