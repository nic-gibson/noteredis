"""Shared helpers for the HTML renderers.

Two rules hold everywhere here:

* **Escape everything.** Keys and values come from the server, and a key called
  ``<script>`` is legal Redis. Every string that reaches the output goes through
  :func:`escape`.
* **No colours, no fonts, no sizes.** Jupyter styles tables in rendered output
  already, and it does so per theme. Emitting our own palette would look wrong
  in half of them, so the HTML here is structural only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from html import escape
from typing import Any

from ..formatter import Double, Status, Verbatim, format_reply, quote_bytes

__all__ = ["cell_text", "escape", "rows_table", "table", "union_labels"]


def cell_text(value: Any) -> str:
    """A reply value as one line of table text.

    Bytes that are printable UTF-8 are shown as themselves -- a table of
    ``"Ada Lovelace"`` quoted and escaped would defeat the point of the table.
    Anything else falls back to the formatter's own escaping, so binary stays
    legible and unambiguous rather than turning into replacement characters.
    """
    if value is None:
        return "(nil)"
    if isinstance(value, str):
        # Already display text -- a renderer that pre-formatted a cell, or a
        # value we parsed out of INFO. Sending it back through the formatter
        # would quote it a second time.
        return value
    if isinstance(value, bool):
        return "(true)" if value else "(false)"
    if isinstance(value, Double):
        return value.raw
    if isinstance(value, (Status, Verbatim)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            return quote_bytes(raw)
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded):
            return quote_bytes(raw)
        return decoded
    if isinstance(value, int):
        return str(value)
    # An aggregate or something unexpected: fall back to the text redis-cli
    # would have printed rather than inventing a second rendering for it.
    return format_reply(value).rstrip("\n")


def union_labels(
    pairs_per_row: Iterable[Sequence[tuple[Any, Any]]], max_columns: int
) -> list[str] | None:
    """Field names across every row, in first-seen order, as display labels.

    The shared column strategy for a table with one row per hit and no fixed
    schema -- ``XRANGE`` entries, ``FT.SEARCH`` documents, ``FT.AGGREGATE``
    groups. ``None`` once the union would need more than ``max_columns``
    columns: past that point the table stops being easier to read than the
    plain text.
    """
    labels: list[str] = []
    for pairs in pairs_per_row:
        for name, _ in pairs:
            label = cell_text(name)
            if label not in labels:
                if len(labels) >= max_columns:
                    return None
                labels.append(label)
    return labels


def rows_table(pairs_per_row: Iterable[Sequence[tuple[Any, Any]]], max_columns: int) -> str | None:
    """An HTML table with one row per pairs-shaped entry, or ``None`` past ``max_columns``.

    Shared by every renderer with no fixed schema and no id column of its
    own -- ``FT.AGGREGATE``'s groups, ``XINFO GROUPS``/``CONSUMERS``' entries.
    """
    rows = list(pairs_per_row)
    columns = union_labels(rows, max_columns)
    if not columns:
        return None
    out_rows = []
    for pairs in rows:
        field_map = {cell_text(name): value for name, value in pairs}
        out_rows.append([field_map.get(column, "") for column in columns])
    return table(columns, out_rows)


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], caption: str | None = None) -> str:
    """An HTML table. Cells are rendered with :func:`cell_text` and escaped."""
    out = ["<table>"]
    if caption is not None:
        out.append(f"<caption>{escape(caption)}</caption>")
    out.append("<thead><tr>")
    out.extend(f"<th>{escape(header)}</th>" for header in headers)
    out.append("</tr></thead>")
    out.append("<tbody>")
    for row in rows:
        out.append("<tr>")
        out.extend(f"<td>{escape(cell_text(cell))}</td>" for cell in row)
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)
