"""Switching between rich and plain output.

The kernel-level half of rendering: which mimebundle actually goes out, that
``text/plain`` is the redis-cli text in either mode, and that a ``%render`` in
one cell cannot change how the next cell looks.
"""

from __future__ import annotations

from typing import Any

import pytest

from redis_kernel.client import RedisSession
from redis_kernel.formatter import format_reply
from redis_kernel.kernel import RedisKernel
from redis_kernel.magics import MagicError, handle_magic

HASH = [b"name", b"Ada Lovelace"]


class _CannedSession(RedisSession):
    """Answers every command with the same hash reply."""

    def execute(self, args: list[str]) -> Any:
        return HASH


@pytest.fixture
def kernel() -> RedisKernel:
    kernel = RedisKernel()
    kernel.redis = _CannedSession()
    kernel.sent = []  # type: ignore[attr-defined]
    kernel.send_response = (  # type: ignore[method-assign]
        lambda socket, msg_type, content: kernel.sent.append((msg_type, content))
    )
    return kernel


def _messages(kernel: RedisKernel, cell: str) -> list[tuple[str, dict[str, Any]]]:
    kernel.sent.clear()  # type: ignore[attr-defined]
    kernel.do_execute(cell, silent=False)
    return kernel.sent  # type: ignore[attr-defined,no-any-return]


# --------------------------------------------------------------------------- #
# What goes on the wire
# --------------------------------------------------------------------------- #


def test_rich_is_the_default(kernel: RedisKernel) -> None:
    assert kernel.redis.render_mode_now == "rich"


def test_rich_mode_sends_a_mimebundle(kernel: RedisKernel) -> None:
    ((msg_type, content),) = _messages(kernel, "HGETALL user:1")
    assert msg_type == "display_data"
    assert set(content["data"]) == {"text/plain", "text/html"}
    assert "<table>" in content["data"]["text/html"]


def test_plain_mode_sends_only_the_text(kernel: RedisKernel) -> None:
    handle_magic(kernel.redis, ["%config", "render", "plain"])
    ((msg_type, content),) = _messages(kernel, "HGETALL user:1")
    # A stream message, so consecutive commands stay in one block of output.
    assert msg_type == "stream"
    assert content["text"] == format_reply(HASH)


@pytest.mark.parametrize("mode", ["rich", "plain"])
def test_text_plain_is_the_redis_cli_text_in_either_mode(kernel: RedisKernel, mode: str) -> None:
    """The invariant renderers exist under: rich output only ever adds."""
    handle_magic(kernel.redis, ["%config", "render", mode])
    ((msg_type, content),) = _messages(kernel, "HGETALL user:1")
    text = content["text"] if msg_type == "stream" else content["data"]["text/plain"]
    assert text == format_reply(HASH)


def test_plain_mode_does_not_even_consult_the_renderers(kernel: RedisKernel) -> None:
    """Not merely hidden: nothing rich should reach the saved notebook."""
    handle_magic(kernel.redis, ["%config", "render", "plain"])
    called = []
    import redis_kernel.kernel as kernel_module

    original = kernel_module.render
    kernel_module.render = lambda args, reply: called.append(args)  # type: ignore[assignment]
    try:
        _messages(kernel, "HGETALL user:1")
    finally:
        kernel_module.render = original  # type: ignore[assignment]
    assert called == []


# --------------------------------------------------------------------------- #
# The one-cell override
# --------------------------------------------------------------------------- #


def test_render_overrides_the_rest_of_the_cell(kernel: RedisKernel) -> None:
    handle_magic(kernel.redis, ["%config", "render", "plain"])
    messages = _messages(kernel, "HGETALL a\n%render rich\nHGETALL b")
    types = [msg_type for msg_type, _ in messages]
    # plain reply, the magic's own output, then a rich reply
    assert types == ["stream", "stream", "display_data"]


def test_the_override_does_not_leak_into_the_next_cell(kernel: RedisKernel) -> None:
    _messages(kernel, "%render plain\nHGETALL a")
    assert kernel.redis.render_override is None
    ((msg_type, _),) = _messages(kernel, "HGETALL b")
    assert msg_type == "display_data"  # back to the session setting


def test_the_override_is_cleared_even_when_the_cell_fails(kernel: RedisKernel) -> None:
    kernel.redis.autoconnect = False
    kernel.redis.execute = RedisSession.execute.__get__(kernel.redis)  # real one: refuses
    _messages(kernel, "%render plain\nGET a")
    assert kernel.redis.render_override is None


def test_the_session_setting_survives_a_cell(kernel: RedisKernel) -> None:
    _messages(kernel, "%config render plain")
    assert kernel.redis.render_mode == "plain"
    ((msg_type, _),) = _messages(kernel, "HGETALL a")
    assert msg_type == "stream"


# --------------------------------------------------------------------------- #
# The magics themselves
# --------------------------------------------------------------------------- #


def test_render_with_no_argument_reports_the_mode() -> None:
    session = RedisSession()
    assert handle_magic(session, ["%render"]) == "rich\n"
    handle_magic(session, ["%render", "plain"])
    assert handle_magic(session, ["%render"]) == "plain\n"


def test_config_lists_the_render_setting() -> None:
    assert "render = rich" in handle_magic(RedisSession(), ["%config"])


@pytest.mark.parametrize("magic", [["%config", "render", "fancy"], ["%render", "fancy"]])
def test_an_unknown_mode_is_refused(magic: list[str]) -> None:
    with pytest.raises(MagicError, match="expected one of rich, plain"):
        handle_magic(RedisSession(), magic)


def test_render_takes_a_single_mode() -> None:
    with pytest.raises(MagicError, match="a single mode"):
        handle_magic(RedisSession(), ["%render", "rich", "plain"])


def test_help_documents_render() -> None:
    text = handle_magic(RedisSession(), ["%help"])
    assert "%render" in text
