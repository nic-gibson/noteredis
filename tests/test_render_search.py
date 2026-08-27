"""``FT.SEARCH`` and ``FT.AGGREGATE`` tables.

Reply shapes are built by hand from RediSearch's serializer, not captured
from a live server -- same convention as the rest of ``test_render.py``.
Both protocols are exercised for every shape, plus the flags that change it
(``WITHSCORES``, ``NOCONTENT``, ``WITHPAYLOADS``, ``WITHCURSOR``) and the one
this renderer deliberately does not understand (``WITHSCHEMA``).
"""

from __future__ import annotations

from typing import Any

import pytest

from redis_kernel.formatter import Double, RedisMap, Status
from redis_kernel.render import RENDERERS, render


def _map(pairs: dict[Any, Any]) -> RedisMap:
    """A RESP3 map with ``Status`` (simple-string) keys, as the server sends.

    ``extra_attributes`` is the one exception: its field names are bulk
    strings, so those entries are built with raw ``bytes`` keys already.
    """
    return RedisMap(
        [(Status(k.encode()) if isinstance(k, str) else k, v) for k, v in pairs.items()]
    )


def test_renderers_are_registered() -> None:
    assert {"FT.SEARCH", "FT.AGGREGATE"} <= set(RENDERERS)


# --------------------------------------------------------------------------- #
# FT.SEARCH
# --------------------------------------------------------------------------- #

SEARCH_RESP2 = [
    2,
    b"doc:1",
    [b"title", b"Hello", b"body", b"World"],
    b"doc:2",
    [b"title", b"Bye"],
]

SEARCH_RESP3 = _map(
    {
        "total_results": 2,
        "results": [
            _map(
                {"id": b"doc:1", "extra_attributes": _map({b"title": b"Hello", b"body": b"World"})}
            ),
            _map({"id": b"doc:2", "extra_attributes": _map({b"title": b"Bye"})}),
        ],
    }
)


@pytest.mark.parametrize("reply", [SEARCH_RESP2, SEARCH_RESP3], ids=["resp2", "resp3"])
def test_search_renders_a_table_with_the_union_of_fields(reply: Any) -> None:
    bundle = render(["FT.SEARCH", "idx", "*"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>id</th><th>title</th><th>body</th>" in html
    assert "<td>doc:1</td><td>Hello</td><td>World</td>" in html
    # doc:2 has no body: blank cell, not a dropped column.
    assert "<td>doc:2</td><td>Bye</td><td></td>" in html


def test_search_is_case_insensitive_about_the_command_and_its_flags() -> None:
    bundle = render(
        ["ft.search", "idx", "*", "withscores"], [1, b"doc:1", b"1.5", [b"title", b"Hi"]]
    )
    assert bundle is not None
    assert "<th>score</th>" in bundle["text/html"]


SEARCH_WITHSCORES_RESP2 = [1, b"doc:1", b"1.5", [b"title", b"Hello"]]
SEARCH_WITHSCORES_RESP3 = _map(
    {
        "total_results": 1,
        "results": [
            _map(
                {
                    "id": b"doc:1",
                    "score": Double("1.5"),
                    "extra_attributes": _map({b"title": b"Hello"}),
                }
            )
        ],
    }
)


@pytest.mark.parametrize(
    "reply", [SEARCH_WITHSCORES_RESP2, SEARCH_WITHSCORES_RESP3], ids=["resp2", "resp3"]
)
def test_search_withscores_adds_a_score_column(reply: Any) -> None:
    bundle = render(["FT.SEARCH", "idx", "*", "WITHSCORES"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>id</th><th>score</th><th>title</th>" in html
    assert "<td>doc:1</td><td>1.5</td><td>Hello</td>" in html


SEARCH_NOCONTENT_RESP2 = [3, b"doc:1", b"doc:2", b"doc:3"]
SEARCH_NOCONTENT_RESP3 = _map(
    {
        "total_results": 3,
        "results": [_map({"id": b"doc:1"}), _map({"id": b"doc:2"}), _map({"id": b"doc:3"})],
    }
)


@pytest.mark.parametrize(
    "reply", [SEARCH_NOCONTENT_RESP2, SEARCH_NOCONTENT_RESP3], ids=["resp2", "resp3"]
)
def test_search_nocontent_is_an_id_only_table(reply: Any) -> None:
    bundle = render(["FT.SEARCH", "idx", "*", "NOCONTENT"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>id</th>" in html
    assert "<th>title</th>" not in html
    assert "<td>doc:2</td>" in html


def test_search_withpayloads_adds_a_payload_column() -> None:
    reply = [1, b"doc:1", b"\x01\x02", [b"title", b"Hello"]]
    bundle = render(["FT.SEARCH", "idx", "*", "WITHPAYLOADS"], reply)
    assert bundle is not None
    assert "<th>id</th><th>payload</th><th>title</th>" in bundle["text/html"]


def test_search_withcursor_is_unwrapped_before_parsing() -> None:
    inner = [1, b"doc:1", [b"title", b"Hello"]]
    bundle = render(["FT.SEARCH", "idx", "*", "WITHCURSOR"], [inner, 42])
    assert bundle is not None
    assert "<td>doc:1</td><td>Hello</td>" in bundle["text/html"]


@pytest.mark.parametrize(
    "reply",
    [
        [],  # empty: no hits to show
        b"a bulk string",
        None,
        42,
        [0],  # total only, no hits -- still nothing to show
    ],
)
def test_search_returns_nothing_for_an_unremarkable_or_empty_reply(reply: Any) -> None:
    assert render(["FT.SEARCH", "idx", "*"], reply) is None


def test_search_bails_out_rather_than_crash_on_a_truncated_row() -> None:
    # WITHSCORES claimed, but the score element never arrives.
    assert render(["FT.SEARCH", "idx", "*", "WITHSCORES"], [1, b"doc:1"]) is None


# --------------------------------------------------------------------------- #
# FT.AGGREGATE
# --------------------------------------------------------------------------- #

AGGREGATE_RESP2 = [
    2,
    [b"state", b"NY", b"count", b"3"],
    [b"state", b"CA", b"count", b"5"],
]

AGGREGATE_RESP3 = _map(
    {
        "total_results": 2,
        "results": [
            _map({"extra_attributes": _map({b"state": b"NY", b"count": b"3"})}),
            _map({"extra_attributes": _map({b"state": b"CA", b"count": b"5"})}),
        ],
    }
)


@pytest.mark.parametrize("reply", [AGGREGATE_RESP2, AGGREGATE_RESP3], ids=["resp2", "resp3"])
def test_aggregate_renders_a_table_of_groups(reply: Any) -> None:
    bundle = render(["FT.AGGREGATE", "idx", "*", "GROUPBY", "1", "@state"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>state</th><th>count</th>" in html
    assert "<td>NY</td><td>3</td>" in html
    assert "<td>CA</td><td>5</td>" in html


def test_aggregate_withcursor_is_unwrapped_before_parsing() -> None:
    inner = [2, [b"state", b"NY"]]
    bundle = render(["FT.AGGREGATE", "idx", "*", "WITHCURSOR"], [inner, 7])
    assert bundle is not None
    assert "<td>NY</td>" in bundle["text/html"]

    bundle_resp3 = render(["FT.AGGREGATE", "idx", "*", "WITHCURSOR"], [AGGREGATE_RESP3, 7])
    assert bundle_resp3 is not None
    assert "<td>NY</td>" in bundle_resp3["text/html"]


def test_aggregate_withschema_falls_back_to_plain_text() -> None:
    # [schema, total, row...] -- the extra leading element is not our shape.
    reply = [[b"state", b"TAG"], 1, [b"state", b"NY"]]
    assert render(["FT.AGGREGATE", "idx", "*", "WITHSCHEMA"], reply) is None


@pytest.mark.parametrize("reply", [[], [0], b"nope", None])
def test_aggregate_returns_nothing_for_an_unremarkable_or_empty_reply(reply: Any) -> None:
    assert render(["FT.AGGREGATE", "idx", "*"], reply) is None
