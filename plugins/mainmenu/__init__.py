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

from core import modal as core_modal
from core import runner
from core.theme import palette_for


def _collapse_overlay_spacing(text: str) -> str:
    """Save-time: collapse runs of blank lines to a single blank line.

    Double-spacing and above become one empty line between paragraphs.
    Leading/trailing blanks are dropped. Intra-line spaces are untouched.
    """
    lines: list[str] = []
    pending_blank = False
    for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if ln.strip() == "":
            if lines:
                pending_blank = True
            continue
        if pending_blank:
            lines.append("")
            pending_blank = False
        lines.append(ln)
    return "\n".join(lines)


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
        return "-"
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
        return "-"


def _elided(prefix: str, items: list[str], sep: str = ", ", width: int = 77) -> str:
    """Build prefix + sep.join(items) elided with '...' to fit width (visible cols).
    Width is inner width (79 - 2 box chars). CP437-safe '...' not '…'.
    e.g. prefix='DMs: (10 new) from ', items=['Anna','Bob',...] -> 'DMs: (10 new) from Anna, Bob, ...'
    """
    if not items:
        return prefix.rstrip()
    # try full join, then truncate tail
    full = prefix + sep.join(items)
    if len(full) <= width:
        return full
    # need elision: keep adding items until we exceed width - len(' ...')
    ell = " ..."
    avail = width - len(prefix) - len(ell)
    if avail <= 0:
        return (prefix + "...")[:width]
    out = []
    cur_len = 0
    for idx, it in enumerate(items):
        add = len(it) + (len(sep) if out else 0)
        if cur_len + add > avail:
            break
        out.append(it)
        cur_len += add
    if not out:
        # even first item too long — truncate it
        return (prefix + items[0][: max(0, width - len(prefix) - len(ell))] + ell)[:width]
    return prefix + sep.join(out) + ell


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', s)


def _hint_for_session(session) -> str:
    """Hint text: arrows+WASD on ANSI/CP437, WASD only on plain/UTF-8."""
    is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
    codec = getattr(session, "codec", "cp437")
    # CP437 arrows are 0x18-0x1B; when encoded as cp437 they become single-byte glyphs.
    # Use Unicode arrows that map to those bytes via cp437 codec; fallback to WASD.
    if not is_plain and codec == "cp437":
        # 4 arrows (CP437 0x18-0x1B) + '/' + WASD = 9, plus ' select ' → 17
        # Use raw control bytes that encode as 0x18-0x1B on cp437 wire and show as arrows in SyncTERM
        return " \x18\x19\x1B\x1A/WASD select "
    else:
        return " WASD select "


def _flow_cells(labels, hint, active_idx):
    """Flow-tab layout math (Dave's algorithm, pure len()).

    Each tab renders as a ``| <label> | `` cell (len(label)+5 visible cols);
    the ACTIVE cell's label is centered to max(len(label), len(hint)) so the
    funnel hint fits its stop. Returns (label widths, active_x, slot_w) where
    active_x is the exact visible column of the active cell's opening '|'.
    """
    if not labels:
        return [], 0, max(len(hint), 1)
    widths = [max(len(lab), len(hint)) if i == active_idx else len(lab)
              for i, lab in enumerate(labels)]
    active_x = 5 * active_idx + sum(len(lab) for lab in labels[:active_idx])
    return widths, active_x, widths[active_idx]


def _build_top(labels, active_idx, hint, is_plain, screen_width=79, session=None):
    """Funnel/head row: rule whose '|' sits directly below the active tab's
    opening '|' and whose backslash closes the stop after the hint —
    everything below belongs to this heading. Slides with the active tab.
    """
    _w, x, slot = _flow_cells(labels, hint, active_idx)
    fill = "-" if is_plain else "─"
    head = "| " + hint.center(slot) + " " + chr(92)
    left = fill * x
    right = fill * max(0, screen_width - x - len(head))
    row = (left + head + right)[:screen_width].ljust(screen_width, fill)
    if is_plain:
        return row
    p = palette_for(session)
    hl = len(head)
    return (f"{p.muted}{row[:x]}{p.reset}"
            f"{p.text}{row[x:x+hl]}{p.reset}"
            f"{p.muted}{row[x+hl:]}{p.reset}")


def _list_row(disp: str, selected: bool, is_plain: bool, pal) -> str:
    """One PIM list row: phosphor text, selection uses tab colours (not REVERSE)."""
    inner = disp[:74].ljust(74)
    if is_plain:
        mark = "> " if selected else "  "
        return f"│{mark}{inner} │"
    bar = pal.muted
    rst = pal.reset
    if selected:
        return f"{bar}│{rst}{pal.tab_fg}{pal.tab_bg} {inner} {rst}{bar}│{rst}"
    return f"{bar}│{rst}{pal.text}  {inner} {rst}{bar}│{rst}"


def list_pane(bbs, session, items: list[str], hint: str) -> str:
    """Funnel + selectable list + bottom + hint. Tab bar is drawn by mainmenu."""
    is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
    pal = palette_for(session)
    tabs = visible_tabs(load_tabs(bbs), getattr(session, "user", None))
    labels = [x["label"] for x in tabs]
    aid = getattr(session, "_pim_active_tab", None) or (tabs[0]["id"] if tabs else "")
    active_idx = max(0, next((i for i, x in enumerate(tabs) if x["id"] == aid), 0))
    top = _build_top(
        labels, active_idx, _hint_for_session(session), is_plain,
        screen_width=79, session=session,
    )
    bot = "+" + "-" * 77 + "+" if is_plain else f"{pal.muted}└{'─' * 77}┘{pal.reset}"
    selected = int(getattr(session, "_pim_selected", 0) or 0)
    if selected < 0:
        selected = 0
    if items and selected >= len(items):
        selected = len(items) - 1
        try:
            session._pim_selected = selected
        except Exception:
            pass
    lines = [top]
    if not items:
        lines.append(_list_row("(nothing here)".ljust(74), False, is_plain, pal))
    else:
        for idx, text in enumerate(items):
            lines.append(_list_row(str(text)[:74].ljust(74), idx == selected, is_plain, pal))
    lines.append(bot)
    lines.append(hint if is_plain else f"{pal.success}{hint}{pal.reset}")
    return "\r\n".join(lines)


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
            # PIM navigation is handled here; classic keys fall through to
            # _handle().
            key = await runner.read_key(self.bbs, session)
            if key is None:
                break
            if key == "/":
                # The `>` is a hotkey prompt, not a shell. `/` switches that
                # one key into a line read: type `theme` or `theme amber` and
                # Enter. Result paints in an overlay (same geometry as the
                # Social notepad) so the PIM does not scroll; any key dismisses.
                rest = await runner.read_command(self.bbs, session)
                if rest is None:
                    break
                await self._dispatch_slash(session, rest)
                continue
            # PIM tab/pane navigation (build-plan § Step 8)
            if self._is_pim(session) and await self._handle_pim_key(session, key):
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
        cur = getattr(session, "_pim_active_tab", None)
        if cur:
            return cur
        tabs = load_tabs(self.bbs) if self.bbs is not None else []
        return tabs[0]["id"] if tabs else ""

    async def _run_slash(self, session, line: str) -> str:
        """Dispatch ``line`` capturing ``bbs.send`` so callers can overlay it."""
        from core.slash import handle_slash

        chunks: list[str] = []

        class _Capture:
            async def send(_self, _session, text):
                chunks.append(text or "")

            def __getattr__(_self, name):
                return getattr(self.bbs, name)

        await handle_slash(_Capture(), session, line)
        return "".join(chunks)

    async def _dispatch_slash(self, session, rest: str) -> None:
        """Run a `/command` typed after the `/` hotkey.

        Bare ``theme`` opens an up/down picker in the overlay. Other commands
        (including ``theme amber``) capture output into an info modal.
        """
        rest = (rest or "").strip("\r\n").strip()
        line = rest if rest.startswith("/") else "/" + rest
        bits = line.lstrip("/").split(None, 1)
        word = (bits[0] if bits else "").lower()
        arg = bits[1].strip() if len(bits) > 1 else ""
        if word == "theme" and not arg:
            await self._theme_picker(session)
            return
        body = await self._run_slash(session, line)
        await core_modal.notice(self.bbs, session, body)

    async def _theme_picker(self, session) -> None:
        """Up/down theme list in the modal; Enter applies, ESC cancels."""
        from core.theme import theme_name_for, theme_names

        names = theme_names()
        saved = theme_name_for(session)
        default = names.index(saved) if saved in names else 0
        idx = await core_modal.choose(
            self.bbs,
            session,
            names,
            default=default,
            compact=False,
            hint=" arrows select  Enter apply  ESC cancel ",
        )
        if idx is None:
            return
        await self._run_slash(session, f"/theme {names[idx]}")

    async def _compose_picker(self, session) -> str | None:
        """Post / Editor / Discard. ESC keeps the draft and returns None."""
        options = ("Post", "Open in full screen editor", "Discard draft")
        idx = await core_modal.choose(self.bbs, session, list(options), default=0)
        if idx is None:
            return None
        return ("post", "editor", "discard")[idx]

    def _render_tabs(self, session, tabs: list[dict], active_id: str) -> str:
        """Tab row as '| label | ' cells (same _flow_cells math as the funnel
        row, so alignment holds by construction, not by coordinates)."""
        is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
        p = palette_for(session)
        labels = [x["label"] for x in tabs]
        if not labels:
            return " " * 79
        active_idx = max(0, next((i for i, x in enumerate(tabs) if x["id"] == active_id), 0))
        hint = _hint_for_session(session)
        widths, _x, _s = _flow_cells(labels, hint, active_idx)
        parts = []
        for i, tb in enumerate(tabs):
            lab = tb["label"]
            cell = lab.center(widths[i]) if i == active_idx else lab
            if is_plain:
                parts.append(cell.upper() if i == active_idx else cell)
            elif i == active_idx:
                parts.append(f"{p.tab_fg}{p.tab_bg}{cell}{p.reset}")
            else:
                parts.append(f"{p.muted}{cell}{p.reset}")
        row = "".join(f"| {c} | " for c in parts)
        vis = 5 * len(parts) + sum(widths)
        return row + " " * max(0, 79 - vis)

    async def _render_pane(self, session, tab: dict) -> str:
        """Delegate the middle pane to the plugin named by this tab."""
        plugin = tab.get("plugin") or self.bbs.get_plugin(tab.get("id"))
        if plugin is None:
            return ""
        result = plugin.render_home_pane(session)
        if asyncio.iscoroutine(result):
            result = await result
        return result or ""

    async def _handle_pim_key(self, session, key: str) -> bool:
        """Tab switch lives here; other keys go to the active home plugin."""
        tabs = visible_tabs(load_tabs(self.bbs), getattr(session, "user", None))
        if not tabs:
            return False
        active_id = self._active_tab_id(session)
        if not any(t["id"] == active_id for t in tabs):
            active_id = tabs[0]["id"]
        active_idx = next((i for i, t in enumerate(tabs) if t["id"] == active_id), 0)

        if key in ("1", "2", "3", "4", "5"):
            idx = int(key) - 1
            if 0 <= idx < len(tabs):
                session._pim_active_tab = tabs[idx]["id"]
                session._pim_selected = 0
                return True
            return False

        if key in ("LEFT", "H"):
            active_idx = (active_idx - 1) % len(tabs)
            session._pim_active_tab = tabs[active_idx]["id"]
            session._pim_selected = 0
            return True
        if key in ("RIGHT", "L"):
            active_idx = (active_idx + 1) % len(tabs)
            session._pim_active_tab = tabs[active_idx]["id"]
            session._pim_selected = 0
            return True

        plugin = tabs[active_idx].get("plugin") or self.bbs.get_plugin(tabs[active_idx]["id"])
        if plugin is not None:
            handled = plugin.handle_home_key(session, key)
            if asyncio.iscoroutine(handled):
                handled = await handled
            if handled:
                return True

        if key in ("UP", "K"):
            sel = getattr(session, "_pim_selected", 0)
            session._pim_selected = max(0, sel - 1)
            return True
        if key in ("DOWN", "J"):
            sel = getattr(session, "_pim_selected", 0)
            session._pim_selected = sel + 1
            return True
        return False

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
            # File beats generator per docs/screens.md — if a sysop dropped
            # plugins/mainmenu/screens/pim.ans/.asc/.txt, render it instead
            # of the generated tabbed chrome.
            try:
                pim_file = self.bbs.screens.render(session, self.name, "pim")
                if pim_file and "[missing screen" not in pim_file:
                    await self.bbs.send(session, pim_file + "\r\n")
                else:
                    tabs = visible_tabs(load_tabs(self.bbs), getattr(session, "user", None))
                    active_id = self._active_tab_id(session)
                    # clamp active to visible set
                    if tabs and not any(t["id"] == active_id for t in tabs):
                        active_id = tabs[0]["id"]
                        session._pim_active_tab = active_id  # type: ignore[attr-defined]
                    # band A: tabs (funnel row in _render_pane connects
                    # directly — no separator line, saves a row on 80x24)
                    tab_bar = self._render_tabs(session, tabs, active_id)
                    await self.bbs.send(session, tab_bar + "\r\n")
                    if tabs:
                        active_tab = next((t for t in tabs if t["id"] == active_id), tabs[0])
                        pane = await self._render_pane(session, active_tab)
                        await self.bbs.send(session, pane + "\r\n")
            except Exception:
                # Fall back to generated chrome on any render error
                tabs = visible_tabs(load_tabs(self.bbs), getattr(session, "user", None))
                active_id = self._active_tab_id(session)
                if tabs and not any(t["id"] == active_id for t in tabs):
                    active_id = tabs[0]["id"]
                    session._pim_active_tab = active_id  # type: ignore[attr-defined]
                tab_bar = self._render_tabs(session, tabs, active_id)
                await self.bbs.send(session, tab_bar + "\r\n")
                if tabs:
                    active_tab = next((t for t in tabs if t["id"] == active_id), tabs[0])
                    pane = await self._render_pane(session, active_tab)
                    await self.bbs.send(session, pane + "\r\n")
        else:
            # Classic: file beats generator per docs/screens.md
            screen = self.bbs.screens.render(session, self.name, "main")
            await self.bbs.send(session, screen)

        # Pin the themed ``>`` prompt on the very last terminal line.
        p = palette_for(session)
        G = p.prompt
        R = p.reset
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
        p = palette_for(session)
        C = p.accent
        B = ANSI.BOLD
        W = p.text
        R = p.reset

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
        from core.version import NAME, display

        return (
            "\r\n--- System Information ---\r\n"
            f"  Name:     {NAME}\r\n"
            f"  Version:  {display()}\r\n"
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


__all__ = [
    "MainmenuPlugin",
    "_collapse_overlay_spacing",
    "_elided",
    "_build_top",
    "_hint_for_session",
    "list_pane",
]