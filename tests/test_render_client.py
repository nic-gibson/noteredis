"""``CLIENT INFO``/``CLIENT LIST`` tables.

Line format confirmed against ``catClientInfoString`` in
``src/networking.c``: whitespace-separated ``key=value`` tokens, sent as a
RESP3 verbatim string (a plain bulk string under RESP2).
"""

from __future__ import annotations

from typing import Any

import pytest

from noteredis.formatter import Verbatim
from noteredis.render import RENDERERS, render

ONE_LINE = b"id=3 addr=127.0.0.1:1234 name=alice age=10 cmd=get\n"
TWO_LINES = (
    b"id=3 addr=127.0.0.1:1234 name=alice age=10 cmd=get\n"
    b"id=4 addr=127.0.0.1:5678 name= age=2 cmd=set\n"
)


def test_renderers_are_registered() -> None:
    assert {"CLIENT INFO", "CLIENT LIST"} <= set(RENDERERS)


@pytest.mark.parametrize("wrap", [bytes, lambda b: Verbatim(b, "txt")], ids=["resp2", "resp3"])
def test_client_info_renders_a_property_value_table(wrap: Any) -> None:
    bundle = render(["CLIENT", "INFO"], wrap(ONE_LINE))
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>property</th><th>value</th>" in html
    assert "<td>id</td><td>3</td>" in html
    assert "<td>name</td><td>alice</td>" in html


@pytest.mark.parametrize("wrap", [bytes, lambda b: Verbatim(b, "txt")], ids=["resp2", "resp3"])
def test_client_list_renders_one_row_per_client(wrap: Any) -> None:
    bundle = render(["CLIENT", "LIST"], wrap(TWO_LINES))
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>id</th>" in html
    assert "<td>3</td>" in html
    assert "<td>4</td>" in html
    assert "<td>alice</td>" in html


def test_client_info_declines_a_multi_line_reply() -> None:
    # More than one line: not the single-client shape CLIENT INFO promises.
    assert render(["CLIENT", "INFO"], TWO_LINES) is None


@pytest.mark.parametrize("reply", [b"", b"\n", b"no equals signs here", None, 42])
def test_declines_other_shapes(reply: Any) -> None:
    assert render(["CLIENT", "LIST"], reply) is None
