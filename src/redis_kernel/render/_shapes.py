"""Reading reply shapes without caring which protocol delivered them.

The same command answers differently under RESP2 and RESP3 -- ``HGETALL`` is a
flat array in one and a map in the other -- and a renderer that only understood
one of them would silently stop working when someone typed ``%protocol 3``.
Every renderer goes through these two functions instead.
"""

from __future__ import annotations

from typing import Any

from ..formatter import RedisMap, RedisSet, Verbatim

__all__ = ["as_items", "as_pairs", "as_text"]


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


def as_text(reply: Any) -> str | None:
    """Read a bulk or verbatim reply as text, or ``None``."""
    if isinstance(reply, Verbatim):
        return bytes(reply).decode("utf-8", "replace")
    if isinstance(reply, (bytes, bytearray)):
        return bytes(reply).decode("utf-8", "replace")
    if isinstance(reply, str):
        return reply
    return None
