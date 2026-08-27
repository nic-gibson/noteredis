"""``LATENCY HISTORY``/``LATENCY LATEST`` tables.

Shapes confirmed against ``latencyCommandReplyWithSamples``/
``...WithLatestEvents`` in ``src/latency.c`` -- plain arrays, identical on
both protocols.
"""

from __future__ import annotations

from typing import Any

import pytest

from redis_kernel.render import RENDERERS, render


def test_renderers_are_registered() -> None:
    assert {"LATENCY HISTORY", "LATENCY LATEST"} <= set(RENDERERS)


def test_latency_history_renders_a_table() -> None:
    reply = [[1700000000, 5], [1700000060, 12]]
    bundle = render(["LATENCY", "HISTORY", "command"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>timestamp</th><th>latency (ms)</th>" in html
    assert "<td>1700000000</td><td>5</td>" in html


def test_latency_latest_renders_a_table() -> None:
    reply = [[b"command", 1700000000, 5, 20]]
    bundle = render(["LATENCY", "LATEST"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>event</th><th>timestamp</th><th>latest (ms)</th><th>max (ms)</th>" in html
    assert "<td>command</td><td>1700000000</td><td>5</td><td>20</td>" in html


@pytest.mark.parametrize("reply", [[], [[1, 2, 3]], b"nope", None])
def test_latency_history_declines_other_shapes(reply: Any) -> None:
    assert render(["LATENCY", "HISTORY", "command"], reply) is None


@pytest.mark.parametrize("reply", [[], [[1, 2]], b"nope", None])
def test_latency_latest_declines_other_shapes(reply: Any) -> None:
    assert render(["LATENCY", "LATEST"], reply) is None
