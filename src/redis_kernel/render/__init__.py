"""Rich mimebundle renderers, keyed by command name.

``text/plain`` is produced by ``formatter.py`` and is never touched here.
Renderers only *add* representations, and the contract is strict: a renderer
handed a reply shape it did not expect must return ``None`` so the plain text
stands alone. A renderer bug must never lose the user's output, which is why
:func:`render` swallows exceptions as well.

Register one with the :func:`renderer` decorator::

    @renderer("HGETALL")
    def _hgetall(args, reply):
        ...
        return {"text/html": html}

The first renderer that returns a bundle wins. Command lookup tries the
subcommand first (``XINFO STREAM``) then the bare command (``XINFO``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["RENDERERS", "render", "renderer"]

Renderer = Callable[[list[str], Any], "dict[str, Any] | None"]

#: Command name (or ``"COMMAND SUB"``) -> renderers to try, in order.
RENDERERS: dict[str, list[Renderer]] = {}


def renderer(*commands: str) -> Callable[[Renderer], Renderer]:
    """Register a renderer for one or more command names."""

    def decorate(func: Renderer) -> Renderer:
        for command in commands:
            RENDERERS.setdefault(command.upper(), []).append(func)
        return func

    return decorate


def render(args: list[str], reply: Any) -> dict[str, Any] | None:
    """Extra mimebundle entries for this reply, or ``None`` if there are none."""
    if not args:
        return None
    for key in _keys(args):
        for func in RENDERERS.get(key, ()):
            try:
                bundle = func(args, reply)
            except Exception:
                continue  # a broken renderer is not allowed to break output
            if bundle:
                return bundle
    return None


def _keys(args: list[str]) -> list[str]:
    """Lookup keys for ``args``, most specific first."""
    command = args[0].upper()
    if len(args) > 1:
        return [f"{command} {args[1].upper()}", command]
    return [command]
