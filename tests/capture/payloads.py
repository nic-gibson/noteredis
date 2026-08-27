"""RESP payloads used to capture redis-cli's exact output.

Each entry is raw RESP wire bytes plus the protocol version to ask redis-cli
for. ``generate.py`` serves these from a fake server, records what redis-cli
prints, and writes ``tests/captured/redis_cli_replies.json``. The formatter
tests then replay the same bytes through our parser and formatter and require
the output to match, so no expectation is ever written by hand.

Add a case here, re-run ``python tests/capture/generate.py``, and commit the
regenerated JSON.
"""

from __future__ import annotations

# name -> (protocol, wire bytes)
PAYLOADS: dict[str, tuple[int, bytes]] = {}


def add(name: str, wire: bytes, protocol: int = 3) -> None:
    PAYLOADS[name] = (protocol, wire)


def both(name: str, wire: bytes) -> None:
    """Register a RESP2-legal payload under both protocol versions."""
    add(f"{name}_resp2", wire, protocol=2)
    add(f"{name}_resp3", wire, protocol=3)


# --------------------------------------------------------------------------- #
# Scalars valid in both protocols
# --------------------------------------------------------------------------- #

both("status_ok", b"+OK\r\n")
both("status_pong", b"+PONG\r\n")
both("status_multiword", b"+some status here\r\n")
both("status_type", b"+string\r\n")
both("integer_one", b":1\r\n")
both("integer_zero", b":0\r\n")
both("integer_negative", b":-42\r\n")
both("integer_max", b":9223372036854775807\r\n")
both("bulk_hello", b"$5\r\nhello\r\n")
both("bulk_empty", b"$0\r\n\r\n")
both("bulk_numeric", b"$3\r\n1.5\r\n")
both("error_err", b"-ERR value is not an integer or out of range\r\n")
both("error_wrongtype", b"-WRONGTYPE Operation against a key holding the wrong kind of value\r\n")
both("error_no_code", b"-just a message\r\n")
both("error_moved", b"-MOVED 3999 127.0.0.1:6381\r\n")

# --------------------------------------------------------------------------- #
# Bulk string escaping -- sdscatrepr
# --------------------------------------------------------------------------- #

both("escape_specials", b'$9\r\na\nb\tc\\d"e\r\n')
both("escape_binary", b"$4\r\nx\xac\x01y\r\n")
both("escape_utf8", b"$9\r\nh\xc3\xa9llo\xe2\x86\x92\r\n")
both("escape_cr", b"$3\r\na\rb\r\n")
both("escape_bell_backspace", b"$4\r\na\ab\b\r\n")
both("escape_del", b"$3\r\na\x7fb\r\n")
both("escape_all_bytes", b"$16\r\n" + bytes(range(16)) + b"\r\n")
both("escape_high_bytes", b"$16\r\n" + bytes(range(240, 256)) + b"\r\n")

# --------------------------------------------------------------------------- #
# Nil and arrays
# --------------------------------------------------------------------------- #

both("nil_bulk", b"$-1\r\n")
both("nil_array", b"*-1\r\n")
both("array_empty", b"*0\r\n")
both("array_flat", b"*3\r\n$1\r\na\r\n$1\r\nb\r\n$1\r\nc\r\n")
both("array_mixed", b"*3\r\n:1\r\n$3\r\ntwo\r\n$-1\r\n")
both("array_hgetall_resp2", b"*4\r\n$2\r\nf1\r\n$2\r\nv1\r\n$2\r\nf2\r\n$2\r\nv2\r\n")
both(
    "array_nested",
    b"*3\r\n:1\r\n$3\r\ntwo\r\n*3\r\n:3\r\n$5\r\nthree\r\n*2\r\n:4\r\n*1\r\n:5\r\n",
)
both(
    "array_deep_nested",
    b"*2\r\n*2\r\n*2\r\n:1\r\n:2\r\n*1\r\n:3\r\n$4\r\ntail\r\n",
)
both("array_with_empty", b"*3\r\n:1\r\n*0\r\n$1\r\nx\r\n")
both(
    "array_ten",
    b"*10\r\n" + b"".join(b"$3\r\nv%02d\r\n" % i for i in range(1, 11)),
)
both(
    "array_eleven",
    b"*11\r\n" + b"".join(b"$3\r\nv%02d\r\n" % i for i in range(1, 12)),
)
both(
    "array_hundred_first_nested",
    b"*100\r\n" + b"*2\r\n$1\r\nx\r\n$1\r\ny\r\n" + b"".join(b":%d\r\n" % i for i in range(2, 101)),
)
both(
    "array_eleven_last_nested",
    b"*11\r\n"
    + b"".join(b"$3\r\nv%02d\r\n" % i for i in range(1, 11))
    + b"*2\r\n$1\r\nx\r\n$1\r\ny\r\n",
)
both(
    "array_xrange",
    b"*2\r\n"
    b"*2\r\n$3\r\n1-1\r\n*4\r\n$1\r\na\r\n$1\r\n1\r\n$1\r\nb\r\n$1\r\n2\r\n"
    b"*2\r\n$3\r\n2-1\r\n*2\r\n$1\r\nc\r\n$1\r\n3\r\n",
)
both("array_status_and_error", b"*2\r\n+OK\r\n-ERR Bad\r\n")

# --------------------------------------------------------------------------- #
# RESP3-only scalars
# --------------------------------------------------------------------------- #

add("null", b"_\r\n")
add("bool_true", b"#t\r\n")
add("bool_false", b"#f\r\n")
add("double_fraction", b",1.5\r\n")
add("double_integral", b",3\r\n")
add("double_negative", b",-0.25\r\n")
add("double_inf", b",inf\r\n")
add("double_ninf", b",-inf\r\n")
add("double_nan", b",nan\r\n")
add("verbatim_txt", b"=15\r\ntxt:Some string\r\n")
add("verbatim_multiline", b"=21\r\ntxt:line one\nline two\r\n")
add("verbatim_markdown", b"=13\r\nmkd:# Heading\r\n")
add("verbatim_quotes", b'=15\r\ntxt:say "hi" ok\r\n')
add("blob_error", b"!21\r\nERR this is the error\r\n")

# --------------------------------------------------------------------------- #
# RESP3 maps
# --------------------------------------------------------------------------- #

add("map_empty", b"%0\r\n")
add("map_flat", b"%2\r\n$5\r\nfirst\r\n:1\r\n$6\r\nsecond\r\n:2\r\n")
add("map_hgetall", b"%2\r\n$2\r\nf1\r\n$2\r\nv1\r\n$2\r\nf2\r\n$2\r\nv2\r\n")
add(
    "map_nested",
    b"%2\r\n$5\r\nouter\r\n%2\r\n$1\r\na\r\n:1\r\n$1\r\nb\r\n:2\r\n$5\r\nafter\r\n$3\r\nend\r\n",
)
add(
    "map_array_value",
    b"%2\r\n$4\r\nkeys\r\n*3\r\n$1\r\na\r\n$1\r\nb\r\n$1\r\nc\r\n$5\r\ncount\r\n:3\r\n",
)
add("map_empty_array_value", b"%2\r\n$1\r\na\r\n*0\r\n$1\r\nb\r\n:2\r\n")
add("map_nil_value", b"%1\r\n$1\r\na\r\n_\r\n")
add("map_verbatim_value", b"%1\r\n$1\r\na\r\n=15\r\ntxt:Some string\r\n")
add("map_multiline_verbatim_value", b"%1\r\n$1\r\na\r\n=21\r\ntxt:line one\nline two\r\n")
add("map_in_array", b"*2\r\n%2\r\n$1\r\na\r\n:1\r\n$1\r\nb\r\n:2\r\n$4\r\ntail\r\n")
add("map_value_map_value_array", b"%1\r\n$1\r\na\r\n%1\r\n$1\r\nb\r\n*2\r\n:1\r\n:2\r\n")
add("map_bool_values", b"%2\r\n$1\r\na\r\n#t\r\n$1\r\nb\r\n#f\r\n")
add(
    "map_eleven",
    b"%11\r\n" + b"".join(b"$3\r\nk%02d\r\n:%d\r\n" % (i, i) for i in range(1, 12)),
)
add(
    "map_eleven_last_nested",
    b"%11\r\n"
    + b"".join(b"$3\r\nk%02d\r\n:%d\r\n" % (i, i) for i in range(1, 11))
    + b"$4\r\nlast\r\n*2\r\n$1\r\nx\r\n$1\r\ny\r\n",
)
add("array_of_map_with_array", b"*1\r\n%1\r\n$4\r\nkeys\r\n*2\r\n$1\r\na\r\n$1\r\nb\r\n")

# --------------------------------------------------------------------------- #
# Where a map value sits: inline after "=>", or on the line below it
#
# ``cliIsMultilineValueTTY`` is recursive, and the boundary is not where you
# would guess: a one-element aggregate defers to what it holds, so
# ``1# "a" => 1) "x"`` stays inline while two elements move down a line. These
# pin every branch of that decision.
# --------------------------------------------------------------------------- #

_X = b"$1\r\nx\r\n"

add("mapval_array_one_bulk", b"%1\r\n$1\r\na\r\n*1\r\n" + _X)
add("mapval_array_two_bulk", b"%1\r\n$1\r\na\r\n*2\r\n" + _X + _X)
add("mapval_array_one_holding_two", b"%1\r\n$1\r\na\r\n*1\r\n*2\r\n" + _X + _X)
add("mapval_array_one_holding_one", b"%1\r\n$1\r\na\r\n*1\r\n*1\r\n" + _X)
add("mapval_map_one_pair", b"%1\r\n$1\r\na\r\n%1\r\n$1\r\nk\r\n" + _X)
add(
    "mapval_map_two_pairs",
    b"%1\r\n$1\r\na\r\n%2\r\n$1\r\nk\r\n" + _X + b"$1\r\nj\r\n" + _X,
)
add("mapval_map_one_pair_holding_two", b"%1\r\n$1\r\na\r\n%1\r\n$1\r\nk\r\n*2\r\n" + _X + _X)
add("mapval_set_one_member", b"%1\r\n$1\r\na\r\n~1\r\n" + _X)
add("mapval_set_two_members", b"%1\r\n$1\r\na\r\n~2\r\n" + _X + _X)

# A verbatim value is inline whatever it contains -- its own newlines then run
# flush to the left margin, which is redis-cli's behaviour, not an accident.
_VERBATIM_MULTILINE = b"=21\r\ntxt:line one\nline two\r\n"
add(
    "mapval_verbatim_then_entry",
    b"%2\r\n$1\r\na\r\n" + _VERBATIM_MULTILINE + b"$1\r\nb\r\n$1\r\nz\r\n",
)
add("array_multiline_verbatim_then_entry", b"*2\r\n" + _VERBATIM_MULTILINE + b"$1\r\nz\r\n")
add("set_multiline_verbatim_then_entry", b"~2\r\n" + _VERBATIM_MULTILINE + b"$1\r\nz\r\n")
add(
    "mapval_map_holding_multiline_verbatim",
    b"%1\r\n$1\r\na\r\n%1\r\n$1\r\nb\r\n" + _VERBATIM_MULTILINE,
)

# --------------------------------------------------------------------------- #
# RESP3 sets
# --------------------------------------------------------------------------- #

add("set_empty", b"~0\r\n")
add("set_flat", b"~3\r\n$1\r\nc\r\n$1\r\na\r\n$1\r\nb\r\n")
add("set_nested", b"~2\r\n*2\r\n:1\r\n:2\r\n$1\r\nx\r\n")
add("set_in_map_value", b"%1\r\n$1\r\na\r\n~2\r\n$1\r\nx\r\n$1\r\ny\r\n")
add(
    "set_eleven",
    b"~11\r\n" + b"".join(b"$3\r\nv%02d\r\n" % i for i in range(1, 12)),
)
add("array_of_empty_aggregates", b"*3\r\n*0\r\n%0\r\n~0\r\n")
