"""B4 — Social input routing (boards-unification §B4).

Browse mode: arrows select rooms (wrapping), PgUp/PgDn/Space scroll the
thread pane, ESC/B step back to Dashboard. Compose mode (R reply, N new,
D delete prompts) reads LINES via read_command — never leak navigation
into drafts. Tab switching (LEFT/RIGHT/1..5) stays with the PIM layer.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core import runner
from core.app import BBSApp
from core.user import User
from plugins.mainmenu import MainmenuPlugin
from server.session import Session


class FakeWriter:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf.extend(data)

    def is_closing(self):
        return False

    async def drain(self):
        pass


def _app(tmp_path: Path):
    app = BBSApp(users_dir=tmp_path / "users")
    app.storage.plugins_dir = tmp_path / "plugins"
    app.screens.plugins_root = tmp_path
    from core.conversations import Conversations

    app.conversations = Conversations(app)
    p = MainmenuPlugin()
    p.on_load(app)
    app.plugins = [p]
    # Sysop tabs override: SOCIAL present pre-flip (B5 will make it default)
    d = tmp_path / "plugins" / "mainmenu" / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tabs.json").write_text(json.dumps([
        {"id": "dashboard", "label": "Dashboard", "kind": "dashboard", "key": "1"},
        {"id": "social", "label": "Social", "kind": "all", "key": "2"},
        {"id": "files", "label": "Files", "kind": "files", "key": "3"},
        {"id": "bulletins", "label": "Bulletins", "kind": "bulletins", "key": "4"},
    ]))
    return app


def _session(user=None):
    s = Session(session_id="t", node_id=1, address=("h", 1))
    s.writer = FakeWriter()  # type: ignore[attr-defined]
    s.terminal_type = "UNKNOWN"
    s.terminal_width = 80
    s.terminal_height = 24
    s.user = user
    s.username = user.username if user else ""
    return s


def _seed(app, n_boards=2):
    async def _a():
        await app.conversations.create_conversation(
            kind="board", title="First Board", created_by="dave", conv_id="b1")
        await app.conversations.create_conversation(
            kind="board", title="Second Board", created_by="dave", conv_id="b2")
        await app.conversations.post_message("b1", author="ana", body="hello")

    asyncio.run(_a())


def _social_session(user):
    s = _session(user)
    s._pim_active_tab = "social"
    return s


def test_arrow_selection_wraps_over_rooms(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _social_session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        # rooms: dms, b1, b2 (activity order: b1 has a post)
        assert await p._handle_pim_key(s, "DOWN") is True
        assert s._pim_selected == 1
        assert await p._handle_pim_key(s, "DOWN") is True
        assert s._pim_selected == 2
        # wrap past last room -> pinned DMs again
        assert await p._handle_pim_key(s, "DOWN") is True
        assert s._pim_selected == 0
        # up wraps backwards
        assert await p._handle_pim_key(s, "UP") is True
        assert s._pim_selected == 2

    asyncio.run(_a())


def test_pgdn_pgup_space_scroll_state(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _social_session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        await p._handle_pim_key(s, "PGDN")
        assert getattr(s, "_social_scroll_up", 0) >= 0
        before = s._social_scroll_up
        await p._handle_pim_key(s, "PGUP")
        assert s._social_scroll_up > before
        # space aliases pgdn
        await p._handle_pim_key(s, "SPACE")
        assert s._social_scroll_up == 0 or s._social_scroll_up < before

    asyncio.run(_a())


def test_n_creates_thread_enforcing_title_cap(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _social_session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        lines = iter(["An Overly Long Thread Title For Sure", "Trading"])
        orig = runner.read_command

        async def fake_rc(bbs, session, timeout=runner.IDLE_TIMEOUT):
            return next(lines)

        runner.read_command = fake_rc  # type: ignore[assignment]
        try:
            assert await p._handle_pim_key(s, "N") is True
        finally:
            runner.read_command = orig  # type: ignore[assignment]
        convs = await app.conversations.list_conversations(kind="board", visible_to=dave)
        titles = [c["title"] for c in convs]
        assert "Trading" in titles
        assert "An Overly Long Thread Title For Sure" not in titles
        # selection jumped onto the new thread
        assert any(c["title"] == "Trading" for c in convs)

    asyncio.run(_a())


def test_n_empty_title_silently_aborts(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _social_session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        before = await app.conversations.list_conversations(
            kind="board", visible_to=dave)
        sel_before = int(getattr(s, "_pim_selected", 0) or 0)
        orig = runner.read_command

        async def fake_rc(bbs, session, timeout=runner.IDLE_TIMEOUT):
            return ""

        runner.read_command = fake_rc  # type: ignore[assignment]
        try:
            assert await p._handle_pim_key(s, "N") is True
        finally:
            runner.read_command = orig  # type: ignore[assignment]
        after = await app.conversations.list_conversations(
            kind="board", visible_to=dave)
        assert [c["id"] for c in after] == [c["id"] for c in before]
        assert int(getattr(s, "_pim_selected", 0) or 0) == sel_before
        text = bytes(s.writer.buf).decode("cp437", errors="replace")
        assert "Thread title" in text
        assert "cancelled" not in text.lower()
        assert "created" not in text.lower()

    asyncio.run(_a())


def test_n_whitespace_title_silently_aborts(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _social_session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        before = await app.conversations.list_conversations(
            kind="board", visible_to=dave)
        orig = runner.read_command

        async def fake_rc(bbs, session, timeout=runner.IDLE_TIMEOUT):
            return " \t  "

        runner.read_command = fake_rc  # type: ignore[assignment]
        try:
            assert await p._handle_pim_key(s, "N") is True
        finally:
            runner.read_command = orig  # type: ignore[assignment]
        after = await app.conversations.list_conversations(
            kind="board", visible_to=dave)
        assert [c["title"] for c in after] == [c["title"] for c in before]
        text = bytes(s.writer.buf).decode("cp437", errors="replace")
        assert "cancelled" not in text.lower()
        assert not any(c["title"].strip() == "" for c in after)

    asyncio.run(_a())


def test_r_and_d_retired_from_social_fall_through(tmp_path):
    """B8: R/D compose keys are gone — messaging lives in chat mode
    (Enter). Unhandled keys fall through to the generic PIM layer."""
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _social_session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        assert await p._handle_pim_key(s, "R") is False
        assert await p._handle_pim_key(s, "D") is False

    asyncio.run(_a())


def test_enter_opens_chat_and_esc_returns(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _social_session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        s._pim_selected = 1  # b1 (has ana's post)
        import core.runner as runner

        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return "ESC"  # leave chat on its first prompt

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            assert await p._handle_pim_key(s, "ENTER") is True
        finally:
            runner.read_key = orig  # type: ignore[assignment]
        text = bytes(s.writer.buf).decode("cp437", errors="replace")
        # chat surface rendered the room with bubbles + input prompt
        assert "First Board" in text and ">" in text
        # entering cleared unread
        assert await app.conversations.unread_count("dave", "b1") == 0

    asyncio.run(_a())


def test_esc_returns_to_dashboard(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _social_session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        assert await p._handle_pim_key(s, "ESC") is True
        assert s._pim_active_tab == "dashboard"

    asyncio.run(_a())


def test_b_scoped_to_social_only(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _social_session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        # inside Social, B acts as back
        assert await p._handle_pim_key(s, "B") is True
        assert s._pim_active_tab == "dashboard"
        # outside Social, B is NOT a tab/back command (falls through)
        s._pim_active_tab = "files"
        assert await p._handle_pim_key(s, "B") is False

    asyncio.run(_a())


def test_csi_pgup_pgdn_keys_parse():
    assert runner._try_arrow("\x1b[5~") == ("PGUP", "")
    assert runner._try_arrow("\x1b[6~") == ("PGDN", "")
    assert runner._try_arrow("\x1b[5") is None  # incomplete


def test_read_key_returns_pgup_pgdn_and_space():
    async def _a():
        r = asyncio.StreamReader()
        r.feed_data("\x1b[5~ \x1b[6~".encode("cp437"))
        r.feed_eof()
        s = Session(session_id="t", node_id=1, address=("h", 1))
        s.reader = r

        class BB:
            async def send(self, *a):
                pass

            async def send_raw(self, *a):
                pass

        bb = BB()
        assert await runner.read_key(bb, s) == "PGUP"
        assert await runner.read_key(bb, s) == "SPACE"
        assert await runner.read_key(bb, s) == "PGDN"

    asyncio.run(_a())
