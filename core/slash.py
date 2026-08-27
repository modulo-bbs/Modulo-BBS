"""Slash commands — a small shared dispatcher for ``/word`` lines.

Recommended plugin convention (see docs/screens.md):

* Any input loop that reads whole lines may hand ``/…`` entries to
  :func:`handle_slash` *before* interpreting them itself.
* Core owns ``/screen`` — it prints the generated (permission-filtered)
  default menu of the current interface, bypassing any file reskin. A sysop
  on an artsy override can always summon the real command surface.
* Core owns ``/theme`` — list or set the caller's named colour palette
  (a ``themes/*.theme`` file, saved on ``preferences.theme``). At the home
  ``>`` prompt the mainmenu plugin turns a bare ``/theme`` into an up/down
  overlay picker; see ``docs/themes.md``.
* Core owns ``/ver`` (and ``/version``) — print the board version so a
  caller can tell which build is running. See ``core/version.py``.
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
    if not text:
        return False
    # "//theme" (typed the slash twice after the hotkey consumed the first)
    # and "/theme" should dispatch the same way.
    if not text.startswith("/"):
        text = "/" + text
    else:
        text = "/" + text.lstrip("/")
    parts = text[1:].split(None, 1)
    word = (parts[0] if parts else "").lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    # Bare "/" or "/help" — list commands.
    if word in ("", "help"):
        await _cmd_help(bbs, session, arg)
        return True

    # /screen is core-owned: the generated, permission-aware view.
    if word == "screen":
        await _cmd_screen(bbs, session, arg)
        return True
    if word == "theme":
        await _cmd_theme(bbs, session, arg)
        return True
    if word in ("ver", "version"):
        await _cmd_ver(bbs, session, arg)
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
        "\r\nUnknown command. Try /screen, /theme, /ver, /help.\r\n",
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


async def _cmd_theme(bbs, session, arg: str) -> None:
    """``/theme`` — list palettes, or ``/theme amber`` to persist one.

    Saved on ``preferences.theme``; plugins resolve it via
    ``core.theme.palette_for(session)``. Login required to change it.
    """
    from core.theme import load_palette, resolve_theme_name, theme_aliases, theme_name_for, theme_names

    name = (arg or "").strip().lower()
    current = theme_name_for(session)
    names = theme_names()
    aliases = theme_aliases()

    if not name:
        lines = ["", f"Themes (current: {current}):"]
        for t in names:
            mark = " *" if t == current else ""
            lines.append(f"  {t}{mark}")
        lines.append("Use /theme <name> to switch.")
        if aliases:
            bits = ", ".join(f"{a} → {c}" for a, c in sorted(aliases.items()))
            lines.append(f"Aliases: {bits}.")
        await bbs.send(session, "\r\n".join(lines) + "\r\n")
        return

    if name not in names and name not in aliases:
        await bbs.send(
            session,
            "\r\n! unknown theme. Try /theme ("
            + ", ".join(names)
            + ").\r\n",
        )
        return

    chosen = resolve_theme_name(name)

    user = getattr(session, "user", None)
    if user is None:
        await bbs.send(
            session, "\r\n! login required to save a theme preference\r\n"
        )
        return

    prefs = dict(getattr(user, "preferences", None) or {})
    prefs["theme"] = chosen
    try:
        await bbs.users.update(user.username, preferences=prefs)
        fresh = await bbs.users.get(user.username)
        if fresh is not None:
            session.user = fresh
    except Exception:  # noqa: BLE001
        logger.exception("could not save theme preference")
        await bbs.send(session, "\r\n! could not save preference\r\n")
        return

    await bbs.send(session, f"\r\n* Theme set to {chosen}.\r\n")


async def _cmd_ver(bbs, session, arg: str) -> None:
    """``/ver`` — board version (and git short hash when available)."""
    from core.version import NAME, display

    await bbs.send(session, f"\r\n{NAME} {display()}\r\n")


async def _cmd_help(bbs, session, arg: str) -> None:
    cmds = ["/screen", "/theme", "/ver"] + sorted("/" + c for c in _HANDLERS)
    await bbs.send(session, "\r\nCommands: " + ", ".join(cmds) + "\r\n")


register("help", _cmd_help)
