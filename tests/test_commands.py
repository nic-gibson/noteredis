"""How ``COMMAND DOCS`` argument specs become the Shift-Tab syntax line.

Table-driven against synthetic ``COMMAND DOCS`` replies shaped the way Redis
actually sends them (nested maps, byte-string keys) -- no server needed.
"""

from __future__ import annotations

from typing import Any

import pytest

from noteredis.commands import CommandInfo, load_command_table


def _pure(name: str, optional: bool = False) -> dict[bytes, Any]:
    fields: dict[bytes, Any] = {
        b"type": b"pure-token",
        b"token": name.encode(),
        b"name": name.lower().encode(),
    }
    if optional:
        fields[b"flags"] = [b"OPTIONAL"]
    return fields


def _token_arg(token: str, name: str, type_: bytes = b"integer") -> dict[bytes, Any]:
    return {b"type": type_, b"token": token.encode(), b"name": name.encode()}


def _oneof(*args: dict[bytes, Any], optional: bool = False) -> dict[bytes, Any]:
    fields: dict[bytes, Any] = {b"type": b"oneof", b"arguments": list(args)}
    if optional:
        fields[b"flags"] = [b"OPTIONAL"]
    return fields


def _table_from(name: str, arguments: list[dict[bytes, Any]] | None = None) -> CommandInfo:
    def execute(args: list[str]) -> Any:
        assert args == ["COMMAND", "DOCS"]
        doc_fields: dict[bytes, Any] = {}
        if arguments is not None:
            doc_fields[b"arguments"] = arguments
        return {name.encode(): doc_fields}

    table = load_command_table(execute)
    info = table.lookup([name])
    assert info is not None
    return info


def test_plain_arguments_are_space_joined() -> None:
    info = _table_from(
        "GET",
        arguments=[{b"type": b"key", b"name": b"key"}],
    )
    assert info.syntax == "GET key"


def test_optional_argument_is_bracketed() -> None:
    info = _table_from(
        "EXPIRE",
        arguments=[
            {b"type": b"key", b"name": b"key"},
            {b"type": b"integer", b"name": b"seconds"},
            _oneof(_pure("NX"), _pure("XX"), _pure("GT"), _pure("LT"), optional=True),
        ],
    )
    assert info.syntax == "EXPIRE key seconds [NX | XX | GT | LT]"


def test_token_and_name_are_paired() -> None:
    info = _table_from(
        "GETEX",
        arguments=[
            {b"type": b"key", b"name": b"key"},
            _oneof(
                _token_arg("EX", "seconds"),
                _token_arg("PX", "milliseconds"),
                _pure("PERSIST"),
                optional=True,
            ),
        ],
    )
    assert info.syntax == "GETEX key [EX seconds | PX milliseconds | PERSIST]"


def test_multiple_flag_appends_ellipsis() -> None:
    info = _table_from(
        "MSET",
        arguments=[
            {
                b"type": b"block",
                b"flags": [b"MULTIPLE"],
                b"arguments": [
                    {b"type": b"key", b"name": b"key"},
                    {b"type": b"string", b"name": b"value"},
                ],
            }
        ],
    )
    assert info.syntax == "MSET key value ..."


def test_set_matches_redis_io_syntax() -> None:
    """The canonical example: ``SET``'s syntax box on redis.io."""
    info = _table_from(
        "SET",
        arguments=[
            {b"type": b"key", b"name": b"key"},
            {b"type": b"string", b"name": b"value"},
            _oneof(_pure("NX"), _pure("XX"), optional=True),
            _pure("GET", optional=True),
            _oneof(
                _token_arg("EX", "seconds"),
                _token_arg("PX", "milliseconds"),
                _token_arg("EXAT", "unix-time-seconds"),
                _token_arg("PXAT", "unix-time-milliseconds"),
                _pure("KEEPTTL"),
                optional=True,
            ),
        ],
    )
    assert info.syntax == (
        "SET key value [NX | XX] [GET] "
        "[EX seconds | PX milliseconds | EXAT unix-time-seconds | "
        "PXAT unix-time-milliseconds | KEEPTTL]"
    )


def test_subcommand_syntax_carries_the_parent_name() -> None:
    def execute(args: list[str]) -> Any:
        return {
            b"OBJECT": {
                b"subcommands": {
                    b"object|encoding": {
                        b"arguments": [{b"type": b"key", b"name": b"key"}],
                    }
                }
            }
        }

    table = load_command_table(execute)
    info = table.lookup(["OBJECT", "ENCODING"])
    assert info is not None
    assert info.syntax == "OBJECT ENCODING key"


def test_command_with_no_arguments_has_no_argument_text() -> None:
    info = _table_from("DBSIZE")
    assert info.syntax == "DBSIZE"


@pytest.mark.parametrize(
    ("info", "expected_first_line"),
    [
        (CommandInfo(name="GET"), "GET"),
        (CommandInfo(name="SET", syntax="SET key value [NX | XX]"), "SET key value [NX | XX]"),
    ],
)
def test_render_leads_with_syntax_when_available(
    info: CommandInfo, expected_first_line: str
) -> None:
    assert info.render().splitlines()[0] == expected_first_line


def test_render_still_shows_summary_and_details_after_syntax() -> None:
    info = CommandInfo(
        name="SET",
        syntax="SET key value [NX | XX]",
        summary="Set the string value of a key.",
        since="1.0.0",
        arity=3,
    )
    rendered = info.render()
    assert rendered.splitlines() == [
        "SET key value [NX | XX]",
        "",
        "Set the string value of a key.",
        "",
        "since  1.0.0",
        "arity  3",
    ]
