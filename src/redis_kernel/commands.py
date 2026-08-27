"""The command table behind completion and inspection.

Built once from ``COMMAND DOCS``, falling back to ``COMMAND INFO`` on servers
that predate it. One call powers both ``do_complete`` and ``do_inspect``, so
neither has to ship a hardcoded command list that would go stale.

No kernel imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["CommandInfo", "CommandTable", "KeySpec", "load_command_table", "load_key_spec"]


@dataclass(frozen=True)
class KeySpec:
    """Where a command's key arguments sit, as ``COMMAND INFO`` reports them.

    Positions index the whole command, including its name: ``GET key`` has
    ``first=1``, and ``OBJECT ENCODING key`` -- which ``COMMAND INFO`` counts
    from the container -- has ``first=2``. ``first=0`` means the command takes
    no keys.

    ``last`` may be negative, counting back from the end of the command. While
    someone is still typing there is no end yet, so a negative ``last`` is
    treated as unbounded.
    """

    first: int = 0
    last: int = 0
    step: int = 1

    def covers(self, index: int) -> bool:
        """Is argument ``index`` a key name?"""
        if self.first <= 0 or self.step <= 0 or index < self.first:
            return False
        if self.last >= 0 and index > self.last:
            return False
        return (index - self.first) % self.step == 0


@dataclass
class CommandInfo:
    """What we know about one command, for Shift-Tab and completion."""

    name: str
    summary: str = ""
    since: str = ""
    group: str = ""
    complexity: str = ""
    arity: int = 0
    flags: list[str] = field(default_factory=list)
    arguments: list[str] = field(default_factory=list)
    subcommands: dict[str, CommandInfo] = field(default_factory=dict)

    def render(self) -> str:
        """The Shift-Tab text for this command."""
        lines = [self.name]
        if self.summary:
            lines.append("")
            lines.append(self.summary)
        details: list[tuple[str, str]] = []
        if self.since:
            details.append(("since", self.since))
        if self.arity:
            details.append(("arity", str(self.arity)))
        if self.group:
            details.append(("group", self.group))
        if self.complexity:
            details.append(("complexity", self.complexity))
        if self.flags:
            details.append(("flags", " ".join(self.flags)))
        if self.subcommands:
            details.append(("subcommands", " ".join(sorted(self.subcommands))))
        if details:
            lines.append("")
            width = max(len(label) for label, _ in details)
            lines.extend(f"{label:<{width}}  {value}" for label, value in details)
        return "\n".join(lines) + "\n"


class CommandTable:
    """Command metadata, keyed by upper-cased command name."""

    def __init__(
        self,
        commands: dict[str, CommandInfo] | None = None,
        key_specs: dict[str, KeySpec] | None = None,
        execute: Any = None,
    ) -> None:
        self.commands: dict[str, CommandInfo] = commands or {}
        #: Key positions by full command name, filled on demand. Looking one up
        #: costs a ``COMMAND INFO`` round trip and only matters when key
        #: completion is switched on, so it is not part of building the table.
        self.key_specs: dict[str, KeySpec] = key_specs or {}
        self._execute = execute

    def bind(self, execute: Any) -> None:
        """Attach the ``execute(args) -> reply`` used to fetch key specs later."""
        self._execute = execute

    def __bool__(self) -> bool:
        return bool(self.commands)

    def __len__(self) -> int:
        return len(self.commands)

    def lookup(self, args: list[str]) -> CommandInfo | None:
        """Find the command (or subcommand) that ``args`` names."""
        if not args:
            return None
        info = self.commands.get(args[0].upper())
        if info is None:
            return None
        if len(args) > 1:
            sub = info.subcommands.get(args[1].upper())
            if sub is not None:
                return sub
        return info

    def complete_names(self, prefix: str) -> list[str]:
        """Command names starting with ``prefix``, matched case-insensitively.

        Completions keep the case the user was typing, so a lower-case ``ge``
        completes to ``get`` rather than jumping to ``GET``.
        """
        upper = prefix.upper()
        matches = sorted(name for name in self.commands if name.startswith(upper))
        return [_match_case(prefix, name) for name in matches]

    def complete_subcommands(self, command: str, prefix: str) -> list[str]:
        info = self.commands.get(command.upper())
        if info is None:
            return []
        upper = prefix.upper()
        matches = sorted(name for name in info.subcommands if name.startswith(upper))
        return [_match_case(prefix, name) for name in matches]

    def complete_arguments(self, args: list[str], prefix: str) -> list[str]:
        """Known argument tokens for the command in ``args`` (``MATCH``, ``COUNT``...)."""
        info = self.lookup(args)
        if info is None:
            return []
        upper = prefix.upper()
        matches = sorted({tok for tok in info.arguments if tok.startswith(upper)})
        return [_match_case(prefix, name) for name in matches]

    def completes_key(self, args: list[str], index: int) -> bool:
        """Is argument ``index`` of the command in ``args`` a key name?

        ``args`` is what has been typed so far, so ``index`` is normally
        ``len(args)`` -- the position of the token being completed.
        """
        info = self.lookup(args)
        if info is None:
            return False
        return self.key_spec(info.name).covers(index)

    def key_spec(self, name: str) -> KeySpec:
        """Key positions for ``name``, fetching and caching them on first ask.

        An empty spec is cached on failure too: a server that cannot answer
        ``COMMAND INFO`` will not answer it any better on the next Tab.
        """
        cached = self.key_specs.get(name)
        if cached is not None:
            return cached
        if self._execute is None:
            return KeySpec()
        try:
            spec = load_key_spec(self._execute, name)
        except Exception:
            spec = KeySpec()
        self.key_specs[name] = spec
        return spec


def _match_case(prefix: str, completion: str) -> str:
    """Return ``completion`` cased to match how the user is typing ``prefix``."""
    if prefix and not prefix.isupper():
        return completion.lower()
    return completion


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_command_table(execute: Any) -> CommandTable:
    """Build the table using ``execute(args) -> reply``.

    ``execute`` is passed in rather than a session so this stays testable with
    a canned reply and usable from the magic package later.
    """
    for loader in (_load_docs, _load_info):
        try:
            table = loader(execute)
        except Exception:
            continue
        if table:
            # Key positions are fetched lazily, so the table needs a way back
            # to the server after it has been built.
            table.bind(execute)
            return table
    return CommandTable(execute=execute)


def _load_docs(execute: Any) -> CommandTable:
    """Parse ``COMMAND DOCS``, a nested map of everything we want."""
    reply = execute(["COMMAND", "DOCS"])
    pairs = _map_pairs(reply)
    commands: dict[str, CommandInfo] = {}
    for name, spec in pairs:
        info = _command_from_docs(_text(name), spec)
        commands[info.name] = info
    return CommandTable(commands)


def _command_from_docs(name: str, spec: Any, parent: str = "") -> CommandInfo:
    fields = dict(_map_pairs(spec))
    full_name = f"{parent} {name}".strip().upper() if parent else name.upper()
    info = CommandInfo(
        name=full_name,
        summary=_text(fields.get(b"summary", b"")),
        since=_text(fields.get(b"since", b"")),
        group=_text(fields.get(b"group", b"")),
        complexity=_text(fields.get(b"complexity", b"")),
        arity=_int(fields.get(b"arity")),
    )
    info.arguments = _argument_tokens(fields.get(b"arguments"))
    for sub_name, sub_spec in _map_pairs(fields.get(b"subcommands")):
        # COMMAND DOCS names subcommands "parent|child".
        child = _text(sub_name).split("|")[-1]
        info.subcommands[child.upper()] = _command_from_docs(child, sub_spec, parent=full_name)
    return info


def _argument_tokens(arguments: Any) -> list[str]:
    """Collect literal tokens (``MATCH``, ``COUNT``, ``NX``) from an argument spec."""
    tokens: list[str] = []
    for argument in _iter(arguments):
        fields = dict(_map_pairs(argument))
        token = _text(fields.get(b"token", b""))
        if token:
            tokens.append(token.upper())
        # Containers (oneof/block) nest their own arguments.
        tokens.extend(_argument_tokens(fields.get(b"arguments")))
    return tokens


def _load_info(execute: Any) -> CommandTable:
    """Fall back to ``COMMAND INFO``: names, arity and flags, but no summaries.

    The same reply carries key positions, so they are kept as well -- the whole
    keyspace of commands for the price of the call already being made.
    """
    reply = execute(["COMMAND"])
    commands: dict[str, CommandInfo] = {}
    key_specs: dict[str, KeySpec] = {}
    for entry in _iter(reply):
        fields = list(_iter(entry))
        if len(fields) < 3:
            continue
        name = _text(fields[0]).upper()
        commands[name] = CommandInfo(
            name=name,
            arity=_int(fields[1]),
            flags=[_text(flag) for flag in _iter(fields[2])],
        )
        if len(fields) >= 6:
            key_specs[name] = KeySpec(
                first=_int(fields[3]), last=_int(fields[4]), step=_int(fields[5])
            )
    return CommandTable(commands, key_specs)


def load_key_spec(execute: Any, name: str) -> KeySpec:
    """Ask ``COMMAND INFO`` where ``name``'s keys are.

    Subcommands are asked for the way ``COMMAND INFO`` names them, so
    ``OBJECT ENCODING`` goes out as ``object|encoding``. A nil answer -- an
    unknown command, or a server too old to describe subcommands separately --
    yields an empty spec, which completes nothing.
    """
    token = name.strip().replace(" ", "|").lower()
    entries = _iter(execute(["COMMAND", "INFO", token]))
    fields = _iter(entries[0]) if entries else []
    if len(fields) < 6:
        return KeySpec()
    return KeySpec(first=_int(fields[3]), last=_int(fields[4]), step=_int(fields[5]))


# --------------------------------------------------------------------------- #
# Reply shape helpers -- replies differ between RESP2 (flat arrays) and RESP3
# (maps), so normalise both here rather than in every caller.
# --------------------------------------------------------------------------- #


def _map_pairs(reply: Any) -> list[tuple[Any, Any]]:
    """Read a reply as key/value pairs, whether it arrived as a map or an array."""
    if reply is None:
        return []
    pairs = getattr(reply, "pairs", None)
    if pairs is not None:
        return list(pairs)
    if isinstance(reply, dict):
        return list(reply.items())
    if isinstance(reply, (list, tuple)):
        if len(reply) % 2 != 0:
            return []
        return [(reply[i], reply[i + 1]) for i in range(0, len(reply), 2)]
    return []


def _iter(reply: Any) -> list[Any]:
    if reply is None:
        return []
    if isinstance(reply, (list, tuple, set, frozenset)):
        return list(reply)
    return []


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
