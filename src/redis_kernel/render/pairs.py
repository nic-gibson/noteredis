"""Field/value replies as two-column tables: ``HGETALL``, ``CONFIG GET``.

The commands here all answer with the same shape -- a RESP3 map, or a RESP2
flat array of alternating field and value -- so they share one renderer.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import table
from ._shapes import as_pairs

__all__ = ["render_pairs"]

#: Header labels per command, because "field" is wrong for CONFIG GET and
#: "parameter" is wrong for HGETALL.
HEADERS = {
    "HGETALL": ("field", "value"),
    "CONFIG GET": ("parameter", "value"),
    "XINFO STREAM": ("property", "value"),
    "CLIENT INFO": ("property", "value"),
    "HRANDFIELD": ("field", "value"),
}


@renderer("HGETALL", "CONFIG GET", "XINFO STREAM")
def render_pairs(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A two-column table, or nothing if the reply is not pair-shaped."""
    pairs = as_pairs(reply)
    if pairs is None:
        return None
    headers = HEADERS.get(_key(args), ("field", "value"))
    return {"text/html": table(headers, pairs)}


def _key(args: list[str]) -> str:
    command = args[0].upper()
    return f"{command} {args[1].upper()}" if len(args) > 1 else command
