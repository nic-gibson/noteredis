"""Member/score (and field/value) replies as two-column tables.

``ZRANGE ... WITHSCORES`` and friends, plus ``HRANDFIELD ... WITHVALUES``,
share one shape (see ``as_score_pairs``) that ``HGETALL``'s map-shaped
``as_pairs`` cannot read: RESP3 keeps members ordered and repeatable, so it
answers with an array of two-element arrays rather than a map, not a map
keyed by member.

``ZPOPMIN``/``ZPOPMAX`` always carry scores -- there is no flag to check --
but everything else here only carries a second column when the command line
asks for one, so the flag is checked before trying to parse the reply that
way; without it, the reply is just a flat list of members and pairing
consecutive elements would silently invent a wrong table.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import table
from ._shapes import as_score_pairs

__all__ = ["render_score_pairs"]

#: command -> (first column, second column, the flag that must be present --
#: or ``None`` when the command always carries a second column).
_COMMANDS: dict[str, tuple[str, str, str | None]] = {
    "ZRANGE": ("member", "score", "WITHSCORES"),
    "ZREVRANGE": ("member", "score", "WITHSCORES"),
    "ZRANGEBYSCORE": ("member", "score", "WITHSCORES"),
    "ZREVRANGEBYSCORE": ("member", "score", "WITHSCORES"),
    "ZDIFF": ("member", "score", "WITHSCORES"),
    "ZINTER": ("member", "score", "WITHSCORES"),
    "ZUNION": ("member", "score", "WITHSCORES"),
    "ZRANDMEMBER": ("member", "score", "WITHSCORES"),
    "ZPOPMIN": ("member", "score", None),
    "ZPOPMAX": ("member", "score", None),
    "HRANDFIELD": ("field", "value", "WITHVALUES"),
}


@renderer(*_COMMANDS)
def render_score_pairs(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A two-column table, or nothing if the flag is absent or the shape is wrong."""
    info = _COMMANDS.get(args[0].upper())
    if info is None:
        return None
    left, right, flag = info
    if flag is not None and not any(tok.upper() == flag for tok in args[1:]):
        return None
    pairs = as_score_pairs(reply)
    if not pairs:
        return None
    return {"text/html": table([left, right], pairs)}
