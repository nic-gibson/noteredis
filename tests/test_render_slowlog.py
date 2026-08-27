"""``SLOWLOG GET`` table.

Entry shape confirmed against ``slowlogCommand`` in ``src/slowlog.c``: a
fixed-width array, the same on both protocols (``SLOWLOG`` predates RESP3).
"""

from __future__ import annotations

from typing import Any

import pytest

from redis_kernel.render import RENDERERS, render

ENTRIES = [
    [1, 1700000000, 1234, [b"GET", b"foo"], b"127.0.0.1:1234", b"my-client"],
    [2, 1700000005, 50, [b"SET", b"foo", b"bar"], b"127.0.0.1:5678", b""],
]


def test_renderer_is_registered() -> None:
    assert "SLOWLOG GET" in RENDERERS


def test_slowlog_renders_one_row_per_entry() -> None:
    bundle = render(["SLOWLOG", "GET"], ENTRIES)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>id</th>" in html
    assert "<th>command</th>" in html
    assert "<td>GET foo</td>" in html
    assert "<td>SET foo bar</td>" in html
    assert "<td>127.0.0.1:1234</td>" in html


def test_a_seventh_field_from_a_newer_server_is_tolerated() -> None:
    entry = [1, 1700000000, 1234, [b"GET", b"foo"], b"127.0.0.1:1234", b"my-client", 2]
    bundle = render(["SLOWLOG", "GET"], [entry])
    assert bundle is not None
    assert "<td>GET foo</td>" in bundle["text/html"]


@pytest.mark.parametrize(
    "reply",
    [
        [],
        [[1, 2, 3]],  # too few fields
        [[1, 2, 3, b"not a list of args", 4, 5]],
        b"not a list",
        None,
    ],
)
def test_declines_other_shapes(reply: Any) -> None:
    assert render(["SLOWLOG", "GET"], reply) is None
