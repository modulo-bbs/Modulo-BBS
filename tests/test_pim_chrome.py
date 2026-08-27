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
        return self.buf.decode("utf-8", errors="replace")


def _app(tmp_path: Path) -> BBSApp:
    app = BBSApp(users_dir=tmp_path / "users")
    app.storage.plugins_dir = tmp_path / "plugins"
    app.screens.plugins_root = tmp_path
    from core.conversations import Conversations
    from plugins.mainmenu import MainmenuPlugin

    app.conversations = Conversations(app)
    p = MainmenuPlugin()
    p.on_load(app)
    app.plugins = [p]
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
    # pane border + hint (now WASD on plain, arrows+WASD on CP437/ANSI)
    assert "select" in text.lower()

    # top border: funnel '|' and '\' sit directly below the ACTIVE cell's
    # opening and closing '|' (Dave's flow-tab model — cells are '| label | ')
    import re as _re
    def _vis(line: str) -> str:
        return _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
    tab_line = _vis(next(l for l in text.splitlines() if "Dashboard" in l))
    pipes = [i for i, ch in enumerate(tab_line) if ch == "|"]
    assert pipes, "tab bar has no delimiters"
    top_line = next(l for l in text.splitlines() if "select" in l)
    stripped = _vis(top_line)
    # active tab is Dashboard (index 0) -> its cell pipes are pipes[0], pipes[1]
    head_pipes = [i for i, ch in enumerate(stripped) if ch == "|"]
    backslashes = [i for i, ch in enumerate(stripped) if ch == "\\"]
    assert head_pipes and head_pipes[0] == pipes[0], (
        f"funnel pipe {head_pipes[:1]} != active tab pipe {pipes[0]}"
    )
    assert backslashes and backslashes[0] == pipes[1], (
        f"funnel close {backslashes[:1]} != active tab close {pipes[1]}"
    )
    # pane content includes the seeded message preview
    assert "hello world" in text or "General" in text or "dave" in text
    # prompt is pinned at bottom (contains >)
    assert ">" in text


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
