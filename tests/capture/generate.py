"""Capture redis-cli's exact rendering of every payload in ``payloads.py``.

Serves each payload from a fake RESP server, runs the real ``redis-cli``
against it, and writes ``tests/captured/redis_cli_replies.json``. Run this to
regenerate the corpus after adding a payload::

    python tests/capture/generate.py

Requires the ``redis-cli`` binary but no Redis server -- the fake server is
enough, which is also how RESP3 shapes get captured on servers where
``DEBUG PROTOCOL`` is disabled.

``--no-raw`` is passed because redis-cli switches to raw output when stdout is
not a tty, and the format we are reproducing is the interactive one.
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from payloads import PAYLOADS  # noqa: E402

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "captured" / "redis_cli_replies.json"

HELLO_RESP3 = (
    b"%7\r\n"
    b"$6\r\nserver\r\n$5\r\nredis\r\n"
    b"$7\r\nversion\r\n$5\r\n8.6.2\r\n"
    b"$5\r\nproto\r\n:3\r\n"
    b"$2\r\nid\r\n:1\r\n"
    b"$4\r\nmode\r\n$10\r\nstandalone\r\n"
    b"$4\r\nrole\r\n$6\r\nmaster\r\n"
    b"$7\r\nmodules\r\n*0\r\n"
)

HELLO_RESP2 = (
    b"*14\r\n"
    b"$6\r\nserver\r\n$5\r\nredis\r\n"
    b"$7\r\nversion\r\n$5\r\n8.6.2\r\n"
    b"$5\r\nproto\r\n:2\r\n"
    b"$2\r\nid\r\n:1\r\n"
    b"$4\r\nmode\r\n$10\r\nstandalone\r\n"
    b"$4\r\nrole\r\n$6\r\nmaster\r\n"
    b"$7\r\nmodules\r\n*0\r\n"
)


def read_request(stream) -> list[bytes] | None:
    """Read one inline or multibulk request."""
    line = stream.readline()
    if not line:
        return None
    if not line.startswith(b"*"):
        return line.strip().split()
    args = []
    for _ in range(int(line[1:])):
        header = stream.readline()
        if not header.startswith(b"$"):
            return args
        args.append(stream.read(int(header[1:])))
        stream.read(2)  # trailing CRLF
    return args


def serve_once(sock: socket.socket, payload: bytes) -> None:
    """Answer the handshake, then send ``payload`` for the first real command."""
    conn, _ = sock.accept()
    with conn, conn.makefile("rb") as stream:
        while True:
            args = read_request(stream)
            if not args:
                return
            name = args[0].upper()
            if name == b"HELLO":
                wants3 = len(args) > 1 and args[1] == b"3"
                conn.sendall(HELLO_RESP3 if wants3 else HELLO_RESP2)
            elif name in (b"CLIENT", b"COMMAND", b"AUTH", b"SELECT"):
                conn.sendall(b"+OK\r\n")
            else:
                conn.sendall(payload)
                return


def capture(name: str, protocol: int, payload: bytes) -> dict[str, object]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    thread = threading.Thread(target=serve_once, args=(sock, payload), daemon=True)
    thread.start()

    argv = ["redis-cli", "--no-raw", "-h", "127.0.0.1", "-p", str(port)]
    if protocol == 3:
        argv.append("-3")
    argv.append("CAPTURE")

    entry: dict[str, object] = {
        "name": name,
        "protocol": protocol,
        "wire": base64.b64encode(payload).decode("ascii"),
    }
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=10)
        entry["stdout"] = proc.stdout.decode("utf-8", "surrogateescape")
        entry["stderr"] = proc.stderr.decode("utf-8", "surrogateescape")
    except subprocess.TimeoutExpired:
        entry["stdout"] = ""
        entry["stderr"] = "TIMEOUT"
    finally:
        thread.join(timeout=2)
        sock.close()
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="capture just the payloads containing this substring")
    args = parser.parse_args()

    if shutil.which("redis-cli") is None:
        print("redis-cli not found on PATH; cannot regenerate the corpus", file=sys.stderr)
        return 2

    version = subprocess.run(
        ["redis-cli", "--version"], capture_output=True, text=True
    ).stdout.strip()

    entries = []
    unsupported = []
    for name, (protocol, payload) in PAYLOADS.items():
        if args.only and args.only not in name:
            continue
        entry = capture(name, protocol, payload)
        if entry["stderr"]:
            # redis-cli itself cannot render this shape; record it as a known
            # gap rather than as an expectation.
            unsupported.append({**entry, "reason": entry["stderr"].strip()})
            print(f"  skip {name}: {str(entry['stderr']).strip()}", file=sys.stderr)
            continue
        entries.append(entry)
        print(f"  ok   {name}")

    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    CORPUS.write_text(
        json.dumps(
            {
                "_comment": (
                    "Generated by tests/capture/generate.py -- do not edit by hand. "
                    "Each entry is a RESP payload and the exact text redis-cli "
                    "printed for it."
                ),
                "redis_cli_version": version,
                "entries": entries,
                "unsupported_by_redis_cli": unsupported,
            },
            indent=2,
            sort_keys=False,
        )
        + "\n"
    )
    print(f"\nwrote {len(entries)} expectations to {CORPUS}")
    if unsupported:
        print(f"{len(unsupported)} payload(s) redis-cli could not render")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
