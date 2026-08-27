"""``CLIENT LIST`` and ``CLIENT INFO`` as tables.

Both answer with the exact ``key=value`` line ``catClientInfoString`` builds
in ``src/networking.c`` -- one line for INFO, one per client for LIST -- sent
as a RESP3 verbatim string (a plain bulk string under RESP2). Client names
cannot contain whitespace (the server rejects ``CLIENT SETNAME`` otherwise),
so splitting each line on whitespace is always safe.

``CLIENT INFO`` is one client's properties, so it reads best as a tall
property/value table, the same layout ``pairs.py`` uses for ``HGETALL``.
``CLIENT LIST`` is many clients sharing the same properties, so it reads best
as a wide table, one row per client -- the ``XINFO GROUPS`` layout.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import rows_table, table
from ._shapes import as_text

__all__ = ["render_client_info", "render_client_list"]

MAX_COLUMNS = 30


@renderer("CLIENT INFO")
def render_client_info(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A property/value table for the one line CLIENT INFO answers with."""
    del args
    lines = _parse(reply)
    if lines is None or len(lines) != 1:
        return None
    return {"text/html": table(["property", "value"], lines[0])}


@renderer("CLIENT LIST")
def render_client_list(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table of one row per client, or nothing if the shape is unexpected."""
    del args
    lines = _parse(reply)
    if not lines:
        return None
    html = rows_table(lines, MAX_COLUMNS)
    return {"text/html": html} if html else None


def _parse(reply: Any) -> list[list[tuple[str, str]]] | None:
    """Each non-empty line as a list of ``(key, value)`` pairs, or ``None``."""
    text = as_text(reply)
    if text is None:
        return None
    lines: list[list[tuple[str, str]]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        pairs = []
        for token in line.split():
            key, sep, value = token.partition("=")
            if not sep:
                return None
            pairs.append((key, value))
        lines.append(pairs)
    return lines or None
