"""PIM input routing (build-plan Step 8 + boards-unification B5): tabs + pane."""
from __future__ import annotations

import asyncio
import json
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


def test_tab_switch_numeric_and_arrows(tmp_path):
    app = _app(tmp_path)
    user = User(username="dave", groups=[])
    s = _session(user)
    p = app.get_plugin("mainmenu")
    assert p is not None

    async def _a():
        # Seed two board conversations so the Social sidebar has content
        await app.conversations.create_conversation(kind="board", title="General", created_by="dave", conv_id="general")
        await app.conversations.create_conversation(kind="board", title="Tech", created_by="dave", conv_id="tech")
        # default tab is dashboard; RIGHT lands on Social (B5 tab row)
        assert await p._handle_pim_key(s, "RIGHT") is True  # type: ignore[attr-defined]
        assert s._pim_active_tab == "social"  # type: ignore[attr-defined]
        # LEFT steps back to Dashboard (social is tab 2 of 4)
        assert await p._handle_pim_key(s, "LEFT") is True  # type: ignore[attr-defined]
        assert s._pim_active_tab == "dashboard"  # type: ignore[attr-defined]
        # Numeric 2 → Social
        assert await p._handle_pim_key(s, "2") is True  # type: ignore[attr-defined]
        assert s._pim_active_tab == "social"  # type: ignore[attr-defined]
        # invalid numeric ignored
        assert await p._handle_pim_key(s, "9") is False  # type: ignore[attr-defined]

    asyncio.run(_a())


def test_pane_selection_up_down(tmp_path):
    app = _app(tmp_path)
    user = User(username="dave", groups=[])
    s = _session(user)
    p = app.get_plugin("mainmenu")
    assert p is not None

    async def _a():
        s._pim_selected = 0  # type: ignore[attr-defined]
        assert await p._handle_pim_key(s, "DOWN") is True  # type: ignore[attr-defined]
        assert s._pim_selected == 1  # type: ignore[attr-defined]
        assert await p._handle_pim_key(s, "UP") is True  # type: ignore[attr-defined]
        assert s._pim_selected == 0  # type: ignore[attr-defined]
        # UP at 0 stays 0
        assert await p._handle_pim_key(s, "UP") is True  # type: ignore[attr-defined]
        assert s._pim_selected == 0  # type: ignore[attr-defined]
        # J/K aliases
        assert await p._handle_pim_key(s, "J") is True  # type: ignore[attr-defined]
        assert s._pim_selected == 1  # type: ignore[attr-defined]
        assert await p._handle_pim_key(s, "K") is True  # type: ignore[attr-defined]
        assert s._pim_selected == 0  # type: ignore[attr-defined]

    asyncio.run(_a())


def test_enter_opens_and_returns(tmp_path):
    app = _app(tmp_path)
    user = User(username="dave", groups=[])
    s = _session(user)
    p = app.get_plugin("mainmenu")
    assert p is not None

    async def _a():
        await app.conversations.create_conversation(kind="board", title="General", created_by="dave", conv_id="general")
        await app.conversations.post_message("general", author="dave", body="hello")
        s._pim_selected = 0  # type: ignore[attr-defined]
        # The classic full-screen reader is reachable from a conversation-
        # listing tab; defaults no longer include one, so simulate a sysop
        # tabs.json override with a boards branch.
        d = tmp_path / "plugins" / "mainmenu" / "data"
        d.mkdir(parents=True, exist_ok=True)
        (d / "tabs.json").write_text(json.dumps([
            {"id": "boards", "label": "Boards", "kind": "board", "key": "2"},
        ]))
        s._pim_active_tab = "boards"  # type: ignore[attr-defined]
        # Mock the "Press any key" pause so ENTER doesn't block
        import core.runner as runner

        orig = runner.read_key

        async def _fake(bbs, sess, timeout=300):
            return "Q"

        runner.read_key = _fake  # type: ignore[assignment]
        try:
            ok = await p._handle_pim_key(s, "ENTER")  # type: ignore[attr-defined]
            assert ok is True
            # pane reader wrote the message body into the writer
            text = bytes(s.writer.buf).decode("utf-8", errors="replace")  # type: ignore[union-attr]
            assert "hello" in text or "General" in text
        finally:
            runner.read_key = orig  # type: ignore[assignment]

    asyncio.run(_a())


def test_unhandled_key_falls_through(tmp_path):
    app = _app(tmp_path)
    user = User(username="dave", groups=[])
    s = _session(user)
    p = app.get_plugin("mainmenu")
    assert p is not None

    async def _a():
        # I (System Info) is not a PIM navigation key — should fall through
        assert await p._handle_pim_key(s, "I") is False  # type: ignore[attr-defined]
        assert await p._handle_pim_key(s, "X") is False  # type: ignore[attr-defined]

    asyncio.run(_a())


def test_slash_theme_at_prompt_persists_and_waits(tmp_path):
    """`>` is a hotkey prompt: `/` then `theme amber` + Enter must save, and
    the confirmation paints in a CUP overlay so the PIM is not scrolled."""
    from unittest.mock import AsyncMock, patch

    app = _app(tmp_path)

    async def _a():
        user = await app.users.create("dave", password="pw-test-123")
        s = _session(user)
        p = app.get_plugin("mainmenu")
        assert p is not None
        with patch("core.runner.read_key", new_callable=AsyncMock, return_value="ENTER"):
            await p._dispatch_slash(s, "theme amber")  # type: ignore[attr-defined]
        assert s.user.preferences.get("theme") == "amber"
        out = bytes(s.writer.buf).decode("cp437", errors="replace")  # type: ignore[union-attr]
        assert "Theme set to amber" in out
        assert "any key dismiss" in out
        assert "\x1b[2;1H" in out  # CUP to overlay top-left (row 2, col 1)
        assert "┌" in out or "+" in out

    asyncio.run(_a())


def test_slash_theme_list_in_overlay(tmp_path):
    """Bare `/theme` opens an up/down picker; ESC leaves the saved theme."""
    from unittest.mock import AsyncMock, patch

    app = _app(tmp_path)

    async def _a():
        user = await app.users.create("dave", password="pw-test-123")
        s = _session(user)
        p = app.get_plugin("mainmenu")
        assert p is not None
        with patch("core.runner.read_key", new_callable=AsyncMock, return_value="ESC"):
            await p._dispatch_slash(s, "theme")  # type: ignore[attr-defined]
        out = bytes(s.writer.buf).decode("cp437", errors="replace")  # type: ignore[union-attr]
        assert "classic *" in out
        assert "matrix" in out
        assert "arrows select" in out
        assert "\x1b[2;1H" in out
        assert s.user.preferences.get("theme", "classic") in ("classic", None)

    asyncio.run(_a())


def test_slash_theme_picker_enter_applies(tmp_path):
    """DOWN then Enter on `/theme` saves the next named palette."""
    from unittest.mock import patch

    app = _app(tmp_path)

    async def _a():
        user = await app.users.create("dave", password="pw-test-123")
        s = _session(user)
        p = app.get_plugin("mainmenu")
        assert p is not None
        keys = iter(["DOWN", "ENTER"])

        async def _next_key(*_a, **_k):
            return next(keys)

        with patch("core.runner.read_key", side_effect=_next_key):
            await p._dispatch_slash(s, "theme")  # type: ignore[attr-defined]
        assert s.user.preferences.get("theme") == "amber"

    asyncio.run(_a())
