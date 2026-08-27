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
    "MEMORY STATS": ("metric", "value"),
    "BF.INFO": ("metric", "value"),
    "CF.INFO": ("metric", "value"),
}


@renderer("HGETALL", "CONFIG GET", "XINFO STREAM", "MEMORY STATS", "BF.INFO", "CF.INFO")
def render_pairs(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A two-column table, or nothing if the reply is not pair-shaped."""
    pairs = as_pairs(reply)
    if pairs is None:
        return None
    headers = HEADERS.get(_key(args), ("field", "value"))
    return {"text/html": table(headers, pairs)}


def _key(args: list[str]) -> str:
    """The ``HEADERS`` key for ``args``: a subcommand form if one is labelled,
    the bare command otherwise.

    ``CONFIG GET``/``MEMORY STATS`` have a real subcommand as their second
    token; ``BF.INFO``/``CF.INFO`` do not -- their second token is a key
    name, arbitrary user data that must never end up as a lookup key.
    """
    command = args[0].upper()
    if len(args) > 1:
        candidate = f"{command} {args[1].upper()}"
        if candidate in HEADERS:
            return candidate
    return command
