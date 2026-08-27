"""``XINFO GROUPS`` and ``XINFO CONSUMERS`` as tables.

Each answers with an array of one map per group or consumer -- a RESP3
map, or a RESP2 flat array of alternating field and value -- so the table is
the same union-of-fields layout ``search.py`` uses for ``FT.AGGREGATE``,
just without a wrapping ``results`` map to unpack first (see
``xinfoCommand`` in ``src/t_stream.c``).
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import rows_table
from ._shapes import rows_of_pairs

__all__ = ["render_xinfo_rows"]

MAX_COLUMNS = 30


@renderer("XINFO GROUPS", "XINFO CONSUMERS")
def render_xinfo_rows(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table of one row per group/consumer, or nothing if the shape is wrong."""
    del args
    rows = rows_of_pairs(reply)
    if not rows:
        return None
    html = rows_table(rows, MAX_COLUMNS)
    return {"text/html": html} if html else None
