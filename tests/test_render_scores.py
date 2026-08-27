"""Member/score and field/value tables: WITHSCORES/WITHVALUES commands.

RESP2 answers with a flat array; RESP3 answers with an array of two-element
arrays (never a map, since members are ordered and can repeat) -- confirmed
against ``t_zset.c``/``t_hash.c``. Both are exercised for every command.
"""

from __future__ import annotations

from typing import Any

import pytest

from redis_kernel.render import RENDERERS, render

FLAT = [b"a", b"1", b"b", b"2"]
NESTED = [[b"a", b"1"], [b"b", b"2"]]


def test_renderers_are_registered() -> None:
    assert {"ZRANGE", "ZPOPMIN", "ZPOPMAX", "HRANDFIELD"} <= set(RENDERERS)


@pytest.mark.parametrize("reply", [FLAT, NESTED], ids=["resp2", "resp3"])
def test_zrange_withscores_renders_a_member_score_table(reply: Any) -> None:
    bundle = render(["ZRANGE", "z", "0", "-1", "WITHSCORES"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>member</th><th>score</th>" in html
    assert "<td>a</td><td>1</td>" in html
    assert "<td>b</td><td>2</td>" in html


def test_zrange_without_the_flag_renders_nothing() -> None:
    # No WITHSCORES: this is a flat list of members, not member/score pairs.
    assert render(["ZRANGE", "z", "0", "-1"], [b"a", b"b"]) is None


def test_the_flag_check_is_case_insensitive() -> None:
    bundle = render(["zrange", "z", "0", "-1", "withscores"], FLAT)
    assert bundle is not None


@pytest.mark.parametrize("reply", [FLAT, NESTED], ids=["resp2", "resp3"])
def test_zpopmin_always_renders_regardless_of_a_flag(reply: Any) -> None:
    bundle = render(["ZPOPMIN", "z", "2"], reply)
    assert bundle is not None
    assert "<th>member</th><th>score</th>" in bundle["text/html"]


@pytest.mark.parametrize("reply", [FLAT, NESTED], ids=["resp2", "resp3"])
def test_hrandfield_withvalues_renders_a_field_value_table(reply: Any) -> None:
    bundle = render(["HRANDFIELD", "h", "2", "WITHVALUES"], reply)
    assert bundle is not None
    assert "<th>field</th><th>value</th>" in bundle["text/html"]


def test_hrandfield_without_the_flag_renders_nothing() -> None:
    assert render(["HRANDFIELD", "h", "2"], [b"a", b"b"]) is None


@pytest.mark.parametrize(
    "reply",
    [
        [],
        [b"lonely"],
        [[b"a", b"1", b"extra"]],  # a triple, not a pair
        b"not a list",
        None,
    ],
)
def test_declines_other_shapes(reply: Any) -> None:
    assert render(["ZRANGE", "z", "0", "-1", "WITHSCORES"], reply) is None
