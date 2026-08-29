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


def test_esc_only_title_is_rejected(tmp_path):
    """N then ESC stored a board titled ESC, which broke its sidebar row."""
    app = _app(tmp_path)

    async def _a():
        with pytest.raises(ValueError, match="title is required"):
            await app.conversations.create_conversation(
                kind="board", title="\x1b", created_by="dave")

    _run(_a())


def test_control_characters_are_stripped_from_titles(tmp_path):
    app = _app(tmp_path)

    async def _a():
        conv = await app.conversations.create_conversation(
            kind="board", title="Gen\x1beral\tDiscussion", created_by="dave")
        assert "\x1b" not in conv["title"]
        assert "\t" not in conv["title"]
        return conv["title"]

    assert _run(_a()) == "General Discussion"


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


def test_author_deletes_own_board_before_replies(tmp_path):
    app = _app(tmp_path)
    dave = _user("dave")
    ana = _user("ana")

    async def _a():
        empty = await app.conversations.create_conversation(
            kind="board", title="Empty", created_by="dave", conv_id="empty")
        assert await app.conversations.can_delete_conversation("empty", dave)
        assert await app.conversations.delete_conversation("empty", by_user=dave)
        assert await app.conversations.get_conversation("empty") is None
        assert not app.conversations._conv_dir("empty").is_dir()

        own = await app.conversations.create_conversation(
            kind="board", title="Solo", created_by="dave", conv_id="solo")
        await app.conversations.post_message("solo", author="dave", body="just me")
        assert await app.conversations.can_delete_conversation("solo", dave)
        assert await app.conversations.delete_conversation("solo", by_user=dave)

        replied = await app.conversations.create_conversation(
            kind="board", title="Busy", created_by="dave", conv_id="busy")
        await app.conversations.post_message("busy", author="dave", body="op")
        await app.conversations.post_message("busy", author="ana", body="reply")
        assert not await app.conversations.can_delete_conversation("busy", dave)
        with pytest.raises(PermissionError):
            await app.conversations.delete_conversation("busy", by_user=dave)
        assert await app.conversations.get_conversation("busy") is not None

        with pytest.raises(PermissionError):
            await app.conversations.delete_conversation("busy", by_user=ana)

        sysop = _user("root", ["sysop"])
        assert await app.conversations.can_delete_conversation("busy", sysop)
        await app.conversations.mark_read("dave", "busy")
        assert await app.conversations.delete_conversation("busy", by_user=sysop)
        assert await app.conversations.get_conversation("busy") is None
        reads = await asyncio.to_thread(app.conversations._read_reads_sync)
        assert "busy" not in reads.get("dave", {})

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


def test_migrate_legacy_boards(tmp_path):
    """Idempotent import of old messageboard files into conversations."""
    app = _app(tmp_path)
    # Simulate old messageboard data in the same isolated plugins_dir
    mb_root = app.storage.dir("messageboard")
    import json

    boards = [{"id": "general", "name": "General Discussion", "requires": []}]
    (mb_root / "boards.json").write_text(json.dumps(boards), encoding="utf-8")
    # two legacy messages
    gdir = mb_root / "general"
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "1.json").write_text(json.dumps({"id": 1, "author": "dave", "subject": "hi", "body": "hello", "timestamp": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    (gdir / "2.json").write_text(json.dumps({"id": 2, "author": "ana", "subject": "re", "body": "world", "timestamp": "2026-01-01T01:00:00+00:00"}), encoding="utf-8")

    async def _a():
        counts = await app.conversations.migrate_legacy()
        assert counts["boards"] == 1
        lst = await app.conversations.list_conversations(kind="board")
        assert any(c["id"] == "general" for c in lst)
        msgs = await app.conversations.list_messages("general")
        assert len(msgs) == 2
        assert msgs[0]["body"] == "hello"
        # second call is idempotent
        counts2 = await app.conversations.migrate_legacy()
        assert counts2["boards"] == 0
        msgs2 = await app.conversations.list_messages("general")
        assert len(msgs2) == 2

    _run(_a())


def test_migrate_copies_messages_when_conv_preexists(tmp_path):
    """Live incident 2026-08-25: conv 'general' already existed in the index
    while messages lived only in the legacy store — migration skipped the
    board entirely (exists ⇒ migrated was false). Message copy must be gated
    by a marker file, not by conv existence."""
    app = _app(tmp_path)
    import json

    mb_root = app.storage.dir("messageboard")
    (mb_root / "boards.json").write_text(
        json.dumps([{"id": "general", "name": "General"}]), encoding="utf-8"
    )
    gdir = mb_root / "general"
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "1.json").write_text(
        json.dumps({"id": 1, "author": "dave", "subject": "Test", "body": "hello",
                    "timestamp": "2026-08-22T20:27:21-04:00"}),
        encoding="utf-8",
    )

    async def _a():
        # Conversation PRE-exists before migration runs.
        await app.conversations.create_conversation(
            kind="board", title="General", created_by="system", conv_id="general"
        )
        counts = await app.conversations.migrate_legacy()
        assert counts["boards"] == 0  # not re-created...
        msgs = await app.conversations.list_messages("general")
        assert len(msgs) == 1         # ...but its messages ARE copied
        assert msgs[0]["body"] == "hello"
        # marker prevents double-copy on rerun
        counts2 = await app.conversations.migrate_legacy()
        assert counts2["boards"] == 0
        assert len(await app.conversations.list_messages("general")) == 1

    _run(_a())

