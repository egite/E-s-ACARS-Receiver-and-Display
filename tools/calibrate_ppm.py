#!/usr/bin/env python3
"""Measure a dongle's tuning error and optionally correct it in config.json.

Only dumpvdl2 reports frequency error. VDL2 is D8PSK, so its demodulator has to
estimate the carrier offset to recover symbols at all, and it reports that as
`freq_skew`. Classic ACARS is MSK over AM, recovered by envelope detection, which
never needs the carrier frequency - so neither acarsdec nor xng can tell you how far
off the dongle is. The ACARS dongle therefore has no continuous ppm monitoring the way
the VDL2 one does (web/monitor.py only collects skew from VDL2 messages).

This script fills that gap the way the README says to do it by hand: it borrows the
dongle for a couple of minutes, points dumpvdl2 at a VDL2 channel, measures, and gives
the dongle back.

    tools/calibrate_ppm.py                 measure the acars dongle, report only
    tools/calibrate_ppm.py --apply         also write config.json and restart it
    tools/calibrate_ppm.py --receiver vdl2 measure the VDL2 dongle instead
    tools/calibrate_ppm.py --secs 180      listen longer (more samples)

The receiver's decoder is stopped for the duration and restarted on exit, including on
Ctrl-C or error. Run it when a short outage on that band is acceptable; VDL2 traffic is
busiest, so 90 s is usually enough to clear the sample threshold.
"""
import argparse
import json
import os
import re
import signal
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
from monitor import PPM_MIN_SAMPLES, PPM_MIN_SNR_DB  # noqa: E402  (same thresholds as the live monitor)

# Fallback when config.json has no VDL2 frequencies: the strongest channel almost everywhere.
DEFAULT_VDL2_FREQ = 136.975


def load_config(path):
    return json.loads(Path(path).read_text())


def collect(binary, device, gain, ppm, freqs_mhz, port, secs, verbose):
    """Run dumpvdl2 on `device` for `secs` and return its freq_skew samples (ppm)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.settimeout(1.0)

    cmd = [str(binary),
           "--output", f"decoded:json:udp:address=127.0.0.1,port={port}",
           "--rtlsdr", str(device), "--correction", str(int(ppm)),
           "--gain", str(gain)] + [str(round(f * 1_000_000)) for f in freqs_mhz]
    if verbose:
        print("  " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)
    samples, frames, weak = [], 0, 0
    deadline = time.time() + secs
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                err = (proc.stderr.read() or b"").decode(errors="replace").strip()
                raise RuntimeError(f"dumpvdl2 exited early: {err[-300:]}")
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                continue
            for line in data.decode(errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    v = (json.loads(line) or {}).get("vdl2") or {}
                except ValueError:
                    continue
                frames += 1
                skew, sig, noise = v.get("freq_skew"), v.get("sig_level"), v.get("noise_level")
                if skew is None or sig is None or noise is None:
                    continue
                # Weak frames give noisy frequency estimates - same gate the live monitor uses.
                if sig - noise >= PPM_MIN_SNR_DB:
                    samples.append(skew)
                else:
                    weak += 1
            left = int(deadline - time.time())
            print(f"\r  listening... {left:>3}s left, {frames} frames, "
                  f"{len(samples)} usable", end="", flush=True)
    finally:
        print()
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        sock.close()
    return samples, frames, weak


def write_ppm(config_path, name, value):
    """Set receivers.<name>.ppm in place, preserving the file's formatting."""
    text = Path(config_path).read_text()
    block = re.search(rf'"{name}"\s*:\s*\{{[^{{}}]*\}}', text)
    if not block or not re.search(r'"ppm"\s*:\s*-?\d+(\.\d+)?', block.group(0)):
        return False
    new_block = re.sub(r'("ppm"\s*:\s*)-?\d+(\.\d+)?', rf"\g<1>{value}", block.group(0), count=1)
    new_text = text[:block.start()] + new_block + text[block.end():]
    json.loads(new_text)  # never write a broken config
    Path(config_path).write_text(new_text)
    return True


def systemctl(action, unit):
    return subprocess.run(["sudo", "-n", "systemctl", action, unit],
                          capture_output=True, text=True).returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--receiver", default="acars", help="receiver to calibrate (default: acars)")
    ap.add_argument("--secs", type=int, default=90, help="how long to listen (default: 90)")
    ap.add_argument("--freq", default=None,
                    help="VDL2 channel(s) to listen on, comma separated "
                         "(default: every frequency from receivers.vdl2)")
    ap.add_argument("--port", type=int, default=5599, help="scratch UDP port (default: 5599)")
    ap.add_argument("--apply", action="store_true",
                    help="write the corrected ppm to config.json and restart the receiver")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rx = (cfg.get("receivers") or {}).get(args.receiver)
    if not rx:
        sys.exit(f"config.json has no receivers.{args.receiver}")

    vdl2 = (cfg.get("receivers") or {}).get("vdl2") or {}
    binary = ROOT / (vdl2.get("binary") or "third_party/dumpvdl2/build/src/dumpvdl2")
    if not os.access(binary, os.X_OK):
        sys.exit(f"dumpvdl2 not found at {binary}. This tool needs it - it is the only decoder "
                 f"here that measures frequency error.")

    # Listen on the whole VDL2 channel set, not one channel: frames are what limit this
    # measurement, and off-peak a single channel can yield almost none. They all fit in
    # one capture (136.650-136.975 is 325 kHz), which is how the VDL2 receiver runs anyway.
    if args.freq:
        freqs = [float(f) for f in str(args.freq).split(",") if f.strip()]
    else:
        freqs = [float(f) for f in (vdl2.get("frequencies") or [DEFAULT_VDL2_FREQ])]
    span = (max(freqs) - min(freqs)) * 1_000_000
    if span > 2_000_000:
        sys.exit(f"channels span {span/1e6:.2f} MHz - too wide for one capture; pass fewer with --freq")
    ppm_now = int(rx.get("ppm") or 0)
    unit = f"acars-decoder@{args.receiver}.service"

    print(f"=== calibrating '{args.receiver}' (dongle {rx.get('device')}, ppm now {ppm_now}) ===")
    print(f"  listening on {len(freqs)} VDL2 channel(s) "
          f"{min(freqs)}-{max(freqs)} MHz for {args.secs}s via dumpvdl2")
    print(f"  stopping {unit} - this band is off the air until it finishes")

    stopped = systemctl("stop", unit)
    if not stopped:
        print(f"  ! could not stop {unit}; it may fight for the dongle", file=sys.stderr)
    try:
        samples, frames, weak = collect(binary, rx.get("device"), rx.get("gain", 40),
                                        ppm_now, freqs, args.port, args.secs, args.verbose)
    finally:
        print(f"  restarting {unit}...")
        ok = systemctl("start", unit)
        print(f"  {unit}: {'active' if ok else 'FAILED - start it manually'}")

    print()
    print(f"  frames seen      {frames}")
    print(f"  usable (SNR>={PPM_MIN_SNR_DB}dB) {len(samples)}   (discarded {weak} weak)")
    if len(samples) < PPM_MIN_SAMPLES:
        print(f"\n  NOT ENOUGH DATA: need {PPM_MIN_SAMPLES} usable frames, got {len(samples)}.")
        rate = frames / max(args.secs, 1) * 3600
        print(f"  Seen rate was ~{rate:.0f} frames/h; at that rate you need roughly "
              f"{PPM_MIN_SAMPLES/max(rate,1e-9)*3600:.0f}s. VDL2 traffic is far higher by day,")
        print("  so re-run in daylight hours, or pass a longer --secs.")
        return 1

    residual = statistics.median(samples)
    spread = statistics.pstdev(samples) if len(samples) > 1 else 0.0
    suggested = round(ppm_now - residual)
    print(f"  residual error   {residual:+.2f} ppm   (sd {spread:.2f}, "
          f"range {min(samples):+.1f} to {max(samples):+.1f})")
    print(f"  suggested ppm    {suggested}   (current {ppm_now})")
    drift = abs(residual)
    tol = ((cfg.get("health") or {}).get("ppm_tolerance") or 3)
    print()
    if drift < tol:
        print(f"  within tolerance ({drift:.2f} < {tol} ppm) - nothing to do.")
        return 0
    print(f"  OUT OF TOLERANCE ({drift:.2f} >= {tol} ppm)")
    if not args.apply:
        print(f"  re-run with --apply to set ppm={suggested} and restart the receiver.")
        return 0
    if not write_ppm(args.config, args.receiver, suggested):
        print(f"  ! could not find receivers.{args.receiver}.ppm in {args.config}", file=sys.stderr)
        return 1
    print(f"  config.json: ppm {ppm_now} -> {suggested}")
    print(f"  restarting {unit} to apply...")
    print(f"  {unit}: {'active' if systemctl('restart', unit) else 'FAILED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
