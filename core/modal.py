"""Resolve the modal plugin, with a tiny core fallback if it is missing.

Plugins call ``await core.modal.choose(bbs, session, labels)`` so a missing
or swapped modal plugin cannot crash the board.
"""
from __future__ import annotations

import asyncio
import logging

from core import runner

logger = logging.getLogger("modulo.core.modal")


async def choose(
    bbs,
    session,
    labels: list[str],
    *,
    default: int = 0,
    hint: str | None = None,
    compact: bool = True,
) -> int | None:
    """Return a selected index, or None on ESC. Delegates to the modal plugin."""
    plugin = bbs.plugin_for("modal") if bbs is not None else None
    if plugin is not None and hasattr(plugin, "choose"):
        result = plugin.choose(
            session, labels, default=default, hint=hint, compact=compact
        )
        if asyncio.iscoroutine(result):
            return await result
        return result
    logger.warning("modal plugin missing; using numbered fallback choose")
    return await _fallback_choose(bbs, session, labels, default=default)


async def notice(bbs, session, body: str) -> None:
    """Show *body* until any key. Delegates to the modal plugin."""
    plugin = bbs.plugin_for("modal") if bbs is not None else None
    if plugin is not None and hasattr(plugin, "notice"):
        result = plugin.notice(session, body)
        if asyncio.iscoroutine(result):
            await result
        return
    logger.warning("modal plugin missing; using plain notice fallback")
    await _fallback_notice(bbs, session, body)


async def _fallback_choose(bbs, session, labels: list[str], *, default: int = 0) -> int | None:
    names = [str(x) for x in (labels or [])]
    if not names:
        return None
    idx = max(0, min(int(default), len(names) - 1))
    lines = [""]
    for i, name in enumerate(names, 1):
        mark = ">" if i - 1 == idx else " "
        lines.append(f" {mark} {i}. {name}")
    lines.append("")
    lines.append(" Number or ESC: ")
    await bbs.send(session, "\r\n".join(lines))
    key = await runner.read_key(bbs, session)
    if key is None or key in ("ESC", "Q"):
        return None
    if key == "ENTER":
        return idx
    if key.isdigit():
        n = int(key)
        if 1 <= n <= len(names):
            return n - 1
    return None


async def _fallback_notice(bbs, session, body: str) -> None:
    text = (body or "").replace("\n", "\r\n")
    await bbs.send(session, "\r\n" + text + "\r\n[Press any key]\r\n")
    await runner.read_key(bbs, session)
