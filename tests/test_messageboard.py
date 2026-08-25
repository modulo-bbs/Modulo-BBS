"""Tests for the messageboard plugin (definition-holder + boot sync, A3).

The interactive [M] flow is retired — the plugin owns boards.json definitions
and syncs them into the unified conversations store. Storage/permission
primitives (BoardStore, can_delete) remain tested for their legacy-data role.
"""
import json

import pytest

from core.events import EventBus
from core.user import User
from plugins.messageboard import MessageBoardPlugin
from plugins.messageboard.boards import BoardStore, can_delete, load_boards


@pytest.fixture
def store(tmp_path):
    return BoardStore(tmp_path / "messageboard")


class FakeStorage:
    def __init__(self, root):
        self.root = root

    def dir(self, name):
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d


class FakeBBS:
    def __init__(self, root):
        self.storage = FakeStorage(root)
        self.events = EventBus()
        self.conversations = None  # set by tests that exercise boot sync


def _plugin(tmp_path):
    bbs = FakeBBS(tmp_path / "data")
    plugin = MessageBoardPlugin()
    plugin.on_load(bbs)
    return bbs, plugin


# -- storage (legacy data still readable) --------------------------------------

def test_add_list_delete_roundtrip(store):
    m1 = store.add_message("general", "dave", "Hello", "first post")
    m2 = store.add_message("general", "alice", "Re: Hello", "hi back")
    assert m1["id"] == 1 and m2["id"] == 2
    msgs = store.list_messages("general")
    assert [m["id"] for m in msgs] == [1, 2]
    assert store.delete_message("general", 1) is True
    assert [m["id"] for m in store.list_messages("general")] == [2]


def test_delete_missing_message_returns_false(store):
    assert store.delete_message("general", 99) is False


# -- boards config ---------------------------------------------------------------

def test_load_boards_creates_default(tmp_path):
    boards = load_boards(tmp_path)
    assert boards[0]["id"] == "general"
    again = load_boards(tmp_path)
    assert again == boards


def test_visible_boards_respects_requires(tmp_path):
    bbs, plugin = _plugin(tmp_path)
    plugin.boards = [
        {"id": "general", "name": "General", "requires": []},
        {"id": "vip", "name": "VIP", "requires": ["veterans"]},
    ]
    plain = User(username="joe", display_name="", password_hash="x")
    vet = User(username="v", display_name="", password_hash="x",
               groups=["user", "veterans"])
    assert [b["id"] for b in plugin.visible_boards(plain)] == ["general"]
    assert {b["id"] for b in plugin.visible_boards(vet)} == {"general", "vip"}


# -- A3: definition-holder contract ----------------------------------------------

def test_menu_hotkey_retired(tmp_path):
    """The [M] flow is gone: no menu_key, no interactive session hook."""
    from plugins.base import Plugin

    bbs, plugin = _plugin(tmp_path)
    assert plugin.name == "messageboard"
    assert plugin.menu_key == ""
    assert getattr(plugin.__class__, "on_session_start", None) is Plugin.on_session_start


def test_boot_sync_creates_conversations(tmp_path):
    """on_load, called on a RUNNING loop (plugin reload), schedules migrate_legacy."""
    import asyncio

    from core.app import BBSApp
    from core.conversations import Conversations

    app = BBSApp(users_dir=tmp_path / "users")
    app.storage.plugins_dir = tmp_path / "plugins"
    app.conversations = Conversations(app)

    mb_root = app.storage.dir("messageboard")
    (mb_root / "boards.json").write_text(
        json.dumps([{"id": "general", "name": "General Discussion", "requires": []}]),
        encoding="utf-8",
    )

    async def _a():
        # Loop running → on_load schedules the sync task (reload scenario).
        # Cold-boot coverage lives in run_server.py's explicit migrate call,
        # exercised by tests/test_conversations.py::test_migrate_legacy_boards.
        plugin = MessageBoardPlugin()
        plugin.on_load(app)
        await asyncio.sleep(0.05)
        conv = await app.conversations.get_conversation("general")
        assert conv is not None
        assert conv["kind"] == "board"
        assert conv["title"] == "General Discussion"

    asyncio.run(_a())


# -- delete permission -----------------------------------------------------------

def test_can_delete_own_message():
    u = User(username="joe", display_name="", password_hash="x")
    msg = {"author": "joe"}
    assert can_delete(u, msg) is True


def test_cannot_delete_others_without_mod():
    u = User(username="mallory", display_name="", password_hash="x")
    msg = {"author": "joe"}
    assert can_delete(u, msg) is False


def test_moderator_group_can_delete_any():
    mod = User(username="m", display_name="", password_hash="x",
               groups=["user", "moderator"])
    msg = {"author": "joe"}
    assert can_delete(mod, msg) is True
