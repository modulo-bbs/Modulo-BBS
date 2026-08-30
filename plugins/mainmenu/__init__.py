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
from shared.visible import (
    center_display,
    char_width,
    display_width,
    fill_display,
    fit_display,
    hline,
    overlay_display,
    wide_ambiguous_for,
)


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


def _tab_sep(is_plain: bool) -> str:
    """Width probe for a tab junction. ASCII ``+``/``|`` on dumb terminals;
    CP437 box glyphs everywhere else (same cell width as ``│``)."""
    return "|" if is_plain else "│"


def _box(is_plain: bool) -> dict:
    """IBM box pieces (or ASCII ``+``/``-``). Junctions share ``│``'s width."""
    if is_plain:
        return dict(
            h="-", tl="+", tr="+", bl="+", br="+",
            td="+", tu="+", tright="+", tleft="+",
        )
    return dict(
        h="─", tl="┌", tr="┐", bl="└", br="┘",
        td="┬", tu="┴", tright="├", tleft="┤",
    )


def _hint_for_session(session) -> str:
    """Hint under the active tab: arrows+WASD when the codec can show them."""
    is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
    if is_plain:
        return " WASD select "
    codec = getattr(session, "codec", None) or "cp437"
    if codec == "utf-8":
        return " ↑↓←→ · WASD select "
    if codec == "ascii":
        return " WASD select "
    # IBM C0 0x18–0x1B (↑↓←→ in a CP437 font). Not ASCII, not High ASCII
    # 128–255. 0x1B is also ESC; fine here because no SGR follows the byte.
    return " \x18\x19\x1B\x1A · WASD select "


def _flow_cells(labels, hint, active_idx, wide=False, sep="|"):
    """Flow-tab layout math in display columns.

    Shared bars: one junction per tab wall, not ``│ │``. The ACTIVE cell
    is sized to max(label, hint) so the carrier hint fits its stop.
    Returns (label widths, active_x, slot_w) where active_x is the
    display column of the active cell's opening separator.
    """
    def w(s: str) -> int:
        return display_width(s, wide_ambiguous=wide)

    sep_w = w(sep) or 1
    # each preceding tab: pad + label + pad + sep
    chrome = 2 + sep_w
    if not labels:
        return [], 0, max(w(hint), 1)
    widths = [max(w(lab), w(hint)) if i == active_idx else w(lab)
              for i, lab in enumerate(labels)]
    active_x = chrome * active_idx + sum(w(lab) for lab in labels[:active_idx])
    return widths, active_x, widths[active_idx]


def _sep_xs(widths, sep_w: int) -> list[int]:
    """Display column of each tab wall (n_tabs + 1 of them)."""
    xs = []
    x = 0
    for wi in widths:
        xs.append(x)
        x += sep_w + 1 + wi + 1
    xs.append(x)
    return xs


def _paint_display(row: str, spans: list, pal, wide: bool) -> str:
    """Wrap *row* (no SGR) in palette roles. *spans* are (start, end, kind)."""
    def kind_for(col: int) -> str:
        for a, b, k in spans:
            if a <= col < b:
                return k
        return "frame"

    def sgr(kind: str) -> str:
        if kind == "tab":
            return pal.tab_fg + pal.tab_bg
        if kind == "muted":
            return pal.muted
        if kind == "text":
            return pal.text
        return pal.frame

    out: list[str] = []
    col = 0
    prev = None
    for ch in row:
        w = char_width(ch, wide_ambiguous=wide)
        k = kind_for(col)
        if k != prev:
            if prev is not None:
                out.append(pal.reset)
            out.append(sgr(k))
            prev = k
        out.append(ch)
        col += w
    if prev is not None:
        out.append(pal.reset)
    return "".join(out)


def _tab_junction(k: int, n: int, at_right_wall: bool, b: dict) -> str:
    """Glyph on the tab cap at separator *k* (0 = far left)."""
    if k == 0:
        return b["tl"]
    if k == n and at_right_wall:
        return b["tr"]
    return b["td"]


def _enclosure_cols(xs: list[int], active_idx: int, sep_w: int) -> tuple[int, int, int, int]:
    """Left sep, left pad, right pad, right sep of the active tab's hint box."""
    left_sep = xs[active_idx]
    right_sep = xs[active_idx + 1]
    return left_sep, left_sep + sep_w, right_sep - 1, right_sep


def _funnel_junction(k: int, n: int, active_idx: int, at_right_wall: bool, b: dict) -> str:
    """Glyph on the hint carrier at separator *k*.

    Dashboard (tab 0) active: left is ``├`` so the pane wall continues.
    Any later tab: left is ``└`` and the carrier's left/right are ``┤``/``├``
    (one shared wall, not ``│ │``). Idle tab joints are ``┴``. Far right
    of the 79-col box is always ``┤``.
    """
    if k == 0:
        return b["tright"] if active_idx == 0 else b["bl"]
    if k == n and at_right_wall:
        return b["tleft"]
    if k == active_idx:
        return b["tleft"]
    if k == active_idx + 1:
        return b["tleft"] if at_right_wall else b["tright"]
    return b["tu"]


def _build_tab_row(labels, active_idx, hint, is_plain, screen_width=79, session=None):
    """Top cap: ``┌── Dashboard ──┬ Social ┬ … ────────┐``.

    The active tab's pad columns are ``┬`` in text= so they meet the
    hint carrier's ``┴`` and the reverse-video label as one box.
    """
    wide = wide_ambiguous_for(session, is_plain)
    b = _box(is_plain)
    sep = _tab_sep(is_plain)
    sep_w = display_width(sep, wide_ambiguous=wide) or 1
    if not labels:
        return " " * screen_width
    widths, _x, _slot = _flow_cells(labels, hint, active_idx, wide=wide, sep=sep)
    xs = _sep_xs(widths, sep_w)
    right_x = screen_width - sep_w
    row = fill_display(b["h"], screen_width, wide_ambiguous=wide)
    row = overlay_display(row, 0, b["tl"], screen_width, wide_ambiguous=wide)
    row = overlay_display(row, right_x, b["tr"], screen_width, wide_ambiguous=wide)
    n = len(labels)
    for k, x in enumerate(xs):
        g = _tab_junction(k, n, x == right_x, b)
        row = overlay_display(row, x, g, screen_width, wide_ambiguous=wide)
    cells = []
    for i, lab in enumerate(labels):
        cell = center_display(lab, widths[i], wide_ambiguous=wide) if i == active_idx else lab
        if is_plain and i == active_idx:
            cell = cell.upper()
        cells.append(cell)
        start = xs[i] + sep_w + 1
        row = overlay_display(row, start, cell, screen_width, wide_ambiguous=wide)
    _left_sep, left_pad, right_pad, _right_sep = _enclosure_cols(xs, active_idx, sep_w)
    if left_pad < right_pad:
        row = overlay_display(row, left_pad, b["td"], screen_width, wide_ambiguous=wide)
        row = overlay_display(row, right_pad, b["td"], screen_width, wide_ambiguous=wide)
    if is_plain:
        return row
    pal = palette_for(session)
    tw = display_width(b["td"], wide_ambiguous=wide) or 1
    spans = []
    for col in (left_pad, right_pad):
        spans.append((col, col + tw, "text"))
    for i, cell in enumerate(cells):
        start = xs[i] + sep_w + 1
        spans.append((start, start + widths[i], "tab" if i == active_idx else "muted"))
    return _paint_display(row, spans, pal, wide)


def _build_top(labels, active_idx, hint, is_plain, screen_width=79, session=None):
    """Hint carrier under the tab cap.

    Verticals mark which tab the hint belongs to. Left is ``├`` while
    Dashboard is active, ``└`` once the carrier has moved right.
    """
    wide = wide_ambiguous_for(session, is_plain)
    b = _box(is_plain)
    sep = _tab_sep(is_plain)
    sep_w = display_width(sep, wide_ambiguous=wide) or 1
    if not labels:
        return fill_display(b["h"], screen_width, wide_ambiguous=wide)
    widths, x, slot = _flow_cells(labels, hint, active_idx, wide=wide, sep=sep)
    xs = _sep_xs(widths, sep_w)
    right_x = screen_width - sep_w
    inner = center_display(hint, slot, wide_ambiguous=wide)
    start = x + sep_w + 1
    row = fill_display(b["h"], screen_width, wide_ambiguous=wide)
    row = overlay_display(row, start, inner, screen_width, wide_ambiguous=wide)
    n = len(labels)
    for k, col in enumerate(xs):
        g = _funnel_junction(k, n, active_idx, col == right_x, b)
        row = overlay_display(row, col, g, screen_width, wide_ambiguous=wide)
    if xs[-1] != right_x:
        row = overlay_display(row, right_x, b["tleft"], screen_width, wide_ambiguous=wide)
    _left_sep, left_pad, right_pad, _right_sep = _enclosure_cols(xs, active_idx, sep_w)
    if left_pad < right_pad:
        row = overlay_display(row, left_pad, b["tu"], screen_width, wide_ambiguous=wide)
        row = overlay_display(row, right_pad, b["tu"], screen_width, wide_ambiguous=wide)
    if is_plain:
        return row
    pal = palette_for(session)
    tw = display_width(b["tu"], wide_ambiguous=wide) or 1
    spans = [(start, start + slot, "text")]
    for col in (left_pad, right_pad):
        spans.append((col, col + tw, "text"))
    return _paint_display(row, spans, pal, wide)


def _list_row(disp: str, selected: bool, is_plain: bool, pal, *, wide: bool = False) -> str:
    """One PIM list row: phosphor text, selection uses tab colours (not REVERSE)."""
    bar_w = 2 if wide else 1
    inner_w = 79 - bar_w - 2 - 1 - bar_w  # │ + mark + inner + space + │
    inner = fit_display(disp, inner_w, wide_ambiguous=wide)
    if is_plain:
        mark = "> " if selected else "  "
        return f"│{mark}{inner} │"
    bar = pal.frame
    rst = pal.reset
    if selected:
        # Same 2-col left pad as idle (`  `). One space made this row 78
        # and the right │ sat a column left of the floor.
        return f"{bar}│{rst}{pal.tab_fg}{pal.tab_bg}  {inner} {rst}{bar}│{rst}"
    return f"{bar}│{rst}{pal.text}  {inner} {rst}{bar}│{rst}"


def list_pane(bbs, session, items: list[str], hint: str) -> str:
    """Funnel + selectable list + bottom + hint. Tab bar is drawn by mainmenu."""
    is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
    pal = palette_for(session)
    wide = wide_ambiguous_for(session, is_plain)
    tabs = visible_tabs(load_tabs(bbs), getattr(session, "user", None))
    labels = [x["label"] for x in tabs]
    aid = getattr(session, "_pim_active_tab", None) or (tabs[0]["id"] if tabs else "")
    active_idx = max(0, next((i for i, x in enumerate(tabs) if x["id"] == aid), 0))
    top = _build_top(
        labels, active_idx, _hint_for_session(session), is_plain,
        screen_width=79, session=session,
    )
    bot = "+" + "-" * 77 + "+" if is_plain else f"{pal.frame}{hline('└', '─', '┘', 79, wide_ambiguous=wide)}{pal.reset}"
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
        lines.append(_list_row("(nothing here)", False, is_plain, pal, wide=wide))
    else:
        for idx, text in enumerate(items):
            lines.append(_list_row(str(text), idx == selected, is_plain, pal, wide=wide))
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

        import time

        from core import live

        last_key_at = time.monotonic()
        while session.is_active:
            # Single keypress, no Enter -- menu keys are one character.
            # PIM navigation is handled here; classic keys fall through to
            # _handle(). Social idles until a key or a live post (push),
            # not a 1s poll-and-clear. Arm before paint so a post during
            # the draw still wakes the following wait.
            watching = (
                self._is_pim(session)
                and self._active_tab_id(session) == "social"
            )
            if watching:
                live.arm(self.bbs, session)
            try:
                await self._show_menu(session)
                if watching:
                    rem = runner.IDLE_TIMEOUT - (time.monotonic() - last_key_at)
                    if rem <= 0:
                        break
                    key = await runner.read_key_or_wake(
                        self.bbs, session,
                        timeout=rem, idle_on_timeout=True,
                    )
                    if key == live.WAKE:
                        continue
                else:
                    key = await runner.read_key(self.bbs, session)
            finally:
                if watching:
                    live.disarm(self.bbs, session)
            if key is None:
                break
            last_key_at = time.monotonic()
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
        """Tab cap ``┌── label ─┬─ label ─┐`` (same _flow_cells math as
        the hint carrier, so the hint lines up with the active cell)."""
        is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
        labels = [x["label"] for x in tabs]
        if not labels:
            return " " * 79
        active_idx = max(0, next((i for i, x in enumerate(tabs) if x["id"] == active_id), 0))
        return _build_tab_row(
            labels, active_idx, _hint_for_session(session), is_plain,
            screen_width=79, session=session,
        )

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

        from plugins.social.social import forget_social_selection

        if key in ("1", "2", "3", "4", "5"):
            idx = int(key) - 1
            if 0 <= idx < len(tabs):
                session._pim_active_tab = tabs[idx]["id"]
                forget_social_selection(session)
                return True
            return False

        if key in ("LEFT", "H"):
            active_idx = (active_idx - 1) % len(tabs)
            session._pim_active_tab = tabs[active_idx]["id"]
            forget_social_selection(session)
            return True
        if key in ("RIGHT", "L"):
            active_idx = (active_idx + 1) % len(tabs)
            session._pim_active_tab = tabs[active_idx]["id"]
            forget_social_selection(session)
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
                    # band A: tab cap; hint carrier in the pane is the next row
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
    "_build_tab_row",
    "_build_top",
    "_hint_for_session",
    "list_pane",
]