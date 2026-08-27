"""The kernel over the real Jupyter messaging protocol.

Everything else in this suite calls ``do_execute`` and friends directly, which
cannot catch a kernel that fails to launch, a kernelspec that names the wrong
interpreter, or a reply that does not validate against the message schema. This
launches the kernel as a subprocess and talks to it over ZMQ.

Needs a Redis server at ``REDIS_URL`` (default ``redis://localhost:6379/0``).
Skipped, not failed, when there is not one -- the rest of the suite still runs
anywhere.

The kernelspec is installed into a temporary directory rather than the user's,
so running the tests does not put a kernel in anyone's launcher.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse

import pytest

pytest.importorskip("jupyter_kernel_test")

import jupyter_kernel_test

from redis_kernel.client import default_url
from redis_kernel.install import KERNEL_NAME, install

# --------------------------------------------------------------------------- #
# Is there a server, and can we install a spec to talk to it?
# --------------------------------------------------------------------------- #


def _server_is_up(url: str, timeout: float = 1.0) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("redis", "rediss"):
        return False  # a unix socket; not worth probing here
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((parsed.hostname or "localhost", parsed.port or 6379)) == 0
    finally:
        sock.close()


REDIS_URL = default_url()

#: Set in CI. Skipping when the server is missing is right on a laptop and
#: wrong on a build machine, where it would let a broken service container
#: report a green run with these tests never having executed.
REQUIRE_SERVER = os.environ.get("REDIS_KERNEL_REQUIRE_SERVER", "") not in ("", "0")

if not _server_is_up(REDIS_URL):
    _message = f"no Redis server at {REDIS_URL}; start one or set REDIS_URL"
    if REQUIRE_SERVER:
        pytest.fail(f"{_message} (REDIS_KERNEL_REQUIRE_SERVER is set)", pytrace=False)
    pytest.skip(_message, allow_module_level=True)

# Install into a temp directory and point Jupyter at it. This has to happen at
# import time: jupyter_kernel_test looks the spec up in setUpClass.
_SPEC_HOME = tempfile.mkdtemp(prefix="redis-kernel-spec-")
os.environ["JUPYTER_PATH"] = _SPEC_HOME
os.environ["JUPYTER_DATA_DIR"] = _SPEC_HOME
#: A "user" install, but JUPYTER_DATA_DIR above has redirected what that means.
install(user=True, executable=sys.executable)


# --------------------------------------------------------------------------- #
# The standard suite
# --------------------------------------------------------------------------- #


class RedisKernelTests(jupyter_kernel_test.KernelTests):
    """jupyter_kernel_test's checks, for the features this kernel has."""

    kernel_name = KERNEL_NAME
    language_name = "redis"
    file_extension = ".redis"

    #: Must contain the literal "hello, world" in a stdout stream message.
    code_hello_world = 'ECHO "hello, world"'

    #: Shift-Tab on a command name.
    code_inspect_sample = "GET"

    #: Match sets are compared exactly, so these use prefixes with exactly one
    #: completion on a stock server rather than something like "GE".
    completion_samples = [  # noqa: RUF012
        {"text": "%conn", "matches": {"%connect"}},
        {"text": "GETR", "matches": {"GETRANGE"}},
        {"text": "OBJECT ENC", "matches": {"ENCODING"}},
    ]

    complete_code_samples = [  # noqa: RUF012
        "PING",
        'SET greeting "hello world"',
        "MULTI\nEXEC",
    ]
    incomplete_code_samples = [  # noqa: RUF012
        'SET greeting "unbalanced',  # a quote left open
        "MULTI",  # a transaction left open
    ]

    code_display_data = [  # noqa: RUF012
        {"code": "HSET jkt:hash field value\nHGETALL jkt:hash", "mime": "text/html"},
        {"code": "HGETALL jkt:hash", "mime": "text/plain"},
    ]

    # Deliberately not set:
    #   code_generate_error -- a Redis error reply prints "(error) ..." on stdout
    #     and the cell still succeeds, so there is no error *reply* to check.
    #   code_execute_result -- replies go out as stream or display_data
    #     messages; this kernel never sends execute_result.
    #   code_page_something, code_clear_output, code_history_pattern -- no pager,
    #     no clear_output, no history.

    # -- this kernel's own protocol-level behaviour ------------------------ #

    def test_a_cell_runs_every_command_in_order(self) -> None:
        self.flush_channels()
        reply, messages = self.execute_helper("SET proto:a 1\nGET proto:a\nDEL proto:a")
        self.assertEqual(reply["content"]["status"], "ok")
        assert self._stdout(messages) == 'OK\n"1"\n(integer) 1\n'

    def test_an_error_reply_does_not_fail_the_cell(self) -> None:
        """Interactive redis-cli carries on, and so does a cell."""
        self.flush_channels()
        reply, messages = self.execute_helper("NOSUCHCOMMAND\nECHO after")
        # The cell succeeded: Redis rejecting a command is not a kernel fault.
        self.assertEqual(reply["content"]["status"], "ok")
        stdout = self._stdout(messages)
        assert "(error)" in stdout
        assert '"after"' in stdout  # and the next line still ran

    def test_rich_output_carries_the_plain_text_too(self) -> None:
        self.flush_channels()
        _, messages = self.execute_helper("DEL proto:h\nHSET proto:h f v\nHGETALL proto:h")
        bundles = [m["content"]["data"] for m in messages if m["msg_type"] == "display_data"]
        assert len(bundles) == 1
        assert "<table>" in bundles[0]["text/html"]
        # RESP3 is the default protocol, so HGETALL comes back as a map.
        assert bundles[0]["text/plain"] == '1# "f" => "v"\n'

    def test_plain_mode_sends_no_rich_output(self) -> None:
        # Sets its own key up: these tests share a server, and one that leaned
        # on another test's data would pass or fail depending on the order.
        self.flush_channels()
        _, messages = self.execute_helper(
            "%render plain\nDEL proto:plain\nHSET proto:plain f v\nHGETALL proto:plain"
        )
        assert not [m for m in messages if m["msg_type"] == "display_data"]
        assert '1# "f"' in self._stdout(messages)

    def test_status_reports_the_server(self) -> None:
        self.flush_channels()
        _, messages = self.execute_helper("%status")
        assert "server:" in self._stdout(messages)

    def test_a_streaming_command_is_refused_rather_than_hanging(self) -> None:
        """If this regressed, the test would time out instead of failing."""
        self.flush_channels()
        reply, messages = self.execute_helper("SUBSCRIBE proto:channel", timeout=15)
        self.assertEqual(reply["content"]["status"], "ok")
        assert "not supported by this kernel" in self._stdout(messages)

    def test_the_transaction_state_spans_cells(self) -> None:
        self.flush_channels()
        try:
            _, messages = self.execute_helper("MULTI\nSET proto:t 1")
            assert "QUEUED" in self._stdout(messages)
            # A new cell, still inside the transaction.
            self.flush_channels()
            _, messages = self.execute_helper("SET proto:t 2")
            assert "QUEUED" in self._stdout(messages)
        finally:
            self.flush_channels()
            self.execute_helper("DISCARD")

    @staticmethod
    def _stdout(messages: list[dict[str, Any]]) -> str:
        return "".join(
            message["content"]["text"]
            for message in messages
            if message["msg_type"] == "stream" and message["content"]["name"] == "stdout"
        )
