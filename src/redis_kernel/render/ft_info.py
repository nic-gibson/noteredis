"""``FT.INFO`` as tables: index properties, plus one row per field.

The reply is a single RESP3 map (a RESP2 flat array once degraded) --
confirmed against ``IndexInfoCommand`` in ``src/info/info_command.c``. Most
top-level keys are scalar-ish and render as one property/value table, the
same shape ``pairs.py`` uses for ``HGETALL``; a few (``gc_stats``,
``dialect_stats``, ``index_definition``, ``index_options``, ``field
statistics``) are themselves nested structures and fall back to the plain
text redis-cli would have printed for that one cell -- same treatment
``MEMORY STATS`` gives its per-db entries.

``attributes`` -- the field list, and the part people actually read FT.INFO
for -- is pulled into its own table: an array of one map per field, the same
shape ``XINFO GROUPS`` uses.

That field table is RESP3-only, and deliberately so. Under RESP2 a field's
boolean flags (``SORTABLE``, ``NOSTEM``, ...) are appended as bare tokens
with no key of their own -- ``if (has_map) { ReplyKV_Array(reply, "flags")
}`` only wraps them under RESP3 -- so a RESP2 field with an *even* number of
flags (``SORTABLE`` and ``NOSTEM`` together, say) degrades to a flat array
that still has an even length. Pairing it up anyway wouldn't fail loudly; it
would silently mislabel a flag as some other field's value. RESP3 keeps
each field as a real map, which has no such ambiguity, so this only renders
there -- the property table above still shows on both protocols.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import rows_table, table
from ._shapes import as_items, as_pairs

__all__ = ["render_ft_info"]

MAX_COLUMNS = 30
_ATTRIBUTES_KEY = b"attributes"


@renderer("FT.INFO")
def render_ft_info(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A property/value table, plus a field table when RESP3 allows it."""
    del args
    pairs = as_pairs(reply)
    if pairs is None:
        return None

    properties = [(name, value) for name, value in pairs if name != _ATTRIBUTES_KEY]
    if not properties:
        return None
    html = [table(["property", "value"], properties)]

    attributes = next((value for name, value in pairs if name == _ATTRIBUTES_KEY), None)
    rows = _attribute_rows(attributes) if attributes is not None else None
    if rows:
        fields_html = rows_table(rows, MAX_COLUMNS)
        if fields_html:
            html.append(fields_html)

    return {"text/html": "".join(html)}


def _attribute_rows(value: Any) -> list[list[tuple[Any, Any]]] | None:
    """Field entries, but only when every one arrived as a real RESP3 map."""
    items = as_items(value)
    if not items:
        return None
    rows = []
    for entry in items:
        if not isinstance(entry, dict):
            return None
        entry_pairs = as_pairs(entry)
        if entry_pairs is None:
            return None
        rows.append(entry_pairs)
    return rows
