"""Unit tests for ``%load``.

Two layers, matching the two implementations: ``parse_load``/``_load`` in
``magics.py`` (plain text, no kernel, exercised through ``handle_magic``), and
the kernel's own expansion of ``%load`` inline into the cell (rich rendering,
blocking refusal, per-line error handling -- the same path a typed command
gets). No server: every test stubs ``RedisSession.execute``.
"""

from __future__ import annotations

from typing import Any

import pytest
from redis.exceptions import ResponseError

from noteredis.client import RedisSession
from noteredis.formatter import Status, format_reply
from noteredis.kernel import RedisKernel
from noteredis.magics import MagicError, handle_magic, parse_load, read_command_file

# --------------------------------------------------------------------------- #
# parse_load
# --------------------------------------------------------------------------- #


def test_parse_load_takes_a_bare_path() -> None:
    request = parse_load(["demo.redis"])
    assert request.path == "demo.redis"
    assert request.quiet is False


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
def test_parse_load_quiet_flag(flag: str) -> None:
    assert parse_load(["demo.redis", flag]).quiet is True


def test_parse_load_expands_variables(monkeypatch: Any) -> None:
    monkeypatch.setenv("DEMO_DIR", "/demos")
    assert parse_load(["${DEMO_DIR}/setup.redis"]).path == "/demos/setup.redis"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "expected a file path"),
        (["--wat"], "unknown option '--wat'"),
        (["a.redis", "b.redis"], "unexpected argument 'b.redis'"),
    ],
)
def test_parse_load_rejects_bad_input(args: list[str], message: str) -> None:
    with pytest.raises(MagicError, match=message):
        parse_load(args)


# --------------------------------------------------------------------------- #
# read_command_file
# --------------------------------------------------------------------------- #


def test_read_command_file_returns_the_text(tmp_path: Any) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("SET a 1\n")
    assert read_command_file(str(path)) == "SET a 1\n"


def test_read_command_file_missing_is_a_magic_error(tmp_path: Any) -> None:
    missing = tmp_path / "nope.redis"
    with pytest.raises(MagicError, match=r"%load:.*nope\.redis"):
        read_command_file(str(missing))


# --------------------------------------------------------------------------- #
# _load, the plain-text implementation (no kernel)
# --------------------------------------------------------------------------- #


def _session_recording(calls: list[list[str]], reply: Any = None) -> RedisSession:
    reply = Status(b"OK") if reply is None else reply
    session = RedisSession()
    session.execute = lambda args: (calls.append(args), reply)[1]  # type: ignore[method-assign]
    return session


def test_load_runs_every_command_in_order(tmp_path: Any) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("# comment\nSET a 1\n\nSET b 2\n")
    calls: list[list[str]] = []
    session = _session_recording(calls)

    text = handle_magic(session, ["%load", str(path)])

    assert calls == [["SET", "a", "1"], ["SET", "b", "2"]]
    assert text == "OK\nOK\n"


def test_load_reports_an_error_and_keeps_going(tmp_path: Any) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("SET a 1\nINCR a\nSET b 2\n")
    calls: list[list[str]] = []
    session = RedisSession()

    def execute(args: list[str]) -> Any:
        calls.append(args)
        if args[0] == "INCR":
            raise ResponseError("value is not an integer or out of range")
        return Status(b"OK")

    session.execute = execute  # type: ignore[method-assign]
    text = handle_magic(session, ["%load", str(path)])

    assert calls == [["SET", "a", "1"], ["INCR", "a"], ["SET", "b", "2"]]
    assert text == "OK\n(error) value is not an integer or out of range\nOK\n"


def test_load_quiet_suppresses_success_but_not_errors(tmp_path: Any) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("SET a 1\nINCR a\n")

    def execute(args: list[str]) -> Any:
        if args[0] == "INCR":
            raise ResponseError("value is not an integer or out of range")
        return Status(b"OK")

    session = RedisSession()
    session.execute = execute  # type: ignore[method-assign]
    text = handle_magic(session, ["%load", str(path), "--quiet"])

    assert text == "(error) value is not an integer or out of range\n"


def test_load_refuses_a_blocking_command_in_the_file(tmp_path: Any) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("SUBSCRIBE news\nGET after\n")
    calls: list[list[str]] = []
    session = _session_recording(calls)

    text = handle_magic(session, ["%load", str(path)])

    assert calls == [["GET", "after"]]  # SUBSCRIBE never reached the server
    assert text.startswith("(error) SUBSCRIBE never returns")


def test_load_rejects_a_nested_load(tmp_path: Any) -> None:
    inner = tmp_path / "inner.redis"
    inner.write_text("SET a 1\n")
    outer = tmp_path / "outer.redis"
    outer.write_text(f"%load {inner}\n")

    text = handle_magic(RedisSession(), ["%load", str(outer)])
    assert "nested %load is not supported" in text


def test_load_runs_other_magics_in_the_file(tmp_path: Any) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("%select 2\n")
    calls: list[list[str]] = []
    session = _session_recording(calls)

    text = handle_magic(session, ["%load", str(path)])

    assert calls == [["SELECT", "2"]]
    assert text == "OK\n"


def test_load_is_listed_and_documented() -> None:
    assert "%load" in handle_magic(RedisSession(), ["%help"])
    doc = handle_magic(RedisSession(), ["%help", "load"])
    assert doc.startswith("%load <path>")
    assert "--quiet" in doc


# --------------------------------------------------------------------------- #
# %load inside the kernel: rich rendering, same as a typed command
# --------------------------------------------------------------------------- #

HASH = [b"name", b"Ada Lovelace"]


class _CannedSession(RedisSession):
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


def test_a_loaded_command_gets_rich_rendering_like_a_typed_one(
    kernel: RedisKernel, tmp_path: Any
) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("HGETALL user:1\n")

    ((msg_type, content),) = _messages(kernel, f"%load {path}")

    assert msg_type == "display_data"
    assert set(content["data"]) == {"text/plain", "text/html"}
    assert "<table>" in content["data"]["text/html"]


def test_the_cell_keeps_going_after_a_load(kernel: RedisKernel, tmp_path: Any) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("HGETALL user:1\n")

    messages = _messages(kernel, f"%load {path}\nHGETALL user:2")

    assert len(messages) == 2
    assert all(msg_type == "display_data" for msg_type, _ in messages)


def test_load_quiet_emits_nothing_for_a_successful_command(
    kernel: RedisKernel, tmp_path: Any
) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("HGETALL user:1\n")

    assert _messages(kernel, f"%load {path} --quiet") == []


def test_load_quiet_still_reports_an_error(kernel: RedisKernel, tmp_path: Any) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("HGETALL user:1\nINCR user:1\n")
    kernel.redis.execute = lambda args: (  # type: ignore[method-assign]
        HASH if args[0] == "HGETALL" else (_ for _ in ()).throw(ResponseError("boom"))
    )

    out: list[str] = []
    kernel._emit = lambda silent, text: out.append(text)  # type: ignore[method-assign]
    kernel.do_execute(f"%load {path} --quiet", silent=False)

    assert "".join(out) == "(error) boom\n"


def test_a_blocking_command_in_a_loaded_file_never_reaches_the_server(
    kernel: RedisKernel, tmp_path: Any
) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("SUBSCRIBE news\nGET after\n")
    calls: list[list[str]] = []
    kernel.redis.execute = lambda args: calls.append(args) or HASH  # type: ignore[method-assign]

    out: list[str] = []
    kernel._emit = lambda silent, text: out.append(text)  # type: ignore[method-assign]
    status = kernel.do_execute(f"%load {path}", silent=False)["status"]

    assert calls == [["GET", "after"]]
    assert "".join(out).startswith("(error) SUBSCRIBE never returns")
    assert status == "ok"


def test_a_missing_file_is_reported_and_the_cell_continues(
    kernel: RedisKernel, tmp_path: Any
) -> None:
    missing = tmp_path / "nope.redis"
    calls: list[list[str]] = []
    kernel.redis.execute = lambda args: calls.append(args) or HASH  # type: ignore[method-assign]

    out: list[str] = []
    kernel._emit = lambda silent, text: out.append(text)  # type: ignore[method-assign]
    kernel.do_execute(f"%load {missing}\nGET after", silent=False)

    assert calls == [["GET", "after"]]
    assert "".join(out).startswith("(error) %load:")


def test_a_connection_failure_inside_a_load_stops_the_whole_cell(
    kernel: RedisKernel, tmp_path: Any
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    path = tmp_path / "demo.redis"
    path.write_text("SET a 1\nSET b 2\n")
    calls: list[list[str]] = []

    def execute(args: list[str]) -> Any:
        calls.append(args)
        raise RedisConnectionError("gone")

    kernel.redis.execute = execute  # type: ignore[method-assign]
    result = kernel.do_execute(f"%load {path}\nGET after", silent=False)

    assert calls == [["SET", "a", "1"]]  # neither the rest of the file nor the next line ran
    assert result["status"] == "error"


def test_load_text_plain_matches_a_typed_command(kernel: RedisKernel, tmp_path: Any) -> None:
    path = tmp_path / "demo.redis"
    path.write_text("HGETALL user:1\n")
    ((_, content),) = _messages(kernel, f"%load {path}")
    assert content["data"]["text/plain"] == format_reply(HASH)
