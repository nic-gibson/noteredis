"""Unit tests for connection settings and ``%connect``.

No server and no kernel: ``parse_connect`` produces a value object, and the
kwargs it turns into are checked without opening a socket. The password gets
its own tests -- a notebook is a file people commit, so "the secret never
reaches the output" is a behaviour, not a nicety.
"""

from __future__ import annotations

from typing import Any

import pytest
from redis.connection import SSLConnection, UnixDomainSocketConnection

from noteredis.client import (
    ConnectionSettings,
    NotConnected,
    RedisSession,
    ServerInfo,
)
from noteredis.magics import (
    MagicError,
    _plain_doc,
    expand_variables,
    handle_magic,
    parse_connect,
)


@pytest.fixture(autouse=True)
def _no_redis_url(monkeypatch: Any) -> None:
    """Unset ``REDIS_URL`` unless a test asks for it.

    Settings fall back to it by design, and CI sets it to point at the service
    container, so a test that leaves it in place is really testing the
    environment it happens to run in.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)


# --------------------------------------------------------------------------- #
# Flags
# --------------------------------------------------------------------------- #


def test_the_redis_cli_flags_map_onto_settings() -> None:
    settings = parse_connect(
        "-h cache.internal -p 6380 -n 4 --user support --pass hunter2 -3".split()
    )
    assert (settings.host, settings.port, settings.db) == ("cache.internal", 6380, 4)
    assert (settings.username, settings.password) == ("support", "hunter2")
    assert settings.protocol == 3


@pytest.mark.parametrize("flag", ["-a", "--pass"])
def test_password_flag_aliases(flag: str) -> None:
    assert parse_connect([flag, "hunter2"]).password == "hunter2"


@pytest.mark.parametrize(
    ("flags", "protocol"),
    [("-3", 3), ("--resp3", 3), ("-2", 2), ("--resp2", 2)],
)
def test_protocol_flags(flags: str, protocol: int) -> None:
    assert parse_connect([flags]).protocol == protocol


def test_a_unix_socket_replaces_host_and_port() -> None:
    settings = parse_connect(["-s", "/var/run/redis.sock"])
    assert settings.socket == "/var/run/redis.sock"
    assert settings.pool_kwargs()["connection_class"] is UnixDomainSocketConnection
    assert settings.pool_kwargs()["path"] == "/var/run/redis.sock"


def test_tls_flags() -> None:
    settings = parse_connect("--tls --insecure".split())
    assert settings.tls is True
    assert settings.tls_insecure is True


def test_insecure_alone_implies_tls() -> None:
    """Connecting in the clear to someone who asked for --insecure is worse."""
    assert parse_connect(["--insecure"]).tls is True


def test_a_certificate_flag_implies_tls(tmp_path: Any) -> None:
    ca = tmp_path / "ca.crt"
    ca.write_text("x")
    settings = parse_connect(["--cacert", str(ca)])
    assert settings.tls is True
    assert settings.tls_insecure is False
    assert settings.cacert == str(ca)


def test_missing_certificate_files_are_caught_early(tmp_path: Any) -> None:
    """An opaque TLS handshake failure is a bad way to learn about a typo."""
    with pytest.raises(MagicError, match="cacert file not found"):
        parse_connect(["--cacert", str(tmp_path / "nope.crt")])


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("form", [["-u"], []])
def test_a_url_seeds_everything(form: list[str]) -> None:
    settings = parse_connect([*form, "rediss://support:hunter2@cache:6380/2"])
    assert (settings.host, settings.port, settings.db) == ("cache", 6380, 2)
    assert (settings.username, settings.password) == ("support", "hunter2")
    assert settings.tls is True


def test_url_query_arguments_still_work() -> None:
    settings = parse_connect(["-u", "rediss://cache:6380?ssl_cert_reqs=none"])
    assert settings.tls is True
    assert settings.tls_insecure is True


def test_flags_override_the_url_they_follow() -> None:
    settings = parse_connect(["-u", "redis://cache:6379/0", "-h", "other", "-n", "3"])
    assert (settings.host, settings.db) == ("other", 3)


def test_a_unix_url_becomes_a_socket() -> None:
    assert parse_connect(["unix:///var/run/redis.sock"]).socket == "/var/run/redis.sock"


# --------------------------------------------------------------------------- #
# Atomicity
# --------------------------------------------------------------------------- #


def test_omitted_values_fall_back_to_the_environment(monkeypatch: Any) -> None:
    monkeypatch.setenv("REDIS_URL", "rediss://envhost:6390/7")
    settings = parse_connect(["-h", "override"])
    assert settings.host == "override"
    # ...and the rest comes from REDIS_URL, not from any previous %connect.
    assert (settings.port, settings.db, settings.tls) == (6390, 7, True)


def test_connect_replaces_the_whole_session_setting(monkeypatch: Any) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    session = RedisSession()
    session.settings = parse_connect("-h first --tls --insecure --user support".split())
    # A later %connect that says nothing about TLS gets no TLS.
    plain = parse_connect(["-h", "second"])
    assert plain.tls is False
    assert plain.username is None


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #


def test_expand_variables(monkeypatch: Any) -> None:
    monkeypatch.setenv("PW", "hunter2")
    assert expand_variables("${PW}") == "hunter2"
    assert expand_variables("$PW") == "hunter2"
    assert expand_variables("pre-${PW}-post") == "pre-hunter2-post"
    assert expand_variables("nothing") == "nothing"


def test_an_unset_variable_is_an_error_not_an_empty_string(monkeypatch: Any) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(MagicError, match=r"\$\{NOPE\} is not set"):
        parse_connect(["--pass", "${NOPE}"])


def test_the_password_can_come_from_the_environment(monkeypatch: Any) -> None:
    monkeypatch.setenv("REDIS_PASSWORD", "hunter2")
    assert parse_connect(["--pass", "${REDIS_PASSWORD}"]).password == "hunter2"


def test_askpass_uses_the_prompt() -> None:
    asked: list[str] = []

    def prompt(message: str) -> str:
        asked.append(message)
        return "hunter2"

    assert parse_connect(["--askpass"], prompt).password == "hunter2"
    assert asked == ["Password: "]


def test_askpass_without_a_prompt_says_what_to_do_instead() -> None:
    with pytest.raises(MagicError, match=r"--askpass needs a frontend"):
        parse_connect(["--askpass"], None)


def test_askpass_and_pass_together_are_refused() -> None:
    with pytest.raises(MagicError, match="mutually exclusive"):
        parse_connect(["--askpass", "--pass", "hunter2"], lambda _: "x")


@pytest.mark.parametrize("rendered", ["describe", "to_url"])
def test_the_password_never_reaches_the_output(rendered: str) -> None:
    settings = parse_connect("-h cache --user support --pass hunter2".split())
    text = str(getattr(settings, rendered)())
    assert "hunter2" not in text
    assert "support" in text  # the user is fine to show; the secret is not


def test_status_output_reports_that_a_password_is_set() -> None:
    settings = parse_connect("-h cache --pass hunter2 --tls --insecure".split())
    rendered = ServerInfo(settings=settings, version="8.2.1", mode="standalone").render()
    assert "password: (set)" in rendered
    assert "tls:      on (certificate check disabled)" in rendered
    assert "hunter2" not in rendered


def test_status_output_when_no_password_is_used() -> None:
    assert "password: (none)" in ServerInfo(settings=ConnectionSettings()).render()


# --------------------------------------------------------------------------- #
# Turning settings into a client
# --------------------------------------------------------------------------- #


def test_pool_kwargs_for_a_plain_connection() -> None:
    kwargs = ConnectionSettings(host="cache", port=6380, db=2).pool_kwargs()
    assert kwargs["host"] == "cache"
    assert kwargs["port"] == 6380
    assert kwargs["db"] == 2
    assert kwargs["decode_responses"] is False
    assert "connection_class" not in kwargs
    # No credentials means no credential arguments at all, not empty ones.
    assert "username" not in kwargs
    assert "password" not in kwargs


@pytest.mark.parametrize(("insecure", "cert_reqs"), [(True, "none"), (False, "required")])
def test_pool_kwargs_for_tls(insecure: bool, cert_reqs: str) -> None:
    kwargs = ConnectionSettings(tls=True, tls_insecure=insecure).pool_kwargs()
    assert kwargs["connection_class"] is SSLConnection
    assert kwargs["ssl_cert_reqs"] == cert_reqs


def test_pool_kwargs_passes_the_tls_files_through() -> None:
    kwargs = ConnectionSettings(
        tls=True, cacert="/ca", cacertdir="/cadir", cert="/cert", key="/key"
    ).pool_kwargs()
    assert kwargs["ssl_ca_certs"] == "/ca"
    assert kwargs["ssl_ca_path"] == "/cadir"
    assert kwargs["ssl_certfile"] == "/cert"
    assert kwargs["ssl_keyfile"] == "/key"


def test_cluster_kwargs_drops_what_a_cluster_refuses() -> None:
    """RedisCluster raises on 'db', and takes host/port as explicit arguments."""
    kwargs = ConnectionSettings(host="cache", port=6380, db=0, tls=True).cluster_kwargs()
    assert "db" not in kwargs
    assert "host" not in kwargs
    assert "port" not in kwargs
    assert "connection_class" not in kwargs
    assert kwargs["ssl"] is True
    assert kwargs["ssl_cert_reqs"] == "required"


# --------------------------------------------------------------------------- #
# Bad input
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["-h"], "-h needs a value"),
        (["--pass"], "--pass needs a value"),
        (["--wat"], "unknown option '--wat'"),
        (["-p", "chips"], "'chips' is not a port number"),
        (["-n", "many"], "'many' is not a db number"),
        (["cache.internal"], "unexpected argument"),
        (["--sni", "cache"], "cannot honour"),
        (["--tls-ciphers", "HIGH"], "cannot honour"),
    ],
)
def test_bad_flags_are_rejected_with_an_explanation(args: list[str], message: str) -> None:
    with pytest.raises(MagicError, match=message):
        parse_connect(args)


# --------------------------------------------------------------------------- #
# The session
# --------------------------------------------------------------------------- #


def test_the_session_url_is_redacted() -> None:
    session = RedisSession()
    session.settings = parse_connect("-h cache --user support --pass hunter2 --tls".split())
    assert session.url == "rediss://support:***@cache:6379/0"


def test_setting_the_protocol_updates_the_settings() -> None:
    session = RedisSession()
    session.protocol = 3  # what a bare HELLO 3 does
    assert session.settings.protocol == 3
    assert session.protocol == 3


def test_a_command_refuses_to_dial_when_autoconnect_is_off() -> None:
    session = RedisSession()
    session.autoconnect = False
    with pytest.raises(NotConnected, match="Run %connect first"):
        session.execute(["GET", "key"])


def test_config_toggles_autoconnect() -> None:
    session = RedisSession()
    assert handle_magic(session, ["%config", "autoconnect", "off"]) == "autoconnect = off\n"
    assert session.autoconnect is False
    assert "autoconnect = off" in handle_magic(session, ["%config"])


def test_status_names_the_target_before_connecting(monkeypatch: Any) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://cache:6390/1")
    session = RedisSession()
    assert handle_magic(session, ["%status"]) == (
        "not connected. Target would be redis://cache:6390/1\n"
    )


def test_protocol_magic_keeps_the_rest_of_the_connection() -> None:
    session = RedisSession()
    session.settings = parse_connect("-h cache -p 6380 --tls --insecure --user support".split())
    connected: list[Any] = []
    session.connect = lambda settings=None: (  # type: ignore[method-assign]
        connected.append(settings),
        ServerInfo(settings=settings or session.settings),
    )[1]

    handle_magic(session, ["%protocol", "3"])

    (settings,) = connected
    assert settings.protocol == 3
    assert (settings.host, settings.port) == ("cache", 6380)
    assert settings.tls and settings.tls_insecure
    assert settings.username == "support"


# --------------------------------------------------------------------------- #
# Help
# --------------------------------------------------------------------------- #


def test_help_lists_the_magics() -> None:
    text = handle_magic(RedisSession(), ["%help"])
    for name in ("%connect", "%config", "%status", "%protocol"):
        assert name in text
    assert "``" not in text  # RST markup has no place in terminal output


def test_help_explains_the_connection_flags() -> None:
    """%help connect is how someone in a notebook finds out how to connect."""
    text = handle_magic(RedisSession(), ["%help", "connect"])
    assert text.startswith("%connect [url] [flags]")
    for flag in ("-h HOST", "--user", "--askpass", "--tls", "--insecure", "--cacert"):
        assert flag in text
    assert "${REDIS_PASSWORD}" in text
    assert "``" not in text


def test_help_takes_a_name_with_or_without_the_percent() -> None:
    session = RedisSession()
    assert handle_magic(session, ["%help", "status"]) == handle_magic(session, ["%help", "%status"])


def test_help_on_an_unknown_magic() -> None:
    with pytest.raises(MagicError, match="unknown magic"):
        handle_magic(RedisSession(), ["%help", "nope"])


@pytest.mark.parametrize(
    "doc",
    [
        # As Python 3.10-3.12 hand it over: continuation lines still indented.
        "``%x`` -- summary.\n\n    A paragraph.\n\n      A block.\n    ",
        # As Python 3.13 and later hand it over: already dedented.
        "``%x`` -- summary.\n\nA paragraph.\n\n  A block.\n",
    ],
)
def test_help_text_reads_the_same_whatever_python_does_to_docstrings(doc: str) -> None:
    assert _plain_doc(doc) == "%x -- summary.\n\nA paragraph.\n\n  A block.\n"


def test_handle_magic_passes_the_prompt_through() -> None:
    session = RedisSession()
    session.connect = lambda settings=None: ServerInfo(  # type: ignore[method-assign]
        settings=settings or session.settings
    )
    rendered = handle_magic(session, ["%connect", "-h", "cache", "--askpass"], lambda _: "hunter2")
    assert "password: (set)" in rendered
    assert "hunter2" not in rendered
