"""Chrome snapshot for the tabbed PIM home (build-plan § Step 7)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from core.app import BBSApp
from core.user import User
from plugins.mainmenu import _build_tab_row, _build_top, _flow_cells, _hint_for_session
from plugins.mainmenu import _sep_xs, _tab_sep
from server.session import Session
from shared.visible import at_display, display_width, slice_display, strip_ansi


class FakeWriter:
    def __init__(self):
        self.buf = bytearray()
        self.closed = False

    def write(self, data: bytes):
        self.buf.extend(data)

    def is_closing(self):
        return self.closed

    async def drain(self):
        pass

    def text(self) -> str:
        try:
            return self.buf.decode("utf-8")
        except UnicodeDecodeError:
            return self.buf.decode("cp437")


def _app(tmp_path: Path) -> BBSApp:
    app = BBSApp(users_dir=tmp_path / "users")
    app.storage.plugins_dir = tmp_path / "plugins"
    app.screens.plugins_root = tmp_path
    from core.conversations import Conversations
    from plugins.bulletins import BulletinsPlugin
    from plugins.dashboard import DashboardPlugin
    from plugins.files import FilesPlugin
    from plugins.mainmenu import MainmenuPlugin
    from plugins.modal import ModalPlugin
    from plugins.social import SocialPlugin

    app.conversations = Conversations(app)
    loaded = []
    for cls in (ModalPlugin, DashboardPlugin, SocialPlugin, FilesPlugin, BulletinsPlugin, MainmenuPlugin):
        inst = cls()
        inst.on_load(app)
        loaded.append(inst)
    app.plugins = loaded
    return app


def _session(user: User | None = None) -> Session:
    s = Session(session_id="t", node_id=1, address=("h", 1))
    s.writer = FakeWriter()  # type: ignore[assignment]
    s.terminal_type = "ANSI-BBS"
    s.terminal_width = 80
    s.terminal_height = 24
    s.user = user
    s.username = user.username if user else ""
    return s


def test_pim_shows_tabs_and_pane(tmp_path):
    app = _app(tmp_path)
    user = User(username="dave", groups=[])
    # seed a board conversation so pane is not empty
    async def _seed():
        await app.conversations.create_conversation(kind="board", title="General", created_by="dave", conv_id="general")
        await app.conversations.post_message("general", author="dave", body="hello world")

    asyncio.run(_seed())

    s = _session(user)
    # default is PIM (home_mode != "menu")
    p = app.get_plugin("mainmenu")
    assert p is not None
    asyncio.run(p._show_menu(s))  # type: ignore[attr-defined]
    text = s.writer.text()  # type: ignore[union-attr]
    # tab bar: active tab in caps for ANSI we expect colors + label
    assert "Boards" in text
    # pane border + hint (arrows+WASD on CP437 and UTF-8; WASD on plain)
    assert "select" in text.lower()

    # hint sits in the active tab's inner slot, delimited by the carrier walls
    import re as _re
    def _vis(line: str) -> str:
        return _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
    tab_line = _vis(next(l for l in text.splitlines() if "Dashboard" in l))
    assert tab_line.startswith("┌"), tab_line
    assert tab_line.endswith("┐") or at_display(tab_line, 78) == "┐"
    assert "|" not in tab_line
    assert "│ │" not in tab_line  # shared bars, not a doubled cell wall
    assert "┬" in tab_line
    top_line = next(l for l in text.splitlines() if "select" in l)
    stripped = _vis(top_line)
    assert stripped.startswith("├"), stripped  # Dashboard active: T into the pane
    assert at_display(stripped, 78) == "┤"
    assert "┴" in stripped  # idle tab joints
    labels = ["Dashboard", "Social", "Files", "Bulletins"]
    hint = " \x18\x19\x1B\x1A · WASD select "
    widths, _x, _slot = _flow_cells(labels, hint, 0, wide=False, sep="│")
    xs = _sep_xs(widths, 1)
    inner = slice_display(stripped, xs[0] + 2, xs[1] - 1, wide_ambiguous=False)
    assert "select" in inner.lower()
    assert "\\" not in stripped and "/" not in stripped
    assert "│ │" not in stripped
    # pane content includes the seeded message preview
    assert "hello world" in text or "General" in text or "dave" in text
    # prompt is pinned at bottom (contains >)
    assert ">" in text
    from shared.telnet_protocol import ANSI
    assert ANSI.BG_BLUE in text  # classic active-tab background


def test_social_tab_keeps_tab_bar_on_24_row_terminal(tmp_path):
    """Social used to paint 23 pane rows; the trailing CRLF after the pane
    scrolled a 24-row SyncTERM and the Dashboard|Social tab bar vanished."""
    import re as _re

    app = _app(tmp_path)
    user = User(username="dave", groups=[])

    async def _seed():
        await app.conversations.create_conversation(
            kind="board", title="General", created_by="dave", conv_id="general")
        await app.conversations.post_message("general", author="dave", body="hello")

    asyncio.run(_seed())
    s = _session(user)
    s._pim_active_tab = "social"
    p = app.get_plugin("mainmenu")
    asyncio.run(p._show_menu(s))  # type: ignore[attr-defined]
    raw = s.writer.text()  # type: ignore[union-attr]

    W, H = 80, 24
    screen = [""] * H
    row = col = 1
    for tok in _re.split(r"(\x1b\[[0-9;]*[A-Za-z])", raw):
        if not tok:
            continue
        if tok.startswith("\x1b["):
            cmd = tok[-1]
            if cmd == "H":
                p = tok[2:-1].split(";")
                row = int(p[0] or 1)
                col = int(p[1] or 1) if len(p) > 1 else 1
            elif cmd == "J":
                screen = [""] * H
            continue
        for ch in tok:
            if ch == "\r":
                col = 1
            elif ch == "\n":
                row += 1
                if row > H:
                    raise AssertionError(
                        f"Social redraw scrolled a 24-row terminal (row={row})"
                    )
            elif ch != "\x1b":
                if 1 <= row <= H:
                    line = screen[row - 1]
                    if col > len(line) + 1:
                        line += " " * (col - len(line) - 1)
                    if col == len(line) + 1:
                        screen[row - 1] = line + ch
                    else:
                        screen[row - 1] = line[: col - 1] + ch + line[col:]
                col += 1
                if col > W:
                    row += 1
                    col = 1
                    if row > H:
                        raise AssertionError(
                            "Social redraw wrapped off a 24-row terminal"
                        )

    top = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", screen[0])
    assert "Dashboard" in top and "Social" in top, f"tab bar missing: {top!r}"


def test_utf8_social_hint_carrier_joins_tabs(tmp_path):
    """Social active: left corner closes Dashboard; hint is ┤…├ under Social."""
    import re as _re

    app = _app(tmp_path)
    user = User(username="dave", groups=[], preferences={"theme": "amber"})
    s = _session(user)
    s.terminal_type = "xterm-256color"
    s.codec = "utf-8"
    s._pim_active_tab = "social"
    p = app.get_plugin("mainmenu")
    asyncio.run(p._show_menu(s))
    raw = s.writer.text()
    vis = lambda ln: _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", ln)
    lines = [vis(ln) for ln in raw.split("\r\n") if vis(ln).strip()]
    tab = next(ln for ln in lines if "Social" in ln and "Dashboard" in ln)
    funnel = next(ln for ln in lines if "select" in ln)
    assert tab.startswith("┌"), tab
    assert at_display(tab, 78) == "┐"
    assert funnel.startswith("└"), funnel
    assert at_display(funnel, 78) == "┤"
    assert "↑" in funnel
    assert "\\" not in funnel and "/" not in funnel and "|" not in funnel
    assert "│ │" not in tab and "│ │" not in funnel
    labels = ["Dashboard", "Social", "Files", "Bulletins"]
    hint = _hint_for_session(s)
    widths, _x, _slot = _flow_cells(labels, hint, 1, wide=False, sep="│")
    xs = _sep_xs(widths, 1)
    assert at_display(funnel, xs[1]) == "┤"
    assert at_display(funnel, xs[2]) == "├"
    inner = slice_display(funnel, xs[1] + 2, xs[2] - 1, wide_ambiguous=False)
    assert "select" in inner


def test_classic_fallback_when_home_mode_menu(tmp_path):
    app = _app(tmp_path)
    user = User(username="dave", groups=[], preferences={"home_mode": "menu"})
    s = _session(user)
    p = app.get_plugin("mainmenu")
    asyncio.run(p._show_menu(s))  # type: ignore[attr-defined]
    text = s.writer.text()  # type: ignore[union-attr]
    # classic contains Main Menu, not the PIM tab bar
    assert "Main Menu" in text
    assert "up/dn select" not in text


def test_tab_bars_use_frame_role(tmp_path):
    """Tab-strip junctions follow frame=, not the terminal default."""
    from core.theme import palette_for

    app = _app(tmp_path)
    user = User(username="dave", groups=[], preferences={"theme": "amber"})
    s = _session(user)
    p = app.get_plugin("mainmenu")
    asyncio.run(p._show_menu(s))
    pal = palette_for(s)
    raw_tab = next(l for l in s.writer.text().splitlines() if "Dashboard" in l)
    assert pal.frame in raw_tab
    assert "┌" in raw_tab and "┬" in raw_tab
    from shared.telnet_protocol import ANSI

    app = _app(tmp_path)
    user = User(username="dave", groups=[], preferences={"theme": "amber"})
    s = _session(user)
    p = app.get_plugin("mainmenu")
    asyncio.run(p._show_menu(s))  # type: ignore[attr-defined]
    text = s.writer.text()  # type: ignore[union-attr]
    assert ANSI.BG_YELLOW in text
    assert ANSI.BG_BLUE not in text


def test_matrix_paints_list_in_phosphor(tmp_path):
    """CRT mono must colour body text and selection, not leave gray+REVERSE."""
    from shared.telnet_protocol import ANSI

    app = _app(tmp_path)
    user = User(username="dave", groups=[], preferences={"theme": "matrix"})
    s = _session(user)
    p = app.get_plugin("mainmenu")
    asyncio.run(p._show_menu(s))  # type: ignore[attr-defined]
    text = s.writer.text()  # type: ignore[union-attr]
    assert ANSI.BRIGHT_GREEN in text
    assert ANSI.GREEN in text
    assert ANSI.BG_GREEN in text
    assert ANSI.REVERSE not in text
    assert ANSI.DIM not in text


def test_list_row_selected_matches_idle_width():
    """Highlighted digest row used one fewer left pad; right │ sat short."""
    from plugins.mainmenu import _list_row
    from core.theme import load_palette
    from shared.visible import display_width

    pal = load_palette("classic")
    sel = _list_row("Bulletins: (no new)", True, False, pal)
    idle = _list_row("Files: (no new)", False, False, pal)
    assert display_width(sel) == 79
    assert display_width(idle) == 79
    from shared.visible import strip_ansi
    assert strip_ansi(sel).startswith("│  ")
    assert strip_ansi(idle).startswith("│  ")


def test_carrier_glyphs_follow_active_tab():
    """Dashboard gets ├; later tabs close the left with └ and ┤ hint ├."""
    labels = ["Dashboard", "Social", "Files", "Bulletins"]
    hint = " WASD select "
    cap = strip_ansi(_build_tab_row(labels, 0, hint, False, 79, None))
    assert display_width(cap) == 79
    assert at_display(cap, 0) == "┌"
    assert at_display(cap, 78) == "┐"
    assert "┬" in cap

    dash = strip_ansi(_build_top(labels, 0, hint, False, 79, None))
    assert display_width(dash) == 79
    assert at_display(dash, 0) == "├"
    assert at_display(dash, 78) == "┤"
    widths, _, _ = _flow_cells(labels, hint, 0, sep=_tab_sep(False))
    xs = _sep_xs(widths, 1)
    assert at_display(dash, xs[1]) == "├"  # right of Dashboard
    assert at_display(dash, xs[2]) == "┴"
    assert "│ │" not in dash

    social = strip_ansi(_build_top(labels, 1, hint, False, 79, None))
    widths, _, _ = _flow_cells(labels, hint, 1, sep=_tab_sep(False))
    xs = _sep_xs(widths, 1)
    assert at_display(social, 0) == "└"
    assert at_display(social, xs[1]) == "┤"
    assert at_display(social, xs[2]) == "├"
    assert at_display(social, 78) == "┤"
    assert "│ │" not in social
    assert "select" in slice_display(social, xs[1] + 2, xs[2] - 1)

    last = strip_ansi(_build_top(labels, 3, hint, False, 79, None))
    widths, _, _ = _flow_cells(labels, hint, 3, sep=_tab_sep(False))
    xs = _sep_xs(widths, 1)
    assert at_display(last, 0) == "└"
    assert at_display(last, xs[3]) == "┤"
    if xs[4] != 78:
        assert at_display(last, xs[4]) == "├"
    assert at_display(last, 78) == "┤"

