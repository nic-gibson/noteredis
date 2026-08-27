"""Connection handling and session state.

Must stay importable without ``ipykernel`` so a ``%%redis`` cell magic could
reuse it. Nothing here knows about the Jupyter messaging protocol.

Session state lives on :class:`RedisSession`, not in the connection pool,
because a notebook is one interactive session: ``SELECT``, ``MULTI`` and
``HELLO 3`` must all still apply on the next cell. That is also why the client
pins a single connection -- a pool free to hand out a different socket would
silently lose the selected database and any open transaction.
"""

from __future__ import annotations

import os
import shlex
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any

import redis
from redis.cluster import RedisCluster
from redis.connection import SSLConnection, UnixDomainSocketConnection, parse_url

from .formatter import Verbatim
from .resp import parser_for_protocol

__all__ = [
    "BLOCKING_COMMANDS",
    "PLAIN",
    "RENDER_MODES",
    "RICH",
    "SCAN_COUNT",
    "SCAN_MAX_CALLS",
    "SCAN_MAX_MATCHES",
    "CommandLine",
    "ConnectionSettings",
    "NotConnected",
    "RedisSession",
    "ServerInfo",
    "completable_key",
    "default_settings",
    "default_url",
    "escape_glob",
    "is_blocking",
    "split_cell",
    "split_command",
]


class NotConnected(RuntimeError):
    """A command needed a connection and autoconnect is switched off."""


DEFAULT_URL = "redis://localhost:6379/0"

#: Commands that never return on their own. Streaming them is out of scope, so
#: the kernel refuses them rather than wedging the shell channel until a
#: restart. ``BLPOP`` and friends only block forever when their timeout
#: argument is ``0``; :func:`is_blocking` checks that, and a command with a real
#: timeout is left to run and return on its own.
BLOCKING_COMMANDS = frozenset(
    {
        "SUBSCRIBE",
        "PSUBSCRIBE",
        "SSUBSCRIBE",
        "MONITOR",
        "PSYNC",
        "SYNC",
    }
)

#: Commands whose final argument is a timeout in seconds; ``0`` means forever.
TIMEOUT_COMMANDS = frozenset(
    {
        "BLPOP",
        "BRPOP",
        "BLMOVE",
        "BLMPOP",
        "BRPOPLPUSH",
        "BZPOPMIN",
        "BZPOPMAX",
        "BZMPOP",
        "WAIT",
        "WAITAOF",
    }
)


#: Bounds on key completion. ``SCAN`` is a cursor over the whole keyspace and
#: ``MATCH`` filters buckets after they are read, so one call with a ``COUNT``
#: hint routinely comes back empty even when matching keys exist -- a few
#: rounds are needed to be useful. These three limits are what stops Tab from
#: turning into a full keyspace walk against a production server.
SCAN_COUNT = 100
SCAN_MAX_CALLS = 10
SCAN_MAX_MATCHES = 50

#: Characters ``stringmatchlen`` treats as pattern syntax, and which therefore
#: have to be escaped when a typed prefix is spliced into a ``MATCH`` pattern.
GLOB_SPECIALS = "*?[]\\"

#: Rendering modes. ``rich`` adds representations on top of the redis-cli text;
#: ``plain`` sends the text alone. Named as modes rather than a boolean so
#: redis-cli's own ``--raw`` output could join them later without a rename.
RICH = "rich"
PLAIN = "plain"
RENDER_MODES = (RICH, PLAIN)


def default_url() -> str:
    """The server to talk to unless told otherwise."""
    return os.environ.get("REDIS_URL", DEFAULT_URL)


def escape_glob(text: str) -> str:
    """Quote ``text`` for use as a literal inside a ``MATCH`` pattern."""
    out: list[str] = []
    for char in text:
        if char in GLOB_SPECIALS:
            out.append("\\")
        out.append(char)
    return "".join(out)


def completable_key(raw: Any) -> str | None:
    """A key name that can be inserted into a cell verbatim, or ``None``.

    A completion replaces the token being typed literally, so keys that would
    not survive :func:`split_command` are dropped: non-UTF-8 keys, and keys
    holding whitespace, quotes, backslashes or control characters. Redis is
    happy with all of them, but completing one would produce a line that means
    something else -- ``mykey two`` is two arguments, not one key.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(raw, str):
        text = raw
    else:
        return None
    if not text:
        return None
    for char in text:
        if char.isspace() or char in "\"'\\" or ord(char) < 0x20 or ord(char) == 0x7F:
            return None
    return text


# --------------------------------------------------------------------------- #
# Parsing cells into commands
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CommandLine:
    """One command from a cell, with the source line it came from."""

    args: list[str]
    lineno: int
    source: str

    @property
    def name(self) -> str:
        """The command name, upper-cased, or ``""`` for an empty line."""
        return self.args[0].upper() if self.args else ""


def split_command(line: str) -> list[str]:
    """Split one command line into arguments.

    Uses :mod:`shlex`, which covers the quoting people actually type. It is not
    quite ``sdssplitargs`` from redis-cli: that also understands ``\\xNN``
    escapes inside double quotes. Raises :class:`ValueError` on unbalanced
    quotes, which ``do_is_complete`` relies on.
    """
    return shlex.split(line, comments=False, posix=True)


def split_cell(code: str) -> list[CommandLine]:
    """Split a cell into commands, dropping blanks and ``#`` comments.

    Lines that fail to tokenise are returned with their raw source so the
    kernel can report the error against the right line instead of failing the
    whole cell.
    """
    commands: list[CommandLine] = []
    for lineno, raw in enumerate(code.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            args = split_command(line)
        except ValueError:
            args = []
        if not args:
            # Keep it: an unparseable line is an error to report, not a blank.
            commands.append(CommandLine(args=[], lineno=lineno, source=line))
            continue
        commands.append(CommandLine(args=args, lineno=lineno, source=line))
    return commands


def is_blocking(args: list[str]) -> bool:
    """Would this command block the shell channel forever if run inline?

    True only for commands with no way back: the pub/sub and replication
    streams, and the blocking commands given a zero timeout. ``BLPOP key 5``
    returns on its own, so it is allowed through and runs exactly as it would
    in redis-cli.
    """
    if not args:
        return False
    name = args[0].upper()
    if name in BLOCKING_COMMANDS:
        return True
    if name == "XREAD" or name == "XREADGROUP":
        upper = [a.upper() for a in args]
        if "BLOCK" in upper:
            idx = upper.index("BLOCK")
            return idx + 1 < len(args) and args[idx + 1] == "0"
        return False
    if name in TIMEOUT_COMMANDS and len(args) >= 2:
        try:
            return float(args[-1]) == 0
        except ValueError:
            return False
    return False


# --------------------------------------------------------------------------- #
# Connection settings
# --------------------------------------------------------------------------- #


@dataclass
class ConnectionSettings:
    """Everything needed to open a connection, as ``%connect`` collects it.

    A plain value, and deliberately a complete one: a notebook states its whole
    connection in one cell, so re-running that cell connects the same way it
    did the first time. Nothing here is carried over from a previous
    connection -- a runbook that depends on which cells ran, and in what order,
    is not a runbook.

    The password lives here because it has to, and never leaves here: nothing
    in :meth:`describe` or :meth:`to_url` will print it.
    """

    host: str = "localhost"
    port: int = 6379
    #: Unix socket path. Takes precedence over host and port when set.
    socket: str | None = None
    db: int = 0
    protocol: int = 2
    username: str | None = None
    password: str | None = None
    tls: bool = False
    #: Connect over TLS but do not validate the server's certificate. Needed
    #: for a self-signed test cluster, a bad idea anywhere else, which is why
    #: ``%status`` says so out loud rather than just showing "tls: on".
    tls_insecure: bool = False
    cacert: str | None = None
    cacertdir: str | None = None
    cert: str | None = None
    key: str | None = None

    # -- building ---------------------------------------------------------- #

    @classmethod
    def from_url(cls, url: str) -> ConnectionSettings:
        """Seed settings from a URL: the ``-u`` flag, or ``REDIS_URL``.

        Goes through redis-py's own parser, so the query arguments it
        understands keep working and ``rediss://h:6380?ssl_cert_reqs=none``
        describes the same connection as ``--tls --insecure``.
        """
        parsed = parse_url(url)  # type: ignore[no-untyped-call]
        settings = cls()
        if "host" in parsed:
            settings.host = str(parsed["host"])
        if "port" in parsed:
            settings.port = int(parsed["port"])
        if "path" in parsed:
            settings.socket = str(parsed["path"])
        if parsed.get("db") is not None:
            settings.db = int(parsed["db"])
        if parsed.get("protocol") is not None:
            settings.protocol = int(parsed["protocol"])
        if parsed.get("username"):
            settings.username = str(parsed["username"])
        if parsed.get("password"):
            settings.password = str(parsed["password"])
        settings.tls = parsed.get("connection_class") is SSLConnection
        if str(parsed.get("ssl_cert_reqs", "")).lower() == "none":
            settings.tls_insecure = True
        for parsed_name, attribute in (
            ("ssl_ca_certs", "cacert"),
            ("ssl_ca_path", "cacertdir"),
            ("ssl_certfile", "cert"),
            ("ssl_keyfile", "key"),
        ):
            if parsed.get(parsed_name):
                setattr(settings, attribute, str(parsed[parsed_name]))
        return settings

    def pool_kwargs(self) -> dict[str, Any]:
        """Arguments for a :class:`redis.ConnectionPool`.

        ``decode_responses`` is always False: the formatter does its own
        decoding so binary values render the way redis-cli renders them.
        """
        kwargs: dict[str, Any] = {
            "db": self.db,
            "decode_responses": False,
            "protocol": self.protocol,
            "parser_class": parser_for_protocol(self.protocol),
        }
        if self.username:
            kwargs["username"] = self.username
        if self.password:
            kwargs["password"] = self.password

        if self.socket:
            kwargs["connection_class"] = UnixDomainSocketConnection
            kwargs["path"] = self.socket
            return kwargs

        kwargs["host"] = self.host
        kwargs["port"] = self.port
        if not self.tls:
            return kwargs

        kwargs["connection_class"] = SSLConnection
        # redis-py turns hostname checking off along with certificate checking,
        # which python's ssl module insists on: CERT_NONE with check_hostname
        # left on is a ValueError, not an insecure connection.
        kwargs["ssl_cert_reqs"] = "none" if self.tls_insecure else "required"
        for name, value in (
            ("ssl_ca_certs", self.cacert),
            ("ssl_ca_path", self.cacertdir),
            ("ssl_certfile", self.cert),
            ("ssl_keyfile", self.key),
        ):
            if value:
                kwargs[name] = value
        return kwargs

    def cluster_kwargs(self) -> dict[str, Any]:
        """Arguments for :class:`RedisCluster`.

        A cluster client builds its own per-node connections, so it takes
        ``ssl=True`` rather than a pool's ``connection_class``, and it rejects
        ``db`` outright -- cluster mode only has database 0. redis-py drops
        ``parser_class`` on this path of its own accord, so a cluster gets the
        stock parser; RESP3 shapes still arrive, just not through ours.
        """
        kwargs = self.pool_kwargs()
        connection_class = kwargs.pop("connection_class", None)
        kwargs.pop("db", None)
        kwargs.pop("host", None)
        kwargs.pop("port", None)
        if connection_class is SSLConnection:
            kwargs["ssl"] = True
        return kwargs

    # -- describing -------------------------------------------------------- #

    def to_url(self) -> str:
        """A URL for this connection, with any password redacted.

        For display only, and lossy by design: the TLS file arguments have no
        place in a URL. Never round-trip it back through :meth:`from_url` and
        expect the same connection.
        """
        if self.socket:
            return f"unix://{self.socket}?db={self.db}"
        scheme = "rediss" if self.tls else "redis"
        credentials = ""
        if self.username or self.password:
            credentials = f"{self.username or ''}{':***' if self.password else ''}@"
        return f"{scheme}://{credentials}{self.host}:{self.port}/{self.db}"

    def describe(self) -> list[tuple[str, str]]:
        """Label/value pairs for ``%connect`` and ``%status`` output.

        This output gets saved into a notebook and committed, so the password
        is reported as being set, never quoted.
        """
        rows = [("server", self.socket or f"{self.host}:{self.port}")]
        rows.append(("db", str(self.db)))
        rows.append(("protocol", f"RESP{self.protocol}"))
        if self.tls:
            detail = " (certificate check disabled)" if self.tls_insecure else ""
            rows.append(("tls", f"on{detail}"))
            for label, value in (
                ("cacert", self.cacert),
                ("cacertdir", self.cacertdir),
                ("cert", self.cert),
                ("key", self.key),
            ):
                if value:
                    rows.append((label, value))
        else:
            rows.append(("tls", "off"))
        rows.append(("user", self.username or "(default)"))
        rows.append(("password", "(set)" if self.password else "(none)"))
        return rows


def default_settings() -> ConnectionSettings:
    """Settings from ``REDIS_URL``, or localhost if it is unset."""
    return ConnectionSettings.from_url(default_url())


# --------------------------------------------------------------------------- #
# Server description
# --------------------------------------------------------------------------- #


@dataclass
class ServerInfo:
    """What ``%status`` reports: the settings, plus what the server said."""

    settings: ConnectionSettings = field(default_factory=ConnectionSettings)
    version: str = "?"
    mode: str = "?"

    def render(self) -> str:
        rows = self.settings.describe()
        rows.append(("version", self.version))
        rows.append(("mode", self.mode))
        width = max(len(label) for label, _ in rows)
        return "".join(f"{label + ':':<{width + 2}}{value}\n" for label, value in rows)


# --------------------------------------------------------------------------- #
# The session
# --------------------------------------------------------------------------- #


@dataclass
class RedisSession:
    """A single interactive connection plus the state that outlives a cell."""

    #: How to connect. Replaced wholesale by ``%connect``, never patched.
    settings: ConnectionSettings = field(default_factory=default_settings)

    #: Selected database, tracked so it survives into the next cell and is
    #: reset by a reconnect.
    db: int = 0
    #: True between ``MULTI`` and its ``EXEC``/``DISCARD``, possibly spanning
    #: cells, which is why ``do_is_complete`` has to know about it.
    in_transaction: bool = False
    #: Off by default: ``SCAN``-based key completion is a real load generator
    #: against a production keyspace. Toggled with ``%config``.
    complete_keys: bool = False
    #: Whether a command may open the connection by itself. On, so the kernel
    #: behaves like redis-cli with no arguments; turn it off with ``%config``
    #: in a runbook that must only ever talk to the server it names.
    autoconnect: bool = True
    #: ``"rich"`` adds ``text/html`` and friends to a reply's mimebundle;
    #: ``"plain"`` sends only the redis-cli text. Set with ``%config render``.
    #: Rich is the default because it is the reason for having a kernel.
    render_mode: str = RICH
    #: Set by ``%render`` for the remainder of one cell and cleared by the
    #: kernel when the cell ends, so a cell cannot change how later cells look.
    render_override: str | None = field(default=None, repr=False)

    _client: Any = field(default=None, repr=False)
    _info: ServerInfo | None = field(default=None, repr=False)

    # -- connection ------------------------------------------------------- #

    @property
    def connected(self) -> bool:
        return self._client is not None

    @property
    def render_mode_now(self) -> str:
        """The mode this reply should be rendered in.

        A ``%render`` earlier in the same cell wins; otherwise the session
        setting stands.
        """
        return self.render_override or self.render_mode

    @property
    def url(self) -> str:
        """The current target as a URL, with any password redacted."""
        return self.settings.to_url()

    @property
    def protocol(self) -> int:
        return self.settings.protocol

    @protocol.setter
    def protocol(self, value: int) -> None:
        # A bare HELLO changes the protocol under us, so the settings have to
        # keep up or %status would report the wrong one.
        self.settings.protocol = int(value)

    def connect(self, settings: ConnectionSettings | None = None) -> ServerInfo:
        """(Re)connect, resetting per-connection session state.

        Called with settings, those become the session's -- in full, replacing
        whatever came before. Called without, it reconnects to the same place,
        which is what a bare ``%connect`` and the autoconnect path both want.
        """
        self.close()
        if settings is not None:
            self.settings = settings

        self._client = self._build_client(self.settings)
        # A reconnect starts on the settings' database, outside any transaction.
        self.db = self.settings.db
        self.in_transaction = False
        self._info = None
        return self.info()

    def _build_client(self, settings: ConnectionSettings) -> Any:
        """Build a client, upgrading to :class:`RedisCluster` if the peer is one.

        ``parser_class`` has to go through the pool: ``Redis.__init__`` does not
        accept it.
        """
        pool = redis.ConnectionPool(**settings.pool_kwargs())
        # single_connection_client pins one socket, so SELECT and MULTI stick.
        client = redis.Redis(connection_pool=pool, single_connection_client=True)
        _silence_callbacks(client)

        if self._is_cluster(client):
            client.close()
            cluster = RedisCluster(
                host=settings.host,
                port=settings.port,
                **settings.cluster_kwargs(),
            )
            _silence_callbacks(cluster)
            return cluster
        return client

    @staticmethod
    def _is_cluster(client: Any) -> bool:
        """Is the peer running in cluster mode?

        ``INFO`` is used rather than ``CLUSTER INFO`` because it answers on
        every server, including ones with the cluster commands disabled.
        """
        try:
            reply = client.execute_command("INFO", "server")
        except Exception:
            return False
        return b"redis_mode:cluster" in _reply_bytes(reply)

    def close(self) -> None:
        if self._client is not None:
            with suppress(Exception):
                self._client.close()  # already gone; nothing useful to report
            self._client = None
        self._info = None

    # -- executing -------------------------------------------------------- #

    def execute(self, args: list[str]) -> Any:
        """Run one command and return the raw reply.

        Redis error replies are raised, as redis-py raises them; the kernel
        turns them into ``(error) ...`` and carries on to the next line.

        Opens the connection if there is not one yet, unless ``autoconnect`` is
        off -- in a runbook that names its server, dialling localhost because a
        cell ran early is worse than refusing.
        """
        if self._client is None:
            if not self.autoconnect:
                raise NotConnected("not connected. Run %connect first")
            self.connect()
        assert self._client is not None
        reply = self._client.execute_command(*args)
        self._note_state_change(args)
        return reply

    def _note_state_change(self, args: list[str]) -> None:
        """Update session state after a command that succeeded."""
        name = args[0].upper() if args else ""
        if name == "SELECT" and len(args) > 1:
            with suppress(ValueError):
                self.db = int(args[1])
        elif name == "MULTI":
            self.in_transaction = True
        elif name in ("EXEC", "DISCARD", "RESET"):
            self.in_transaction = False
        elif name == "HELLO" and len(args) > 1:
            with suppress(ValueError):
                self.protocol = int(args[1])
        elif name == "SWAPDB":
            pass  # does not change which db we are on

    # -- completing key names --------------------------------------------- #

    def scan_keys(
        self,
        prefix: str,
        *,
        count: int = SCAN_COUNT,
        limit: int = SCAN_MAX_MATCHES,
        max_calls: int = SCAN_MAX_CALLS,
    ) -> list[str]:
        """Key names beginning with ``prefix``, found with a bounded ``SCAN``.

        Never issues ``KEYS``. Answers nothing in three cases where scanning
        would do harm rather than good: with no connection yet, because
        pressing Tab must not be the thing that dials a server; while a
        transaction is open, where the ``SCAN`` would be queued into the user's
        ``MULTI`` instead of answered; and once the call or match budget is
        spent, so an unhelpful prefix costs a bounded amount of server work.
        """
        if self._client is None or self.in_transaction:
            return []

        pattern = escape_glob(prefix) + "*"
        found: set[str] = set()
        calls = 0
        for node in self._scan_targets():
            cursor = "0"
            while calls < max_calls and len(found) < limit:
                calls += 1
                cursor, keys = self._scan_once(node, cursor, pattern, count)
                for raw in keys:
                    key = completable_key(raw)
                    if key is not None and key.startswith(prefix):
                        found.add(key)
                if cursor == "0":
                    break  # this node's keyspace is exhausted
            if calls >= max_calls or len(found) >= limit:
                break
        return sorted(found)[:limit]

    def _scan_targets(self) -> list[Any]:
        """Nodes to scan: one implicit node standalone, every primary in a cluster.

        ``SCAN`` is not a keyed command, so a cluster client has no slot to
        route it by and each primary has to be asked for its own share of the
        keyspace, with its own cursor.
        """
        get_primaries = getattr(self._client, "get_primaries", None)
        if get_primaries is None:
            return [None]
        try:
            return list(get_primaries()) or [None]
        except Exception:
            return [None]

    def _scan_once(self, node: Any, cursor: str, pattern: str, count: int) -> tuple[str, list[Any]]:
        """One ``SCAN`` call, returning the next cursor and the keys it found.

        Response callbacks are cleared on the client, so this is the raw
        two-element reply rather than redis-py's parsed tuple. Anything that
        does not look like one is reported as an exhausted cursor: a malformed
        reply should end the scan, not loop on it.
        """
        assert self._client is not None
        args = ["SCAN", cursor, "MATCH", pattern, "COUNT", str(count)]
        if node is None:
            reply = self._client.execute_command(*args)
        else:
            reply = self._client.execute_command(*args, target_nodes=node)
        if isinstance(reply, dict):
            # A cluster client answers per node when asked for several; we ask
            # for one at a time, so unwrap whichever came back.
            reply = next(iter(reply.values()), None)
        if not isinstance(reply, (list, tuple)) or len(reply) != 2:
            return "0", []
        keys = reply[1] if isinstance(reply[1], (list, tuple, set, frozenset)) else []
        return _cursor_text(reply[0]), list(keys)

    def pubsub_connection(self) -> Any:
        """A client for streaming commands, so the main socket stays usable."""
        if self._client is None:
            self.connect()
        assert self._client is not None
        return self._client

    # -- describing ------------------------------------------------------- #

    def info(self) -> ServerInfo:
        """Where we are connected and what is answering, for ``%status``.

        Reported from the settings rather than read back off the connection
        pool: the settings are what the user asked for, and they cover TLS and
        the ACL user, which a pool's kwargs describe only indirectly. The
        version and mode are the server's own answer, so they are cached -- but
        ``db`` and ``protocol`` are re-read every time, because a bare
        ``SELECT`` or ``HELLO`` moves them without going through ``%connect``.
        """
        current = replace(self.settings, db=self.db)
        if self._info is not None:
            self._info.settings = current
            return self._info

        info = ServerInfo(settings=current)
        if self._client is None:
            return info  # not cached: the next call may have a connection

        try:
            fields = _parse_info(_reply_bytes(self._client.execute_command("INFO", "server")))
            info.version = fields.get("redis_version", "?")
            info.mode = fields.get("redis_mode", "?")
        except Exception:
            pass  # %status should still show what we do know

        self._info = info
        return info


def _silence_callbacks(client: Any) -> None:
    """Stop redis-py rewriting replies, so the formatter sees what the server sent.

    Response callbacks turn ``SET`` into ``True`` and ``HGETALL`` into a dict,
    which destroys the shapes this kernel exists to render.

    A standalone client keeps them in one dictionary. A cluster builds a
    ``Redis`` per node instead, and assigning to the cluster object itself would
    only create an attribute nothing reads -- so each node's client is cleared.
    A node discovered *after* this point keeps redis-py's defaults, the same
    best-effort caveat that applies to the parser (see
    :meth:`ConnectionSettings.cluster_kwargs`).
    """
    callbacks = getattr(client, "response_callbacks", None)
    if callbacks is not None:
        callbacks.clear()
    get_nodes = getattr(client, "get_nodes", None)
    if get_nodes is None:
        return
    for node in get_nodes():
        node_callbacks = getattr(
            getattr(node, "redis_connection", None), "response_callbacks", None
        )
        if node_callbacks is not None:
            node_callbacks.clear()


def _cursor_text(value: Any) -> str:
    """A ``SCAN`` cursor as text, however the protocol delivered it."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("ascii", "replace")
    return str(value)


def _reply_bytes(reply: Any) -> bytes:
    """Coerce a bulk/verbatim reply to bytes for local parsing."""
    if isinstance(reply, Verbatim):
        return bytes(reply)
    if isinstance(reply, (bytes, bytearray)):
        return bytes(reply)
    if isinstance(reply, str):
        return reply.encode("utf-8")
    return b""


def _parse_info(payload: bytes) -> dict[str, str]:
    """Parse an ``INFO`` section into a flat dict."""
    fields: dict[str, str] = {}
    for raw_line in payload.decode("utf-8", "replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key] = value
    return fields
