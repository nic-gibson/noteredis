"""The formatter, checked against real ``redis-cli`` output.

Expectations here are not hand-written. ``tests/captured/redis_cli_replies.json``
pairs a RESP payload with the exact text ``redis-cli`` printed for it, captured
by ``tests/capture/generate.py`` from the real binary. Each test walks the route
the kernel walks -- wire bytes, through the project's RESP parser, through the
formatter -- and demands the result match character for character.

Regenerate the corpus after adding a payload to ``tests/capture/payloads.py``::

    python tests/capture/generate.py
"""

from __future__ import annotations

import base64
import json
import pathlib
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from redis_kernel.formatter import format_error, format_reply
from redis_kernel.resp import parser_for_protocol

CORPUS_PATH = pathlib.Path(__file__).parent / "captured" / "redis_cli_replies.json"

if not CORPUS_PATH.exists():  # pragma: no cover - a fresh checkout has the corpus
    pytest.skip(
        f"{CORPUS_PATH} is missing; run: python tests/capture/generate.py",
        allow_module_level=True,
    )

CORPUS = json.loads(CORPUS_PATH.read_text())
ENTRIES: list[dict[str, Any]] = CORPUS["entries"]
UNSUPPORTED = {entry["name"]: entry for entry in CORPUS["unsupported_by_redis_cli"]}


# --------------------------------------------------------------------------- #
# Driving the parser without a socket
# --------------------------------------------------------------------------- #


class _PayloadBuffer:
    """Stands in for redis-py's ``SocketBuffer`` over a fixed payload.

    Only the two methods the parsers call, with redis-py's own semantics:
    ``readline`` returns a line with the CRLF stripped, and ``read(n)`` returns
    n bytes and swallows the CRLF after them. An exhausted buffer returns
    ``b""``, which is how the parsers learn the connection has closed.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def readline(self, timeout: Any = None) -> bytes:
        if self._pos >= len(self._data):
            return b""
        end = self._data.find(b"\r\n", self._pos)
        if end == -1:  # truncated payload: hand back what there is
            line, self._pos = self._data[self._pos :], len(self._data)
            return line
        line = self._data[self._pos : end]
        self._pos = end + 2
        return line

    def read(self, length: int, timeout: Any = None) -> bytes:
        chunk = self._data[self._pos : self._pos + length]
        self._pos += length + 2  # the payload plus its CRLF
        return chunk


def parse(wire: bytes, protocol: int) -> Any:
    """Decode one reply exactly as the kernel's connection would."""
    parser = parser_for_protocol(protocol)(socket_read_size=max(len(wire), 1))
    parser._buffer = _PayloadBuffer(wire)
    return parser._read_response()


def render(reply: Any) -> str:
    """Render a reply the way ``do_execute`` does.

    redis-py raises error replies rather than returning them, so the kernel
    puts those through ``format_error`` and everything else through
    ``format_reply``. The same split applies here, or the error expectations
    would be testing a path the kernel never takes.
    """
    if isinstance(reply, BaseException):
        return format_error(reply)
    return format_reply(reply)


# --------------------------------------------------------------------------- #
# The corpus
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("entry", ENTRIES, ids=[entry["name"] for entry in ENTRIES])
def test_output_matches_redis_cli(entry: dict[str, Any]) -> None:
    reply = parse(base64.b64decode(entry["wire"]), entry["protocol"])
    assert render(reply) == entry["stdout"]


def test_the_corpus_is_not_empty() -> None:
    """A corpus that failed to generate would otherwise pass everything."""
    assert len(ENTRIES) > 100


def test_both_protocols_are_covered() -> None:
    assert {entry["protocol"] for entry in ENTRIES} == {2, 3}


@pytest.mark.parametrize(
    "shape",
    [
        "status",  # bare OK, not "OK"
        "integer",  # (integer) 1
        "bulk",  # always double-quoted
        "escape",  # C-style escapes for non-printables
        "nil",  # (nil)
        "array",  # 1) 2) index prefixes, right-aligned, nested
        "bool",  # RESP3 (true)/(false)
        "double",  # RESP3 (double) 1.5, and inf/nan
        "verbatim",  # RESP3 =, printed raw
        "map",  # RESP3 1# "k" => "v", (empty hash)
        "set",  # RESP3 1~
        "error",  # (error) ...
    ],
)
def test_every_documented_rule_has_expectations(shape: str) -> None:
    """Guards the corpus itself: a rule with no payload is a silent gap."""
    assert any(entry["name"].startswith(shape) for entry in ENTRIES)


# --------------------------------------------------------------------------- #
# Documented divergences from redis-cli
# --------------------------------------------------------------------------- #


def test_blob_errors_are_rendered_though_redis_cli_cannot_parse_them() -> None:
    """RESP3 blob errors (``!``) make redis-cli 8.8 give up on the connection.

    Captured as a known gap rather than an expectation. A notebook cannot
    abort, so the kernel renders one like any other error.
    """
    entry = UNSUPPORTED["blob_error"]
    assert "Protocol error" in entry["reason"]
    reply = parse(base64.b64decode(entry["wire"]), entry["protocol"])
    assert render(reply) == "(error) ERR this is the error\n"


def test_big_numbers_print_bare() -> None:
    """redis-cli has no ``REDIS_REPLY_BIGNUM`` case: it exits(1) instead.

    There is nothing to copy, so there is no payload for this in the corpus
    either. The digits go out bare, which is what ``formatter.py`` documents.
    """
    digits = b"3492890328409238509324850943850943825024385"
    assert render(parse(b"(" + digits + b"\r\n", 3)) == digits.decode() + "\n"


# --------------------------------------------------------------------------- #
# The harness itself
# --------------------------------------------------------------------------- #


def test_a_closed_connection_is_reported_not_hung() -> None:
    with pytest.raises(RedisConnectionError):
        parse(b"", 2)


@pytest.mark.parametrize("protocol", [2, 3])
def test_the_buffer_mirrors_redis_pys_framing(protocol: int) -> None:
    """If the stand-in buffer framed replies wrongly, every test above would lie."""
    assert render(parse(b"$5\r\nhello\r\n", protocol)) == '"hello"\n'
    assert render(parse(b"*2\r\n$1\r\na\r\n:2\r\n", protocol)) == '1) "a"\n2) (integer) 2\n'
