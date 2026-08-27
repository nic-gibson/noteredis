"""How ``do_complete`` wires key completion in.

Kept apart from ``test_completion.py`` so that stays importable without
``ipykernel``, the same rule the ``client`` and ``commands`` modules follow. No
server: the kernel is handed a session with a canned ``SCAN`` and a
pre-built command table, so nothing dials out.
"""

from __future__ import annotations

from typing import Any

import pytest

from noteredis.commands import KeySpec
from noteredis.kernel import RedisKernel
from test_completion import FakeServer, _session, _table


@pytest.fixture
def kernel() -> RedisKernel:
    kernel = RedisKernel()
    # Pre-built, so no COMMAND DOCS goes out.
    kernel._table = _table(GET=KeySpec(1, 1, 1))
    kernel.redis = _session(FakeServer({"0": [b"0", [b"user:1", b"user:2"]]}))
    return kernel


def test_the_kernel_keeps_its_messaging_session(kernel: RedisKernel) -> None:
    """``Kernel.session`` is the ZMQ session; the connection lives on ``redis``."""
    assert kernel.redis.scan_keys("user:") == ["user:1", "user:2"]
    assert not isinstance(getattr(kernel, "session", None), type(kernel.redis))


def test_keys_are_not_offered_by_default(kernel: RedisKernel) -> None:
    assert kernel.do_complete("GET user:", 9)["matches"] == []


def test_keys_are_offered_once_switched_on(kernel: RedisKernel) -> None:
    kernel.redis.complete_keys = True
    reply = kernel.do_complete("GET user:", 9)
    assert reply["matches"] == ["user:1", "user:2"]
    # The frontend replaces from the start of the token being completed.
    assert reply["cursor_start"] == 4
    assert reply["cursor_end"] == 9


def test_keys_are_not_offered_at_a_non_key_position(kernel: RedisKernel) -> None:
    kernel.redis.complete_keys = True
    assert kernel.do_complete("GET user:1 user:", 16)["matches"] == []


def test_keys_are_not_offered_inside_a_transaction(kernel: RedisKernel) -> None:
    kernel.redis.complete_keys = True
    kernel.redis.in_transaction = True
    assert kernel.do_complete("GET user:", 9)["matches"] == []


def test_an_unbuilt_table_is_not_built_inside_a_transaction() -> None:
    """Building it means COMMAND DOCS, which a MULTI would queue rather than answer."""
    kernel = RedisKernel()
    asked: list[list[str]] = []

    def execute(args: list[str]) -> Any:
        asked.append(args)
        raise AssertionError("no command may be issued while a transaction is open")

    kernel.redis.execute = execute  # type: ignore[method-assign]
    kernel.redis.in_transaction = True

    assert kernel.do_complete("GE", 2)["matches"] == []
    assert kernel.do_inspect("GET", 3)["found"] is False
    assert asked == []
    assert kernel._table is None  # nothing cached, so it builds properly later


def test_magics_still_complete(kernel: RedisKernel) -> None:
    assert kernel.do_complete("%con", 4)["matches"] == ["%config", "%connect"]
