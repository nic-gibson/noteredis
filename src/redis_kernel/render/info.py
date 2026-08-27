"""``INFO`` as one table per section.

The reply is a single blob of ``# Section`` headings and ``key:value`` lines.
Two hundred lines of it is exactly the sort of output a table helps with, and
the section headings are already there to group by.
"""

from __future__ import annotations

from typing import Any

from . import renderer
from ._html import table
from ._shapes import as_text

__all__ = ["render_info"]


@renderer("INFO")
def render_info(args: list[str], reply: Any) -> dict[str, Any] | None:
    """A table per section, or nothing if this does not look like INFO."""
    del args
    text = as_text(reply)
    if text is None:
        return None

    sections = _parse(text)
    if not sections:
        return None
    return {
        "text/html": "".join(
            table(("field", "value"), fields.items(), caption=name)
            for name, fields in sections
            if fields
        )
    }


def _parse(text: str) -> list[tuple[str, dict[str, str]]]:
    """Split an ``INFO`` blob into ``(section, {field: value})``.

    Lines before the first heading -- which is how some proxies answer -- go
    under a blank section name rather than being dropped.
    """
    sections: list[tuple[str, dict[str, str]]] = []
    current: dict[str, str] = {}
    name = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if current:
                sections.append((name, current))
            name, current = line.lstrip("# ").strip(), {}
            continue
        if ":" not in line:
            return []  # not INFO-shaped; let the plain text stand alone
        field, _, value = line.partition(":")
        current[field] = value
    if current:
        sections.append((name, current))
    return sections
