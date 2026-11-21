#!/usr/bin/env python3
"""Reference client for the archive query service (web/query_server.py).

Fetches everything the archive holds for one aircraft over a time range and writes it as
JSON lines, the message records only, to stdout or a file. It is also the worked example
for docs/query-protocol.md: standard library only, so it can be copied to the machine that
needs the data.

    python3 query_client.py 192.168.21.130 AB3982 --from 2026-11-01 --to 2026-11-08 -o ab3982.jsonl
    python3 query_client.py 192.168.21.130 AB3982 --from 2026-11-20T12:00Z --kinds acars cpdlc
"""
import argparse
import json
import socket
import sys


def query(host, icao, start=None, end=None, kinds=None, port=8687, timeout=300):
    """Yields (header, messages iterator). Raises RuntimeError on an error record or a
    reply that stops before its end record."""
    request = {"icao": icao}
    if start is not None:
        request["from"] = start
    if end is not None:
        request["to"] = end
    if kinds:
        request["kinds"] = list(kinds)
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(json.dumps(request).encode() + b"\n")
        sock.shutdown(socket.SHUT_WR)  # optional: tells the server nothing more is coming
        with sock.makefile("r", encoding="utf-8") as reply:
            header = json.loads(reply.readline() or "null")
            if not header:
                raise RuntimeError("server closed the connection without replying")
            if header["record"] == "error":
                raise RuntimeError(f"{header['error']}: {header['message']}")
            if header.get("protocol") != 1:
                raise RuntimeError(f"unsupported protocol version {header.get('protocol')}")
            yield header
            count = 0
            for line in reply:
                rec = json.loads(line)
                if rec["record"] == "message":
                    count += 1
                    yield rec
                elif rec["record"] == "end":
                    if rec["count"] != count:
                        raise RuntimeError(f"end record says {rec['count']} messages, received {count}")
                    return
                elif rec["record"] == "error":
                    raise RuntimeError(f"{rec['error']}: {rec['message']}")
                # Any other record type is from a newer server: skip it.
            raise RuntimeError(f"reply cut short after {count} messages (no end record)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("host")
    ap.add_argument("icao")
    ap.add_argument("--from", dest="start", help="ISO 8601 or Unix seconds; default: start of the archive")
    ap.add_argument("--to", dest="end", help="ISO 8601 or Unix seconds, exclusive; default: now")
    ap.add_argument("--kinds", nargs="+", help="only these kinds (acars cpdlc atn link partial xid)")
    ap.add_argument("--port", type=int, default=8687)
    ap.add_argument("-o", "--output", help="write here instead of stdout")
    args = ap.parse_args()

    out = open(args.output, "w") if args.output else sys.stdout
    replies = query(args.host, args.icao, args.start, args.end, args.kinds, args.port)
    header = next(replies)
    aircraft = header.get("aircraft") or {}
    print(f"{header['icao']} {aircraft.get('registration') or ''} {aircraft.get('model') or ''} "
          f"{header['from'] or 'archive start'} .. {header['to']}", file=sys.stderr)
    n = 0
    for msg in replies:
        out.write(json.dumps(msg, ensure_ascii=False) + "\n")
        n += 1
    print(f"{n} messages", file=sys.stderr)


if __name__ == "__main__":
    main()
