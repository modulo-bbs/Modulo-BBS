"""Chrome snapshot for the tabbed PIM home (build-plan § Step 7)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from core.app import BBSApp
from core.user import User
from server.session import Session


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

    # top border: funnel open/close sit directly below the ACTIVE cell's
    # opening and closing bars (Dave's flow-tab model — cells are '| label | ')
    import re as _re
    def _vis(line: str) -> str:
        return _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
    def _bars(line: str) -> list[int]:
        return [i for i, ch in enumerate(line) if ch in "|│"]
    tab_line = _vis(next(l for l in text.splitlines() if "Dashboard" in l))
    pipes = _bars(tab_line)
    assert pipes, "tab bar has no delimiters"
    top_line = next(l for l in text.splitlines() if "select" in l)
    stripped = _vis(top_line)
    # active tab is Dashboard (index 0) -> its cell pipes are pipes[0], pipes[1]
    head_pipes = _bars(stripped)
    closes = [i for i, ch in enumerate(stripped) if ch in "\\┐"]
    assert head_pipes and head_pipes[0] == pipes[0], (
        f"funnel pipe {head_pipes[:1]} != active tab pipe {pipes[0]}"
    )
    assert closes and closes[0] == pipes[1], (
        f"funnel close {closes[:1]} != active tab close {pipes[1]}"
    )
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


def test_utf8_social_funnel_connects_left_edge(tmp_path):
    """UTF-8 used to mix ASCII | \\ on a ─ rule, leaving a stub at col 0."""
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
    pane = next(ln for ln in lines if ln.startswith("│") and "DMs" in ln)
    assert tab.startswith("│"), tab
    assert funnel.startswith("│"), funnel
    assert pane.startswith("│"), pane
    assert "↑" in funnel and "\\" not in funnel
    assert "┐" in funnel
    bars = [i for i, ch in enumerate(tab) if ch == "│"]
    funnel_bars = [i for i, ch in enumerate(funnel) if ch == "│"]
    close = funnel.index("┐")
    # Social is tab 1: opening bar matches, close sits on the tab's close
    assert funnel_bars[1] == bars[2]
    assert close == bars[3]


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


def test_amber_theme_recolors_active_tab(tmp_path):
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
