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
    widths = [max(len(lab), len(hint)) if i == active_idx else len(lab)
              for i, lab in enumerate(labels)]
    active_x = 5 * active_idx + sum(len(lab) for lab in labels[:active_idx])
    return widths, active_x, widths[active_idx]


def _build_top(labels, active_idx, hint, is_plain, screen_width=79):
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
    hl = len(head)
    return (f"{ANSI.DIM}{row[:x]}{ANSI.RESET}"
            f"{row[x:x+hl]}"
            f"{ANSI.DIM}{row[x+hl:]}{ANSI.RESET}")


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
        return getattr(session, "_pim_active_tab", None) or "dashboard"

    def _render_tabs(self, session, tabs: list[dict], active_id: str) -> str:
        """Tab row as '| label | ' cells (same _flow_cells math as the funnel
        row, so alignment holds by construction, not by coordinates)."""
        is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
        labels = [x["label"] for x in tabs]
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
                parts.append(f"{ANSI.BRIGHT_WHITE}{ANSI.BG_BLUE}{cell}{ANSI.RESET}")
            else:
                parts.append(f"{ANSI.DIM}{cell}{ANSI.RESET}")
        row = "".join(f"| {c} | " for c in parts)
        vis = 5 * len(parts) + sum(widths)
        return row + " " * max(0, 79 - vis)

    async def _render_pane(self, session, tab: dict) -> str:
        """Middle pane: filtered conversation list for the active tab.

        In Dashboard tab, this is the digest quick-links list (one row per
        area with new activity, elided to 79). In the Social tab it's the
        two-pane room sidebar | thread view (boards-unification B3).
        Otherwise, it's the filtered conversation list
        (``@author (age): preview``).
        """
        # Social: two-pane surface (B3) replaces the conversation list here
        if tab.get("id") == "social":
            from plugins.mainmenu.social import render_social

            return await render_social(self.bbs.conversations, session)

        # Dashboard digest quick-links — each row is a filtered shortcut
        if tab.get("id") == "dashboard" or tab.get("kind") == "dashboard":
            # Build digests: DMs / Bulletins / Files / Boards — each as
            # "Label: (N new) item | item | ..." elided to inner width 77.
            user = getattr(session, "user", None)
            digests: list[tuple[str, str]] = []  # (text, target_tab_id)
            # DMs — only those with unread messages (and at least one message)
            try:
                if user is not None and getattr(user, "username", None):
                    dms = await self.bbs.conversations.unread_conversations(user.username, kind="dm", visible_to=user)
                else:
                    dms = []
            except Exception:
                dms = []
            if dms:
                names: list[str] = []
                uname = getattr(user, "username", "") if user else ""
                seen_names = set()
                for c in dms:
                    parts = c.get("participants", []) or []
                    other = next((p for p in parts if p != uname), None) or c.get("title", c.get("id","?"))
                    if other not in seen_names:
                        names.append(str(other))
                        seen_names.add(other)
                # elide names to fit
                prefix = f"DMs: ({len(dms)} new) from "
                text = _elided(prefix, names, sep=", ", width=74)
                digests.append((text, "social"))
            else:
                digests.append(("DMs: (no new messages)", "social"))
            # Bulletins
            try:
                bul = self.bbs.get_plugin("bulletins")
                if bul is not None and user is not None:
                    ids = bul.unseen(user)
                    if ids:
                        vis = {b["id"]: b for b in bul.scan()}
                        titles = [vis.get(i, {"title": i})["title"] for i in ids[:8]]
                        prefix = f"Bulletins: ({len(ids)} new) "
                        text = _elided(prefix, titles, sep=" | ", width=74)
                        digests.append((text, "bulletins"))
                    else:
                        digests.append(("Bulletins: (no new)", "bulletins"))
                else:
                    # no user or no plugin
                    digests.append(("Bulletins: (no new)", "bulletins"))
            except Exception:
                pass
            # Files
            try:
                fp = self.bbs.get_plugin("files")
                if fp is not None and hasattr(fp, "visible_areas"):
                    areas = fp.visible_areas(user) if user else []
                    total = 0
                    names = []
                    for a in areas:
                        try:
                            n = len(fp.store.list_files(a["id"]))
                            total += n
                            names.append(a.get("name", a["id"]))
                        except Exception:
                            pass
                    if total:
                        prefix = f"Files: ({total} new) "
                        text = _elided(prefix, names, sep=" | ", width=74)
                        digests.append((text, "files"))
                    else:
                        digests.append(("Files: (no new)", "files"))
            except Exception:
                pass
            # Boards — only those with unread messages
            try:
                if user is not None and getattr(user, "username", None):
                    boards = await self.bbs.conversations.unread_conversations(user.username, kind="board", visible_to=user)
                else:
                    boards = []
            except Exception:
                boards = []
            if boards:
                titles = [c.get("title", c.get("id","?")) for c in boards[:8]]
                prefix = f"Boards: ({len(boards)} new) "
                text = _elided(prefix, titles, sep=" | ", width=74)
                digests.append((text, "social"))
            else:
                digests.append(("Boards: (no new)", "social"))
            # Render digests inside the same box chrome
            # dynamic top: slashes follow tab ' | ' delimiters, hint is arrows+WASD on ANSI, WASD on plain
            is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
            from plugins.mainmenu.tabs import load_tabs, visible_tabs
            tabs_for_top = visible_tabs(load_tabs(self.bbs), getattr(session, "user", None))
            labels = [x["label"] for x in tabs_for_top]
            _aid = self._active_tab_id(session)
            active_idx = max(0, next((i for i, x in enumerate(tabs_for_top) if x["id"] == _aid), 0))
            hint = _hint_for_session(session)
            top = _build_top(labels, active_idx, hint, is_plain, screen_width=79)
            bot = "+" + "-" * 77 + "+" if is_plain else f"{ANSI.DIM}└{'─' * 77}┘{ANSI.RESET}"
            lines = [top]
            # highlight the selected digest row
            selected = getattr(session, "_pim_selected", 0)
            if selected < 0:
                selected = 0
            if selected >= len(digests):
                selected = max(0, len(digests)-1)
                try:
                    session._pim_selected = selected
                except Exception:
                    pass
            for idx, (text, _) in enumerate(digests):
                is_sel = idx == selected
                # ensure digest fits 74 inside box
                disp = text[:74].ljust(74)
                if is_sel:
                    if is_plain:
                        row = f"│> {disp} │"
                    else:
                        row = f"│{ANSI.REVERSE} {disp} {ANSI.RESET} │"
                else:
                    row = f"│  {disp} │"
                lines.append(row)
            lines.append(bot)
            lines.append("  Arrows/WASD or 1/2/3 to switch tabs, Enter to open, Q to disconnect")
            # stash target map for Enter handler
            try:
                session._pim_dashboard_targets = [t for _, t in digests]
            except Exception:
                pass
            return "\r\n".join(lines)

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

        # dynamic top: slashes follow tab ' | ' delimiters, hint is arrows+WASD on ANSI, WASD on plain
        is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
        from plugins.mainmenu.tabs import load_tabs, visible_tabs
        tabs_for_top = visible_tabs(load_tabs(self.bbs), getattr(session, "user", None))
        labels = [x["label"] for x in tabs_for_top]
        _aid = self._active_tab_id(session)
        active_idx = max(0, next((i for i, x in enumerate(tabs_for_top) if x["id"] == _aid), 0))
        hint = _hint_for_session(session)
        top = _build_top(labels, active_idx, hint, is_plain, screen_width=79)
        bot = "+" + "-" * 77 + "+" if is_plain else f"{ANSI.DIM}└{'─' * 77}┘{ANSI.RESET}"
        lines = [top]
        if not convs:
            label = tab.get('label','conversations').lower()
            lines.append(f"│  (no {label} yet)".ljust(77) + "│")
        else:
            selected = getattr(session, "_pim_selected", 0)
            if selected < 0:
                selected = 0
            if selected >= len(convs):
                selected = max(0, len(convs) - 1)
                try:
                    session._pim_selected = selected  # type: ignore[attr-defined]
                except Exception:
                    pass
            for idx, c in enumerate(convs):
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
                is_sel = idx == selected
                if is_sel:
                    if is_plain:
                        row = f"│> {preview[:74].ljust(74)} │"
                    else:
                        row = f"│{ANSI.REVERSE} {preview[:74].ljust(74)} {ANSI.RESET} │"
                else:
                    row = f"│  {preview[:74].ljust(74)} │"
                lines.append(row)
        lines.append(bot)
        lines.append("  Arrows/WASD or 1/2/3 to switch tabs, Enter to open, Q to disconnect")
        return "\r\n".join(lines)

    async def _handle_pim_key(self, session, key: str) -> bool:
        """PIM navigation: tabs + pane highlight. Returns True if consumed.

        Consumed keys re-render the chrome on the next loop; unhandled keys
        fall through to classic ``_handle()`` (e.g. ``I`` for System Info).
        """
        tabs = visible_tabs(load_tabs(self.bbs), getattr(session, "user", None))
        if not tabs:
            return False
        active_id = self._active_tab_id(session)
        # normalize active
        if not any(t["id"] == active_id for t in tabs):
            active_id = tabs[0]["id"]
        active_idx = next((i for i, t in enumerate(tabs) if t["id"] == active_id), 0)

        # numeric tab switch: 1/2/3 → tab by order
        if key in ("1", "2", "3", "4", "5"):
            idx = int(key) - 1
            if 0 <= idx < len(tabs):
                session._pim_active_tab = tabs[idx]["id"]  # type: ignore[attr-defined]
                session._pim_selected = 0  # type: ignore[attr-defined]
                return True
            return False

        if key == "LEFT":
            # vi H also handled via LEFT arrow; H key itself is LEFT
            active_idx = (active_idx - 1) % len(tabs)
            session._pim_active_tab = tabs[active_idx]["id"]  # type: ignore[attr-defined]
            session._pim_selected = 0  # type: ignore[attr-defined]
            return True
        if key == "RIGHT":
            active_idx = (active_idx + 1) % len(tabs)
            session._pim_active_tab = tabs[active_idx]["id"]  # type: ignore[attr-defined]
            session._pim_selected = 0  # type: ignore[attr-defined]
            return True
        # B4: Social owns its own keys (rooms/scroll/compose/back) once the
        # tab-switch keys above had their chance. Everything it declines
        # falls through to the generic pane handling below.
        if tabs[active_idx].get("id") == "social":
            if await self._handle_social_key(session, key):
                return True
        # UP/DN move highlight inside pane
        if key == "UP":
            sel = getattr(session, "_pim_selected", 0)
            session._pim_selected = max(0, sel - 1)  # type: ignore[attr-defined]
            return True
        if key == "DOWN":
            # clamp to conv count for active tab
            sel = getattr(session, "_pim_selected", 0)
            # we don't know count without I/O here — allow free increment,
            # _render_pane will clamp visually; next open will validate.
            session._pim_selected = sel + 1  # type: ignore[attr-defined]
            return True
        if key == "ENTER":
            # Dashboard digest: Enter jumps to the target tab (quick-link)
            if tabs[active_idx].get("id") == "dashboard":
                targets = getattr(session, "_pim_dashboard_targets", None)
                if targets:
                    sel = getattr(session, "_pim_selected", 0)
                    sel = max(0, min(sel, len(targets)-1))
                    session._pim_active_tab = targets[sel]
                    session._pim_selected = 0
                    return True
                return True
            await self._open_selected(session, tabs[active_idx])
            return True
        # H/L as vim left/right when not already consumed above
        if key == "H":
            active_idx = (active_idx - 1) % len(tabs)
            session._pim_active_tab = tabs[active_idx]["id"]  # type: ignore[attr-defined]
            session._pim_selected = 0  # type: ignore[attr-defined]
            return True
        if key == "L":
            active_idx = (active_idx + 1) % len(tabs)
            session._pim_active_tab = tabs[active_idx]["id"]  # type: ignore[attr-defined]
            session._pim_selected = 0  # type: ignore[attr-defined]
            return True
        if key in ("K",):
            sel = getattr(session, "_pim_selected", 0)
            session._pim_selected = max(0, sel - 1)  # type: ignore[attr-defined]
            return True
        if key in ("J",):
            sel = getattr(session, "_pim_selected", 0)
            session._pim_selected = sel + 1  # type: ignore[attr-defined]
            return True
        return False

    async def _handle_social_key(self, session, key: str) -> bool:
        """Social tab keys (boards-unification §B4). Returns True if consumed.

        Browse keys (arrows/PgUp/PgDn/Space/ESC/B/ENTER) act immediately;
        compose actions (R/N/D) drop into LINE mode via ``read_command`` —
        per the plan's hard rule the two modes never blur, so no navigation
        keystroke can leak into a draft.
        """
        from plugins.mainmenu.social import social_rooms

        user = getattr(session, "user", None)
        rooms = await social_rooms(self.bbs.conversations, user)
        n = len(rooms)
        sel = int(getattr(session, "_pim_selected", 0) or 0)
        sel = max(0, min(sel, n - 1)) if n else 0

        def _page() -> int:
            return max(6, int(getattr(session, "terminal_height", 24) or 24) - 6)

        if key in ("UP", "K") and n:
            session._pim_selected = (sel - 1) % n  # type: ignore[attr-defined]
            return True
        if key in ("DOWN", "J") and n:
            session._pim_selected = (sel + 1) % n  # type: ignore[attr-defined]
            return True
        if key in ("PGDN", "SPACE"):
            up = int(getattr(session, "_social_scroll_up", 0) or 0)
            session._social_scroll_up = max(0, up - _page())  # type: ignore[attr-defined]
            return True
        if key == "PGUP":
            up = int(getattr(session, "_social_scroll_up", 0) or 0)
            session._social_scroll_up = up + _page()  # type: ignore[attr-defined]
            return True
        if key == "ESC" or key == "B":
            # Back/up one level: Social → Dashboard. B is scoped to this
            # handler only — outside Social nothing changes.
            session._pim_active_tab = "dashboard"  # type: ignore[attr-defined]
            session._pim_selected = 0  # type: ignore[attr-defined]
            session._social_scroll_up = 0  # type: ignore[attr-defined]
            return True
        if key == "ENTER":
            # Enter opens the highlighted room full-screen (the pane already
            # live-follows selection; this is the explicit "go inside").
            conv = await self._social_target_conv(session, rooms, sel)
            if conv is None:
                await self.bbs.send(session, "\r\n(no room selected)\r\n")
                return True
            await self._thread_reader(session, conv)
            return True
        if key == "N":
            await self._social_new_thread(session)
            return True
        if key == "R":
            await self._social_compose(session, rooms, sel, delete=False)
            return True
        if key == "D":
            await self._social_compose(session, rooms, sel, delete=True)
            return True
        return False

    async def _social_target_conv(self, session, rooms, sel):
        """Conversation behind the highlighted sidebar row."""
        room = rooms[sel] if sel < len(rooms) else None
        if room is None:
            return None
        if room.kind == "dms":
            dms = await self.bbs.conversations.list_conversations(
                kind="dm", visible_to=getattr(session, "user", None))
            dms.sort(key=lambda c: c.get("last_message_at") or c.get("created", ""),
                     reverse=True)
            return dms[0] if dms else None
        try:
            return await self.bbs.conversations.get_conversation(room.id)
        except Exception:
            return None

    async def _social_new_thread(self, session):
        """Compose mode: prompt for a title (≤15 chars), then create+select."""
        while True:
            await self.bbs.send(session, "\r\nThread title (max 15 chars): ")
            raw = await runner.read_command(self.bbs, session)
            if raw is None:
                return
            title = raw.strip()
            if not title:
                await self.bbs.send(session, "(cancelled)\r\n")
                return
            if len(title) > 15:
                await self.bbs.send(session,
                                    f"Too long ({len(title)} chars) — 15 max.\r\n")
                continue
            conv = await self.bbs.conversations.create_conversation(
                kind="board", title=title, created_by=session.user.username)
            from plugins.mainmenu.social import social_rooms

            rooms = await social_rooms(self.bbs.conversations, session.user)
            for i, r in enumerate(rooms):
                if r.id == conv["id"]:
                    session._pim_selected = i  # type: ignore[attr-defined]
                    break
            session._social_scroll_up = 0  # type: ignore[attr-defined]
            await self.bbs.send(session, f"Thread '{title}' created.\r\n")
            return

    async def _social_compose(self, session, rooms, sel, *, delete: bool):
        """R = post into the highlighted room; D = delete one of your posts
        (or any, for moderators). Both are LINE-mode compose flows."""
        conv = await self._social_target_conv(session, rooms, sel)
        if conv is None:
            await self.bbs.send(session, "\r\n(no room selected)\r\n")
            return
        if not delete:
            await self.bbs.send(
                session,
                f"\r\nMessage to {conv['title']} — empty line sends, /A aborts.\r\n",
            )
            lines: list[str] = []
            while True:
                line = await runner.read_command(self.bbs, session)
                if line is None:
                    return
                if line.strip().upper() == "/A":
                    await self.bbs.send(session, "\r\nAborted.\r\n")
                    return
                if not line.strip():
                    break
                lines.append(line)
            body = "\n".join(lines).strip()
            if not body:
                await self.bbs.send(session, "\r\n(empty — nothing posted)\r\n")
                return
            await self.bbs.conversations.post_message(
                conv["id"], author=session.user.username, body=body)
            try:
                await self.bbs.conversations.mark_read(session.user.username, conv["id"])
            except Exception:
                pass
            session._social_scroll_up = 0  # show the new post (tail anchor)
            await self.bbs.send(session, "\r\nPosted.\r\n")
            return
        await self.bbs.send(session, "\r\nDelete message #: ")
        raw = await runner.read_command(self.bbs, session)
        if raw is None or not raw.strip().isdigit():
            return
        mid = int(raw.strip())
        try:
            ok = await self.bbs.conversations.delete_message(
                conv["id"], mid, by_user=session.user)
            await self.bbs.send(
                session, f"\r\n{'Deleted #' + str(mid) if ok else 'No such message.'}\r\n")
        except PermissionError as e:
            await self.bbs.send(session, f"\r\nNot allowed: {e}\r\n")
        except Exception as e:  # noqa: BLE001
            await self.bbs.send(session, f"\r\nFailed: {e}\r\n")

    async def _open_selected(self, session, tab: dict) -> None:
        """Open the highlighted conversation in a full-screen reader.

        Full loop for Step 9: paged, threaded view with quoting, one-key
        reply, mod delete, and Find. Uses the classic /S save / /A abort
        line editor for posting (per Dave's prior decision).
        """
        kind = tab.get("kind", "board")
        filter_kind = None if kind == "all" else kind
        try:
            convs = await self.bbs.conversations.list_conversations(
                kind=filter_kind, visible_to=getattr(session, "user", None)
            )
        except Exception:
            convs = []
        if not convs:
            await self.bbs.send(session, "\r\n(no conversations in this tab)\r\n")
            await self.bbs.send(session, "\r\n[Press any key]\r\n")
            await runner.read_key(self.bbs, session)
            return
        sel = getattr(session, "_pim_selected", 0)
        sel = max(0, min(sel, len(convs) - 1))
        conv = convs[sel]
        await self._thread_reader(session, conv)

    async def _thread_reader(self, session, conv: dict) -> None:
        """Full-screen paged thread view for one conversation.

        Shared by the classic tab flow (_open_selected) and Social's
        ENTER-to-open. Q/ESC back out to whatever invoked it.
        """
        # mark as read on entry so digests/quick-links clear on return
        try:
            uname = getattr(getattr(session, "user", None), "username", None)
            if uname:
                await self.bbs.conversations.mark_read(uname, conv["id"])
        except Exception:
            pass
        page = 0
        per_page = max(5, getattr(session, "terminal_height", 24) - 8)
        while getattr(session, "is_active", True):
            try:
                msgs = await self.bbs.conversations.list_messages(conv["id"])
            except Exception:
                msgs = []
            total_pages = max(1, -(-len(msgs) // per_page)) if msgs else 1
            page = max(0, min(page, total_pages - 1))
            start = page * per_page
            slice_ = msgs[start : start + per_page]

            await self.bbs.send(session, "\x1b[2J\x1b[H")
            header = f" {conv.get('title','?')} - {len(msgs)} messages  (page {page+1}/{total_pages})"
            await self.bbs.send(session, header + "\r\n" + "─" * 79 + "\r\n")
            if not slice_:
                if not msgs:
                    await self.bbs.send(session, "(no messages yet — press R to be the first to post)\r\n")
                else:
                    await self.bbs.send(session, "(no messages on this page)\r\n")
            else:
                # Build parent lookup for threading indent
                for m in slice_:
                    author = m.get("author", "?")
                    body = (m.get("body", "") or "").split("\n")[0][:70]
                    # indent threaded replies
                    indent = ""
                    pid = m.get("parent_id")
                    if pid:
                        # depth: count parents (simple)
                        indent = "  "
                    created = m.get("created", "")[:16].replace("T", " ")
                    line = f"{indent}#{m.get('id', '?')} [{author}] {created}  {body}"
                    await self.bbs.send(session, line[:79] + "\r\n")
            await self.bbs.send(session, "─" * 79 + "\r\n")
            await self.bbs.send(session, " R)reply  D)delete  F)find  N)next P)prev  ESC/Q back\r\n")
            await self.bbs.send(session, f"\x1b[{getattr(session, 'terminal_height', 24)};1H\x1b[2K\x1b[92m  >\x1b[0m")
            key = await runner.read_key(self.bbs, session)
            if key is None or key == "Q" or key == "ESC":
                return
            if key == "N":
                if page + 1 < total_pages:
                    page += 1
                continue
            if key == "P":
                if page > 0:
                    page -= 1
                continue
            if key == "F":
                await self.bbs.send(session, "\r\nFind: ")
                q = await runner.read_command(self.bbs, session)
                if q is None or not q.strip():
                    continue
                hits = await self.bbs.conversations.find_messages(q.strip(), limit=20)
                # filter hits to this conversation for the reader view
                hits = [h for h in hits if h.get("conversation_id") == conv["id"]]
                await self.bbs.send(session, f"\r\nFound {len(hits)} hit(s) for '{q.strip()}' in this conversation:\r\n")
                for h in hits[:10]:
                    author = h.get("author", "?")
                    body = (h.get("body", "") or "").split("\n")[0][:60]
                    await self.bbs.send(session, f"  #{h.get('id')} [{author}] {body}\r\n")
                await self.bbs.send(session, "\r\n[Press any key]\r\n")
                await runner.read_key(self.bbs, session)
                continue
            if key == "R":
                await self.bbs.send(session, "\r\nReply to # (blank for new thread): ")
                num_raw = await runner.read_command(self.bbs, session)
                if num_raw is None:
                    return
                num_raw = num_raw.strip()
                parent_id = int(num_raw) if num_raw.isdigit() else None
                # Classic line editor: empty line to finish, /A to abort
                await self.bbs.send(session, "\r\nType your message. Empty line to finish, /A aborts.\r\n")
                lines: list[str] = []
                # If replying, prepend quoted original
                if parent_id is not None:
                    orig = next((m for m in msgs if m.get("id") == parent_id), None)
                    if orig:
                        quoted = "\n".join(f"> {ln}" for ln in orig.get("body", "").split("\n"))
                        lines.append(f"On {orig.get('created','')[:19]}, {orig.get('author','?')} wrote:")
                        lines.append(quoted)
                        lines.append("")
                while getattr(session, "is_active", True):
                    await self.bbs.send(session, "")
                    line = await runner.read_command(self.bbs, session)
                    if line is None:
                        return
                    if line.strip().upper() == "/A":
                        await self.bbs.send(session, "\r\nAborted.\r\n")
                        lines = []
                        break
                    if not line.strip():
                        break
                    lines.append(line)
                if not lines or all(not l.strip() or l.startswith(">") for l in lines):
                    # only quotes, no new content
                    if not any(l.strip() and not l.startswith(">") and not l.startswith("On ") for l in lines):
                        continue
                body = "\n".join(lines)
                if not body.strip():
                    continue
                try:
                    await self.bbs.conversations.post_message(conv["id"], author=session.user.username, body=body, parent_id=parent_id)
                    try:
                        await self.bbs.conversations.mark_read(session.user.username, conv["id"])
                    except Exception:
                        pass
                    # Stay on last page so new message is visible
                    msgs = await self.bbs.conversations.list_messages(conv["id"])
                    total_pages = max(1, -(-len(msgs) // per_page))
                    page = total_pages - 1
                except Exception as e:
                    await self.bbs.send(session, f"\r\nFailed to post: {e}\r\n")
                    await runner.read_key(self.bbs, session)
                continue
            if key == "D":
                await self.bbs.send(session, "\r\nDelete #: ")
                num_raw = await runner.read_command(self.bbs, session)
                if num_raw is None or not num_raw.strip().isdigit():
                    continue
                mid = int(num_raw.strip())
                try:
                    ok = await self.bbs.conversations.delete_message(conv["id"], mid, by_user=session.user)
                    if not ok:
                        await self.bbs.send(session, "\r\nNo such message.\r\n")
                        await runner.read_key(self.bbs, session)
                    else:
                        await self.bbs.send(session, f"\r\nDeleted #{mid}.\r\n")
                        await runner.read_key(self.bbs, session)
                except PermissionError as e:
                    await self.bbs.send(session, f"\r\nNot allowed: {e}\r\n")
                    await runner.read_key(self.bbs, session)
                except Exception as e:
                    await self.bbs.send(session, f"\r\nFailed: {e}\r\n")
                    await runner.read_key(self.bbs, session)
                continue
            # numeric -> jump to page? ignore, prompt already handles
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
                    # band B: pane
                    active_tab = next((t for t in tabs if t["id"] == active_id), tabs[0] if tabs else {"id": "boards", "label": "Boards", "kind": "board"})
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