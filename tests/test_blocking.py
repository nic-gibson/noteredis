"""Blocking and streaming commands are refused, not run.

Streaming pub/sub, MONITOR and stream tails is a deliberate non-goal, so the
requirement is narrow but firm: a command that would never return has to be
turned away without touching the connection, and the rest of the cell has to
keep running. These tests pin that, because "we chose not to" and "we forgot"
look identical from the outside once the code drifts.
"""

from __future__ import annotations

from typing import Any

import pytest

from noteredis.client import RedisSession, is_blocking
from noteredis.kernel import RedisKernel

# --------------------------------------------------------------------------- #
# What counts as blocking
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "command",
    [
        "SUBSCRIBE news",
        "psubscribe news.*",  # case is not significant
        "SSUBSCRIBE shard",
        "MONITOR",
        "PSYNC ? -1",
        "SYNC",
        # Zero timeout: wait forever.
        "BLPOP queue 0",
        "BRPOP queue 0",
        "BLMOVE src dst LEFT RIGHT 0",
        "BZPOPMIN zset 0",
        "WAIT 1 0",
        "XREAD BLOCK 0 STREAMS s $",
        "XREADGROUP GROUP g c BLOCK 0 STREAMS s >",
    ],
)
def test_these_never_return_on_their_own(command: str) -> None:
    assert is_blocking(command.split()) is True


@pytest.mark.parametrize(
    "command",
    [
        # A real timeout comes back by itself, so it runs as redis-cli would.
        "BLPOP queue 5",
        "BRPOP queue 0.5",
        "WAIT 1 100",
        "XREAD BLOCK 5000 STREAMS s $",
        # No BLOCK argument at all.
        "XREAD COUNT 2 STREAMS s 0",
        # Ordinary commands, including ones whose names start the same way.
        "GET key",
        "PUBLISH news hello",
        "XADD s '*' f v",
        "",
    ],
)
def test_these_are_left_alone(command: str) -> None:
    assert is_blocking(command.split()) is False


def test_a_non_numeric_timeout_is_not_treated_as_blocking() -> None:
    """The server will reject it; guessing 'forever' here would be worse."""
    assert is_blocking(["BLPOP", "queue", "soon"]) is False


# --------------------------------------------------------------------------- #
# What the kernel does with one
# --------------------------------------------------------------------------- #


class _RefusingSession(RedisSession):
    """A session that fails the test if the kernel tries to use it."""

    def execute(self, args: list[str]) -> Any:
        raise AssertionError(f"a blocking command reached the server: {args}")


@pytest.fixture
def kernel() -> RedisKernel:
    kernel = RedisKernel()
    kernel.redis = _RefusingSession()
    return kernel


def _run(kernel: RedisKernel, cell: str) -> tuple[str, str]:
    out: list[str] = []
    kernel._emit = lambda silent, text: out.append(text)  # type: ignore[method-assign]
    status = kernel.do_execute(cell, silent=False)["status"]
    return "".join(out), status


def test_a_blocking_command_is_refused_without_reaching_the_server(
    kernel: RedisKernel,
) -> None:
    text, status = _run(kernel, "SUBSCRIBE news")
    assert text == (
        "(error) SUBSCRIBE never returns on its own and is not supported by "
        "this kernel. Use redis-cli to watch a live feed\n"
    )
    # A refused command is a Redis-level error, not a kernel fault.
    assert status == "ok"


def test_the_rest_of_the_cell_still_runs(kernel: RedisKernel) -> None:
    """Same as any other error: report the line, carry on to the next."""
    replies: list[list[str]] = []
    kernel.redis.execute = lambda args: replies.append(args) or b"v"  # type: ignore[method-assign]

    text, status = _run(kernel, "MONITOR\nGET after\n")

    assert text.startswith("(error) MONITOR never returns")
    assert replies == [["GET", "after"]]
    assert status == "ok"


def test_the_error_names_the_command_that_was_refused(kernel: RedisKernel) -> None:
    text, _ = _run(kernel, "blpop queue 0")
    assert text.startswith("(error) BLPOP never returns")


def test_interrupt_is_acknowledged(kernel: RedisKernel) -> None:
    """Nothing of ours loops, so an interrupt has nothing to stop -- but the
    protocol still expects an answer."""
    assert kernel.do_interrupt() == {"status": "ok"}
