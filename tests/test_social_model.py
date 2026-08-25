"""B2 — Social sidebar model (boards-unification §B2).

``social_rooms()`` produces the room list backing the two-pane Social
surface: pinned DMs aggregate on top, then ``kind=board`` conversations
sorted by recent activity (last_message_at, falling back to created),
each carrying its unread count for the viewing user.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.app import BBSApp
from core.conversations import Conversations
from core.user import User
from plugins.mainmenu.social import Room, social_rooms


def _convs(tmp_path: Path) -> Conversations:
    app = BBSApp(users_dir=tmp_path / "users")
    app.storage.plugins_dir = tmp_path / "plugins"
    return Conversations(app)


def _user(name: str = "dave", *groups: str) -> User:
    return User(username=name, groups=list(groups))


def test_dms_pinned_first_with_total_unread(tmp_path):
    async def _a():
        convs = _convs(tmp_path)
        dave = _user("dave")
        dm1 = await convs.create_conversation(
            kind="dm", title="Hey Dave", created_by="api_test",
            participants=["api_test", "dave"], conv_id="dm-1",
        )
        await convs.post_message("dm-1", author="api_test", body="one")
        await convs.create_conversation(
            kind="board", title="General Discussion", created_by="system",
            conv_id="general-discussion",
        )
        rooms = await social_rooms(convs, dave)
        assert rooms[0].id == "dms"
        assert rooms[0].kind == "dms"
        assert rooms[0].unread == 1

    asyncio.run(_a())


def test_boards_sorted_by_recent_activity_not_creation(tmp_path):
    async def _a():
        convs = _convs(tmp_path)
        dave = _user("dave")
        await convs.create_conversation(
            kind="board", title="Trading Post", created_by="system", conv_id="trading",
        )
        await convs.create_conversation(
            kind="board", title="General Discussion", created_by="system",
            conv_id="general-discussion",
        )
        # Activity inverts creation order: trading gets the newest post.
        await convs.post_message("general-discussion", author="dave", body="old news")
        await convs.post_message("trading", author="dave", body="fresh")
        rooms = await social_rooms(convs, dave)
        board_ids = [r.id for r in rooms if r.kind == "board"]
        assert board_ids == ["trading", "general-discussion"]

    asyncio.run(_a())


def test_unread_flags_respect_last_read(tmp_path):
    async def _a():
        convs = _convs(tmp_path)
        dave = _user("dave")
        await convs.create_conversation(
            kind="board", title="General Discussion", created_by="system",
            conv_id="general-discussion",
        )
        await convs.post_message("general-discussion", author="ana", body="hi")
        await convs.post_message("general-discussion", author="bob", body="yo")
        await convs.set_last_read("dave", "general-discussion", 1)
        rooms = await social_rooms(convs, dave)
        room = next(r for r in rooms if r.id == "general-discussion")
        assert room.unread == 1
        await convs.mark_read("dave", "general-discussion")
        rooms = await social_rooms(convs, dave)
        room = next(r for r in rooms if r.id == "general-discussion")
        assert room.unread == 0

    asyncio.run(_a())


def test_titles_capped_at_15_chars(tmp_path):
    async def _a():
        convs = _convs(tmp_path)
        dave = _user("dave")
        await convs.create_conversation(
            kind="board", title="A Very Long Board Title Indeed",
            created_by="system", conv_id="long-title",
        )
        rooms = await social_rooms(convs, dave)
        room = next(r for r in rooms if r.id == "long-title")
        assert len(room.title) <= 15

    asyncio.run(_a())


def test_anonymous_gets_no_dms_row_and_no_gated_boards(tmp_path):
    async def _a():
        convs = _convs(tmp_path)
        await convs.create_conversation(
            kind="board", title="Public", created_by="system", conv_id="public",
        )
        await convs.create_conversation(
            kind="board", title="Sysop Only", created_by="system",
            requires=["sysop"], conv_id="sysop-only",
        )
        rooms = await social_rooms(convs, None)
        assert all(r.kind != "dms" for r in rooms)
        assert [r.id for r in rooms] == ["public"]

    asyncio.run(_a())


def test_gated_board_hidden_from_non_member(tmp_path):
    async def _a():
        convs = _convs(tmp_path)
        pleb = _user("pleb")
        mod = _user("mod", "sysop")
        await convs.create_conversation(
            kind="board", title="Public", created_by="system", conv_id="public",
        )
        await convs.create_conversation(
            kind="board", title="Sysop Only", created_by="system",
            requires=["sysop"], conv_id="sysop-only",
        )
        ids = [r.id for r in await social_rooms(convs, pleb)]
        assert ids == ["dms", "public"]
        ids = [r.id for r in await social_rooms(convs, mod)]
        assert "sysop-only" in ids

    asyncio.run(_a())


def test_empty_store_yields_only_pinned_for_authed_user(tmp_path):
    async def _a():
        convs = _convs(tmp_path)
        rooms = await social_rooms(convs, _user("dave"))
        assert len(rooms) == 1
        assert rooms[0].id == "dms"
        assert rooms[0].unread == 0

    asyncio.run(_a())
