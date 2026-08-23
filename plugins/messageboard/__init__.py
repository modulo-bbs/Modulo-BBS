"""Message board plugin: multiple sub-boards with group-gated access."""
from __future__ import annotations

import asyncio

from plugins.base import Plugin

from .boards import BoardStore, can_delete, load_boards

DEFAULT_KEYS = {"LIST": "L", "POST": "P", "REPLY": "R", "DELETE": "D", "QUIT": "Q"}


class MessageBoardPlugin(Plugin):
    name = "messageboard"
    version = "1.0.0"
    description = "Message boards with group-gated areas"
    menu_label = "[M] Message Boards"
    menu_key = "M"
    menu_order = 10

    def __init__(self):
        self.bbs = None
        self.store = None
        self.boards: list[dict] = []
        self.keys = dict(DEFAULT_KEYS)

    def on_load(self, bbs):
        self.bbs = bbs
        d = bbs.storage.dir("messageboard")
        self.store = BoardStore(d)
        self.boards = load_boards(d)
        self.keys = bbs.keys_for("messageboard", DEFAULT_KEYS)

    def visible_boards(self, user) -> list[dict]:
        return [b for b in self.boards if user.can_access(b.get("requires", []))]

    # -- entry -----------------------------------------------------------------

    async def on_session_start(self, session) -> bool:
        while getattr(session, "is_active", True):
            vis = self.visible_boards(session.user)
            lines = ["", " Message Boards", " ==============="]
            if not vis:
                lines.append(" (no boards available)")
            for i, b in enumerate(vis, 1):
                n = self.store.count(b["id"])
                lines.append(f" {i}. {b['name']} ({n} msgs)")
            lines.append("")
            await self.bbs.send(session, "\r\n".join(lines) + "\r\n")
            pick = await self._get_key(session)
            if pick is None:
                return False
            if pick == self._k("QUIT"):
                return False
            if pick.isdigit() and 1 <= int(pick) <= len(vis):
                await self._board_menu(session, vis[int(pick) - 1])
            else:
                await self.bbs.send(session, "\r\nInvalid selection.\r\n")
        return False

    async def _board_menu(self, session, board: dict):
        while getattr(session, "is_active", True):
            msgs = self.store.list_messages(board["id"])
            lines = ["", f" {board['name']} -- {len(msgs)} messages",
                     " " + "=" * (len(board['name']) + 12)]
            for i, m in enumerate(msgs, 1):
                lines.append(f" {i}. [{m['author']}] {m['subject']}")
            lines.append("")
            await self.bbs.send(session, "\r\n".join(lines) + "\r\n")
            cmd = await self._get_key(session)
            if cmd is None:
                return
            u = cmd.upper()
            if not u or u == self._k("QUIT"):
                return
            if u == self._k("LIST"):
                continue
            if u == self._k("POST"):
                await self._post(session, board)
            elif u == self._k("REPLY"):
                num = await self._ask(session, "Reply to #: ")
                await self._post(session, board, reply_to=num)
            elif u == self._k("DELETE"):
                await self._delete(session, board)
            else:
                # numeric -> read that message
                if cmd.isdigit():
                    await self._read(session, board, int(cmd))

    async def _read(self, session, board, msg_id: int):
        m = self.store.get_message(board["id"], msg_id)
        if not m:
            await self.bbs.send(session, "\r\nNo such message.\r\n")
            return
        body = m["body"].replace("\n", "\r\n")
        await self.bbs.send(
            session,
            f"\r\nMsg #{m['id']} | {m['author']} | {m['timestamp'][:19]}\r\n"
            f"Subject: {m['subject']}\r\n"
            "-" * 40 + "\r\n" + body + "\r\n" + "-" * 40 + "\r\n",
        )

    async def _post(self, session, board, reply_to=None):
        subject = await self._ask(session, "Subject: ")
        if not subject:
            await self.bbs.send(session, "\r\nCancelled.\r\n")
            return
        if reply_to is not None:
            orig = self.store.get_message(board["id"], int(reply_to))
            if orig:
                subject = f"Re: {orig['subject']}"
        await self.bbs.send(
            session, "\r\nType your message. Empty line to finish,"
                      " /A on empty-subject aborts.\r\n"
        )
        body_lines = []
        while getattr(session, "is_active", True):
            line = await self._ask(session, "")
            if line.strip().upper() == "/A":
                await self.bbs.send(session, "\r\nAborted.\r\n")
                return
            if not line.strip():
                break
            body_lines.append(line)
        if reply_to is not None:
            orig = self.store.get_message(board["id"], int(reply_to))
            if orig:
                quoted = "".join(f"> {ln}\n" for ln in orig["body"].split("\n"))
                body_lines.insert(0, quoted.rstrip())
                body_lines.insert(0, f"On {orig['timestamp'][:19]}, "
                                     f"{orig['author']} wrote:")
        body = "\n".join(body_lines)
        msg = self.store.add_message(board["id"], session.user.username,
                                     subject, body)
        event = "messageboard:reply" if reply_to else "messageboard:post"
        self.bbs.events.emit(event, {"session": session, "msg": msg})
        await self.bbs.send(session, f"\r\nSaved as message #{msg['id']}.\r\n")

    async def _delete(self, session, board):
        num = await self._ask(session, "Delete #: ")
        if not num.isdigit():
            await self.bbs.send(session, "\r\nInvalid.\r\n")
            return
        msg = self.store.get_message(board["id"], int(num))
        if not msg:
            await self.bbs.send(session, "\r\nNo such message.\r\n")
            return
        if not can_delete(session.user, msg):
            await self.bbs.send(session, "\r\nNot allowed.\r\n")
            return
        self.store.delete_message(board["id"], int(num))
        self.bbs.events.emit("messageboard:delete",
                             {"session": session, "msg": msg})
        await self.bbs.send(session, f"\r\nDeleted #{num}.\r\n")

    # -- helpers --------------------------------------------------------------

    def _k(self, name: str) -> str:
        return self.keys.get(name, "?")

    async def _ask(self, session, prompt: str) -> str:
        await self.bbs.send(session, prompt)
        from core import runner

        text = await runner.read_command(self.bbs, session)
        if text is None:
            return ""
        return text.strip()

    async def _get_key(self, session) -> str | None:
        """Single keypress, no Enter. None = connection gone."""
        from core import runner

        return await runner.read_key(self.bbs, session)


__all__ = ["MessageBoardPlugin"]
