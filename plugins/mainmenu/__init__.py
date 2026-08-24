"""The main menu core-plugin for Modulo BBS.

There is no core menu system -- the menu is just another plugin. This plugin
iterates ``bbs.plugins``, sorts them by ``menu_order`` and renders each
plugin's ``menu_label`` / ``menu_key`` (every plugin self-describes via the
base class), so swapping in a different menu plugin changes nothing else.

Built-in options stay inside this plugin: ``[I] System Info`` and
``[Q] Disconnect``. Per the hard boundary, disconnect is requested through
``bbs.disconnect(session)`` -- the plugin never closes a socket itself.

The menu runs inside its ``on_session_start`` (like the login plugin, the
whole interactive flow lives in the session-start hook) so it can be driven
as a step in the logon sequence. Hotkey-selected plugins are entered via
``core.runner.run_plugin_flow`` (session-start hook followed by a
``handle_command`` loop until the plugin returns False).
"""

from __future__ import annotations

import asyncio
import sys

from plugins.base import Plugin
from shared.telnet_protocol import ANSI

from core import runner


def user_can_access(user, requires) -> bool:
    """Menu visibility gate. Mirrors core User.can_access() for safety when
    user is None or missing (fail-closed for non-empty requirements)."""
    if not requires:
        return True
    if user is None:
        return False
    return user.can_access(requires)

# Session state (guarded so this module imports standalone too).
try:  # pragma: no cover - guard for environments without server.session
    from server.session import SessionState
except Exception:  # noqa: BLE001
    SessionState = None


class MainmenuPlugin(Plugin):
    """Renders the post-login menu and dispatches selections."""

    name = "mainmenu"
    version = "1.0.0"
    description = "Primary command menu (plugin items + System Info / Disconnect)."
    menu_label = "Main Menu"
    menu_key = ""                       # the menu is not itself hotkeyed
    menu_order = 1

    def __init__(self):
        self.bbs = None

    # -- lifecycle ------------------------------------------------------------

    def on_load(self, bbs):
        self.bbs = bbs
        # The generated menu is the *fallback* for screen "main"; a sysop can
        # override it by dropping main.ans / main.asc / main.txt into
        # plugins/mainmenu/screens/ (see docs/screens.md).
        bbs.screens.register_generator(self.name, "main", self._generate_main)

    async def on_session_start(self, session):
        """Render the menu and run the dispatch loop until disconnect."""
        if self.bbs is None:
            return
        if SessionState is not None:
            session.state = SessionState.MAIN_MENU
        self.bbs.events.emit("menu:open", {"session": session, "menu_name": "main"})

        while session.is_active:
            await self._show_menu(session)
            # Single keypress, no Enter -- menu keys are one character.
            key = await runner.read_key(self.bbs, session)
            if key is None:
                break
            if key == "/":
                # Slash command: collect the rest of the line and hand it to
                # the shared dispatcher (/screen, /help, …). The "/" itself
                # was already echoed by the input layer -- do NOT echo again.
                from core.slash import handle_slash

                rest = await runner.read_command(self.bbs, session)
                if rest is None:
                    break
                line = ("/" + rest.strip("\r\n")).strip()
                await handle_slash(self.bbs, session, line)
                continue
            await self._handle(session, key)

    # -- rendering / prompt ---------------------------------------------------

    async def _show_menu(self, session) -> None:
        """Clear screen, render the main menu, show a bottom-aligned ``>`` prompt.

        The prompt always appears regardless of whether the screen came from
        a file override or the generated fallback — it is rendered here, not
        baked into either source.
        """
        h = getattr(session, "terminal_height", 24)

        # ANSI clear screen + home cursor.
        await self.bbs.send(session, "\x1b[2J\x1b[H")

        # Screen content (file or generator).
        screen = self.bbs.screens.render(session, self.name, "main")
        await self.bbs.send(session, screen)

        # Count visible lines to align the prompt on the bottom row.
        from shared.codecs import _ANSI_RE

        clean = _ANSI_RE.sub("", screen)
        lines_used = clean.count("\r\n") + 1
        pad = max(0, h - lines_used - 1)
        if pad:
            await self.bbs.send(session, "\r\n" * pad)

        # Green ``>`` prompt on the bottom line.
        G = ANSI.BRIGHT_GREEN
        R = ANSI.RESET
        await self.bbs.send(session, f"\r\n{G}  >{R}")

    # -- dispatch -------------------------------------------------------------

    async def _handle(self, session, choice: str):
        """Process one main-menu selection."""
        if choice in ("Q", "QUIT", "EXIT", "OFF", "BYE"):
            await self.bbs.send(session, "\r\nGoodbye! Thanks for calling.\r\n")
            await self.bbs.disconnect(session)
            return

        if choice in ("I", "3", "INFO", "SYSTEM", "?"):
            await self.bbs.send(session, self._system_info(session))
            return

        # Sysop-only: graceful shutdown with confirmation.
        if choice == "X":
            await self._sysop_shutdown(session)
            return

        for plugin in self._menuable(session):
            if choice == plugin.menu_key.upper():
                self.bbs.events.emit("menu:select", {
                    "session": session, "option": choice, "menu_name": "main",
                })
                await runner.run_plugin_flow(self.bbs, plugin, session)
                return

        await self.bbs.send(session, "\r\nInvalid selection.\r\n")

    # -- rendering ------------------------------------------------------------

    def _generate_main(self, session=None) -> str:
        """Generated default for screen ``main`` (overridable by file).

        When called with a session (``/screen``), the listing is filtered to
        the caller's permissions — sysops see [S]/[X], regular users don't.
        """
        w = min(80, 60)
        bar = "=" * w
        C = ANSI.BRIGHT_CYAN
        B = ANSI.BOLD
        W = ANSI.BRIGHT_WHITE
        R = ANSI.RESET

        user = getattr(session, "user", None) if session is not None else None
        lines = [C + B + bar + R, C + B + "  Main Menu" + R, C + B + bar + R, ""]
        for plugin in self._menuable_for(user):
            label = getattr(plugin, "menu_label", "") or plugin.name
            if label.startswith("["):
                lines.append(C + f"  {label}" + R)
            else:
                lines.append(C + f"  [{plugin.menu_key.upper()}] {label}" + R)
        lines.append(C + "  [I] System Info" + R)
        if user is not None and user.in_group("sysop"):
            lines.append(C + "  [X] Shutdown" + R)
        lines.append(C + "  [Q] Disconnect" + R)
        return "\r\n".join(lines)

    async def _sysop_shutdown(self, session) -> None:
        """Confirm and execute a graceful BBS shutdown (sysop only)."""
        user = getattr(session, "user", None)
        if not user or not user.in_group("sysop"):
            await self.bbs.send(session, "\r\nInvalid selection.\r\n")
            return

        await self.bbs.send(session, "\r\nShutdown the BBS? [Y/N] ")
        key = await runner.read_key(self.bbs, session)
        if key != "Y":
            return

        await self.bbs.send(session, "\r\nShutting down...\r\n")
        if self.bbs.server:
            # Schedule shutdown on the event loop so this coroutine can
            # return cleanly — stop() closes all writers including ours.
            asyncio.ensure_future(self.bbs.server.stop("SysOp shutdown. Goodbye!"))

    def _system_info(self, session) -> str:
        """Static system information block."""
        mgr = self.bbs.session_manager
        return (
            "\r\n--- System Information ---\r\n"
            f"  Name:     Modulo BBS\r\n"
            f"  Version:  0.1-alpha\r\n"
            f"  Runtime:  Python {sys.version.split()[0]}\r\n"
            f"  Nodes:    {mgr.active_count}/{mgr.max_nodes}\r\n"
            f"  Session:  {session.session_id} @ Node {session.node_id}\r\n"
            f"  Terminal: {session.terminal_type}\r\n"
        )

    def _menuable(self, session=None):
        """Plugins that appear as hotkey-selectable main-menu items.

        A plugin's ``menu_requires`` gate decides visibility per user
        (e.g. the sysop menu only lists for sysops).
        """
        user = getattr(session, "user", None) if session is not None else None
        return self._menuable_for(user)

    def _menuable_for(self, user):
        items = [
            p
            for p in self.bbs.plugins
            if getattr(p, "menu_key", "")
            and user_can_access(user, getattr(p, "menu_requires", None))
        ]
        items.sort(key=lambda p: (getattr(p, "menu_order", 100), p.menu_key.upper()))
        return items


__all__ = ["MainmenuPlugin"]