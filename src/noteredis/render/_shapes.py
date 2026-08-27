"""Reading reply shapes without caring which protocol delivered them.

The same command answers differently under RESP2 and RESP3 -- ``HGETALL`` is a
flat array in one and a map in the other -- and a renderer that only understood
one of them would silently stop working when someone typed ``%protocol 3``.
Every renderer goes through these two functions instead.
"""

from __future__ import annotations

from typing import Any

from ..formatter import RedisMap, RedisSet, Verbatim

__all__ = ["as_items", "as_pairs", "as_score_pairs", "as_text", "rows_of_pairs"]


def as_pairs(reply: Any) -> list[tuple[Any, Any]] | None:
    """Read a reply as key/value pairs, or ``None`` if it is not pair-shaped.

    Covers a RESP3 map (which keeps its own ``pairs``, so duplicate keys
    survive) and a RESP2 flat array of alternating keys and values. An odd
    number of elements is not pair-shaped, and neither is an empty reply --
    there is nothing to draw a table of.
    """
    pairs = getattr(reply, "pairs", None)
    if pairs is not None:
        return list(pairs) or None
    if isinstance(reply, RedisMap):  # pragma: no cover - always carries pairs
        return list(reply.items()) or None
    if isinstance(reply, (list, tuple)) and not isinstance(reply, RedisSet):
        if not reply or len(reply) % 2:
            return None
        return [(reply[i], reply[i + 1]) for i in range(0, len(reply), 2)]
    return None


def as_items(reply: Any) -> list[Any] | None:
    """Read a reply as a non-empty sequence, or ``None``."""
    if isinstance(reply, (list, tuple)):
        return list(reply) or None
    return None


def as_score_pairs(reply: Any) -> list[tuple[Any, Any]] | None:
    """Read a member+score (or field+value) reply, RESP2 or RESP3, or ``None``.

    ``ZRANGE ... WITHSCORES``, ``ZPOPMIN``/``ZPOPMAX``, and
    ``HRANDFIELD ... WITHVALUES`` answer with a flat array under RESP2
    (member, score, member, score, ...) and, when there is more than one
    pair, an array of two-element arrays under RESP3. Unlike ``HGETALL``,
    RESP3 does not upgrade this to a map: a sorted set's members are ordered
    and can repeat in ways a map's keys cannot.
    """
    items = as_items(reply)
    if not items:
        return None
    if isinstance(items[0], (list, tuple)):
        pairs = []
        for item in items:
            pair = as_items(item)
            if pair is None or len(pair) != 2:
                return None
            pairs.append((pair[0], pair[1]))
        return pairs
    if len(items) % 2:
        return None
    return [(items[i], items[i + 1]) for i in range(0, len(items), 2)]


def rows_of_pairs(reply: Any) -> list[list[tuple[Any, Any]]] | None:
    """Read a reply as a list of pairs-shaped entries, or ``None``.

    ``XINFO GROUPS``/``XINFO CONSUMERS`` and similar answer with one entry
    per item, each itself pairs-shaped (a RESP2 flat array or a RESP3 map) --
    unlike ``FT.AGGREGATE``'s ``results`` array, there is no wrapping map to
    unpack first.
    """
    items = as_items(reply)
    if items is None:
        return None
    rows = []
    for entry in items:
        pairs = as_pairs(entry)
        if pairs is None:
            return None
        rows.append(pairs)
    return rows


def as_text(reply: Any) -> str | None:
    """Read a bulk or verbatim reply as text, or ``None``."""
    if isinstance(reply, Verbatim):
        return bytes(reply).decode("utf-8", "replace")
    if isinstance(reply, (bytes, bytearray)):
        return bytes(reply).decode("utf-8", "replace")
    if isinstance(reply, str):
        return reply
    return None
