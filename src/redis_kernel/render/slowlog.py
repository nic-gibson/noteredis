"""``SLOWLOG GET`` as a table.

Each entry is a fixed-shape array -- id, timestamp, duration, the command and
its arguments, client address, client name (``slowlogCommand`` in
``src/slowlog.c``) -- the same on both protocols; ``SLOWLOG`` predates RESP3
and was never given a map form. A newer field some servers append after
client name is read if present and ignored otherwise, so older and newer
servers both render.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import cell_text, table
from ._shapes import as_items

__all__ = ["render_slowlog"]


@renderer("SLOWLOG GET")
def render_slowlog(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table of one row per entry, or nothing if the shape is unexpected."""
    del args
    entries = as_items(reply)
    if not entries:
        return None

    rows = []
    for entry in entries:
        fields = as_items(entry)
        if fields is None or len(fields) < 6:
            return None
        command_args = as_items(fields[3])
        if command_args is None:
            return None
        command = " ".join(cell_text(token) for token in command_args)
        rows.append([fields[0], fields[1], fields[2], command, fields[4], fields[5]])

    headers = ["id", "timestamp", "duration (µs)", "command", "client addr", "client name"]
    return {"text/html": table(headers, rows)}
