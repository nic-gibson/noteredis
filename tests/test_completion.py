"""Unit tests for key-name completion.

No server: the command table is fed canned ``COMMAND INFO`` replies and the
session is handed a stand-in client serving canned ``SCAN`` pages. Reply shapes
are the raw ones, since the kernel clears redis-py's response callbacks.
"""

from __future__ import annotations

from typing import Any

import pytest

from redis_kernel.client import RedisSession, completable_key, escape_glob
from redis_kernel.commands import CommandInfo, CommandTable, KeySpec, load_key_spec

# --------------------------------------------------------------------------- #
# Where the keys are
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("spec", "index", "expected"),
    [
        # GET key
        (KeySpec(1, 1, 1), 1, True),
        (KeySpec(1, 1, 1), 2, False),
        # MSET k v k v -- every other argument, to the end of the command
        (KeySpec(1, -1, 2), 1, True),
        (KeySpec(1, -1, 2), 2, False),
        (KeySpec(1, -1, 2), 3, True),
        (KeySpec(1, -1, 2), 99, True),
        # OBJECT ENCODING key -- COMMAND INFO counts the container
        (KeySpec(2, 2, 1), 1, False),
        (KeySpec(2, 2, 1), 2, True),
        # PING, EVAL and friends: no positional keys at all
        (KeySpec(0, 0, 0), 1, False),
        # A nonsense step must not divide by zero or match everything
        (KeySpec(1, -1, 0), 1, False),
    ],
)
def test_key_spec_covers(spec: KeySpec, index: int, expected: bool) -> None:
    assert spec.covers(index) is expected


def test_load_key_spec_parses_command_info() -> None:
    calls: list[list[str]] = []

    def execute(args: list[str]) -> Any:
        calls.append(args)
        return [[b"get", 2, [b"readonly", b"fast"], 1, 1, 1, [], [], [], []]]

    assert load_key_spec(execute, "GET") == KeySpec(first=1, last=1, step=1)
    assert calls == [["COMMAND", "INFO", "get"]]


def test_load_key_spec_asks_for_subcommands_the_way_redis_names_them() -> None:
    calls: list[list[str]] = []

    def execute(args: list[str]) -> Any:
        calls.append(args)
        return [[b"object|encoding", 3, [b"readonly"], 2, 2, 1, [], [], [], []]]

    assert load_key_spec(execute, "OBJECT ENCODING") == KeySpec(first=2, last=2, step=1)
    assert calls == [["COMMAND", "INFO", "object|encoding"]]


def test_load_key_spec_tolerates_a_nil_answer() -> None:
    assert load_key_spec(lambda args: [None], "NOSUCHCOMMAND") == KeySpec()


# --------------------------------------------------------------------------- #
# The table's view of it
# --------------------------------------------------------------------------- #


def _table(execute: Any = None, **specs: KeySpec) -> CommandTable:
    commands = {
        "GET": CommandInfo(name="GET"),
        "MSET": CommandInfo(name="MSET"),
        "OBJECT": CommandInfo(
            name="OBJECT", subcommands={"ENCODING": CommandInfo(name="OBJECT ENCODING")}
        ),
    }
    return CommandTable(commands, key_specs=dict(specs), execute=execute)


def test_completes_key_uses_the_position_being_typed() -> None:
    table = _table(GET=KeySpec(1, 1, 1), MSET=KeySpec(1, -1, 2))
    assert table.completes_key(["GET"], 1) is True
    assert table.completes_key(["GET", "foo"], 2) is False
    assert table.completes_key(["MSET", "a", "1"], 3) is True


def test_completes_key_resolves_subcommands() -> None:
    table = _table()
    table.key_specs["OBJECT ENCODING"] = KeySpec(2, 2, 1)
    assert table.completes_key(["OBJECT", "ENCODING"], 2) is True


def test_completes_key_is_false_for_unknown_commands() -> None:
    assert _table().completes_key(["NOSUCHCOMMAND"], 1) is False


def test_key_spec_is_fetched_once_and_cached() -> None:
    calls: list[list[str]] = []

    def execute(args: list[str]) -> Any:
        calls.append(args)
        return [[b"get", 2, [b"readonly"], 1, 1, 1, [], [], [], []]]

    table = _table(execute)
    assert table.completes_key(["GET"], 1) is True
    assert table.completes_key(["GET"], 1) is True
    assert len(calls) == 1


def test_a_failing_lookup_is_cached_too() -> None:
    calls: list[list[str]] = []

    def execute(args: list[str]) -> Any:
        calls.append(args)
        raise RuntimeError("no COMMAND INFO here")

    table = _table(execute)
    assert table.completes_key(["GET"], 1) is False
    assert table.completes_key(["GET"], 1) is False
    assert len(calls) == 1


def test_an_unbound_table_completes_no_keys() -> None:
    assert _table().completes_key(["GET"], 1) is False


# --------------------------------------------------------------------------- #
# Patterns and insertable keys
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("prefix", "pattern"),
    [
        ("user:", "user:"),
        ("user:*", "user:\\*"),
        ("a?b", "a\\?b"),
        ("[set]", "\\[set\\]"),
        ("back\\slash", "back\\\\slash"),
        ("", ""),
    ],
)
def test_escape_glob(prefix: str, pattern: str) -> None:
    assert escape_glob(prefix) == pattern


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"user:1", "user:1"),
        ("user:1", "user:1"),
        ("naïve:key".encode(), "naïve:key"),
        (b"\xac\xed\x00", None),  # not UTF-8
        (b"two words", None),  # would tokenise as two arguments
        (b'say"what', None),
        (b"back\\slash", None),
        (b"tab\there", None),
        (b"bell\x07", None),
        (b"", None),
        (None, None),
        (42, None),
    ],
)
def test_completable_key(raw: Any, expected: str | None) -> None:
    assert completable_key(raw) == expected


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #


class FakeServer:
    """Serves canned ``SCAN`` pages, keyed by the cursor asked for."""

    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        self.calls: list[tuple[Any, ...]] = []

    def execute_command(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        assert args[0] == "SCAN"
        return self.pages.get(str(args[1]), [b"0", []])


class FakeCluster:
    """A cluster client: one keyspace per primary, each with its own cursor."""

    def __init__(self, per_node: dict[str, dict[str, Any]]) -> None:
        self.per_node = per_node
        self.calls: list[tuple[str | None, Any]] = []

    def get_primaries(self) -> list[str]:
        return list(self.per_node)

    def execute_command(self, *args: Any, target_nodes: Any = None) -> Any:
        self.calls.append((target_nodes, args[1]))
        pages = self.per_node.get(target_nodes, {})
        return pages.get(str(args[1]), [b"0", []])


def _session(client: Any) -> RedisSession:
    session = RedisSession()
    session._client = client
    return session


def test_scan_keys_follows_the_cursor_to_the_end() -> None:
    server = FakeServer(
        {
            "0": [b"17", [b"user:2"]],
            "17": [b"5", []],
            "5": [b"0", [b"user:1"]],
        }
    )
    assert _session(server).scan_keys("user:") == ["user:1", "user:2"]
    assert [call[1] for call in server.calls] == ["0", "17", "5"]


def test_scan_keys_escapes_the_prefix_into_the_pattern() -> None:
    server = FakeServer({"0": [b"0", []]})
    _session(server).scan_keys("we*rd")
    assert server.calls[0] == ("SCAN", "0", "MATCH", "we\\*rd*", "COUNT", "100")


def test_scan_keys_stops_at_the_call_budget() -> None:
    class NeverEnding(FakeServer):
        """A cursor that never comes home: unbudgeted, this walks forever."""

        def execute_command(self, *args: Any, **kwargs: Any) -> Any:
            self.calls.append(args)
            return [b"9", []]

    server = NeverEnding({})
    assert _session(server).scan_keys("k", max_calls=3) == []
    assert len(server.calls) == 3


def test_scan_keys_stops_once_it_has_enough_matches() -> None:
    server = FakeServer({"0": [b"7", [b"k1", b"k2", b"k3"]], "7": [b"8", [b"k4"]]})
    assert _session(server).scan_keys("k", limit=2) == ["k1", "k2"]
    assert len(server.calls) == 1


def test_scan_keys_drops_keys_it_cannot_insert() -> None:
    server = FakeServer({"0": [b"0", [b"ok:1", b"has space", b"\xff\xfe", b"ok:2"]]})
    assert _session(server).scan_keys("") == ["ok:1", "ok:2"]


def test_scan_keys_dedupes_across_pages() -> None:
    server = FakeServer({"0": [b"4", [b"k1", b"k1"]], "4": [b"0", [b"k1", b"k2"]]})
    assert _session(server).scan_keys("k") == ["k1", "k2"]


def test_scan_keys_is_silent_without_a_connection() -> None:
    assert RedisSession().scan_keys("user:") == []


def test_scan_keys_refuses_inside_a_transaction() -> None:
    server = FakeServer({"0": [b"0", [b"user:1"]]})
    session = _session(server)
    session.in_transaction = True
    assert session.scan_keys("user:") == []
    assert server.calls == []  # nothing was queued into the MULTI


def test_scan_keys_survives_a_malformed_reply() -> None:
    server = FakeServer({"0": [b"nonsense"]})
    assert _session(server).scan_keys("k") == []


def test_scan_keys_visits_every_primary_of_a_cluster() -> None:
    cluster = FakeCluster(
        {
            "node-a": {"0": [b"0", [b"k1"]]},
            "node-b": {"0": [b"6", [b"k2"]], "6": [b"0", [b"k3"]]},
        }
    )
    assert _session(cluster).scan_keys("k") == ["k1", "k2", "k3"]
    assert cluster.calls == [("node-a", "0"), ("node-b", "0"), ("node-b", "6")]


def test_scan_keys_unwraps_a_per_node_cluster_reply() -> None:
    class PerNode(FakeServer):
        def execute_command(self, *args: Any, **kwargs: Any) -> Any:
            self.calls.append(args)
            return {"node-a": [b"0", [b"k1"]]}

    assert _session(PerNode({})).scan_keys("k") == ["k1"]
