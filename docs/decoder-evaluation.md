# Decoder evaluation: acarsdec vs xng

Measured on the receiver at KFNL, 15–18 September 2026. Raw data in
[`tools/decoder-trial/results/`](../tools/decoder-trial/results/); harness in
[`tools/decoder-trial/`](../tools/decoder-trial/).

## Why look at all

The receiver runs GPL-2.0 acarsdec and GPL-3.0 dumpvdl2 while the repository
itself is CC0. Nothing here links against them — `receiver.py` spawns them as
separate processes that speak JSON over UDP — so there is no derivative-work
problem today, but the whole stack depends on two copyleft decoders.

Every other VHF ACARS decoder on GitHub turned out to be acarsdec lineage:
`acarsx` is acarsdec 2.3 with 13 commits, `acarsrx` is 11 commits of Rust,
`acars-decoding-library` is a text parser with no demodulator, and SDRangel's
ACARS demod is still an unreleased preview. Switching to any of them trades a
maintained decoder for a stale copy of the same code.

The one real candidate is **[xng](https://github.com/airframesio/xng)** (Rust,
Apache-2.0/MIT), which would replace *both* decoders with one permissive binary,
ships prebuilt `.deb`s, and emits acarsdec-compatible JSON that `web/server.py`
already parses.

## What the measurements say

**xng decodes more.** A 4-hour alternating A/B at 960 kS/s on two channels, with
zero overflows on either side:

| Over 2 h each | acarsdec | xng |
|---|---:|---:|
| Clean messages | 829 | **1285** (+55%) |
| Distinct aircraft | 92 | **117** |
| CPU, one core | 3.0% | 24.3% (8.1×) |

xng led in 5 of 6 cycles; median ratio 1.58, geometric mean 1.70, range
0.64–4.08. Junk is negligible: 31 CRC-bad plus 4 malformed tails out of 1320 raw.
Treat **+55% as the centre of a range** — dropping the single outlier cycle still
leaves +25%. The figure comes from 130.025 and 130.450 only; the advantage on
weaker channels is assumed, not measured.

**xng costs about 8× the CPU, on one thread.** Per-thread sampling at production
settings found **97.9% on a single `tokio-rt-worker`** out of nine threads. This
was measured with the machine quiet — load 3.0, ~250% of the CPU idle — so more
cores do not help. Only a faster core does.

| Config | Sample rate | CPU (one core) | Result |
|---|---:|---:|---|
| acarsdec, 5 ch | 2.484 MS/s | 10.8% | fine |
| xng, 2 ch | 0.960 MS/s | 24.3% | fine |
| xng, 3 ch | 1.248 MS/s | 45.4% | fine |
| xng, 5 ch | 2.496 MS/s | 100% | **67 overflows in 180 s** |

**The 3-channel config is viable on a Pi 4.** A 4 h 25 m soak held 45.2–45.4%
across 53 samples — a 0.2pp spread — with zero overflows and RSS flat at 41 MB.
Those three channels carry ~96% of ACARS traffic here (130.450 alone is ~78%).
Four channels would need ~1.58 MS/s, extrapolating to ~77% of the single worker
thread: too close to the overflow threshold to trust without its own soak.

**xng filters its own bad frames before UDP.** Of 3462 messages in that soak, 127
(3.7%) were CRC-bad; they reach the JSONL but not the UDP output. At the matched
sample point the log held 3230 CRC-ok and UDP had delivered 3229. `web/server.py`
needs no filtering change.

## Where this leaves things

On this Pi 4, xng is deployable at 3 channels and out of reach at 5. On hardware
with stronger single-thread performance it should clear 5 channels comfortably,
making it both a licensing win and a material reception improvement. See
[`handoff-cn62.html`](handoff-cn62.html) for the migration plan.

## Caveats and gotchas

- **xng has no ppm correction for RTL-SDR.** `--max-ppm` is VDL2-only. The ACARS
  dongle runs `-p 2`; at 131 MHz that is a 262 Hz offset, negligible for AM/MSK
  envelope detection, but there is no flag to match it.
- **Sample rates differ in their constraints.** xng wants an integer multiple of
  the mode's channel rate (24 kHz for ACARS); acarsdec wants a multiple of 12 kHz.
  RTL-SDR itself has an unusable gap between 300 kHz and 900 kHz.
- **acarsdec cannot replay wideband IQ**, so an identical-input comparison is not
  possible. `soundfile.c` maps each *audio* channel to one ACARS channel and calls
  `demodMSK` directly, with no frequency translation. Hence the time-sliced A/B.
- **SoapySDR ABI split** on this machine: `/usr/local` (ABI 0.8-3, airspy +
  sdrplay) versus the distro's (ABI 0.8, rtlsdr + miri). xng links the distro one,
  so `driver=sdrplay` fails an ABI check and `driver=miri` segfaults at 2.5 MS/s.
  The RSP1A is effectively unusable with xng.
- **`ps -eo pcpu` is a lifetime average, not current load.** It reported a stuck
  process at 86% that was actually consuming 140%. Sample `/proc/pid/stat` deltas.
- **`install.sh:57` clones acarsdec unpinned.** Two installs a week apart get
  different code with no record of what worked, and `--depth 1` fetches no tags so
  acarsdec self-reports as a bare commit hash rather than a version.
