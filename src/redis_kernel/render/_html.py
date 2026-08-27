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

__all__ = ["cell_text", "escape", "table"]


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
