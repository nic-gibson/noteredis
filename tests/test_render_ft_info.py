"""``FT.INFO`` tables.

Shapes confirmed against ``IndexInfoCommand`` in
``src/info/info_command.c``: a top-level map with an ``attributes`` array of
one map per field. RESP2 degrades both to flat arrays -- but a field's
boolean flags (``SORTABLE``, ``NOSTEM``, ...) are bare tokens with no key of
their own under RESP2, so a field with an *even* number of them set would
silently mislabel data if paired up naively. The field table is therefore
RESP3-only; the property table works on both.
"""

from __future__ import annotations

from typing import Any

import pytest

from noteredis.formatter import RedisMap, Status
from noteredis.render import RENDERERS, render


def _map(pairs: dict[Any, Any]) -> RedisMap:
    return RedisMap(
        [(Status(k.encode()) if isinstance(k, str) else k, v) for k, v in pairs.items()]
    )


def test_renderer_is_registered() -> None:
    assert "FT.INFO" in RENDERERS


# --------------------------------------------------------------------------- #
# The property table -- both protocols
# --------------------------------------------------------------------------- #

INFO_RESP2 = [
    b"index_name",
    b"idx",
    b"attributes",
    [[b"identifier", b"title", b"attribute", b"title", b"type", b"TEXT"]],
    b"num_docs",
    3,
]

INFO_RESP3 = _map(
    {
        "index_name": b"idx",
        "attributes": [_map({"identifier": b"title", "attribute": b"title", "type": b"TEXT"})],
        "num_docs": 3,
    }
)


@pytest.mark.parametrize("reply", [INFO_RESP2, INFO_RESP3], ids=["resp2", "resp3"])
def test_renders_a_property_table(reply: Any) -> None:
    bundle = render(["FT.INFO", "idx"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>property</th><th>value</th>" in html
    assert "<td>index_name</td><td>idx</td>" in html
    assert "<td>num_docs</td><td>3</td>" in html
    # attributes gets its own table, not a raw dump in the property table.
    assert "<td>attributes</td>" not in html


# --------------------------------------------------------------------------- #
# The field table -- RESP3 only
# --------------------------------------------------------------------------- #


def test_resp3_also_renders_a_field_table() -> None:
    bundle = render(["FT.INFO", "idx"], INFO_RESP3)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<th>identifier</th><th>attribute</th><th>type</th>" in html
    assert "<td>title</td><td>title</td><td>TEXT</td>" in html


def test_resp2_renders_only_the_property_table() -> None:
    """RESP2's field row is a flat array; even a clean one is left alone."""
    bundle = render(["FT.INFO", "idx"], INFO_RESP2)
    assert bundle is not None
    html = bundle["text/html"]
    assert html.count("<table>") == 1
    assert "<th>identifier</th>" not in html


def test_a_resp2_field_with_flags_does_not_mislabel_data() -> None:
    """SORTABLE and NOSTEM together is an even number of bare trailing
    tokens -- pairing them up naively would silently produce
    ("SORTABLE", "NOSTEM") as if it were real data instead of just not
    rendering a field table at all.
    """
    reply = [
        b"index_name",
        b"idx",
        b"attributes",
        [
            [
                b"identifier",
                b"title",
                b"attribute",
                b"title",
                b"type",
                b"TEXT",
                b"SORTABLE",
                b"NOSTEM",
            ]
        ],
    ]
    bundle = render(["FT.INFO", "idx"], reply)
    assert bundle is not None
    html = bundle["text/html"]
    assert "<td>index_name</td><td>idx</td>" in html
    assert "SORTABLE" not in html
    assert "<th>identifier</th>" not in html


def test_a_resp3_field_with_an_unexpected_shape_skips_just_the_field_table() -> None:
    reply = _map({"index_name": b"idx", "attributes": [b"not a map"]})
    bundle = render(["FT.INFO", "idx"], reply)
    assert bundle is not None
    assert "<th>identifier</th>" not in bundle["text/html"]
    assert "<td>index_name</td><td>idx</td>" in bundle["text/html"]


@pytest.mark.parametrize("reply", [[], [b"lonely"], b"nope", None, 42])
def test_declines_other_shapes(reply: Any) -> None:
    assert render(["FT.INFO", "idx"], reply) is None
