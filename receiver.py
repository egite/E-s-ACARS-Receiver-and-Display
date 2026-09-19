#!/usr/bin/env python3
"""Start the ACARS or VDL2 decoder using the settings in config.json.

    python3 receiver.py acars
    python3 receiver.py vdl2

Each receiver picks its decoder with receivers.<name>.decoder:

    acars:  "acarsdec" (default) or "xng"
    vdl2:   "dumpvdl2" (default) or "xng"

xng (airframesio/xng) is a single permissively-licensed binary that covers both modes.
It is selected per receiver, so ACARS can run on xng while VDL2 stays on dumpvdl2.

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
    rate = max(rate, 960_000)  # RTL-SDR has no usable sample rates between 300 kHz and 900 kHz
    if rate > 2_560_000:
        sys.exit(f"config.json: receivers.{name}.frequencies span {span/1e6:.3f} MHz, which needs "
                 f"{rate/1e6:.3f} MS/s - beyond what an RTL-SDR handles without dropping samples. "
                 f"Use fewer channels or set sample_rate explicitly.")
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
    center, rate = xng_capture_plan(name, rx)
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
    cmd = [binary, "listen",
           "--sdr", f"driver=rtlsdr,serial={rx['device']}",
           "--mode", name,
           "-r", str(rate),
           "-c", f"{center/1e6:.6f}M",
           "--channels", channels,
           "--demod-effort", str(rx.get("demod_effort", "live")),
           "--udp", f"127.0.0.1:{port}"]
    if not gain_is_auto(rx):
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
    cmd += ["--rtlsdr", str(rx["device"]), "-p", str(rx.get("ppm", 0))]
    if not gain_is_auto(rx):
        cmd += ["-g", str(rx["gain"])]
    return cmd + [f"{f:.3f}" for f in rx["frequencies"]]


def vdl2_command(rx, cfg):
    cmd = [str(ROOT / rx.get("binary", "third_party/dumpvdl2/build/src/dumpvdl2")),
           "--output", f"decoded:json:udp:address=127.0.0.1,port={cfg['vdl2_udp_port']}"]
    if sys.stdout.isatty():
        cmd += ["--output", "decoded:text:file:path=-"]
    if rx.get("log_file"):
        cmd += ["--output", f"decoded:text:file:path={rx['log_file']}"]
    cmd += ["--rtlsdr", str(rx["device"]), "--correction", str(rx.get("ppm", 0))]
    if not gain_is_auto(rx):
        cmd += ["--gain", str(rx["gain"])]
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
