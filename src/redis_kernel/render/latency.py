"""``LATENCY HISTORY`` and ``LATENCY LATEST`` as tables.

Both answer with a plain array regardless of protocol -- ``LATENCY`` predates
RESP3 and was never given a map form (``latencyCommandReplyWithSamples``/
``...WithLatestEvents`` in ``src/latency.c``): HISTORY is one
``[timestamp, latency_ms]`` pair per sample, LATEST is one
``[event, timestamp, latest_ms, max_ms]`` row per event type.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import table
from ._shapes import as_items

__all__ = ["render_latency_history", "render_latency_latest"]


@renderer("LATENCY HISTORY")
def render_latency_history(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table of one row per sample, or nothing if the shape is unexpected."""
    del args
    rows = _fixed_width_rows(reply, 2)
    if rows is None:
        return None
    return {"text/html": table(["timestamp", "latency (ms)"], rows)}


@renderer("LATENCY LATEST")
def render_latency_latest(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table of one row per event, or nothing if the shape is unexpected."""
    del args
    rows = _fixed_width_rows(reply, 4)
    if rows is None:
        return None
    return {"text/html": table(["event", "timestamp", "latest (ms)", "max (ms)"], rows)}


def _fixed_width_rows(reply: Any, width: int) -> list[list[Any]] | None:
    entries = as_items(reply)
    if not entries:
        return None
    rows = []
    for entry in entries:
        row = as_items(entry)
        if row is None or len(row) != width:
            return None
        rows.append(row)
    return rows
