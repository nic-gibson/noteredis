"""``TS.RANGE``/``TS.REVRANGE`` as tables.

Both answer with an array of ``[timestamp, value, ...]`` samples -- the same
shape on both protocols, confirmed against redis-py's own ``parse_range``/
``parse_range_unified`` (``redis/commands/timeseries/utils.py``): a sample
normally carries one value, or more than one when queried with multiple
aggregators.

``TS.MRANGE``/``TS.MREVRANGE`` are not handled here yet -- they wrap this
same per-series shape in a per-key map (RESP3) or a list of ``[key, labels,
samples]`` triples (RESP2), which needs its own renderer.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import table
from ._shapes import as_items

__all__ = ["render_range"]


@renderer("TS.RANGE", "TS.REVRANGE")
def render_range(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table of one row per sample, or nothing if the shape is unexpected."""
    del args
    samples = as_items(reply)
    if not samples:
        return None

    first = as_items(samples[0])
    if first is None or len(first) < 2:
        return None
    width = len(first)

    rows = []
    for sample in samples:
        row = as_items(sample)
        if row is None or len(row) != width:
            return None
        rows.append(row)

    headers = ["timestamp"] + (["value"] if width == 2 else [f"value {i}" for i in range(1, width)])
    return {"text/html": table(headers, rows)}
