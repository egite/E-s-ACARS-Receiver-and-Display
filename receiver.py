#!/usr/bin/env python3
"""Start the ACARS or VDL2 decoder using the settings in config.json.

    python3 receiver.py acars
    python3 receiver.py vdl2

Each receiver picks its decoder with receivers.<name>.decoder:

    acars:  "acarsdec" (default) or "xng"
    vdl2:   "dumpvdl2" (default) or "xng"

xng (airframesio/xng) is a single permissively-licensed binary that covers both modes.
It is selected per receiver, so ACARS can run on xng while VDL2 stays on dumpvdl2.

Each receiver also names its radio with receivers.<name>.sdr: "rtlsdr" (default) or
"sdrplay" (an SDRplay RSP, e.g. the RSP1A, through the vendor API service). On an RSP
`device` is the serial number, `gain` is the IF gain reduction in dB (20-59, or "auto" for
the AGC) and `lna_state` the LNA step, which is how SDRplay's own tools describe gain.

Decoded messages go to the web app as JSON over UDP. When run in a terminal the decoded
text is also printed, and receivers.<name>.log_file (if set) appends it to a file.
Set ACARS_CONFIG to use a config file other than ./config.json.
"""
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_config():
    return json.loads(Path(os.environ.get("ACARS_CONFIG", ROOT / "config.json")).read_text())


def gain_is_auto(rx):
    return rx.get("gain") in (None, "auto")


# Which decoder each receiver uses when config.json doesn't say.
DEFAULT_DECODER = {"acars": "acarsdec", "vdl2": "dumpvdl2"}
# Where each decoder's binary lives by default. xng is looked up on PATH (the .deb
# installs /usr/bin/xng); override with receivers.<name>.xng_binary.
DEFAULT_BINARY = {"acarsdec": "third_party/acarsdec/build/acarsdec",
                  "dumpvdl2": "third_party/dumpvdl2/build/src/dumpvdl2",
                  "xng": "xng"}
# xng requires the capture rate to be an integer multiple of the mode's channel rate.
# Only ACARS is documented (24 kHz), so any other mode must set sample_rate explicitly.
XNG_CHANNEL_RATE = {"acars": 24000}
SDRS = ("rtlsdr", "sdrplay", "shared")
# A "shared" receiver has no radio of its own: it decodes a slice of the other receiver's
# capture, handed over through a FIFO by the SoapyTee module (tools/soapy-tee). The slice
# runs at dumpvdl2's native rate, and the host's capture rate must be a multiple of it that
# acarsdec (12 kHz grid) and xng (24 kHz grid) both accept: steps of 4.2 MS/s.
SHARED_SLICE_RATE = 1_050_000
SHARED_OVERSAMPLE = SHARED_SLICE_RATE // 105_000
SHARED_RATE_STEP = 4_200_000
RUN_DIR = ROOT / "run"
# Capture rates each radio streams cleanly, in Hz. An RTL-SDR has no usable rates between
# 300 kHz and 900 kHz and drops samples above ~2.56 MS/s; an RSP streams 2-10.66 MS/s
# undecimated (both ends rounded to xng's 24 kHz grid).
RATE_LIMITS = {"rtlsdr": (960_000, 2_560_000), "sdrplay": (2_016_000, 10_656_000)}
# SDRplay IF gain reduction range (gRdB in the API; "IFGR" in SoapySDR and rtl_airband).
IFGR_MIN, IFGR_MAX = 20, 59
# The RSP's analogue IF filter: 1.536 MHz until the capture is 5 MS/s wide (the SoapySDR
# module picks it from the rate; dumpvdl2's native input hard-codes it).
RSP_NARROW_FILTER, RSP_WIDE_FILTER_RATE = 1_536_000, 5_000_000


def sdr_for(name, rx):
    chosen = rx.get("sdr") or "rtlsdr"
    if chosen not in SDRS:
        sys.exit(f"config.json: receivers.{name}.sdr is {chosen!r}; expected one of {SDRS}")
    return chosen


def shared_partner(name, cfg):
    """The enabled receiver that decodes a slice of `name`'s capture, if any: (its name, rx)."""
    for other, orx in (cfg.get("receivers") or {}).items():
        if other != name and orx and orx.get("enabled", True) and orx.get("sdr") == "shared" \
                and orx.get("host") in (None, name):
            return other, orx
    return None, None


def shared_host(name, rx, cfg):
    """For a shared receiver: the name and rx of the receiver whose radio it borrows."""
    host = rx.get("host") or ("vdl2" if name == "acars" else "acars")
    hrx = (cfg.get("receivers") or {}).get(host)
    if not hrx or not hrx.get("enabled", True):
        sys.exit(f"config.json: receivers.{name} is \"shared\" but receivers.{host} is missing or disabled")
    if sdr_for(host, hrx) != "sdrplay":
        sys.exit(f"config.json: receivers.{name} shares receivers.{host}'s radio, which must be an "
                 f"SDRplay RSP (sdr: \"sdrplay\"); an RTL-SDR can't capture both bands at once")
    return host, hrx


def slice_center(rx):
    freqs = [float(f) * 1_000_000 for f in rx["frequencies"]]
    span = max(freqs) - min(freqs)
    if span > 0.8 * SHARED_SLICE_RATE:
        sys.exit(f"config.json: shared receiver's frequencies span {span/1e6:.3f} MHz, more than the "
                 f"{SHARED_SLICE_RATE/1e6:.2f} MS/s slice carries")
    return round((max(freqs) + min(freqs)) / 2 / 1000) * 1000


def shared_fifo(name):
    return RUN_DIR / f"{name}.iq"


def shared_capture_plan(name, rx, cfg):
    """Centre and rate of a capture that covers the host's channels and its partner's slice,
    plus the tee's device-arg additions. None when nobody shares this receiver's radio."""
    partner, prx = shared_partner(name, cfg)
    if not partner:
        return None
    freqs = [float(f) * 1_000_000 for f in rx["frequencies"] + prx["frequencies"]]
    center = (max(freqs) + min(freqs)) / 2
    span = max(freqs) - min(freqs)
    rate = math.ceil((span + 2 * 48_000) / SHARED_RATE_STEP) * SHARED_RATE_STEP
    if rate > RATE_LIMITS["sdrplay"][1]:
        sys.exit(f"config.json: receivers.{name} and receivers.{partner} together span {span/1e6:.3f} MHz, "
                 f"more than one RSP capture can hold ({RATE_LIMITS['sdrplay'][1]/1e6:.2f} MS/s)")
    if rx.get("sample_rate") and int(rx["sample_rate"]) != rate:
        print(f"note: receivers.{name}.sample_rate ignored; a shared capture runs at {rate} S/s.", file=sys.stderr)
    tee = {"tee_driver": "sdrplay", "tee_fifo": str(shared_fifo(partner)),
           "tee_center": str(slice_center(prx)), "tee_rate": str(SHARED_SLICE_RATE)}
    return center, rate, tee


def sdrplay_ifgr(name, rx):
    """IF gain reduction for an RSP, or None for the AGC. In config it is `gain`, the same
    key the RTL-SDR uses, but in dB of *reduction*: 20 is the most gain, 59 the least."""
    if gain_is_auto(rx):
        return None
    try:
        value = float(rx["gain"])
        ifgr = int(value)
        if ifgr != value or not IFGR_MIN <= ifgr <= IFGR_MAX:
            raise ValueError
    except (TypeError, ValueError):
        sys.exit(f"config.json: receivers.{name}.gain is {rx['gain']!r}; on an SDRplay RSP it is "
                 f"the IF gain reduction, {IFGR_MIN}-{IFGR_MAX} dB, or \"auto\"")
    return ifgr


def soapy_gain(ifgr):
    """What to pass as SoapySDR's overall gain for an RSP IF gain reduction.

    SoapySDR spreads one "gain" number over the device's gain elements, each counted from
    its minimum. SoapySDRPlay3 lists IFGR (20-59) first, so overall gain g sets
    IFGR = 20 + g for g up to 39, and only then starts raising the LNA state. There is
    no way to pick the LNA state independently through this path."""
    return ifgr - IFGR_MIN


def soapy_device(sdr, rx, tee=None):
    """SoapySDR device string for acarsdec --soapysdr and xng --sdr. Both modules select
    by serial; an RTL-SDR index would need a different key, so stick to serials. With
    `tee`, the radio is opened through SoapyTee so a partner receiver gets a slice."""
    if tee:
        return "driver=tee," + ",".join(f"{k}={v}" for k, v in tee.items()) + f",serial={rx['device']}"
    return f"driver={sdr},serial={rx['device']}"


def rsp_filter_note(name, rx, basis_hz, remedy=""):
    """Warn when channels sit outside the RSP's IF filter. basis_hz is what decides the
    filter - the capture rate (xng) or the bandwidth the decoder asks for (acarsdec: the
    channel span) - or None when the decoder fixes it at 1.536 MHz (dumpvdl2). Returns
    True when a note was printed, for the tests."""
    freqs = [float(f) * 1_000_000 for f in rx["frequencies"]]
    span = max(freqs) - min(freqs)
    if basis_hz is not None and basis_hz >= RSP_WIDE_FILTER_RATE:
        return False
    if span <= RSP_NARROW_FILTER:
        return False
    print(f"note: receivers.{name}.frequencies span {span/1e6:.3f} MHz but the RSP's IF filter "
          f"is {RSP_NARROW_FILTER/1e6:.3f} MHz wide here, so channels more than "
          f"{RSP_NARROW_FILTER/2e6:.2f} MHz from centre are attenuated. {remedy}".rstrip(),
          file=sys.stderr)
    return True


def lna_note(name, rx, decoder):
    if rx.get("lna_state"):
        print(f"note: receivers.{name}.lna_state={rx['lna_state']} is ignored by {decoder}, which "
              f"reaches the RSP through SoapySDR; only dumpvdl2 sets the LNA state.", file=sys.stderr)


def decoder_for(name, rx):
    chosen = rx.get("decoder") or DEFAULT_DECODER[name]
    allowed = (DEFAULT_DECODER[name], "xng")
    if chosen not in allowed:
        sys.exit(f"config.json: receivers.{name}.decoder is {chosen!r}; expected one of {allowed}")
    return chosen


def xng_capture_plan(name, rx):
    """Centre frequency and sample rate for xng, derived from the channel list.

    xng tunes one capture centred on the channel set, so the rate has to span the
    channels and be a whole number of the mode's channel rate.
    """
    freqs = sorted(float(f) * 1_000_000 for f in rx["frequencies"])
    center = (freqs[0] + freqs[-1]) / 2
    if rx.get("sample_rate"):
        return center, int(rx["sample_rate"])
    unit = XNG_CHANNEL_RATE.get(name)
    if not unit:
        sys.exit(f"config.json: set receivers.{name}.sample_rate - xng's channel rate for "
                 f"{name} isn't known here, so it can't be derived.")
    span = freqs[-1] - freqs[0]
    rate = math.ceil((span + 2 * unit) / unit) * unit
    sdr = sdr_for(name, rx)
    floor, ceiling = RATE_LIMITS[sdr]
    rate = max(rate, floor)
    if rate > ceiling:
        limit = ("beyond what an RTL-SDR handles without dropping samples" if sdr == "rtlsdr"
                 else "beyond an RSP's 10.66 MS/s")
        sys.exit(f"config.json: receivers.{name}.frequencies span {span/1e6:.3f} MHz, which needs "
                 f"{rate/1e6:.3f} MS/s - {limit}. Use fewer channels or set sample_rate explicitly.")
    return center, rate


def ppm_scale(ppm):
    """Factor that turns a true frequency into the one to ask a mis-tuned dongle for.

    An RTL-SDR's crystal error stretches its whole frequency axis by (1 + ppm/1e6), so
    asking for f / (1 + ppm/1e6) lands it on f. acarsdec and dumpvdl2 get this for free
    from librtlsdr's own correction (-p / --correction); xng has no such option, so we
    pre-compensate the numbers we hand it.

    Both the centre *and* the channel list are scaled. Scaling only the centre would
    leave every channel off by centre x ppm (about 260 Hz at 131 MHz and 2 ppm) - the
    same error, just moved. Scaling both leaves a residual of only offset x ppm, where
    offset is the channel's distance from centre: ~2 Hz at the edge of a 2.4 MHz capture.
    """
    return 1.0 / (1.0 + (ppm or 0) / 1e6)


def xng_command(name, rx, cfg):
    port = cfg["acars_udp_port"] if name == "acars" else cfg["vdl2_udp_port"]
    shared = shared_capture_plan(name, rx, cfg)
    center, rate = shared[:2] if shared else xng_capture_plan(name, rx)
    tee = shared[2] if shared else None
    scale = ppm_scale(rx.get("ppm"))
    center *= scale
    # 6 decimal places in MHz is 1 Hz: enough to carry the correction, which .3f would
    # round away entirely (1 kHz steps vs a 262 Hz correction at 2 ppm).
    channels = ",".join(f"{float(f) * scale:.6f}" for f in rx["frequencies"])
    # A bare name is left for PATH lookup (the .deb installs /usr/bin/xng); a relative
    # path is resolved against the project root, like the other decoders' binaries.
    binary = str(rx.get("xng_binary") or DEFAULT_BINARY["xng"])
    if os.path.sep in binary and not os.path.isabs(binary):
        binary = str(ROOT / binary)
    sdr = sdr_for(name, rx)
    cmd = [binary, "listen",
           "--sdr", soapy_device(sdr, rx, tee),
           "--mode", name,
           "-r", str(rate),
           "-c", f"{center/1e6:.6f}M",
           "--channels", channels,
           "--demod-effort", str(rx.get("demod_effort", "live")),
           "--udp", f"127.0.0.1:{port}"]
    if sdr == "sdrplay":
        ifgr = sdrplay_ifgr(name, rx)
        if ifgr is not None:
            cmd += ["-g", str(soapy_gain(ifgr))]
        lna_note(name, rx, "xng")
        rsp_filter_note(name, rx, rate, f"A sample_rate of {RSP_WIDE_FILTER_RATE} or more selects "
                        f"the 5 MHz filter (xng's CPU use grows with the rate).")
    elif not gain_is_auto(rx):
        cmd += ["-g", str(rx["gain"])]
    if rx.get("log_file"):
        cmd += ["--jsonl", str(rx["log_file"])]
    if rx.get("ppm"):
        shift = center - center / scale  # centre is already scaled at this point
        print(f"note: receivers.{name}.ppm={rx['ppm']} applied by pre-compensating the tuned "
              f"frequencies ({shift:+.0f} Hz at centre); xng has no ppm option.", file=sys.stderr)
    return cmd


def acars_command(rx, cfg):
    cmd = [str(ROOT / rx.get("binary", "third_party/acarsdec/build/acarsdec")),
           "--output", f"json:udp:host=127.0.0.1,port={cfg['acars_udp_port']}"]
    if sys.stdout.isatty():
        cmd += ["--output", "full:file"]
    if rx.get("log_file"):
        cmd += ["--output", f"full:file:path={rx['log_file']}"]
    sdr = sdr_for("acars", rx)
    shared = shared_capture_plan("acars", rx, cfg)
    if sdr == "sdrplay":
        # acarsdec's own --sdrplay input never sets the RSP's IF filter, leaving the API
        # default of 200 kHz: one channel's worth. Its SoapySDR input asks for the channel
        # span, so that is the path to use.
        cmd += ["--soapysdr", soapy_device(sdr, rx, shared and shared[2]), "-p", str(rx.get("ppm", 0))]
        ifgr = sdrplay_ifgr("acars", rx)
        if ifgr is not None:
            cmd += ["-g", str(soapy_gain(ifgr))]
        lna_note("acars", rx, "acarsdec")
        freqs = [float(f) * 1_000_000 for f in rx["frequencies"]]
        rsp_filter_note("acars", rx, max(freqs) - min(freqs) + 2 * 12_000,  # what acarsdec asks for
                        "acarsdec asks for a filter as wide as the channel span, so only a span of "
                        f"{RSP_WIDE_FILTER_RATE/1e6:g} MHz or more (e.g. adding a 136 MHz channel) "
                        "gets the 5 MHz filter.")
    else:
        cmd += ["--rtlsdr", str(rx["device"]), "-p", str(rx.get("ppm", 0))]
        if not gain_is_auto(rx):
            cmd += ["-g", str(rx["gain"])]
    if shared:
        # The capture must also hold the partner's slice, so pin centre and rate rather
        # than letting acarsdec fit its own channels.
        center, rate = shared[:2]
        cmd += ["-c", f"{center/1e6:.6f}", "-m", str(rate // 12_000)]
    elif rx.get("sample_rate"):
        rate = int(rx["sample_rate"])
        if rate % 12_000:
            sys.exit(f"config.json: receivers.acars.sample_rate {rate} is not a multiple of 12000, "
                     f"which acarsdec requires")
        cmd += ["-m", str(rate // 12_000)]
    return cmd + [f"{f:.3f}" for f in rx["frequencies"]]


def dumpvdl2_input_args(name, rx):
    """dumpvdl2's input flags for a receiver's radio: the device, ppm and gain. Shared with
    tools/calibrate_ppm.py, which points dumpvdl2 at the ACARS radio to measure it."""
    sdr = sdr_for(name, rx)
    if sdr == "shared":
        # Samples arrive through the FIFO the host's SoapyTee writes: 16-bit I/Q of the
        # slice centred on this receiver's channels. Gain and ppm belong to the host.
        return ["--iq-file", str(shared_fifo(name)), "--sample-format", "S16_LE",
                "--centerfreq", str(slice_center(rx)), "--oversample", str(SHARED_OVERSAMPLE)]
    if sdr == "sdrplay":
        args = ["--sdrplay3", str(rx["device"]), "--correction", str(rx.get("ppm", 0))]
        ifgr = sdrplay_ifgr(name, rx)
        if ifgr is not None:  # manual gain needs both; either one missing means AGC
            args += ["--ifgr", str(ifgr), "--lna-state", str(int(rx.get("lna_state") or 0))]
        rsp_filter_note(name, rx, None)
        return args
    args = ["--rtlsdr", str(rx["device"]), "--correction", str(rx.get("ppm", 0))]
    if not gain_is_auto(rx):
        args += ["--gain", str(rx["gain"])]
    return args


def vdl2_command(rx, cfg):
    cmd = [str(ROOT / rx.get("binary", "third_party/dumpvdl2/build/src/dumpvdl2")),
           "--output", f"decoded:json:udp:address=127.0.0.1,port={cfg['vdl2_udp_port']}"]
    if sys.stdout.isatty():
        cmd += ["--output", "decoded:text:file:path=-"]
    if rx.get("log_file"):
        cmd += ["--output", f"decoded:text:file:path={rx['log_file']}"]
    cmd += dumpvdl2_input_args("vdl2", rx)
    return cmd + [str(round(f * 1_000_000)) for f in rx["frequencies"]]  # dumpvdl2 wants Hz


NATIVE_COMMANDS = {"acars": acars_command, "vdl2": vdl2_command}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in NATIVE_COMMANDS:
        sys.exit(f"usage: {sys.argv[0]} {'|'.join(NATIVE_COMMANDS)}")
    name = sys.argv[1]
    cfg = load_config()
    rx = cfg.get("receivers", {}).get(name)
    if not rx or not rx.get("enabled", True):
        print(f"{name} receiver is not enabled in config.json; nothing to do.")
        return
    if not rx.get("frequencies"):
        sys.exit(f"config.json: receivers.{name}.frequencies is empty")

    decoder = decoder_for(name, rx)
    if sdr_for(name, rx) == "shared":
        shared_host(name, rx, cfg)
        if decoder != "dumpvdl2":
            sys.exit(f"config.json: a shared receiver runs dumpvdl2 (it reads the slice as an I/Q file); "
                     f"receivers.{name}.decoder is {decoder!r}")
    if sdr_for(name, rx) == "shared" or shared_partner(name, cfg)[0]:
        RUN_DIR.mkdir(exist_ok=True)
        fifo = shared_fifo(name if sdr_for(name, rx) == "shared" else shared_partner(name, cfg)[0])
        if not fifo.exists():
            os.mkfifo(fifo, 0o660)
    cmd = xng_command(name, rx, cfg) if decoder == "xng" else NATIVE_COMMANDS[name](rx, cfg)
    env = dict(os.environ)
    # The distro librtlsdr detaches the DVB kernel driver automatically; some self-built copies don't.
    preload = cfg.get("librtlsdr_preload")
    if preload and Path(preload).exists():
        env["LD_PRELOAD"] = preload
    print(" ".join(cmd), file=sys.stderr)
    os.chdir(ROOT)
    os.execvpe(cmd[0], cmd, env)


if __name__ == "__main__":
    main()
