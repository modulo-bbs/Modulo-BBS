"""Tests for core/conversations.py — unified engine (Phase 1, build-plan § Steps 2-3)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.app import BBSApp
from core.user import User


def _app(tmp_path: Path) -> BBSApp:
    app = BBSApp(users_dir=tmp_path / "users")
    # Isolate conversations storage to the test tmp — otherwise index
    # leaks between tests via the shared plugins/conversations/data dir.
    app.storage.plugins_dir = tmp_path / "plugins"
    from core.conversations import Conversations

    app.conversations = Conversations(app)
    return app


def _run(coro):
    return asyncio.run(coro)


def _user(username: str, groups=None) -> User:
    return User(username=username, groups=groups or [])


# -- schema / storage (Step 2) ---------------------------------------------


def test_create_board_persists(tmp_path):
    app = _app(tmp_path)

    async def _a():
        conv = await app.conversations.create_conversation(
            kind="board", title="General Discussion", created_by="dave"
        )
        assert conv["id"] == "general-discussion"
        assert conv["kind"] == "board"
        assert conv["message_count"] == 0
        # list survives round-trip
        lst = await app.conversations.list_conversations()
        assert any(c["id"] == "general-discussion" for c in lst)

    _run(_a())


def test_create_requires_title(tmp_path):
    app = _app(tmp_path)

    async def _a():
        with pytest.raises(ValueError, match="title is required"):
            await app.conversations.create_conversation(kind="board", title="  ", created_by="dave")

    _run(_a())


def test_invalid_kind_rejected(tmp_path):
    app = _app(tmp_path)

    async def _a():
        with pytest.raises(ValueError, match="kind must be one of"):
            await app.conversations.create_conversation(kind="nonsense", title="hi", created_by="dave")

    _run(_a())


def test_duplicate_id_rejected(tmp_path):
    app = _app(tmp_path)

    async def _a():
        await app.conversations.create_conversation(kind="board", title="General", created_by="dave", conv_id="general")
        with pytest.raises(ValueError, match="already exists"):
            await app.conversations.create_conversation(kind="board", title="General 2", created_by="dave", conv_id="general")

    _run(_a())


# -- CRUD + threading + find (Step 3) --------------------------------------


def test_post_and_list_threaded(tmp_path):
    app = _app(tmp_path)

    async def _a():
        conv = await app.conversations.create_conversation(kind="board", title="General", created_by="dave", conv_id="general")
        m1 = await app.conversations.post_message("general", author="dave", body="hello")
        m2 = await app.conversations.post_message("general", author="ana", body="hi dave", parent_id=m1["id"])
        msgs = await app.conversations.list_messages("general")
        assert [m["id"] for m in msgs] == [1, 2]
        assert msgs[1]["parent_id"] == 1
        # index counters bumped
        c = await app.conversations.get_conversation("general")
        assert c is not None
        assert c["message_count"] == 2
        assert c["last_message_at"] is not None

    _run(_a())


def test_post_requires_body(tmp_path):
    app = _app(tmp_path)

    async def _a():
        await app.conversations.create_conversation(kind="board", title="General", created_by="dave", conv_id="general")
        with pytest.raises(ValueError, match="body is required"):
            await app.conversations.post_message("general", author="dave", body="  ")

    _run(_a())


def test_delete_own_vs_moderator(tmp_path):
    app = _app(tmp_path)
    dave = _user("dave")
    ana = _user("ana")
    mod = _user("mod", ["moderator"])

    async def _a():
        await app.conversations.create_conversation(kind="board", title="General", created_by="dave", conv_id="general")
        m = await app.conversations.post_message("general", author="dave", body="hello")
        # ana cannot delete dave's message
        with pytest.raises(PermissionError):
            await app.conversations.delete_message("general", m["id"], by_user=ana)
        # dave can delete own
        assert await app.conversations.delete_message("general", m["id"], by_user=dave) is True
        # second delete → not found
        assert await app.conversations.delete_message("general", m["id"], by_user=dave) is False
        # mod can delete any
        m2 = await app.conversations.post_message("general", author="dave", body="again")
        assert await app.conversations.delete_message("general", m2["id"], by_user=mod) is True

    _run(_a())


def test_find_across_conversations(tmp_path):
    app = _app(tmp_path)

    async def _a():
        await app.conversations.create_conversation(kind="board", title="General", created_by="dave", conv_id="general")
        await app.conversations.create_conversation(kind="channel", title="Lobby", created_by="dave", conv_id="lobby")
        await app.conversations.post_message("general", author="dave", body="hello world")
        await app.conversations.post_message("lobby", author="ana", body="HELLO from lobby")
        hits = await app.conversations.find_messages("hello")
        assert len(hits) == 2
        # case-insensitive, author also searchable
        hits2 = await app.conversations.find_messages("ANA")
        assert any(h["author"] == "ana" for h in hits2)
        # kind filter
        hits3 = await app.conversations.find_messages("hello", kind="board")
        assert all(h["conversation_kind"] == "board" for h in hits3)
        # empty query → []
        assert await app.conversations.find_messages("  ") == []

    _run(_a())


def test_list_conversations_filters_by_kind_and_visibility(tmp_path):
    app = _app(tmp_path)
    dave = _user("dave", ["sysop"])
    pleb = _user("pleb", [])

    async def _a():
        await app.conversations.create_conversation(kind="board", title="Public", created_by="dave", conv_id="public", requires=[])
        await app.conversations.create_conversation(kind="board", title="Private", created_by="dave", conv_id="private", requires=["sysop"])
        await app.conversations.create_conversation(kind="channel", title="Lobby", created_by="dave", conv_id="lobby")
        # pleb sees only public board + channel
        vis = await app.conversations.list_conversations(visible_to=pleb)
        assert {c["id"] for c in vis} == {"public", "lobby"}
        # dave (sysop) sees all
        vis2 = await app.conversations.list_conversations(visible_to=dave)
        assert {c["id"] for c in vis2} == {"public", "private", "lobby"}
        # kind filter
        boards = await app.conversations.list_conversations(kind="board", visible_to=dave)
        assert all(c["kind"] == "board" for c in boards)

    _run(_a())


def test_dm_visibility_only_participants_and_sysop(tmp_path):
    app = _app(tmp_path)
    dave = _user("dave", [])
    ana = _user("ana", [])
    bob = _user("bob", [])
    sysop = _user("sysop", ["sysop"])

    async def _a():
        await app.conversations.create_conversation(kind="dm", title="dave-ana", created_by="dave", conv_id="dm1", participants=["dave", "ana"])
        # ana sees it
        assert any(c["id"] == "dm1" for c in await app.conversations.list_conversations(visible_to=ana))
        # bob does not
        assert not any(c["id"] == "dm1" for c in await app.conversations.list_conversations(visible_to=bob))
        # sysop sees all DMs
        assert any(c["id"] == "dm1" for c in await app.conversations.list_conversations(visible_to=sysop))

    _run(_a())
