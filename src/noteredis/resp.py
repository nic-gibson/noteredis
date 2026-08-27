"""Faithful RESP parsers that plug into redis-py's ``parser_class`` hook.

redis-py's own parsers are lossy in ways that matter for byte-for-byte
``redis-cli`` fidelity:

============  ==========================  =================================
RESP byte     redis-py gives you          what that loses
============  ==========================  =================================
``+``         ``bytes``                   same as ``$``; status prints bare
``~``         ``list``                    same as ``*``; needs ``~`` markers
``(``         ``int``                     same as ``:``
``,``         ``float``                   the server's exact digits
``=``         ``bytes``                   the ``txt:``/``mkd:`` format hint
``-``         ``ResponseError(msg)``      the error code is stripped off
============  ==========================  =================================

We subclass redis-py's parsers rather than replacing the transport, so
connection setup, TLS, ACL auth, retries, cluster MOVED/ASK handling and pubsub
all keep working. Only the reply decoding changes, and every value we return
subclasses the type redis-py would have produced, so redis-py's internals --
which do things like ``handshake_metadata.get(b"proto")`` -- are unaffected.

Nothing here decodes text: values stay ``bytes`` so ``formatter.py`` can escape
them exactly as redis-cli does. No kernel imports.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

from redis._parsers.resp2 import _RESP2Parser
from redis._parsers.resp3 import _RESP3Parser
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import InvalidResponse
from redis.utils import SENTINEL

from .formatter import (
    RAW_ERROR_ATTR,
    BigNumber,
    Double,
    Push,
    RedisMap,
    RedisSet,
    Status,
    Verbatim,
)

__all__ = ["FaithfulRESP2Parser", "FaithfulRESP3Parser", "parser_for_protocol"]

SERVER_CLOSED = "Connection closed by server."


class _FaithfulMixin:
    """Shared error handling for both protocol versions."""

    #: Supplied by redis-py's parser base, which this mixin is always combined
    #: with. Declared so the call below type-checks without reaching into it.
    parse_error: Callable[[str], BaseException]

    def _faithful_error(self, text: str) -> BaseException:
        """Build redis-py's exception for ``text`` but keep the full original.

        Going through ``parse_error`` preserves the exception *class*, which is
        what makes cluster redirects (``MovedError``/``AskError``) and
        ``BusyLoadingError`` retries keep working. We only add the raw text
        back, since ``parse_error`` strips the error code.
        """
        # parse_error is untyped upstream; the annotation pins what we rely on.
        error: BaseException = self.parse_error(text)
        # Exotic exception types can refuse attributes; the text is a bonus.
        with suppress(AttributeError):
            setattr(error, RAW_ERROR_ATTR, text)
        return error


class FaithfulRESP2Parser(_FaithfulMixin, _RESP2Parser):
    """RESP2, distinguishing simple strings from bulk strings."""

    def _read_response(self, disable_decoding: bool = False, timeout: Any = SENTINEL) -> Any:
        raw = self._buffer.readline(timeout=timeout)  # type: ignore[union-attr]
        if not raw:
            raise RedisConnectionError(SERVER_CLOSED)

        byte, response = raw[:1], raw[1:]

        if byte == b"-":
            error = self._faithful_error(response.decode("utf-8", "replace"))
            # Connection-level problems are raised immediately; a
            # ResponseError may belong to a pipeline, so it is returned.
            if isinstance(error, RedisConnectionError):
                raise error
            return error
        if byte == b"+":
            return Status(response)
        if byte == b":":
            return int(response)
        if byte == b"$":
            if response == b"-1":
                return None
            return bytes(self._buffer.read(int(response), timeout=timeout))  # type: ignore[union-attr]
        if byte == b"*":
            if response == b"-1":
                return None
            return [
                self._read_response(disable_decoding=disable_decoding, timeout=timeout)
                for _ in range(int(response))
            ]
        raise InvalidResponse(f"Protocol Error: {raw!r}")


class FaithfulRESP3Parser(_FaithfulMixin, _RESP3Parser):
    """RESP3, preserving set order, double digits, verbatim hints and big numbers."""

    def _read_response(
        self,
        disable_decoding: bool = False,
        push_request: bool = False,
        timeout: Any = SENTINEL,
    ) -> Any:
        raw = self._buffer.readline(timeout=timeout)  # type: ignore[union-attr]
        if not raw:
            raise RedisConnectionError(SERVER_CLOSED)

        byte, response = raw[:1], raw[1:]
        read = self._buffer.read  # type: ignore[union-attr]

        def recurse() -> Any:
            return self._read_response(
                disable_decoding=disable_decoding,
                push_request=push_request,
                timeout=timeout,
            )

        # Errors: simple (`-`) and blob (`!`).
        if byte in (b"-", b"!"):
            text = read(int(response), timeout=timeout) if byte == b"!" else response
            error = self._faithful_error(bytes(text).decode("utf-8", "replace"))
            if isinstance(error, RedisConnectionError):
                raise error
            return error

        # Scalars.
        if byte == b"+":
            return Status(response)
        if byte == b"_":
            return None
        if byte == b":":
            return int(response)
        if byte == b"(":
            return BigNumber(bytes(response))
        if byte == b",":
            return Double(bytes(response))
        if byte == b"#":
            return response == b"t"
        if byte == b"$":
            if response == b"-1":  # not legal in RESP3, but servers downgrade
                return None
            return bytes(read(int(response), timeout=timeout))
        if byte == b"=":
            blob = bytes(read(int(response), timeout=timeout))
            # "txt:payload" -- keep the hint, hand on the payload.
            return Verbatim(blob[4:], blob[:3].decode("ascii", "replace"))

        # Aggregates.
        if byte == b"*":
            if response == b"-1":
                return None
            return [recurse() for _ in range(int(response))]
        if byte == b"~":
            return RedisSet([recurse() for _ in range(int(response))])
        if byte == b"%":
            return RedisMap([(recurse(), recurse()) for _ in range(int(response))])
        if byte == b"|":
            # Attributes are metadata attached to the *next* reply. redis-cli
            # cannot render them at all, so we read and discard, then return
            # the reply they decorate.
            RedisMap([(recurse(), recurse()) for _ in range(int(response))])
            return recurse()
        if byte == b">":
            frame = Push([recurse() for _ in range(int(response))])
            handled = self.handle_push_response(frame)  # type: ignore[no-untyped-call]
            if push_request:
                return handled if handled is not None else frame
            # Not a push request: the caller is still waiting for its reply.
            return self._read_response(
                disable_decoding=disable_decoding,
                push_request=push_request,
                timeout=timeout,
            )

        raise InvalidResponse(f"Protocol Error: {raw!r}")


def parser_for_protocol(protocol: int) -> type:
    """The parser class to hand redis-py for ``protocol`` (2 or 3).

    redis-py auto-upgrades ``_RESP2Parser`` to ``_RESP3Parser`` when the
    protocol is 3, but only for that exact class -- our subclasses are not
    covered, so we pick the right one here.
    """
    return FaithfulRESP3Parser if int(protocol) == 3 else FaithfulRESP2Parser
