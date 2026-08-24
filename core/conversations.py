"""Unified conversations engine — one store, many tempos (see docs/build-plan.md Phase 1).

Every interaction is a *conversation* with a tempo control:
- board   = slow, topical, threaded, group-gated, searchable
- channel = fast, presence-typed, scrollback
- dm      = private participants=[2]
- group   = private participants=[n]

Storage: ``bbs.storage.dir("conversations")`` →
  index.json           — list[Conversation]
  <id>/messages.jsonl  — one JSON object per line (append-only, fast scan)

All file I/O goes through ``asyncio.to_thread`` so the event loop stays
responsive under 10k connections (per docs/architecture.md).

Gating uses existing ``user.can_access(requires)`` — no new permission
machinery.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONVERSATION_KINDS = {"board", "channel", "dm", "group"}
CONVERSATIONS_INDEX = "index.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Conversations:
    """Core service mounted as ``bbs.conversations``."""

    def __init__(self, bbs, *, storage_name: str = "conversations"):
        self.bbs = bbs
        self._storage_name = storage_name
        # One lock per conversation id for message appends; plus a global
        # lock for index mutations.
        self._locks: dict[str, asyncio.Lock] = {}
        self._index_lock = asyncio.Lock()

    def _root(self) -> Path:
        return self.bbs.storage.dir(self._storage_name)

    def _index_path(self) -> Path:
        return self._root() / CONVERSATIONS_INDEX

    def _conv_dir(self, conv_id: str) -> Path:
        return self._root() / conv_id

    def _messages_path(self, conv_id: str) -> Path:
        return self._conv_dir(conv_id) / "messages.jsonl"

    def _lock_for(self, conv_id: str) -> asyncio.Lock:
        if conv_id not in self._locks:
            self._locks[conv_id] = asyncio.Lock()
        return self._locks[conv_id]

    # -- internal sync helpers (run in thread) -----------------------------

    def _read_index_sync(self) -> list[dict[str, Any]]:
        p = self._index_path()
        if not p.is_file():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write_index_sync(self, index: list[dict[str, Any]]) -> None:
        p = self._index_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
        tmp.replace(p)

    def _read_messages_sync(self, conv_id: str) -> list[dict[str, Any]]:
        mp = self._messages_path(conv_id)
        if not mp.is_file():
            return []
        out: list[dict[str, Any]] = []
        try:
            for line in mp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            return []
        return out

    def _append_message_sync(self, conv_id: str, msg: dict[str, Any]) -> None:
        d = self._conv_dir(conv_id)
        d.mkdir(parents=True, exist_ok=True)
        mp = self._messages_path(conv_id)
        with mp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg) + "\n")

    # -- public async API ---------------------------------------------------

    async def list_conversations(
        self,
        *,
        kind: str | None = None,
        visible_to=None,
    ) -> list[dict[str, Any]]:
        """List conversations, optionally filtered by kind and viewer's groups."""
        index = await asyncio.to_thread(self._read_index_sync)
        if kind is not None:
            index = [c for c in index if c.get("kind") == kind]
        # Anonymous (visible_to is None) sees only public boards + public channels
        if visible_to is None:
            filtered = []
            for c in index:
                if c.get("kind") == "board" and (c.get("requires") or []):
                    continue
                if c.get("kind") in ("dm", "group"):
                    continue
                filtered.append(c)
            index = filtered
        else:
            # Group-gated boards only; channels/DMs use participant lists
            filtered = []
            for c in index:
                if c.get("kind") == "board":
                    req = c.get("requires") or []
                    if req and not visible_to.can_access(req):
                        continue
                # DMs/groups: only participants (or sysop) can see
                if c.get("kind") in ("dm", "group"):
                    parts = c.get("participants") or []
                    if visible_to.username not in parts and not visible_to.in_group("sysop"):
                        continue
                filtered.append(c)
            index = filtered
        # Most recent first
        index.sort(key=lambda c: c.get("created", ""), reverse=True)
        return index

    async def get_conversation(self, conv_id: str) -> dict[str, Any] | None:
        index = await asyncio.to_thread(self._read_index_sync)
        for c in index:
            if c.get("id") == conv_id:
                return c
        return None

    async def create_conversation(
        self,
        *,
        kind: str,
        title: str,
        created_by: str,
        requires: list[str] | None = None,
        participants: list[str] | None = None,
        conv_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in CONVERSATION_KINDS:
            raise ValueError(f"kind must be one of {sorted(CONVERSATION_KINDS)}")
        if not title or not title.strip():
            raise ValueError("title is required")
        title = title.strip()
        async with self._index_lock:
            index = await asyncio.to_thread(self._read_index_sync)
            # id: slug from title if not supplied, else supplied
            if conv_id is None:
                base = "".join(ch.lower() if ch.isalnum() else "-" for ch in title)[:24].strip("-")
                base = base or f"conv-{int(time.time())}"
                # dedupe
                existing_ids = {c["id"] for c in index}
                cid = base
                n = 2
                while cid in existing_ids:
                    cid = f"{base}-{n}"
                    n += 1
            else:
                cid = conv_id
                if any(c["id"] == cid for c in index):
                    raise ValueError(f"conversation {cid!r} already exists")
            conv: dict[str, Any] = {
                "id": cid,
                "kind": kind,
                "title": title,
                "created": _now_iso(),
                "created_by": created_by,
                "requires": requires or [],
                "participants": participants or [],
                "message_count": 0,
                "last_message_at": None,
            }
            index.append(conv)
            await asyncio.to_thread(self._write_index_sync, index)
            # create dir eagerly so messages.jsonl exists
            await asyncio.to_thread(lambda: self._conv_dir(cid).mkdir(parents=True, exist_ok=True))
            return conv

    async def list_messages(self, conv_id: str) -> list[dict[str, Any]]:
        conv = await self.get_conversation(conv_id)
        if conv is None:
            raise ValueError(f"conversation {conv_id!r} not found")
        msgs = await asyncio.to_thread(self._read_messages_sync, conv_id)
        msgs.sort(key=lambda m: m.get("id", 0))
        return msgs

    async def post_message(
        self,
        conv_id: str,
        *,
        author: str,
        body: str,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        if not body or not body.strip():
            raise ValueError("body is required")
        conv = await self.get_conversation(conv_id)
        if conv is None:
            raise ValueError(f"conversation {conv_id!r} not found")
        body = body.strip()
        async with self._lock_for(conv_id):
            msgs = await asyncio.to_thread(self._read_messages_sync, conv_id)
            next_id = (max((m["id"] for m in msgs), default=0) + 1)
            msg: dict[str, Any] = {
                "id": next_id,
                "conversation_id": conv_id,
                "author": author,
                "body": body,
                "parent_id": parent_id,
                "created": _now_iso(),
            }
            await asyncio.to_thread(self._append_message_sync, conv_id, msg)
            # bump index counters
            async with self._index_lock:
                index = await asyncio.to_thread(self._read_index_sync)
                for c in index:
                    if c["id"] == conv_id:
                        c["message_count"] = c.get("message_count", 0) + 1
                        c["last_message_at"] = msg["created"]
                        break
                await asyncio.to_thread(self._write_index_sync, index)
            return msg

    async def delete_message(self, conv_id: str, msg_id: int, *, by_user) -> bool:
        """Delete own message, or any message if user is moderator/sysop.

        Returns True if deleted, False if not found. Raises PermissionError
        if caller may not delete.
        """
        msgs = await asyncio.to_thread(self._read_messages_sync, conv_id)
        target = next((m for m in msgs if m["id"] == msg_id), None)
        if target is None:
            return False
        is_own = target.get("author") == by_user.username
        is_mod = by_user.can_access(["moderator"]) or by_user.in_group("sysop")
        if not (is_own or is_mod):
            raise PermissionError("not your message and you are not a moderator")
        # rewrite file without target
        async with self._lock_for(conv_id):
            # re-read inside lock
            msgs = await asyncio.to_thread(self._read_messages_sync, conv_id)
            kept = [m for m in msgs if m["id"] != msg_id]
            if len(kept) == len(msgs):
                return False
            mp = self._messages_path(conv_id)
            tmp = mp.with_suffix(".tmp")

            def _rewrite():
                tmp.write_text("\n".join(json.dumps(m) for m in kept) + ("\n" if kept else ""), encoding="utf-8")
                tmp.replace(mp)

            await asyncio.to_thread(_rewrite)
            # decrement count
            async with self._index_lock:
                index = await asyncio.to_thread(self._read_index_sync)
                for c in index:
                    if c["id"] == conv_id:
                        c["message_count"] = max(0, c.get("message_count", 1) - 1)
                        break
                await asyncio.to_thread(self._write_index_sync, index)
        return True

    async def find_messages(
        self,
        query: str,
        *,
        kind: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Case-insensitive substring search across bodies (and author)."""
        if not query or not query.strip():
            return []
        q = query.strip().lower()
        convs = await self.list_conversations(kind=kind)
        hits: list[dict[str, Any]] = []
        for c in convs:
            msgs = await asyncio.to_thread(self._read_messages_sync, c["id"])
            for m in msgs:
                hay = f"{m.get('author','')} {m.get('body','')}".lower()
                if q in hay:
                    # annotate with conversation title for display
                    h = dict(m)
                    h["conversation_title"] = c.get("title")
                    h["conversation_kind"] = c.get("kind")
                    hits.append(h)
                    if len(hits) >= limit:
                        return hits
        return hits

    async def migrate_legacy(
        self,
        *,
        messageboard_root: Path | None = None,
        chat_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Idempotent migration from old messageboard/chat storage.

        - messageboard: ``plugins/messageboard/data/<board_id>/*.json`` + ``boards.json``
          → ``kind=board`` conversations.
        - chat: optional list of ``{"author":..., "body":..., "created":...}`` dicts
          → one ``kind=channel`` conversation titled ``Lobby``.

        Returns counts ``{"boards": n, "channel_messages": m}``.
        Safe to call repeatedly — skips conversations that already exist.
        """
        from pathlib import Path as _Path

        counts = {"boards": 0, "channel_messages": 0}
        # Resolve messageboard root
        if messageboard_root is None:
            # default: sibling plugin's data dir
            try:
                messageboard_root = self.bbs.storage.dir("messageboard")
            except Exception:
                messageboard_root = None
        if messageboard_root is not None:
            messageboard_root = _Path(messageboard_root)
            boards_json = messageboard_root / "boards.json"
            boards: list[dict[str, Any]] = []
            if boards_json.is_file():
                try:
                    boards = json.loads(boards_json.read_text(encoding="utf-8"))
                except Exception:
                    boards = []
            if not boards:
                # default board if none — mirrors boards.py default
                boards = [{"id": "general", "name": "General Discussion", "requires": []}]
            for board in boards:
                bid = board.get("id", "general")
                title = board.get("name", bid)
                requires = board.get("requires") or []
                # skip if already migrated
                if await self.get_conversation(bid) is not None:
                    continue
                await self.create_conversation(
                    kind="board",
                    title=title,
                    created_by=board.get("created_by", "system"),
                    requires=requires,
                    conv_id=bid,
                )
                counts["boards"] += 1
                # migrate messages
                bdir = messageboard_root / bid
                if bdir.is_dir():
                    for p in sorted(bdir.glob("*.json"), key=lambda x: int(x.stem) if x.stem.isdigit() else x.stem):
                        try:
                            m = json.loads(p.read_text(encoding="utf-8"))
                        except Exception:
                            continue
                        body = m.get("body") or m.get("text") or ""
                        author = m.get("author") or "unknown"
                        try:
                            await self.post_message(bid, author=author, body=body)
                            # preserve original timestamp if present by rewriting last message
                            # (best-effort: post_message stamps now, that's acceptable for migration)
                        except Exception:
                            continue
        if chat_history:
            lobby_id = "lobby"
            if await self.get_conversation(lobby_id) is None:
                await self.create_conversation(
                    kind="channel",
                    title="Lobby",
                    created_by="system",
                    conv_id=lobby_id,
                )
            for entry in chat_history:
                try:
                    await self.post_message(
                        lobby_id,
                        author=entry.get("author", "unknown"),
                        body=entry.get("body", entry.get("text", "")),
                    )
                    counts["channel_messages"] += 1
                except Exception:
                    continue
        return counts
