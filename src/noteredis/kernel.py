"""The Jupyter kernel.

A wrapper kernel: subclasses :class:`ipykernel.kernelbase.Kernel` and talks to
Redis through redis-py, rather than subclassing ``IPythonKernel``. This is the
only module allowed to import the kernel machinery -- ``formatter``, ``client``,
``resp`` and ``commands`` all stay free of it.
"""

from __future__ import annotations

from typing import Any

from ipykernel.kernelbase import Kernel, StdinNotImplementedError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from . import __version__
from .client import (
    RICH,
    CommandLine,
    NotConnected,
    RedisSession,
    is_blocking,
    split_cell,
    split_command,
)
from .commands import CommandTable, load_command_table
from .formatter import format_error, format_reply
from .magics import MagicError, handle_magic, is_magic, magic_names, parse_load, read_command_file
from .render import render

__all__ = ["RedisKernel"]


class RedisKernel(Kernel):
    """A kernel whose cells are Redis commands."""

    implementation = "noteredis"
    implementation_version = __version__

    language = "redis"
    language_version = "8"
    # Not ClassVar, though ruff would prefer it: ipykernel declares both of
    # these as instance attributes, and a ClassVar here contradicts the base.
    language_info = {  # noqa: RUF012
        "name": "redis",
        "mimetype": "text/x-redis",
        "file_extension": ".redis",
        "pygments_lexer": "text",
        "codemirror_mode": "text",
    }

    help_links = [  # noqa: RUF012
        {"text": "Redis commands", "url": "https://redis.io/commands/"},
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        #: The Redis connection. Not ``self.session``: ``Kernel`` already owns
        #: that name for the ZMQ messaging session ``send_response`` writes to,
        #: and it is a typed trait, so assigning over it raises.
        self.redis = RedisSession()
        self._table: CommandTable | None = None

    # -- banner ----------------------------------------------------------- #

    @property
    def banner(self) -> str:
        try:
            info = self.redis.info() if self.redis.connected else None
        except RedisError:
            info = None
        target = info.render().strip() if info else f"not connected ({self.redis.url})"
        return f"noteredis {__version__}\n{target}\n"

    # -- execution -------------------------------------------------------- #

    def do_execute(
        self,
        code: str,
        silent: bool,
        store_history: bool = True,
        user_expressions: dict[str, Any] | None = None,
        allow_stdin: bool = False,
        *,
        cell_id: str | None = None,
    ) -> dict[str, Any]:
        """Run every command in the cell, in order.

        Errors are reported per line and execution continues, which is what
        interactive redis-cli does -- not ``redis-cli < file``, which stops. A
        Redis error reply is therefore still ``status: ok``; ``status: error``
        is reserved for the kernel itself failing.
        """
        try:
            return self._execute_cell(code, silent)
        finally:
            # A %render in this cell applied to this cell. Clearing here, rather
            # than at the top of the next one, covers the paths that return
            # early as well.
            self.redis.render_override = None

    def _execute_cell(self, code: str, silent: bool) -> dict[str, Any]:
        for command in split_cell(code):
            abort = self._run_command(command, silent)
            if abort is not None:
                return abort

        return {
            "status": "ok",
            "execution_count": self.execution_count,
            "payload": [],
            "user_expressions": {},
        }

    def _run_command(
        self, command: CommandLine, silent: bool, quiet: bool = False
    ) -> dict[str, Any] | None:
        """Run one command line, returning an error reply if the cell must stop.

        Shared between the top-level cell loop and ``%load``, so a command
        loaded from a file gets exactly the same magic dispatch,
        blocking-command refusal and rich rendering as one typed directly
        into a cell. ``quiet`` (distinct from Jupyter's own ``silent``) hides
        a successful reply without hiding an error in it -- what ``%load
        --quiet`` needs so a failed setup command still gets noticed.
        """
        if not command.args:
            # Only unbalanced quotes reach here; split_cell kept the line.
            self._emit(silent, f"(error) unbalanced quotes: {command.source}\n")
            return None

        if is_magic(command.args):
            if command.args[0].lower() == "%load":
                return self._run_load(command.args[1:], silent, quiet)
            try:
                text = handle_magic(self.redis, command.args, self._askpass)
            except MagicError as exc:
                self._emit(silent, f"(error) {exc}\n")
                return None
            self._emit(silent or quiet, text)
            return None

        if is_blocking(command.args):
            # Deliberately out of scope: this kernel is for runbooks, not
            # for watching a live feed. Refusing keeps the shell channel
            # answering; running it would wedge the kernel until restart.
            self._emit(
                silent,
                f"(error) {command.name} never returns on its own and is not "
                "supported by this kernel. Use redis-cli to watch a live feed\n",
            )
            return None

        try:
            reply = self.redis.execute(command.args)
        except NotConnected as exc:
            # autoconnect is off and nothing has run %connect yet. The cell
            # is not at fault, so report and stop rather than repeating it
            # for every remaining line.
            self._emit(silent, f"(error) {exc}\n")
            return self._error_reply(exc)
        except (RedisConnectionError, RedisTimeoutError) as exc:
            # The connection, not the command, is the problem: stop here
            # and report it as a kernel fault.
            self._emit(silent, format_error(exc))
            return self._error_reply(exc)
        except RedisError as exc:
            # An error reply. Print it and carry on with the next line.
            self._emit(silent, format_error(exc))
            return None

        if not silent and not quiet:
            self._emit_reply(command.args, reply)
        return None

    def _run_load(
        self, args: list[str], silent: bool, quiet: bool = False
    ) -> dict[str, Any] | None:
        """``%load <path> [-q|--quiet]``, expanded inline into the cell.

        Its commands run through :meth:`_run_command` exactly like the rest
        of the cell -- rich rendering, blocking refusal, and per-line error
        handling included -- rather than through the plain-text fallback in
        ``magics.py`` that a kernel-less caller would get.
        """
        try:
            request = parse_load(args)
            text = read_command_file(request.path)
        except MagicError as exc:
            self._emit(silent, f"(error) {exc}\n")
            return None

        loaded_quiet = quiet or request.quiet
        for command in split_cell(text):
            if command.args and command.args[0].lower() == "%load":
                self._emit(silent, "(error) %load: nested %load is not supported\n")
                continue
            abort = self._run_command(command, silent, loaded_quiet)
            if abort is not None:
                return abort
        return None

    def _askpass(self, prompt: str) -> str:
        """Ask the frontend for a password, masked, over the stdin channel.

        This is the one part of ``%connect`` the magics cannot do themselves,
        and the reason a password never has to appear in a saved notebook:
        JupyterLab shows a masked box, and the value reaches the kernel without
        passing through the cell source or its output.

        A frontend with no stdin -- nbconvert, papermill -- raises, and the
        error says what to do instead rather than hanging on input nobody can
        give.
        """
        try:
            return str(self.getpass(prompt))
        except StdinNotImplementedError as exc:
            raise MagicError(
                "--askpass needs a frontend that can prompt for input. "
                "Use --pass ${VAR} and set the variable in the environment"
            ) from exc

    def _emit(self, silent: bool, text: str) -> None:
        if silent or not text:
            return
        self.send_response(self.iopub_socket, "stream", {"name": "stdout", "text": text})

    def _emit_reply(self, args: list[str], reply: Any) -> None:
        """Send one reply, as plain text plus any richer representations.

        ``text/plain`` is always the exact redis-cli rendering; renderers only
        ever add to the bundle. A renderer that raises is dropped rather than
        allowed to lose the user's output.

        In ``plain`` mode the renderers are not consulted at all -- not merely
        hidden -- so nothing but the redis-cli text is saved into the notebook.
        """
        plain = format_reply(reply)
        bundle: dict[str, Any] = {"text/plain": plain}
        extra = None
        if self.redis.render_mode_now == RICH:
            try:
                extra = render(args, reply)
            except Exception:
                extra = None
        if extra:
            bundle.update(extra)

        if len(bundle) == 1:
            # Nothing rich to add; a stream message keeps consecutive commands
            # in one contiguous block of output.
            self._emit(False, plain)
            return
        self.send_response(
            self.iopub_socket,
            "display_data",
            {"data": bundle, "metadata": {}, "transient": {}},
        )

    def _error_reply(self, exc: BaseException) -> dict[str, Any]:
        return {
            "status": "error",
            "execution_count": self.execution_count,
            "ename": type(exc).__name__,
            "evalue": str(exc),
            "traceback": [],
        }

    # -- completion ------------------------------------------------------- #

    @property
    def table(self) -> CommandTable:
        """The command table, built on first use so kernel start stays fast."""
        if self._table is None:
            if self.redis.in_transaction:
                # Building it means COMMAND DOCS, which inside MULTI would be
                # queued into the user's transaction rather than answered.
                # Complete nothing this once rather than corrupt the EXEC.
                return CommandTable()
            try:
                self._table = load_command_table(self.redis.execute)
            except RedisError:
                self._table = CommandTable()
        return self._table

    def do_complete(self, code: str, cursor_pos: int) -> dict[str, Any]:
        cursor = _Cursor(code, cursor_pos)
        matches: list[str]

        if cursor.prefix.startswith("%"):
            matches = [name for name in magic_names() if name.startswith(cursor.prefix)]
        else:
            before = cursor.tokens_before()
            if not before:
                matches = self.table.complete_names(cursor.prefix)
            elif len(before) == 1:
                # Position 1 could be a subcommand (CONFIG GET) or an argument
                # token (SET key val EX); offer both.
                matches = self.table.complete_subcommands(before[0], cursor.prefix)
                matches += self.table.complete_arguments(before, cursor.prefix)
            else:
                matches = self.table.complete_arguments(before, cursor.prefix)
            matches = _unique(matches + self._key_matches(before, cursor.prefix))

        return {
            "status": "ok",
            "matches": matches,
            "cursor_start": cursor.token_start,
            "cursor_end": cursor_pos,
            "metadata": {},
        }

    def _key_matches(self, args: list[str], prefix: str) -> list[str]:
        """Key names for the argument being completed, when it is a key.

        Off unless ``%config complete_keys on``: completing a key name means
        ``SCAN``ning what may be a production keyspace, so it is the user's
        call, not a default. Skipped inside a transaction, where the ``SCAN``
        would land in the user's ``MULTI`` rather than come back with an answer.
        """
        if not args or not self.redis.complete_keys or self.redis.in_transaction:
            return []
        if not self.table.completes_key(args, len(args)):
            return []
        try:
            return self.redis.scan_keys(prefix)
        except RedisError:
            # A completion is a convenience; failing one is not worth reporting
            # into the user's output.
            return []

    def do_inspect(
        self, code: str, cursor_pos: int, detail_level: int = 0, omit_sections: Any = ()
    ) -> dict[str, Any]:
        cursor = _Cursor(code, cursor_pos)
        try:
            args = split_command(cursor.line)
        except ValueError:
            args = cursor.line.split()
        info = self.table.lookup(args) if args else None
        if info is None:
            return {"status": "ok", "found": False, "data": {}, "metadata": {}}
        return {
            "status": "ok",
            "found": True,
            "data": {"text/plain": info.render()},
            "metadata": {},
        }

    # -- continuation ----------------------------------------------------- #

    def do_is_complete(self, code: str) -> dict[str, Any]:
        """Decide whether Enter should execute or keep the line open.

        Incomplete for unbalanced quotes, and for a ``MULTI`` still waiting on
        its ``EXEC``/``DISCARD`` -- counting the cell's own transaction
        commands on top of whatever the session was already in.
        """
        for line in code.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                split_command(stripped)
            except ValueError:
                return {"status": "incomplete", "indent": ""}

        if self._transaction_open(code):
            return {"status": "incomplete", "indent": ""}
        return {"status": "complete"}

    def _transaction_open(self, code: str) -> bool:
        open_now = self.redis.in_transaction
        for command in split_cell(code):
            name = command.name
            if name == "MULTI":
                open_now = True
            elif name in ("EXEC", "DISCARD", "RESET"):
                open_now = False
        return open_now

    # -- interrupt / shutdown --------------------------------------------- #

    def do_interrupt(self) -> dict[str, Any]:
        """Acknowledge an interrupt. There is never a loop of ours to stop.

        Streaming commands are refused rather than run (see ``do_execute``), so
        the only thing that can be in flight is a single command redis-py is
        waiting on -- and that ends with its own timeout or the socket, not
        with a flag we could set here.
        """
        return {"status": "ok"}

    def do_shutdown(self, restart: bool) -> dict[str, Any]:
        self.redis.close()
        self._table = None
        return {"status": "ok", "restart": restart}


# --------------------------------------------------------------------------- #
# Cursor helpers
# --------------------------------------------------------------------------- #


def _unique(items: list[str]) -> list[str]:
    """Drop duplicates, keeping the order matches were offered in."""
    return list(dict.fromkeys(items))


class _Cursor:
    """Where the cursor sits, decomposed for completion and inspection."""

    def __init__(self, code: str, cursor_pos: int) -> None:
        pos = max(0, min(cursor_pos, len(code)))
        line_start = code.rfind("\n", 0, pos) + 1
        line_end = code.find("\n", pos)
        #: The whole line the cursor is on.
        self.line = code[line_start : len(code) if line_end == -1 else line_end]

        token_start = pos
        while token_start > line_start and not code[token_start - 1].isspace():
            token_start -= 1
        #: Offset into the cell where the token being completed starts, which
        #: is what a ``complete_reply`` needs for ``cursor_start``.
        self.token_start = token_start
        #: The partial token under the cursor.
        self.prefix = code[token_start:pos]
        #: Everything on the line before that token.
        self.head = code[line_start:token_start]

    def tokens_before(self) -> list[str]:
        """The complete tokens preceding the one being completed."""
        try:
            return split_command(self.head)
        except ValueError:
            return self.head.split()
