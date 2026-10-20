# To do

Things agreed on but parked until the message-decoding work is finished.

## "Flush to file" button on the Settings page

Asked for 20 October 2026. A button under Settings that saves what the server holds in RAM to
disk and then restarts the program, so that a program update can be applied without losing
what's on screen.

What is actually at risk, measured from `web/server.py` as it stands: the message store keeps
the last `history_hours` of messages "in RAM only (nothing survives a restart)", along with
the aircraft tracker's flight cards and the weather samples behind the wind map. The messages
themselves are not at risk - the archive (`archive/messages/*.jsonl`) is written as each one
arrives - but after a restart the page starts empty.

Two ways to build it, to decide when we get here:

- **Flush, then reload on start.** The button writes the store and tracker to a snapshot file
  and restarts; on start the server loads the snapshot if it is recent. Simple, but it is a
  second copy of what the archive already holds, and a crash (rather than the button) still
  loses it.
- **Rebuild from the archive on start.** On every start, replay the last `history_hours` of
  the archive through the translator (and `web/blocks.py` for multi-block reports) to refill
  the store. The button then only has to restart. This also survives crashes and power cuts,
  and picks up translator improvements for the messages already on screen. Cost: replaying a
  day of archive takes on the order of a minute on this hardware, so start-up is slower.

Either way the restart needs to work under systemd (`systemd/acars-web.service`): exit and let
`Restart=` bring it back, rather than re-exec in place.

## Before then

- Restart the receiver after pulling and confirm the page loads: commit `4f66e82` moved
  `BlockAssembler` from `server.py` into `web/blocks.py`, which could not be run here (no
  aiohttp on the analysis machine).
- After installing `AircraftDatabase.sqb` in the project folder and restarting, check the log
  says "aircraft database: N registrations", and that new archive lines from the ACARS
  receiver carry `icao` with `icao_src` and, when ADS-B has the aircraft, `adsb` (commit
  `3aa3e8e`; also untested against a running server).
- Keep the decoders' end-of-message flag. ARINC 618 ends every block but the last with ETB
  and the last with ETX, so a report's completeness is known on the air; acarsdec and
  dumpvdl2 each pass some form of it on, but `normalize_acars`/`normalize_vdl2` drop it (no
  archived frame carries it to check the field names against - look at live decoder JSON
  on the receiver). With it, `BlockAssembler` could tell a finished report from one still
  missing blocks instead of waiting out `BLOCK_WINDOW_SECS`.
- Push the decoding commits to GitHub (`origin`) when ready; none have been pushed.
