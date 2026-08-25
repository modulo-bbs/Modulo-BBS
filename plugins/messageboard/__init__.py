"""Message board plugin: board definitions + boot sync into the unified
conversations engine.

The classic [M] interactive flow was retired when boards became a tab of the
PIM (and later the Social surface): two stores drifting apart was the root
cause of stranded posts (live incident 2026-08-25). The plugin now owns only
``boards.json`` — the sysop-facing definition file (ids, display names,
group gates). On load it schedules the idempotent migration so every defined
board exists as a ``kind=board`` conversation; all reads/writes flow through
``core/conversations.py`` via the ``boards.*`` API ops or the PIM surface.
"""
from __future__ import annotations

import asyncio

from plugins.base import Plugin

from .boards import load_boards


class MessageBoardPlugin(Plugin):
    name = "messageboard"
    version = "2.0.0"
    description = "Board definitions synced into the unified conversations store"
    menu_label = "Message Boards"
    menu_key = ""                       # retired from the classic menu (A3)
    menu_order = 10

    def __init__(self):
        self.bbs = None
        self.boards: list[dict] = []

    def on_load(self, bbs):
        self.bbs = bbs
        d = bbs.storage.dir("messageboard")
        self.boards = load_boards(d)
        # Opportunistic boot sync (idempotent, marker-gated message copy):
        # guarantees every defined board exists as a kind=board conversation
        # before any boards.* op or PIM render needs it. run_server.py also
        # calls migrate_legacy() explicitly; this covers manual reloads.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(bbs.conversations.migrate_legacy())
        except RuntimeError:
            pass

    def visible_boards(self, user) -> list[dict]:
        return [b for b in self.boards if user.can_access(b.get("requires", []))]


__all__ = ["MessageBoardPlugin"]
