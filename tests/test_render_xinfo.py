"""``XINFO GROUPS``/``XINFO CONSUMERS`` tables.

Each entry is pairs-shaped on its own -- a RESP2 flat array or a RESP3 map --
confirmed against ``xinfoCommand`` in ``src/t_stream.c``.
"""

from __future__ import annotations

from typing import Any

import pytest

from noteredis.formatter import RedisMap
from noteredis.render import RENDERERS, render

GROUPS_RESP2 = [
    [b"name", b"g1", b"consumers", 1, b"pending", 0],
    [b"name", b"g2", b"consumers", 2, b"pending", 3],
]
GROUPS_RESP3 = [
    RedisMap([(b"name", b"g1"), (b"consumers", 1), (b"pending", 0)]),
    RedisMap([(b"name", b"g2"), (b"consumers", 2), (b"pending", 3)]),
]


def test_renderers_are_registered() -> None:
    assert {"XINFO GROUPS", "XINFO CONSUMERS"} <= set(RENDERERS)


@pytest.mark.parametrize("reply", [GROUPS_RESP2, GROUPS_RESP3], ids=["resp2", "resp3"])
def test_xinfo_groups_renders_a_table(reply: Any) -> None:
    bundle = render(["XINFO", "GROUPS", "s"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>name</th><th>consumers</th><th>pending</th>" in html
    assert "<td>g1</td><td>1</td><td>0</td>" in html
    assert "<td>g2</td><td>2</td><td>3</td>" in html


def test_xinfo_consumers_shares_the_renderer() -> None:
    bundle = render(["XINFO", "CONSUMERS", "s", "g1"], GROUPS_RESP2)
    assert bundle is not None


@pytest.mark.parametrize("reply", [[], [b"not an entry"], [[b"odd", b"number", b"of"]], None])
def test_declines_other_shapes(reply: Any) -> None:
    assert render(["XINFO", "GROUPS", "s"], reply) is None
