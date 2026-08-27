"""``TS.RANGE``/``TS.REVRANGE`` tables.

Shape confirmed against redis-py's own ``parse_range``/``parse_range_unified``
(``redis/commands/timeseries/utils.py``): a plain array of samples, identical
on both protocols.
"""

from __future__ import annotations

from typing import Any

import pytest

from redis_kernel.render import RENDERERS, render


def test_renderers_are_registered() -> None:
    assert {"TS.RANGE", "TS.REVRANGE"} <= set(RENDERERS)


def test_range_renders_a_timestamp_value_table() -> None:
    reply = [[1700000000000, b"1.5"], [1700000010000, b"2.5"]]
    bundle = render(["TS.RANGE", "temp", "-", "+"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>timestamp</th><th>value</th>" in html
    assert "<td>1700000000000</td><td>1.5</td>" in html


def test_multiple_aggregators_get_numbered_value_columns() -> None:
    reply = [[1700000000000, b"1.5", b"3.0"]]
    bundle = render(["TS.RANGE", "temp", "-", "+"], reply)
    assert bundle is not None
    assert "<th>timestamp</th><th>value 1</th><th>value 2</th>" in bundle["text/html"]


@pytest.mark.parametrize(
    "reply",
    [
        [],
        [[1700000000000]],  # a timestamp with no value
        [[1700000000000, b"1.5"], [1700000010000]],  # inconsistent widths
        b"nope",
        None,
    ],
)
def test_declines_other_shapes(reply: Any) -> None:
    assert render(["TS.RANGE", "temp", "-", "+"], reply) is None
