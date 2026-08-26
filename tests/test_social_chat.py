"""B8 — Social chat mode (Telegram-style): type at the prompt, Enter sends,
UP/DOWN scrolls history, polling picks up other nodes' messages, NEW badges
mark arrivals since entry. Replaces the classic reader on Social rooms.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

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


def _seed(app):
    async def _a():
        await app.conversations.create_conversation(
            kind="board", title="General", created_by="dave", conv_id="b1")
        await app.conversations.post_message("b1", author="ana", body="hello room")

    asyncio.run(_a())


def test_typing_at_prompt_posts_and_esc_exits(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    s.terminal_type = "ANSI-BBS"  # exercise the CP437 box-drawing path
    p = app.get_plugin("mainmenu")

    async def _a():
        keys = iter(["h", "i", " ", "t", "h", "e", "r", "e", "ENTER", "ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return next(keys)

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await p._social_chat(s, {"id": "b1", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]

        msgs = await app.conversations.list_messages("b1")
        assert msgs[-1]["body"] == "hi there"
        assert msgs[-1]["author"] == "dave"
        text = bytes(s.writer.buf).decode("cp437", errors="replace")
        import re as _re

        plain = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
        assert "\u250c" in text          # bubbles drawn
        assert "> hi there" in plain     # draft echoed at prompt

    asyncio.run(_a())


def test_enter_at_end_always_tail_anchored(tmp_path):
    """Long thread: entering lands on the newest messages, not the top."""
    app = _app(tmp_path)

    async def _seed_many():
        await app.conversations.create_conversation(
            kind="board", title="General", created_by="dave", conv_id="big")
        for n in range(1, 41):
            await app.conversations.post_message("big", author=f"u{n}", body=f"msg {n}")

    asyncio.run(_seed_many())
    dave = User(username="dave", groups=[])
    s = _session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        keys = iter(["ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return next(keys)

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await p._social_chat(s, {"id": "big", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]

        text = bytes(s.writer.buf).decode("cp437", errors="replace")
        assert "msg 40" in text
        assert "msg 1\n" not in text.replace("\r\n", "\n").split("msg 5")[0]

    asyncio.run(_a())


def test_up_scrolls_history_down_returns(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        keys = iter(["UP", "UP", "DOWN", "ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return next(keys)

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await p._social_chat(s, {"id": "b1", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]
        text = bytes(s.writer.buf).decode("cp437", errors="replace")
        assert "history" in text   # status line shows hidden/newer count

    asyncio.run(_a())


def test_echo_suppressed_during_chat_restored_after(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    p = app.get_plugin("mainmenu")

    async def _a():
        assert not getattr(s, "suppress_echo", False)
        keys = iter(["ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return next(keys)

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await p._social_chat(s, {"id": "b1", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]
        assert not getattr(s, "suppress_echo", False)

    asyncio.run(_a())
