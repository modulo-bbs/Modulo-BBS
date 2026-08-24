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
    # pane content includes the seeded message preview
    assert "hello world" in text or "General" in text or "dave" in text
    # prompt is pinned at bottom (contains >)
    assert ">" in text


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
