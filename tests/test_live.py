"""Push live Social updates — wake idle waiters instead of a 1s poll."""
from __future__ import annotations

import asyncio
from pathlib import Path

from core import live, runner
from core.app import BBSApp
from core.conversations import Conversations
from server.session import Session


class RecordingBBS:
    async def send(self, session, text):
        pass

    async def send_raw(self, session, data):
        pass


def test_post_wakes_armed_session(tmp_path: Path):
    async def _a():
        app = BBSApp(users_dir=tmp_path / "users")
        app.storage.plugins_dir = tmp_path / "plugins"
        app.conversations = Conversations(app)
        s = Session(session_id="t", node_id=1, address=("h", 1))
        live.arm(app, s)
        await app.conversations.create_conversation(
            kind="board", title="General", created_by="dave", conv_id="b1")
        assert s._live_wake.is_set()  # create also wakes
        s._live_wake.clear()
        await app.conversations.post_message("b1", author="ana", body="hello")
        assert s._live_wake.is_set()
        live.disarm(app, s)
        s._live_wake.clear()
        await app.conversations.post_message("b1", author="ana", body="again")
        assert not s._live_wake.is_set()

    asyncio.run(_a())


def test_disarmed_session_is_not_woken(tmp_path: Path):
    async def _a():
        app = BBSApp(users_dir=tmp_path / "users")
        app.storage.plugins_dir = tmp_path / "plugins"
        app.conversations = Conversations(app)
        watching = Session(session_id="a", node_id=1, address=("h", 1))
        idle = Session(session_id="b", node_id=2, address=("h", 1))
        live.arm(app, watching)
        await app.conversations.create_conversation(
            kind="board", title="General", created_by="dave", conv_id="b1")
        assert watching._live_wake.is_set()
        assert getattr(idle, "_live_wake", None) is None

    asyncio.run(_a())


def test_read_key_or_wake_returns_wake_without_a_key():
    async def _a():
        r = asyncio.StreamReader()
        s = Session(session_id="t", node_id=1, address=("h", 1))
        s.reader = r
        bbs = RecordingBBS()
        live.arm(bbs, s)

        async def _poke():
            await asyncio.sleep(0.02)
            s._live_wake.set()

        asyncio.create_task(_poke())
        key = await runner.read_key_or_wake(
            bbs, s, timeout=2.0, idle_on_timeout=False,
        )
        assert key == live.WAKE

    asyncio.run(_a())
