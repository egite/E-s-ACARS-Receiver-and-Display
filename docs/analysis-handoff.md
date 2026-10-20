# Moving the analysis off the receiver

Written 20 October 2026, for the session that starts on the i7-9700. The receiver stays on the
CN62; only the reading of the archive moves. `docs/decoding-handoff.md` is the state of the
decoding itself and the evidence bar; read it first. This file is the practical side: what is
where, what to copy, what is unfinished.

## Why move

The CN62 is an i7-5500U: two cores, four threads, 3.4 GB of RAM with about 1.2 GB free. It runs
the two decoders, the web app and the archive writer, and sat at a load average of 4.6 while
the archive sweeps ran. A sweep of the 1.3 million archived messages takes several minutes
there and slows the decoders while it runs. The i7-9700 has eight cores at about twice the
single-thread speed and nothing else to do.

## What stays on the CN62

Everything that touches the dongles or the live feed: `acars-decoder@acars`,
`acars-decoder@vdl2`, `acars-web` (port 8686), the archive writer inside it, and `rtl_airband`
(hand-started, not a unit, by decision). The box is `pi@192.168.21.130`, hostname `E-CN62`,
passwordless sudo for `pi`. Do not run `run_*.sh` there; they fight the services for the dongles.

## What to move, and how

**The repository.** Clone it. At the time of writing the work of 19-20 October is **uncommitted**
on the CN62 (`web/translate.py`, `tools/crack_routes.py`, `tools/sweep_unread.py`,
`tests/corpus.jsonl`, `README.md`, both handoff docs). Commit it there before cloning, or the
new parsers do not come across. `git status` on the CN62 shows the list.

**The archive.** `archive/` is gitignored and exists only on the CN62. `archive/messages/` is
one file per UTC day, 485 MB for 21 days, retention 120 days. Every line is a message plus
`adsb`, the aircraft's position at that moment, and that pairing is what every parser is judged
against, so the copy is the whole point. Pull it, don't push:

```bash
rsync -av --delete pi@192.168.21.130:/home/pi/ACARS/archive/messages/ ~/ACARS/archive/messages/
```

Run that before each session, or from cron. The files for past days never change; today's
file grows. Nothing in the analysis writes back to the CN62.

One caveat from the other handoff still applies: `decoded` (what libacars made of a CPDLC
message) is only in files from 20 October on, so earlier files replay every CPDLC message as
unread. The sweep tool excludes them for that reason.

**Nothing else.** `web/airports.json` is in the repo. `config.json` is gitignored and holds the
receiver's settings; the analysis tools do not read it (`tools/crack_routes.py` has the
receiver location as a constant if you need it, and `--archive` to point at the copy).
No SDR libraries, no libacars, no systemd units. `web/translate.py` imports only the standard
library and `airports.py`, so `python3 tools/sweep_unread.py` works on a bare checkout.
Python here is 3.14; nothing version-specific is used.

## The tools

- `tools/sweep_unread.py` - the archive sweep that found the families read on 20 October.
  No arguments: buckets of unread messages by label, airline and text shape, with examples.
  `--family REGEX` extracts one family to a JSONL file with the ADS-B pairing;
  `--check FILE` runs the translator over it and reports position error against ADS-B and
  ground speed error where the summary has one. Read its docstring before trusting a count.
- `tools/crack_routes.py` - the route-field attack and its honest scorer. Docstring first.
- The corpus check in the README (`tests/corpus.jsonl`, 7,571 messages including 33 added
  on 20 October for the new formats). The generic count was 944 at the time of writing; a
  change that raises it has broken something.

## The method that worked, in one paragraph

Sweep, pick the biggest bucket whose examples have structure, extract the family with its
ADS-B pairing, guess the layout, and only believe a field when something outside the message
agrees with it: position against the fix, ground speed against the fix, Mach and wind against
ground speed through the wind triangle, fuel against a plausible burn rate over consecutive
reports from one aircraft. Two fields were withheld on 20 October because they failed this
test despite looking right (the FedEx "heading", the label 15 OFF "ETA"). The wrong turns
and their causes are in `docs/decoding-handoff.md`; they were all measurement, not decoding.

## Unfinished

1. **The uncommitted work above.** Commit on the CN62 first.
2. **`acars-web` on the CN62 has not been restarted** since the parsers were added, so the
   live display still runs the old translator. The server does not replay the archive at
   startup, so a restart drops the seven days of in-memory history (the archive on disk is
   unaffected). Restart when that is acceptable: `sudo systemctl restart acars-web.service`.
   After that, any further change to `translate.py` reaches the live display the same way:
   pull on the CN62, restart the service.
3. **Open decoding items**, in order, in `docs/decoding-handoff.md`: Southwest label 37
   origin and destination; the last business-aviation key characters; the rare labels; label
   15 `(1N…` (nine messages).
4. **Analysis on the i7 changes nothing live.** A parser is only in production once it is
   committed, pulled on the CN62 and the web service restarted. Keep that loop in mind so the
   two checkouts do not drift.
