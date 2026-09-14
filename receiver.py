#!/usr/bin/env python3
"""Start the ACARS or VDL2 decoder using the settings in config.json.

    python3 receiver.py acars
    python3 receiver.py vdl2

Decoded messages go to the web app as JSON over UDP. When run in a terminal the decoded
text is also printed, and receivers.<name>.log_file (if set) appends it to a file.
Set ACARS_CONFIG to use a config file other than ./config.json.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_config():
    return json.loads(Path(os.environ.get("ACARS_CONFIG", ROOT / "config.json")).read_text())


def gain_is_auto(rx):
    return rx.get("gain") in (None, "auto")


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


COMMANDS = {"acars": acars_command, "vdl2": vdl2_command}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"usage: {sys.argv[0]} {'|'.join(COMMANDS)}")
    name = sys.argv[1]
    cfg = load_config()
    rx = cfg.get("receivers", {}).get(name)
    if not rx or not rx.get("enabled", True):
        print(f"{name} receiver is not enabled in config.json; nothing to do.")
        return
    if not rx.get("frequencies"):
        sys.exit(f"config.json: receivers.{name}.frequencies is empty")

    cmd = COMMANDS[name](rx, cfg)
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
