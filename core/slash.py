"""Slash commands — a small shared dispatcher for ``/word`` lines.

Recommended plugin convention (see docs/screens.md):

* Any input loop that reads whole lines may hand ``/…`` entries to
  :func:`handle_slash` *before* interpreting them itself.
* Core owns ``/screen`` — it prints the generated (permission-filtered)
  default menu of the current interface, bypassing any file reskin. A sysop
  on an artsy override can always summon the real command surface.
* Plugins may register extra slash commands; keep them lowercase and
  namespaced to the plugin when they're not universal.

Single-key menus don't consult this dispatcher (a leading ``/`` there is
just an invalid key); line-mode flows do.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from core import runner

logger = logging.getLogger("modulo.slash")

#: (command-word) -> async fn(bbs, session, argument) -> bool handled
_HANDLERS: dict[str, Callable[[object, object, str], Awaitable[bool]]] = {}


def register(command: str, fn) -> None:
    """Register ``/command``. ``fn`` is async: (bbs, session, arg) -> bool."""
    _HANDLERS[command.lower().lstrip("/")] = fn


async def handle_slash(bbs, session, line: str) -> bool:
    """Dispatch a ``/…`` line. Returns True if handled.

    Unrecognised commands print a short hint and count as handled so the
    caller's loop just re-prompts.
    """
    text = (line or "").strip()
    if not text.startswith("/"):
        return False
    parts = text[1:].split(None, 1)
    word = (parts[0] if parts else "").lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    # /screen is core-owned: the generated, permission-aware view.
    if word == "screen":
        await _cmd_screen(bbs, session, arg)
        return True

    fn = _HANDLERS.get(word)
    if fn is not None:
        try:
            handled = await fn(bbs, session, arg)
            return True if handled is None else bool(handled)
        except Exception:  # noqa: BLE001 - a broken command must not kill a session
            logger.exception("slash command /%s failed", word)
            await bbs.send(session, "\r\n! command failed\r\n")
            return True

    await bbs.send(
        session,
        "\r\nUnknown command. Try /screen (show real menu), /help.\r\n",
    )
    return True


async def _cmd_screen(bbs, session, arg: str) -> None:
    """``/screen`` — toggle generated-view mode (saved in user preferences).

    On: every reskin is bypassed; menus render as generated defaults,
    filtered to your permissions. Off: sysop skins show normally.
    ``/screen <plugin> [name]`` still one-shots a specific generated screen
    without touching the preference.
    """
    svc = getattr(bbs, "screens", None)
    if svc is None:
        await bbs.send(session, "\r\n! screen service unavailable\r\n")
        return

    # One-shot form: /screen <plugin> [name] — peek without toggling.
    if arg:
        targets: list[tuple[str, str]] = []
        bits = arg.split()
        if len(bits) >= 2:
            targets.append((bits[0], bits[1]))
        elif (bits[0], bits[0]) in svc._generators:
            targets.append((bits[0], bits[0]))
        else:
            for name in sorted(svc.screen_names(bits[0])):
                if (bits[0], name) in svc._generators:
                    targets.append((bits[0], name))
        if not targets:
            await bbs.send(session, "\r\nNo such generated screen.\r\n")
            return
        for plugin, name in targets:
            await bbs.send(
                session,
                f"\r\n--- {plugin}/{name} (generated defaults, your permissions) ---\r\n",
            )
            await bbs.send(session, svc.render_default(session, plugin, name))
            await bbs.send(session, "\r\n")
        return

    # Toggle form: flip preferences["screen_mode"].
    user = getattr(session, "user", None)
    if user is None:
        await bbs.send(
            session, "\r\n! login required to save a screen preference\r\n"
        )
        return
    prefs = dict(getattr(user, "preferences", None) or {})
    turning_on = prefs.get("screen_mode") != "generated"
    if turning_on:
        prefs["screen_mode"] = "generated"
    else:
        prefs.pop("screen_mode", None)

    try:
        await bbs.users.update(user.username, preferences=prefs)
        # Update in-memory copy too so this session sees it immediately.
        fresh = await bbs.users.get(user.username)
        if fresh is not None:
            session.user = fresh
    except Exception:  # noqa: BLE001
        logger.exception("could not save screen_mode preference")
        await bbs.send(session, "\r\n! could not save preference\r\n")
        return

    state = (
        "ON - menus render as generated defaults (your permissions)"
        if turning_on
        else "OFF - sysop skins show normally"
    )
    await bbs.send(session, f"\r\n* Machine view {state}.\r\n")
    if not turning_on:
        await bbs.send(
            session,
            "(use /screen again to switch back on)\r\n",
        )


async def _cmd_help(bbs, session, arg: str) -> None:
    cmds = ["/screen"] + sorted("/" + c for c in _HANDLERS)
    await bbs.send(session, "\r\nCommands: " + ", ".join(cmds) + "\r\n")


register("help", _cmd_help)
