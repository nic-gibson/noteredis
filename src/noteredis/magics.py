"""Cell magics for session control: ``%connect``, ``%status``, ``%select``, ``%protocol``,
``%load``.

Magics are the one place a cell does something that is not a Redis command, so
they are kept apart from the command path. Each handler returns the text to
print; raising :class:`MagicError` prints ``(error) ...`` exactly like a Redis
error reply, so a typo in a magic reads the same as a typo in a command.

No ipykernel here either, so a ``%%redis`` cell magic could reuse this. The one
thing a magic cannot do for itself is prompt for a password -- that needs the
kernel's stdin channel -- so the caller passes a :data:`Prompt` in.
"""

from __future__ import annotations

import os
import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, replace

from .client import (
    RENDER_MODES,
    CommandLine,
    ConnectionSettings,
    RedisSession,
    default_settings,
    is_blocking,
    split_cell,
)
from .formatter import format_error, format_reply

__all__ = [
    "MAGICS",
    "LoadRequest",
    "MagicError",
    "Prompt",
    "expand_variables",
    "handle_magic",
    "is_magic",
    "magic_names",
    "parse_connect",
    "parse_load",
    "read_command_file",
]


class MagicError(Exception):
    """A magic was used wrongly. Rendered as ``(error) <message>``."""


#: Asks the frontend for a secret, masked. Supplied by the kernel, which is the
#: only part of this package that can reach the stdin channel.
Prompt = Callable[[str], str]

Handler = Callable[[RedisSession, list[str], "Prompt | None"], str]


def is_magic(args: list[str]) -> bool:
    return bool(args) and args[0].startswith("%")


def magic_names() -> list[str]:
    return sorted(MAGICS)


def handle_magic(session: RedisSession, args: list[str], prompt: Prompt | None = None) -> str:
    name = args[0].lower()
    handler = MAGICS.get(name)
    if handler is None:
        raise MagicError(f"unknown magic '{args[0]}'. Try %help")
    return handler(session, args[1:], prompt)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


#: Flags that take a value, mapped to the setting they fill in. Names are
#: redis-cli's, so anyone who knows ``redis-cli -h host --tls --insecure`` can
#: type the same thing here.
VALUE_FLAGS = {
    "-h": "host",
    "-p": "port",
    "-n": "db",
    "-s": "socket",
    "-a": "password",
    "--pass": "password",
    "--user": "username",
    "--cacert": "cacert",
    "--cacertdir": "cacertdir",
    "--cert": "cert",
    "--key": "key",
    "-u": "url",
}

#: Real redis-cli flags that redis-py has no equivalent for. Named explicitly
#: so they fail with an explanation rather than "unknown option".
UNSUPPORTED_FLAGS = {
    "--sni": "redis-py derives SNI from the host name",
    "--tls-ciphers": "redis-py exposes no cipher list",
    "--tls-ciphersuites": "redis-py exposes no cipher list",
}

#: Settings that are file or directory paths, checked early: a missing CA file
#: otherwise surfaces as an opaque TLS handshake failure.
PATH_SETTINGS = ("cacert", "cacertdir", "cert", "key")

_VARIABLE = re.compile(r"\$(?:\{(\w+)\}|(\w+))")


def expand_variables(text: str) -> str:
    """Expand ``$VAR`` and ``${VAR}`` from the kernel's environment.

    This is what keeps a password out of a committed notebook: the cell says
    ``--pass ${REDIS_PASSWORD}`` and the value stays in the environment. An
    unset variable is an error, not an empty string -- authenticating with the
    literal text ``${REDIS_PASSWORD}`` would fail in a far more confusing way.
    """

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        try:
            return os.environ[name]
        except KeyError:
            raise MagicError(f"${{{name}}} is not set in the kernel's environment") from None

    return _VARIABLE.sub(substitute, text)


def parse_connect(args: list[str], prompt: Prompt | None = None) -> ConnectionSettings:
    """Turn ``%connect`` flags into a complete set of settings.

    Every call describes the whole connection. What is left out falls back to
    ``REDIS_URL`` and the built-in defaults, never to the settings a previous
    ``%connect`` left behind, so re-running the cell always connects to the
    same place.
    """
    values: dict[str, str] = {}
    tls = insecure = askpass = False
    protocol: int | None = None
    url: str | None = None

    remaining = list(args)
    while remaining:
        arg = remaining.pop(0)
        if arg == "--tls":
            tls = True
        elif arg == "--insecure":
            insecure = True
        elif arg == "--askpass":
            askpass = True
        elif arg in ("-3", "--resp3"):
            protocol = 3
        elif arg in ("-2", "--resp2"):
            protocol = 2
        elif arg in UNSUPPORTED_FLAGS:
            raise MagicError(
                f"%connect: {arg} is a redis-cli flag this kernel cannot honour "
                f"({UNSUPPORTED_FLAGS[arg]})"
            )
        elif arg in VALUE_FLAGS:
            if not remaining:
                raise MagicError(f"%connect: {arg} needs a value")
            values[VALUE_FLAGS[arg]] = remaining.pop(0)
        elif arg.startswith("-"):
            raise MagicError(f"%connect: unknown option '{arg}'. Try %help")
        elif url is None and "://" in arg:
            url = arg  # bare URL, the form %connect has always accepted
        else:
            raise MagicError(f"%connect: unexpected argument '{arg}'")

    url = values.pop("url", url)
    if askpass and "password" in values:
        raise MagicError("%connect: --askpass and --pass are mutually exclusive")

    try:
        values = {name: expand_variables(value) for name, value in values.items()}
    except MagicError as exc:
        raise MagicError(f"%connect: {exc}") from None

    settings = ConnectionSettings.from_url(expand_variables(url)) if url else default_settings()

    for name in PATH_SETTINGS:
        path = values.get(name)
        if path and not os.path.exists(path):
            raise MagicError(f"%connect: {name} file not found: {path}")

    for name in ("host", "socket", "username", "password", *PATH_SETTINGS):
        if name in values:
            setattr(settings, name, values[name])
    for name in ("port", "db"):
        if name in values:
            setattr(settings, name, _parse_int(name, values[name]))

    if protocol is not None:
        settings.protocol = protocol
    if askpass:
        if prompt is None:
            raise MagicError(
                "%connect: --askpass needs a frontend that can prompt. Use --pass ${VAR} instead"
            )
        settings.password = prompt("Password: ")
    # --insecure and the certificate flags are meaningless without TLS, and
    # taking them as a request for TLS beats connecting in the clear to
    # someone who plainly asked for a secure connection.
    settings.tls = settings.tls or tls or insecure or any(values.get(n) for n in PATH_SETTINGS)
    settings.tls_insecure = settings.tls_insecure or insecure
    return settings


def _connect(session: RedisSession, args: list[str], prompt: Prompt | None) -> str:
    """``%connect [url] [flags]`` -- open a connection, replacing the current one.

    Flags are redis-cli's own:

      -h HOST        server host                -p PORT      server port
      -n DB          database number            -s PATH      unix socket
      -u URL         redis:// or rediss:// URL  -2 / -3      RESP version
      --user NAME    ACL username               --pass PASS  password
      --askpass      prompt for the password, masked, without recording it
      --tls          connect over TLS           --insecure   skip cert checks
      --cacert FILE  CA to verify against       --cacertdir DIR
      --cert FILE    client certificate         --key FILE   client key

    Values expand ``$VAR`` and ``${VAR}`` from the environment, so a committed
    notebook can say ``--pass ${REDIS_PASSWORD}`` and keep the secret out of
    the file. ``--askpass`` keeps it out of both the file and the environment.

    Every call states the whole connection: anything omitted falls back to
    ``REDIS_URL`` and the defaults, never to a previous ``%connect``. Re-running
    the cell therefore always connects to the same place.
    """
    settings = parse_connect(args, prompt)
    try:
        info = session.connect(settings)
    except Exception as exc:
        raise MagicError(f"%connect: {exc}") from exc
    return info.render()


def _status(session: RedisSession, args: list[str], prompt: Prompt | None) -> str:
    """``%status`` -- server, db, protocol, TLS, ACL user, server version."""
    del prompt
    if args:
        raise MagicError("%status takes no arguments")
    if not session.connected:
        return f"not connected. Target would be {session.url}\n"
    return session.info().render()


def _select(session: RedisSession, args: list[str], prompt: Prompt | None) -> str:
    """``%select <db>`` -- shorthand for the ``SELECT`` command."""
    del prompt
    if len(args) != 1:
        raise MagicError("%select: expected a database number")
    index = _parse_int("db", args[0], magic="%select")
    try:
        session.execute(["SELECT", str(index)])
    except Exception as exc:
        raise MagicError(f"%select: {exc}") from exc
    return "OK\n"


def _protocol(session: RedisSession, args: list[str], prompt: Prompt | None) -> str:
    """``%protocol [2|3]`` -- show or switch RESP version via ``HELLO``.

    Keeps the current connection's settings and changes only the protocol,
    which is the whole point of it: ``%connect`` is for saying where to go.
    """
    del prompt
    if not args:
        return f"RESP{session.protocol}\n"
    if len(args) != 1 or args[0] not in ("2", "3"):
        raise MagicError("%protocol: expected 2 or 3")
    # HELLO switches an existing connection, but the parser is chosen per
    # connection, so reconnect to get the matching one.
    try:
        session.connect(replace(session.settings, protocol=int(args[0])))
    except Exception as exc:
        raise MagicError(f"%protocol: {exc}") from exc
    return f"RESP{session.protocol}\n"


@dataclass(frozen=True)
class LoadRequest:
    """A parsed ``%load`` invocation: what to read, and how loud to be."""

    path: str
    quiet: bool


def parse_load(args: list[str]) -> LoadRequest:
    """Parse ``%load <path> [-q|--quiet]``.

    The path expands ``$VAR``/``${VAR}`` the same way ``%connect``'s flags do,
    so a shared demo repo can be checked out anywhere and pointed at with an
    environment variable rather than a hard-coded path.
    """
    path: str | None = None
    quiet = False
    for arg in args:
        if arg in ("-q", "--quiet"):
            quiet = True
        elif arg.startswith("-"):
            raise MagicError(f"%load: unknown option '{arg}'. Try %help load")
        elif path is None:
            path = arg
        else:
            raise MagicError(f"%load: unexpected argument '{arg}'")
    if path is None:
        raise MagicError("%load: expected a file path")
    return LoadRequest(path=expand_variables(path), quiet=quiet)


def read_command_file(path: str) -> str:
    """Read a ``%load`` file as text.

    Raised as :class:`MagicError` rather than a raw traceback -- the same
    treatment ``%connect`` already gives a missing certificate file.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise MagicError(f"%load: {exc.strerror or exc}: {path}") from exc
    except UnicodeDecodeError as exc:
        raise MagicError(f"%load: {path} is not valid UTF-8 text") from exc


def _load(session: RedisSession, args: list[str], prompt: Prompt | None) -> str:
    """``%load <path> [-q|--quiet]`` -- run the Redis commands in a file.

    Each line is one command, exactly as in a cell: blank lines and ``#``
    comments are skipped, and a command that errors prints ``(error) ...``
    and the file keeps going rather than stopping it. ``--quiet`` runs the
    file but suppresses every one of its replies -- for demo setup or
    teardown nobody needs to watch, without hiding a real error in it.

    A loaded file may itself contain other magics such as ``%select``, but
    not another ``%load``: nesting would risk a file loading itself. Blocking
    commands (``SUBSCRIBE``, ``BLPOP key 0``, ...) are refused, the same as
    they are in a cell.

    This is the plain-text implementation, used when there is no kernel
    around to add rich per-command rendering; the kernel itself runs a
    loaded file's commands the same way it runs the rest of the cell, so
    typed and loaded commands render alike.
    """
    request = parse_load(args)
    text = read_command_file(request.path)
    lines = [
        _run_loaded_line(session, command, request.quiet, prompt) for command in split_cell(text)
    ]
    return "".join(lines)


def _run_loaded_line(
    session: RedisSession, command: CommandLine, quiet: bool, prompt: Prompt | None
) -> str:
    if not command.args:
        return f"(error) unbalanced quotes: {command.source}\n"

    if is_magic(command.args):
        if command.args[0].lower() == "%load":
            return "(error) %load: nested %load is not supported\n"
        try:
            text = handle_magic(session, command.args, prompt)
        except MagicError as exc:
            return f"(error) {exc}\n"
        return "" if quiet else text

    if is_blocking(command.args):
        return (
            f"(error) {command.name} never returns on its own and is not "
            "supported by this kernel. Use redis-cli to watch a live feed\n"
        )

    try:
        reply = session.execute(command.args)
    except Exception as exc:
        return format_error(exc)
    return "" if quiet else format_reply(reply)


def _show(value: object) -> str:
    return "on" if value is True else "off" if value is False else str(value)


def _parse_bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in ("on", "true", "yes", "1"):
        return True
    if lowered in ("off", "false", "no", "0"):
        return False
    raise MagicError(f"%config: expected on or off, got '{text}'")


def _parse_render_mode(text: str, magic: str = "%config") -> str:
    mode = text.strip().lower()
    if mode not in RENDER_MODES:
        raise MagicError(f"{magic}: expected one of {', '.join(RENDER_MODES)}, got '{text}'")
    return mode


#: Kernel settings ``%config`` can read and write: the attribute on the session,
#: how to parse a value for it, and why it defaults as it does. Deliberately
#: distinct from the ``CONFIG`` command.
CONFIG_SETTINGS: dict[str, tuple[str, Callable[[str], object], str]] = {
    "complete_keys": (
        "complete_keys",
        _parse_bool,
        "completing key names via SCAN (off by default)",
    ),
    "autoconnect": (
        "autoconnect",
        _parse_bool,
        "let a command open the connection by itself (on by default)",
    ),
    "render": (
        "render_mode",
        _parse_render_mode,
        f"how replies are rendered: {' or '.join(RENDER_MODES)}",
    ),
}


def _config(session: RedisSession, args: list[str], prompt: Prompt | None) -> str:
    """``%config [name [value]]`` -- kernel settings, not server config."""
    del prompt
    if not args:
        lines = [f"{name} = {_show(_setting(session, name))}" for name in CONFIG_SETTINGS]
        return "\n".join(lines) + "\n"
    name = args[0]
    if name not in CONFIG_SETTINGS:
        raise MagicError(f"%config: unknown setting '{name}'. Known: {', '.join(CONFIG_SETTINGS)}")
    attribute, parse, _ = CONFIG_SETTINGS[name]
    if len(args) == 1:
        return f"{name} = {_show(_setting(session, name))}\n"
    if len(args) > 2:
        raise MagicError("%config: expected at most a name and a value")
    setattr(session, attribute, parse(args[1]))
    return f"{name} = {_show(_setting(session, name))}\n"


def _setting(session: RedisSession, name: str) -> object:
    return getattr(session, CONFIG_SETTINGS[name][0])


def _render(session: RedisSession, args: list[str], prompt: Prompt | None) -> str:
    """``%render [rich|plain]`` -- rendering mode for the rest of this cell only.

    A one-cell override of ``%config render``, cleared when the cell ends, so
    looking at one reply as a table does not quietly change how every later
    cell renders. With no argument it reports the mode in force; setting it
    is silent, so it can sit on its own line without cluttering the output.
    """
    del prompt
    if not args:
        return f"{session.render_mode_now}\n"
    if len(args) > 1:
        raise MagicError("%render: expected a single mode")
    session.render_override = _parse_render_mode(args[0], magic="%render")
    return ""


def _help(session: RedisSession, args: list[str], prompt: Prompt | None) -> str:
    """``%help [magic]`` -- list the magics, or explain one of them.

    ``%help connect`` is how the connection flags are meant to be found: a
    notebook is often the only documentation to hand.
    """
    del session, prompt
    if args:
        name = args[0] if args[0].startswith("%") else f"%{args[0]}"
        handler = MAGICS.get(name.lower())
        if handler is None:
            raise MagicError(f"%help: unknown magic '{args[0]}'")
        return _plain_doc(handler.__doc__ or "")

    width = max(len(name) for name in MAGICS)
    lines = []
    for name in sorted(MAGICS):
        doc = (MAGICS[name].__doc__ or "").strip().splitlines()[0]
        # Strip the leading "``%name <args>`` -- " from the docstring.
        _, _, summary = doc.partition("-- ")
        lines.append(f"{name:<{width}}  {_strip_markup(summary or doc)}")
    lines.append("")
    lines.append("%help <magic> explains one of them in full.")
    return "\n".join(lines) + "\n"


def _plain_doc(doc: str) -> str:
    """A docstring as terminal text: dedented, with the RST markup dropped.

    The first line is dedented separately from the rest because the two are not
    indented alike -- and because Python 3.13 strips docstring indentation at
    compile time while the versions before it do not, so neither shape can be
    assumed.
    """
    first, _, rest = doc.strip().partition("\n")
    body = f"{first}\n{textwrap.dedent(rest)}" if rest.strip() else first
    return _strip_markup(body).rstrip() + "\n"


def _strip_markup(text: str) -> str:
    """Drop the RST double backticks; a terminal has no use for them."""
    return text.replace("``", "")


def _parse_int(name: str, text: str, magic: str = "%connect") -> int:
    try:
        return int(text)
    except ValueError as exc:
        raise MagicError(f"{magic}: '{text}' is not a {name} number") from exc


MAGICS: dict[str, Handler] = {
    "%connect": _connect,
    "%status": _status,
    "%select": _select,
    "%protocol": _protocol,
    "%config": _config,
    "%render": _render,
    "%load": _load,
    "%help": _help,
}
