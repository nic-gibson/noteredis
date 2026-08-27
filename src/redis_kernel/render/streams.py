"""Stream ranges as tables: ``XRANGE``, ``XREVRANGE``.

A stream range is a list of ``[id, [field, value, ...]]`` entries. Entries need
not share a field set, so the columns are the union of the fields seen, in the
order they first appear, and an entry missing one gets a blank cell. That is
the view that makes a stream readable; the plain text keeps the exact reply.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import cell_text, table
from ._shapes import as_items, as_pairs

__all__ = ["render_stream_range"]

#: Beyond this many columns the table stops being easier to read than the text.
MAX_COLUMNS = 30


@renderer("XRANGE", "XREVRANGE")
def render_stream_range(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table of one row per entry, or nothing if the shape is not a range."""
    del args
    entries = as_items(reply)
    if entries is None:
        return None

    parsed: list[tuple[str, dict[str, Any]]] = []
    fields: list[str] = []
    for entry in entries:
        pair = as_items(entry)
        if pair is None or len(pair) != 2:
            return None  # not a stream range after all; leave it to the text
        pairs = as_pairs(pair[1])
        if pairs is None:
            return None
        row = {}
        for name, value in pairs:
            label = cell_text(name)
            if label not in fields:
                if len(fields) >= MAX_COLUMNS:
                    return None
                fields.append(label)
            row[label] = value
        parsed.append((cell_text(pair[0]), row))

    if not fields:
        return None
    rows = [[entry_id, *(row.get(field, "") for field in fields)] for entry_id, row in parsed]
    return {"text/html": table(["id", *fields], rows)}
