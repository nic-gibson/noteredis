"""Render RESP replies exactly the way ``redis-cli`` renders them.

This is a reimplementation of ``cliFormatReplyTTY`` from
``redis/src/redis-cli.c``. It must stay importable without ``ipykernel`` (and
without ``redis``) so the same rendering can back a ``%%redis`` cell magic later.

Every rule here is pinned by a test whose expectation was captured from a real
``redis-cli`` run rather than written by hand. See ``tests/test_formatter.py``.

Marker types
------------
Plain Python values cannot express every RESP shape: a RESP3 boolean is an
``int``, ``+OK`` and ``$2 OK`` are both ``bytes``, ``,3`` loses its exact
digits as a ``float``, and a big number is indistinguishable from an integer.
The marker types below carry that missing information, and each one *subclasses
the natural Python type* -- ``Status`` is ``bytes``, ``Double`` is ``float`` --
so redis-py's own handshake and cluster code keeps working unchanged while the
formatter can still tell the shapes apart. ``resp.py`` produces them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "RAW_ERROR_ATTR",
    "BigNumber",
    "Double",
    "Error",
    "Push",
    "RedisMap",
    "RedisSet",
    "Status",
    "Verbatim",
    "error_text",
    "format_error",
    "format_reply",
    "quote_bytes",
]

#: Attribute ``resp.py`` sets on redis-py exceptions to preserve the full
#: on-the-wire error text, including the error code redis-py strips off.
RAW_ERROR_ATTR = "resp_raw_error"


# --------------------------------------------------------------------------- #
# Marker types
# --------------------------------------------------------------------------- #


class Status(bytes):
    """A RESP simple string (``+OK``). Printed bare, unlike a bulk string."""

    __slots__ = ()


class Verbatim(bytes):
    """A RESP3 verbatim string (``=15\\r\\ntxt:Some string``).

    The value is the payload *without* the three-character format hint, which
    is kept in :attr:`fmt` for rich renderers. redis-cli ignores the hint and
    prints the payload raw -- no quoting, no escaping.
    """

    # No __slots__: a variable-length builtin subtype cannot declare them.
    fmt: str

    def __new__(cls, data: bytes, fmt: str = "txt") -> Verbatim:
        self = super().__new__(cls, data)
        self.fmt = fmt
        return self


class Double(float):
    """A RESP3 double (``,1.5``).

    :attr:`raw` holds the characters the server sent. redis-cli prints them
    through ``%s`` without reformatting, so ``inf``/``-inf``/``nan`` pass
    straight through and ``,3`` prints as ``3`` rather than ``3.0``.
    """

    raw: str

    def __new__(cls, raw: str | bytes) -> Double:
        text = raw.decode("ascii", "replace") if isinstance(raw, bytes) else raw
        self = super().__new__(cls, _double_value(text))
        self.raw = text
        return self


class BigNumber(int):
    """A RESP3 big number (``(3492890328409238509324850943850943825024385``).

    This is the one shape with no redis-cli behaviour to copy: its switch has
    no ``REDIS_REPLY_BIGNUM`` case, so the default branch prints
    ``Unknown reply type: 13`` to stderr and calls ``exit(1)``. A notebook
    cannot abort, so we print the digits bare. A documented divergence.
    """

    # No __slots__: a variable-length builtin subtype cannot declare them.
    raw: str

    def __new__(cls, raw: str | bytes) -> BigNumber:
        text = raw.decode("ascii", "replace") if isinstance(raw, bytes) else raw
        self = super().__new__(cls, int(text))
        self.raw = text
        return self


class RedisSet(list):
    """A RESP3 set (``~``).

    A ``list`` rather than a ``set``: replies can contain unhashable members,
    and redis-cli prints them in the order the server sent them.
    """

    __slots__ = ()


class RedisMap(dict):
    """A RESP3 map (``%``).

    Subclasses ``dict`` so redis-py can keep doing ``reply.get(b"proto")``,
    but rendering walks :attr:`pairs`, which survives duplicate and
    unhashable keys that the dict view would drop.
    """

    __slots__ = ("pairs",)
    pairs: list[tuple[Any, Any]]

    def __new__(cls, pairs: Iterable[tuple[Any, Any]]) -> RedisMap:
        items = list(pairs)
        try:
            self = super().__new__(cls, items)
            dict.__init__(self, items)
        except TypeError:
            # An unhashable key; keep the mapping empty and render from pairs.
            self = super().__new__(cls)
        self.pairs = items
        return self

    def __init__(self, pairs: Iterable[tuple[Any, Any]] = ()) -> None:
        # __new__ has already populated the dict; don't undo it.
        del pairs
        super().__init__(self)


class Push(list):
    """A RESP3 out-of-band push frame (``>3 message ch1 hello``)."""

    __slots__ = ()


class Error(Exception):
    """A RESP error reply, for tests and for non-redis-py callers.

    Real replies arrive as redis-py exceptions carrying
    :data:`RAW_ERROR_ATTR`; both render identically.
    """

    def __init__(self, text: bytes | str) -> None:
        self.text = text.decode("utf-8", "replace") if isinstance(text, bytes) else text
        super().__init__(self.text)


def _double_value(text: str) -> float:
    """Parse a RESP3 double, whose infinities are spelled ``inf``/``-inf``."""
    lowered = text.strip().lower()
    if lowered in ("inf", "+inf", "infinity"):
        return float("inf")
    if lowered in ("-inf", "-infinity"):
        return float("-inf")
    if lowered == "nan":
        return float("nan")
    return float(text)


# --------------------------------------------------------------------------- #
# Bulk string quoting -- sdscatrepr() from sds.c
# --------------------------------------------------------------------------- #

# Single-character escapes, matching sdscatrepr's switch.
_ESCAPES = {
    ord("\\"): b"\\\\",
    ord('"'): b'\\"',
    ord("\n"): b"\\n",
    ord("\r"): b"\\r",
    ord("\t"): b"\\t",
    ord("\a"): b"\\a",
    ord("\b"): b"\\b",
}


def quote_bytes(data: bytes) -> str:
    """Quote and escape ``data`` the way ``sdscatrepr`` does.

    Anything outside printable ASCII becomes ``\\xNN`` in lowercase hex. Note
    that this byte-escapes UTF-8 rather than preserving it -- that is what
    redis-cli does, and why the client decodes nothing before this point.
    """
    out = bytearray(b'"')
    for byte in data:
        escape = _ESCAPES.get(byte)
        if escape is not None:
            out += escape
        elif 0x20 <= byte <= 0x7E:  # C locale isprint()
            out.append(byte)
        else:
            out += b"\\x%02x" % byte
    out += b'"'
    return out.decode("ascii")


# --------------------------------------------------------------------------- #
# Error text
# --------------------------------------------------------------------------- #


def error_text(exc: BaseException) -> str:
    """The on-the-wire error text for ``exc``, error code included.

    redis-py's ``parse_error`` strips the leading code for everything in its
    ``EXCEPTION_CLASSES`` table, so ``ERR value is not an integer`` reaches us
    as just ``value is not an integer``. ``resp.py`` stashes the original.
    """
    if isinstance(exc, Error):
        return exc.text
    raw = getattr(exc, RAW_ERROR_ATTR, None)
    if isinstance(raw, str):
        return raw
    return str(exc)


# --------------------------------------------------------------------------- #
# Reply classification
# --------------------------------------------------------------------------- #

_EMPTY = {
    "array": "(empty array)\n",
    "map": "(empty hash)\n",
    "set": "(empty set)\n",
    "push": "(empty push)\n",
}

_SEPARATOR = {"array": ")", "map": "#", "set": "~", "push": ")"}


def _as_aggregate(reply: Any) -> tuple[str, list[Any]] | None:
    """Return ``(kind, flat_elements)`` if ``reply`` is an aggregate, else None.

    ``flat_elements`` is the RESP element list -- for maps, keys and values
    interleaved, which is how redis-cli walks them.
    """
    # Marker types first: each subclasses the builtin it would otherwise
    # be mistaken for.
    if isinstance(reply, Push):
        return "push", list(reply)
    if isinstance(reply, RedisSet):
        return "set", list(reply)
    if isinstance(reply, RedisMap):
        return "map", [x for pair in reply.pairs for x in pair]
    if isinstance(reply, Mapping):
        return "map", [x for pair in reply.items() for x in pair]
    if isinstance(reply, (set, frozenset)):
        # Order is already lost; sort so output is at least reproducible.
        return "set", sorted(reply, key=_unordered_sort_key)
    if isinstance(reply, (str, bytes, bytearray)):
        return None  # sequences, but scalar replies
    if isinstance(reply, (list, tuple)):
        return "array", list(reply)
    if isinstance(reply, Sequence):
        return "array", list(reply)
    return None


def _unordered_sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, bytes):
        return (0, value)
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (2, value)
    return (3, repr(value))


def _is_multiline_value(reply: Any) -> bool:
    """Does this map value start on the line below its ``=>``?

    Mirrors ``cliIsMultilineValueTTY``: non-empty aggregates do, and so does a
    verbatim string containing a newline. Empty aggregates stay inline as
    ``(empty array)``, and bulk strings never wrap because their newlines are
    escaped.
    """
    aggregate = _as_aggregate(reply)
    if aggregate is not None:
        return bool(aggregate[1])
    if isinstance(reply, Verbatim):
        return b"\n" in reply
    return False


# --------------------------------------------------------------------------- #
# The formatter proper
# --------------------------------------------------------------------------- #


def format_reply(reply: Any) -> str:
    """Render ``reply`` as redis-cli would, including the trailing newline."""
    return _format(reply, "")


def format_error(exc: BaseException) -> str:
    """Render an exception the way redis-cli renders an error reply."""
    return f"(error) {error_text(exc)}\n"


def format_lines(replies: Iterable[Any]) -> str:
    """Render several replies back to back, as a multi-command cell does."""
    return "".join(format_reply(reply) for reply in replies)


def _format(reply: Any, prefix: str) -> str:
    """Render ``reply``, indenting continuation lines by ``prefix``.

    Always ends in a newline, matching the C function; callers strip it when
    they need to (as map keys do).
    """
    aggregate = _as_aggregate(reply)
    if aggregate is not None:
        return _format_aggregate(aggregate[0], aggregate[1], prefix)
    return _format_scalar(reply)


def _format_scalar(reply: Any) -> str:
    if reply is None:
        return "(nil)\n"
    # Marker types before the builtins they subclass.
    if isinstance(reply, Status):
        return bytes(reply).decode("utf-8", "replace") + "\n"
    if isinstance(reply, Verbatim):
        return bytes(reply).decode("utf-8", "replace") + "\n"
    if isinstance(reply, Double):
        return f"(double) {reply.raw}\n"
    if isinstance(reply, BigNumber):
        return f"{reply.raw}\n"  # see BigNumber: redis-cli aborts here
    # bool before int: in Python, True is an int.
    if isinstance(reply, bool):
        return "(true)\n" if reply else "(false)\n"
    if isinstance(reply, int):
        return f"(integer) {reply}\n"
    if isinstance(reply, BaseException):
        # redis-py raises errors at the top level but leaves the exception in
        # place inside an array, so render whatever we were handed.
        return format_error(reply)
    if isinstance(reply, float):
        # Lossy: the server's exact digits are gone. resp.py sends Double.
        return f"(double) {_float_repr(reply)}\n"
    if isinstance(reply, (bytes, bytearray)):
        return quote_bytes(bytes(reply)) + "\n"
    if isinstance(reply, str):
        return quote_bytes(reply.encode("utf-8")) + "\n"
    # Unknown type: show something rather than raising and losing the reply.
    return quote_bytes(repr(reply).encode("utf-8")) + "\n"


def _float_repr(value: float) -> str:
    """Best-effort reconstruction of the server's double formatting."""
    if value != value:  # NaN
        return "nan"
    if value == float("inf"):
        return "inf"
    if value == float("-inf"):
        return "-inf"
    if value.is_integer():
        return str(int(value))
    return repr(value)


def _format_aggregate(kind: str, items: list[Any], prefix: str) -> str:
    if not items:
        return _EMPTY[kind]

    is_map = kind == "map"
    count = len(items) // 2 if is_map else len(items)

    # Width of the largest index, and the indent nested replies inherit.
    idxlen = len(str(count))
    child_prefix = prefix + " " * (idxlen + 2)
    separator = _SEPARATOR[kind]

    out: list[str] = []
    for i in range(0, len(items), 2 if is_map else 1):
        human_idx = (i // 2 if is_map else i) + 1
        # The first element inherits the caller's cursor, which already sits
        # past the parent's index prefix.
        out.append("" if i == 0 else prefix)
        out.append(f"{human_idx:>{idxlen}}{separator} ")

        if not is_map:
            out.append(_format(items[i], child_prefix))
            continue

        # Map entry: "<key> => <value>", with the key's newline stripped.
        out.append(_format(items[i], child_prefix)[:-1])
        out.append(" => ")
        value = items[i + 1]
        if _is_multiline_value(value):
            out.append("\n")
            out.append(child_prefix)
        out.append(_format(value, child_prefix))

    return "".join(out)
