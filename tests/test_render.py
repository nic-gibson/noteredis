"""The rich renderers, and the rule that they can only ever add.

Two things matter more than any individual table here:

* ``text/plain`` is always the exact redis-cli text, whatever a renderer does.
* A renderer handed a shape it did not expect returns nothing, so the plain
  text stands alone. Losing someone's output to a rendering bug is the one
  unacceptable failure.

Both protocols are tested for every pair-shaped command, since RESP2 answers
with a flat array where RESP3 answers with a map.
"""

from __future__ import annotations

from typing import Any

import pytest

from noteredis.formatter import RedisMap, RedisSet, Status, Verbatim, format_reply
from noteredis.render import RENDERERS, render
from noteredis.render._html import cell_text, table

# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #


def test_renderers_are_registered() -> None:
    """An empty registry would make every other test here vacuously pass."""
    assert {"HGETALL", "XRANGE", "INFO", "JSON.GET"} <= set(RENDERERS)


def test_no_renderer_for_an_unremarkable_command() -> None:
    assert render(["GET", "key"], b"value") is None


def test_a_renderer_that_raises_is_ignored() -> None:
    from noteredis.render import renderer

    @renderer("BOOM")
    def _explode(args: list[str], reply: Any) -> dict[str, Any] | None:
        raise RuntimeError("renderer bug")

    try:
        assert render(["BOOM"], b"x") is None  # output survives the bug
    finally:
        RENDERERS.pop("BOOM", None)


def test_subcommands_are_matched_before_bare_commands() -> None:
    bundle = render(["XINFO", "STREAM", "s"], [b"length", 3])
    assert bundle is not None
    assert "<th>property</th>" in bundle["text/html"]


# --------------------------------------------------------------------------- #
# Pair-shaped replies
# --------------------------------------------------------------------------- #

HASH_RESP2 = [b"name", b"Ada Lovelace", b"role", b"admin"]
HASH_RESP3 = RedisMap([(b"name", b"Ada Lovelace"), (b"role", b"admin")])


@pytest.mark.parametrize("reply", [HASH_RESP2, HASH_RESP3], ids=["resp2", "resp3"])
def test_hgetall_renders_a_table_in_both_protocols(reply: Any) -> None:
    bundle = render(["HGETALL", "user:1"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>field</th>" in html
    assert "<td>name</td><td>Ada Lovelace</td>" in html
    assert "<td>role</td><td>admin</td>" in html


def test_config_get_labels_its_columns_for_config() -> None:
    bundle = render(["CONFIG", "GET", "maxmemory"], [b"maxmemory", b"0"])
    assert bundle is not None
    assert "<th>parameter</th>" in bundle["text/html"]


@pytest.mark.parametrize(
    "reply",
    [
        [],  # empty: the text says (empty array), a table would say nothing
        [b"lonely"],  # odd number of elements is not pairs
        b"a bulk string",
        None,
        42,
        RedisSet([b"a", b"b"]),  # a set is not pairs, however it is shaped
    ],
)
def test_pair_renderer_declines_other_shapes(reply: Any) -> None:
    assert render(["HGETALL", "user:1"], reply) is None


@pytest.mark.parametrize(
    ("command", "reply"),
    [
        (["MEMORY", "STATS"], [b"peak.allocated", 12345]),
        (["BF.INFO", "filter"], [b"Capacity", 1000, b"Size", 128]),
        (["CF.INFO", "filter"], [b"Size", 128, b"Number of buckets", 512]),
    ],
)
def test_memory_and_probabilistic_info_reuse_the_pairs_table(
    command: list[str], reply: Any
) -> None:
    """Same flat key/value shape as CONFIG GET, just a different command name."""
    bundle = render(command, reply)
    assert bundle is not None
    assert "<th>metric</th>" in bundle["text/html"]


# --------------------------------------------------------------------------- #
# Streams
# --------------------------------------------------------------------------- #

RANGE = [
    [b"1526985054069-0", [b"temp", b"21"]],
    [b"1526985054069-1", [b"temp", b"22", b"hum", b"40"]],
]


def test_xrange_renders_one_row_per_entry() -> None:
    bundle = render(["XRANGE", "s", "-", "+"], RANGE)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>id</th><th>temp</th><th>hum</th>" in html
    assert "<td>1526985054069-0</td><td>21</td><td></td>" in html
    assert "<td>1526985054069-1</td><td>22</td><td>40</td>" in html


def test_a_field_missing_from_an_entry_leaves_the_cell_empty() -> None:
    """Entries need not share a field set, and a blank beats a fake value."""
    bundle = render(["XRANGE", "s", "-", "+"], [[b"1-1", [b"only", b"x"]]])
    assert bundle is not None
    assert "<td>1-1</td><td>x</td>" in bundle["text/html"]


@pytest.mark.parametrize(
    "reply",
    [
        [b"not an entry"],
        [[b"1-1"]],  # id with no field list
        [[b"1-1", [b"odd", b"number", b"of"]]],
        [[b"1-1", b"not a field list"]],
        [[b"1-1", []]],  # an entry with no fields at all
        [],
    ],
)
def test_stream_renderer_declines_other_shapes(reply: Any) -> None:
    assert render(["XRANGE", "s", "-", "+"], reply) is None


def test_a_very_wide_stream_falls_back_to_text() -> None:
    """Past a few dozen columns a table stops being the more readable form."""
    fields: list[bytes] = []
    for i in range(40):
        fields += [f"f{i}".encode(), b"v"]
    assert render(["XRANGE", "s", "-", "+"], [[b"1-1", fields]]) is None


# --------------------------------------------------------------------------- #
# INFO
# --------------------------------------------------------------------------- #

INFO_TEXT = (
    b"# Server\r\nredis_version:8.8.0\r\nuptime_in_seconds:12\r\n"
    b"# Clients\r\nconnected_clients:1\r\n"
)


@pytest.mark.parametrize("reply", [INFO_TEXT, Verbatim(INFO_TEXT, "txt")], ids=["bulk", "verbatim"])
def test_info_renders_a_table_per_section(reply: Any) -> None:
    bundle = render(["INFO"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<caption>Server</caption>" in html
    assert "<caption>Clients</caption>" in html
    assert "<td>redis_version</td><td>8.8.0</td>" in html
    # Values are not re-quoted on the way into a cell.
    assert '"8.8.0"' not in html


def test_info_keeps_a_value_containing_a_colon() -> None:
    bundle = render(["INFO"], b"# Server\r\nexecutable:/usr/bin/redis:8\r\n")
    assert bundle is not None
    assert "<td>executable</td><td>/usr/bin/redis:8</td>" in bundle["text/html"]


@pytest.mark.parametrize("reply", [b"", b"not info at all\r\n", None, [b"an", b"array"]])
def test_info_renderer_declines_other_shapes(reply: Any) -> None:
    assert render(["INFO"], reply) is None


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #


def test_json_get_returns_a_parsed_tree() -> None:
    bundle = render(["JSON.GET", "doc"], b'{"a":[1,2],"b":null}')
    assert bundle == {"application/json": {"a": [1, 2], "b": None}}


@pytest.mark.parametrize("reply", [b"not json", b"", b"   ", None, [b"array"]])
def test_json_renderer_declines_other_shapes(reply: Any) -> None:
    assert render(["JSON.GET", "doc"], reply) is None


def test_a_huge_document_is_not_parsed_into_the_notebook() -> None:
    big = b'["' + b"x" * 1_000_001 + b'"]'
    assert render(["JSON.GET", "doc"], big) is None


# --------------------------------------------------------------------------- #
# HTML safety and cell text
# --------------------------------------------------------------------------- #


def test_values_from_the_server_are_escaped() -> None:
    """`<script>` is a legal Redis key."""
    bundle = render(["HGETALL", "x"], [b"<script>alert(1)</script>", b"a & b"])
    assert bundle is not None
    html = bundle["text/html"]
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "a &amp; b" in html


def test_the_html_carries_no_colours_or_fonts() -> None:
    """Jupyter themes style output tables; our own palette would fight them."""
    bundle = render(["HGETALL", "x"], HASH_RESP2)
    assert bundle is not None
    for banned in ("style=", "color", "font", "background"):
        assert banned not in bundle["text/html"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (b"plain", "plain"),
        ("already text", "already text"),
        ("naïve".encode(), "naïve"),
        (b"\xac\xed\x00", '"\\xac\\xed\\x00"'),  # binary: escaped, as redis-cli does
        (b"two\nlines", '"two\\nlines"'),  # a newline would break the row
        (None, "(nil)"),
        (True, "(true)"),
        (False, "(false)"),
        (7, "7"),
        (Status(b"OK"), "OK"),
    ],
)
def test_cell_text(value: Any, expected: str) -> None:
    assert cell_text(value) == expected


def test_a_nested_aggregate_cell_falls_back_to_the_redis_cli_text() -> None:
    nested = [b"a", b"b"]
    assert cell_text(nested) == format_reply(nested).rstrip("\n")


def test_table_escapes_headers_too() -> None:
    assert "<th>&lt;b&gt;</th>" in table(["<b>"], [])
