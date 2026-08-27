"""``FT.SEARCH`` and ``FT.AGGREGATE`` as tables.

Both commands answer with one row per hit -- a document (``FT.SEARCH``) or an
aggregated group (``FT.AGGREGATE``) -- with no fixed set of fields, so they
share the union-of-columns table ``streams.py`` uses for ``XRANGE``.

The reply shapes below come from reading RediSearch's own serializer
(``src/aggregate/aggregate_exec.c``: ``serializeResult()`` and
``prepareSendChunkReply_Resp2``/``_Resp3``) rather than guessing:

FT.SEARCH RESP2:    [total, id, (score,) (payload,) [field, value, ...], ...]
FT.SEARCH RESP3:    {"total_results": N,
                      "results": [{"id":, "score":, "payload":,
                                    "extra_attributes": {...}}, ...]}
FT.AGGREGATE RESP2: [total, [field, value, ...], ...]
FT.AGGREGATE RESP3: {"total_results": N, "results": [{"extra_attributes": {...}}, ...]}

``WITHCURSOR`` wraps either shape as ``[data, cursor_id]``; that wrapper is
unwrapped rather than tracked as a flag, since a table of the current chunk
is exactly as useful mid-cursor as at the end. ``WITHSCHEMA`` and
``FT.PROFILE`` add structure this does not understand yet, and fall back to
the plain text like any other shape mismatch.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import cell_text, rows_table, table, union_labels
from ._shapes import as_items, as_pairs, rows_of_pairs

__all__ = ["render_aggregate", "render_search"]

#: Beyond this many columns the table stops being easier to read than the text.
MAX_COLUMNS = 30


def _flag(args: list[str], name: str) -> bool:
    """Is ``name`` (e.g. ``WITHSCORES``) among the command's own arguments?

    Read from the command line rather than sniffed from the reply, because
    that is how the server itself decides what to send.
    """
    return any(tok.upper() == name for tok in args[2:])


def _unwrap_cursor(reply: Any) -> Any:
    """Peel off a ``WITHCURSOR`` wrapper: ``[data, cursor_id]`` -> ``data``."""
    items = as_items(reply)
    if (
        items is not None
        and len(items) == 2
        and isinstance(items[0], (list, dict))
        and isinstance(items[1], int)
    ):
        return items[0]
    return reply


@renderer("FT.SEARCH")
def render_search(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table of one row per document, or nothing if the shape is unexpected."""
    hits = _search_hits(args, _unwrap_cursor(reply))
    if not hits:
        return None

    columns = union_labels((hit["fields"] or () for hit in hits), MAX_COLUMNS)
    if columns is None:
        return None

    with_scores = _flag(args, "WITHSCORES")
    with_payloads = _flag(args, "WITHPAYLOADS")
    headers = ["id", *(["score"] if with_scores else []), *(["payload"] if with_payloads else [])]
    headers += columns

    rows = []
    for hit in hits:
        row: list[Any] = [hit["id"]]
        if with_scores:
            row.append(hit["score"])
        if with_payloads:
            row.append(hit["payload"])
        field_map = {cell_text(name): value for name, value in (hit["fields"] or ())}
        row += [field_map.get(column, "") for column in columns]
        rows.append(row)

    return {"text/html": table(headers, rows)}


@renderer("FT.AGGREGATE")
def render_aggregate(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table of one row per group, or nothing if the shape is unexpected."""
    del args
    rows = _aggregate_rows(_unwrap_cursor(reply))
    if not rows:
        return None
    html = rows_table(rows, MAX_COLUMNS)
    return {"text/html": html} if html else None


def _search_hits(args: list[str], reply: Any) -> list[dict[str, Any]] | None:
    if isinstance(reply, dict):
        return _search_hits_resp3(reply)
    items = as_items(reply)
    if items is None:
        return None
    return _search_hits_resp2(args, items)


def _search_hits_resp3(reply: dict[Any, Any]) -> list[dict[str, Any]] | None:
    results = reply.get(b"results")
    if not isinstance(results, list):
        return None
    hits = []
    for row in results:
        if not isinstance(row, dict):
            return None
        fields = row.get(b"extra_attributes")
        hits.append(
            {
                "id": row.get(b"id"),
                "score": row.get(b"score"),
                "payload": row.get(b"payload"),
                "fields": as_pairs(fields) if fields is not None else None,
            }
        )
    return hits


def _search_hits_resp2(args: list[str], items: list[Any]) -> list[dict[str, Any]] | None:
    if not items:
        return None
    with_scores = _flag(args, "WITHSCORES")
    with_payloads = _flag(args, "WITHPAYLOADS")
    no_content = _flag(args, "NOCONTENT")

    rest = items[1:]  # items[0] is the total-results count
    hits = []
    i = 0
    while i < len(rest):
        hit: dict[str, Any] = {"id": rest[i], "score": None, "payload": None, "fields": None}
        i += 1
        if with_scores:
            if i >= len(rest):
                return None
            hit["score"] = rest[i]
            i += 1
        if with_payloads:
            if i >= len(rest):
                return None
            hit["payload"] = rest[i]
            i += 1
        if not no_content:
            if i >= len(rest):
                return None
            hit["fields"] = as_pairs(rest[i])
            i += 1
        hits.append(hit)
    return hits


def _aggregate_rows(reply: Any) -> list[list[tuple[Any, Any]]] | None:
    if isinstance(reply, dict):
        results = reply.get(b"results")
        if not isinstance(results, list):
            return None
        rows = []
        for row in results:
            if not isinstance(row, dict):
                return None
            fields = row.get(b"extra_attributes")
            rows.append((as_pairs(fields) if fields is not None else None) or [])
        return rows

    items = as_items(reply)
    if not items:
        return None
    # items[1:] drops the leading total-results count; a WITHSCHEMA reply's
    # extra leading element ends up parsed as if it were a row instead, which
    # is not pairs-shaped either, so rows_of_pairs correctly bails to None.
    return rows_of_pairs(items[1:])
