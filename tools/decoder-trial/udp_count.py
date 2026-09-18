#!/usr/bin/env python3
"""Count datagrams/messages xng sends over UDP, so we can tell whether its UDP
output includes CRC-bad frames (the JSONL records crc_ok; this side does not)."""
import json, socket, sys, time
from pathlib import Path

port = int(sys.argv[1]); out = Path(sys.argv[2])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port)); s.settimeout(5)
dgrams = msgs = bad = 0
last = 0.0
while True:
    try:
        data, _ = s.recvfrom(65535)
        dgrams += 1
        for line in data.decode(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            msgs += 1
            try:            # if a crc flag survives into the UDP form, note it
                m = json.loads(line)
                d = m.get("decode") or {}
                if d.get("crc_ok") is False:
                    bad += 1
            except json.JSONDecodeError:
                pass
    except socket.timeout:
        pass
    now = time.time()
    if now - last > 5:
        out.write_text(f"{dgrams} {msgs} {bad}\n")
        last = now
