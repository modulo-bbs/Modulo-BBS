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
from plugins.mainmenu.tabs import load_tabs, visible_tabs
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


def _age_label(iso: str) -> str:
    """Human age like '3m ago', '5d ago', '3mo ago' for display."""
    if not iso:
        return "—"
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 30:
            return f"{days}d ago"
        months = days // 30
        if months < 12:
            return f"{months}mo ago"
        years = days // 365
        return f"{years}y ago"
    except Exception:
        return "—"

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

    def _is_pim(self, session) -> bool:
        """True when this session prefers the tabbed PIM home.

        ``home_mode == "menu"`` pins classic list; anything else (unset,
        ``"pim"``, etc.) renders the tabbed chrome. New users default to PIM.
        """
        user = getattr(session, "user", None)
        prefs = getattr(user, "preferences", {}) if user else {}
        return prefs.get("home_mode", "pim") != "menu"

    def _active_tab_id(self, session) -> str:
        return getattr(session, "_pim_active_tab", None) or "boards"

    def _render_tabs(self, session, tabs: list[dict], active_id: str) -> str:
        """Top tab bar: caps=active in plain fallback, colors in ANSI."""
        # Plain fallback when terminal can't do ANSI (e.g. UNKNOWN)
        is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
        parts: list[str] = []
        for t in tabs:
            label = t["label"]
            is_active = t["id"] == active_id
            if is_plain:
                label = label.upper() if is_active else label
                # active tab in caps per Dave's sketch; no colors
                parts.append(f" {label} ")
            else:
                if is_active:
                    parts.append(f"{ANSI.BRIGHT_WHITE}{ANSI.BG_BLUE} {label} {ANSI.RESET}")
                else:
                    parts.append(f"{ANSI.DIM} {label} {ANSI.RESET}")
        # join with " | " and pad handled by bbs.send
        return " | ".join(parts)

    async def _render_pane(self, session, tab: dict) -> str:
        """Middle pane: filtered conversation list for the active tab.

        Shown inside a CP437 box in ANSI mode, plain dashes in fallback.
        Each row: ``@author (age): preview`` — up/dn to select (Step 8).
        """
        kind = tab.get("kind", "board")
        # Scope: None kind means "all" — don't filter by kind
        filter_kind = None if kind == "all" else kind
        try:
            convs = await self.bbs.conversations.list_conversations(
                kind=filter_kind, visible_to=getattr(session, "user", None)
            )
        except Exception:
            convs = []
        # Show most recent first, cap at pane height (terminal minus chrome)
        h = getattr(session, "terminal_height", 24)
        pane_h = max(5, h - 8)  # tabs(1)+sep(1)+prompt(1)+margins
        convs = convs[:pane_h]

        is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
        if is_plain:
            top = "+" + "-" * 78 + "+"
            bot = "+" + "-" * 78 + "+"
            hint = "        up/dn select      "
            # center hint under top border
            top = "+" + "-" * 20 + "/" + hint + "\\" + "-" * (78 - 20 - len(hint) - 1) + "+"
        else:
            top = f"{ANSI.DIM}┌{'─' * 78}┐{ANSI.RESET}"
            bot = f"{ANSI.DIM}└{'─' * 78}┘{ANSI.RESET}"
            hint = "        up/dn select      "
            top = f"{ANSI.DIM}┌{'─' * 20}┤{hint}├{'─' * (78 - 20 - len(hint) - 1)}┐{ANSI.RESET}"

        lines = [top]
        if not convs:
            label = tab.get('label','conversations').lower()
            lines.append(f"│  (no {label} yet)".ljust(79) + "│")
        else:
            for c in convs:
                # preview: last message author + age + body preview
                title = c.get("title", c.get("id", "?"))[:28]
                preview = ""
                try:
                    msgs = await self.bbs.conversations.list_messages(c["id"])
                    if msgs:
                        last = msgs[-1]
                        author = last.get("author", "?")
                        body = (last.get("body", "") or "").split("\n")[0][:36]
                        # age
                        created = c.get("last_message_at") or c.get("created", "")
                        age = _age_label(created)
                        preview = f"@{author} ({age}): {body}"
                    else:
                        preview = f"{title} (no messages)"
                except Exception:
                    preview = title
                row = f"│  {preview[:76].ljust(76)} │"
                lines.append(row)
        lines.append(bot)
        lines.append("  ↑/↓ or 1/2/3 to switch tabs, Enter to open, Q to disconnect")
        return "\r\n".join(lines)

    async def _show_menu(self, session) -> None:
        """Clear screen, render the home surface, show a bottom-aligned ``>`` prompt.

        Branches on ``preferences.home_mode``:
        - ``menu`` → classic file-or-generator ``screens/main`` list.
        - else  → tabbed PIM chrome (tabs + pane). Prompt is owned here,
          never baked into a screen file. Full clear on every redraw
          guarantees no stale rows survive (the .asc blank-line bug).
        """
        h = getattr(session, "terminal_height", 24)

        # Full clear + home cursor. Always — partial overwrites leave stale
        # rows (the .asc blank-line bug proved this).
        await self.bbs.send(session, "\x1b[2J\x1b[H")

        if self._is_pim(session):
            tabs = visible_tabs(load_tabs(self.bbs), getattr(session, "user", None))
            active_id = self._active_tab_id(session)
            # clamp active to visible set
            if tabs and not any(t["id"] == active_id for t in tabs):
                active_id = tabs[0]["id"]
                session._pim_active_tab = active_id  # type: ignore[attr-defined]
            # band A: tabs
            tab_bar = self._render_tabs(session, tabs, active_id)
            await self.bbs.send(session, tab_bar + "\r\n")
            await self.bbs.send(session, "─" * 79 + "\r\n")
            # band B: pane
            active_tab = next((t for t in tabs if t["id"] == active_id), tabs[0] if tabs else {"id": "boards", "label": "Boards", "kind": "board"})
            pane = await self._render_pane(session, active_tab)
            await self.bbs.send(session, pane + "\r\n")
        else:
            # Classic: file beats generator per docs/screens.md
            screen = self.bbs.screens.render(session, self.name, "main")
            await self.bbs.send(session, screen)

        # Pin the green ``>`` prompt on the very last terminal line.
        G = ANSI.BRIGHT_GREEN
        R = ANSI.RESET
        await self.bbs.send(session, f"\x1b[{h};1H")   # last row
        await self.bbs.send(session, "\x1b[2K")        # clear that row
        await self.bbs.send(session, f"{G}  >{R}")

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
        lines = [C + B + bar + R, C + B + "  Main Menu" + R, C + B + bar + R]
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