"""``JSON.GET`` as a JSON tree.

RedisJSON answers with a bulk string of JSON text. Handing the parsed value
back as ``application/json`` gets a collapsible tree in JupyterLab for free,
which beats a single long line for anything nested.
"""

from __future__ import annotations

import json
from typing import Any

from . import renderer
from ._shapes import as_text

__all__ = ["render_json"]

#: Refuse to parse anything longer than this. A JSON.GET on a large document
#: would otherwise put a second copy of it in the notebook, parsed.
MAX_BYTES = 1_000_000


@renderer("JSON.GET", "JSON.MGET", "JSON.ARRPOP")
def render_json(args: list[str], reply: Any) -> dict[str, Any] | None:
    """The reply parsed as JSON, or nothing if it is not JSON after all."""
    del args
    text = as_text(reply)
    if text is None or not text.strip() or len(text) > MAX_BYTES:
        return None
    try:
        return {"application/json": json.loads(text)}
    except (ValueError, RecursionError):
        return None
