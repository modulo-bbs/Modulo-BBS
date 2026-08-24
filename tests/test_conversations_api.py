"""Tests for conversations One-API ops (build-plan § Step 4)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.app import BBSApp
from core.user import User

# Import opdefs to register ops
import core.opdefs  # noqa: F401


def _app(tmp_path: Path) -> BBSApp:
    app = BBSApp(users_dir=tmp_path / "users")
    app.storage.plugins_dir = tmp_path / "plugins"
    from core.conversations import Conversations

    app.conversations = Conversations(app)
    return app


def _run(coro):
    return asyncio.run(coro)


def _user(name, groups=None):
    return User(username=name, groups=groups or [])


def test_conversations_ops_via_registry(tmp_path):
    app = _app(tmp_path)
    dave = _user("dave", ["sysop"])
    pleb = _user("pleb", [])

    async def _a():
        from core.ops import registry

        # sysop creates a board
        board = await registry.call(app, dave, "conversations.create", {"kind": "board", "title": "General"})
        assert board["kind"] == "board"
        # pleb cannot create board
        with pytest.raises(Exception):
            await registry.call(app, pleb, "conversations.create", {"kind": "board", "title": "Nope"})
        # anyone logged in can create channel/dm
        chan = await registry.call(app, pleb, "conversations.create", {"kind": "channel", "title": "Lobby"})
        assert chan["kind"] == "channel"
        dm = await registry.call(app, pleb, "conversations.create", {"kind": "dm", "title": "pleb-dave", "participants": "dave"})
        assert dm["participants"] == ["pleb", "dave"] or dm["participants"] == ["dave", "pleb"] or "pleb" in dm["participants"]
        # list is visibility-filtered
        lst = await registry.call(app, pleb, "conversations.list", {})
        assert any(c["id"] == board["id"] for c in lst["conversations"])
        # pleb can list without kind filter
        lst2 = await registry.call(app, pleb, "conversations.list", {"kind": "board"})
        assert all(c["kind"] == "board" for c in lst2["conversations"])
        # messages
        m = await registry.call(app, dave, "messages.post", {"conversation_id": board["id"], "body": "hello"})
        assert m["author"] == "dave"
        # pleb can read
        page = await registry.call(app, pleb, "messages.list", {"conversation_id": board["id"]})
        assert page["total"] == 1
        # threaded reply
        m2 = await registry.call(app, pleb, "messages.post", {"conversation_id": board["id"], "body": "hi", "parent_id": m["id"]})
        assert m2["parent_id"] == m["id"]
        # find
        found = await registry.call(app, pleb, "messages.find", {"query": "hello"})
        assert found["count"] >= 1
        # delete own
        await registry.call(app, pleb, "messages.delete", {"conversation_id": board["id"], "id": m2["id"]})
        # anonymous cannot create
        with pytest.raises(Exception):
            await registry.call(app, None, "conversations.create", {"kind": "channel", "title": "anon"})

    _run(_a())


def test_conversations_schema_visible():
    from core.ops import registry

    schema = registry.schema()
    ops = schema["operations"] if isinstance(schema, dict) else schema  # type: ignore[index]
    names = {op["name"] for op in ops}  # type: ignore[index]
    for expected in (
        "conversations.list",
        "conversations.get",
        "conversations.create",
        "messages.list",
        "messages.post",
        "messages.delete",
        "messages.find",
    ):
        assert expected in names, f"{expected} missing from schema"


def test_dm_not_visible_to_outsider(tmp_path):
    app = _app(tmp_path)
    dave = _user("dave", [])
    ana = _user("ana", [])
    bob = _user("bob", [])

    async def _a():
        from core.ops import registry

        # dave creates DM with ana
        dm = await registry.call(app, dave, "conversations.create", {"kind": "dm", "title": "dave-ana", "participants": "ana"})
        # ana can get it
        got = await registry.call(app, ana, "conversations.get", {"conversation_id": dm["id"]})
        assert got["id"] == dm["id"]
        # bob cannot
        with pytest.raises(Exception):
            await registry.call(app, bob, "conversations.get", {"conversation_id": dm["id"]})

    _run(_a())
