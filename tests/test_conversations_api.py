"""Tests for conversations One-API ops (build-plan § Step 4)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.app import BBSApp
from core.ops import PermissionDeniedError, ValidationError
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


def test_board_title_capped_for_social_threads(tmp_path):
    """B6: social thread titles cap at 15 chars at the op layer (400)."""
    app = _app(tmp_path)
    dave = _user("dave", ["sysop"])

    async def _a():
        from core.ops import registry

        with pytest.raises(ValidationError):
            await registry.call(app, dave, "conversations.create", {
                "kind": "board", "title": "An Absurdly Long Board Title"})
        ok = await registry.call(app, dave, "conversations.create", {
            "kind": "board", "title": "Trading Post"})
        assert ok["title"] == "Trading Post"
        # other kinds unaffected
        dm = await registry.call(app, dave, "conversations.create", {
            "kind": "dm", "title": "a title much longer than fifteen", "participants": "pleb"})
        assert dm["kind"] == "dm"

    _run(_a())


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


def test_conversations_delete_op(tmp_path):
    app = _app(tmp_path)
    dave = _user("dave")
    ana = _user("ana")
    sysop = _user("root", ["sysop"])

    async def _a():
        from core.ops import registry

        await app.conversations.create_conversation(
            kind="board", title="Mine", created_by="dave", conv_id="mine")
        out = await registry.call(
            app, dave, "conversations.delete", {"conversation_id": "mine"})
        assert out["deleted"] == "mine"
        assert await app.conversations.get_conversation("mine") is None

        await app.conversations.create_conversation(
            kind="board", title="Busy", created_by="dave", conv_id="busy")
        await app.conversations.post_message("busy", author="ana", body="reply")
        with pytest.raises(PermissionDeniedError):
            await registry.call(
                app, dave, "conversations.delete", {"conversation_id": "busy"})
        out2 = await registry.call(
            app, sysop, "conversations.delete", {"conversation_id": "busy"})
        assert out2["deleted"] == "busy"

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
        "conversations.delete",
        "messages.list",
        "messages.post",
        "messages.delete",
        "messages.find",
    ):
        assert expected in names, f"{expected} missing from schema"


def _mb_stub(app, boards):
    """Minimal messageboard plugin stand-in for boards.* op tests."""

    class _Stub:
        name = "messageboard"

        def __init__(self, boards):
            self.boards = boards

        def visible_boards(self, user):
            return [b for b in self.boards if user.can_access(b.get("requires", []))]

    app.plugins.append(_Stub(boards))


def test_boards_ops_write_unified_store(tmp_path):
    """A2 unification: boards.post/messages/delete land in core/conversations
    (One-API parity — same store the PIM Social surface reads)."""
    app = _app(tmp_path)
    _mb_stub(app, [{"id": "general", "name": "General Discussion", "requires": []}])
    dave = _user("dave")
    pleb = _user("pleb")

    async def _a():
        from core.ops import registry

        # Boot migration guarantees defined boards exist as conversations;
        # replicate that guarantee (handlers are strict on purpose).
        await app.conversations.create_conversation(kind="board", title="General Discussion",
                                                    created_by="system", conv_id="general")
        # post via boards.op → readable via unified engine
        res = await registry.call(app, pleb, "boards.post",
                                  {"board": "general", "subject": "Hi", "body": "via API"})
        msgs = await app.conversations.list_messages("general")
        assert any("via API" in m["body"] for m in msgs)
        assert any(m["author"] == "pleb" for m in msgs)
        # subject is preserved as first body line
        assert res["body"].startswith("Hi")
        # boards.messages reads the SAME unified store
        page = await registry.call(app, pleb, "boards.messages", {"board": "general"})
        assert len(page["messages"]) == 1 and page["messages"][0]["body"].startswith("Hi")
        # delete via boards.op works against unified store
        await registry.call(app, pleb, "boards.delete_message",
                            {"board": "general", "id": res["id"]})
        assert await app.conversations.list_messages("general") == []

    _run(_a())


def test_boards_post_gates_and_events(tmp_path):
    app = _app(tmp_path)
    _mb_stub(app, [
        {"id": "public", "name": "Public", "requires": []},
        {"id": "private", "name": "Private", "requires": ["sysop"]},
    ])
    pleb = _user("pleb")

    async def _a():
        from core.ops import registry

        # Boot migration guarantees defined boards exist as conversations;
        # replicate that guarantee (handlers are strict on purpose).
        await app.conversations.create_conversation(kind="board", title="Public",
                                                    created_by="system", conv_id="public")
        # anonymous cannot post
        with pytest.raises(PermissionDeniedError):
            await registry.call(app, None, "boards.post",
                                {"board": "public", "subject": "x", "body": "y"})
        # group gate enforced from unified conversation requires
        conv = await app.conversations.get_conversation("private")
        assert conv is None  # not created yet; create to prove gate uses conv requires
        await app.conversations.create_conversation(kind="board", title="Private",
                                                    created_by="system", conv_id="private",
                                                    requires=["sysop"])
        with pytest.raises(PermissionDeniedError):
            await registry.call(app, pleb, "boards.post",
                                {"board": "private", "subject": "x", "body": "y"})
        # unknown board → validation error
        with pytest.raises(ValidationError):
            await registry.call(app, pleb, "boards.post",
                                {"board": "nope", "subject": "x", "body": "y"})
        # event still emitted on success (bus is async: give its task a tick)
        seen = []
        app.events.on("messageboard:post", lambda data: seen.append(data))
        await registry.call(app, pleb, "boards.post",
                            {"board": "public", "subject": "s", "body": "b"})
        for _ in range(3):
            if seen:
                break
            await asyncio.sleep(0)
        assert len(seen) == 1

    _run(_a())


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
