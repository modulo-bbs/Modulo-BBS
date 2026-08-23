"""Files plugin: file area listings with group-gated areas.

Upload/download of actual bytes is a future extension (X/Y/Zmodem over
telnet, SCP over SSH). For now the plugin manages the *catalog*: sysops
and users register files with name/size/description; download reports
where the bytes would be served from.
"""
from __future__ import annotations

import asyncio

from plugins.base import Plugin

from .areas import AreaStore, load_areas

DEFAULT_KEYS = {"LIST": "L", "UPLOAD": "U", "DOWNLOAD": "D", "DELETE": "Z",
                "QUIT": "Q"}


class FilesPlugin(Plugin):
    name = "files"
    version = "1.0.0"
    description = "File areas with group gating"
    menu_label = "[F] Files"
    menu_key = "F"
    menu_order = 20

    def __init__(self):
        self.bbs = None
        self.store = None
        self.areas: list[dict] = []
        self.keys = dict(DEFAULT_KEYS)

    def on_load(self, bbs):
        self.bbs = bbs
        d = bbs.storage.dir("files")
        self.store = AreaStore(d)
        self.areas = load_areas(d)
        self.keys = bbs.keys_for("files", DEFAULT_KEYS)

    def visible_areas(self, user) -> list[dict]:
        return [a for a in self.areas if user.can_access(a.get("requires", []))]

    async def on_session_start(self, session) -> bool:
        while getattr(session, "is_active", True):
            vis = self.visible_areas(session.user)
            lines = ["", " File Areas", " =========="]
            if not vis:
                lines.append(" (no areas available)")
            for i, a in enumerate(vis, 1):
                n = len(self.store.list_files(a["id"]))
                lines.append(f" {i}. {a['name']} ({n} files)")
            lines.append("")
            await self.bbs.send(session, "\r\n".join(lines) + "\r\n")
            pick = await self._get_key(session)
            if pick is None or pick == self._k("QUIT"):
                return False
            if pick.isdigit() and 1 <= int(pick) <= len(vis):
                await self._area_menu(session, vis[int(pick) - 1])
            else:
                await self.bbs.send(session, "\r\nInvalid selection.\r\n")
        return False

    async def _area_menu(self, session, area: dict):
        while getattr(session, "is_active", True):
            recs = self.store.list_files(area["id"])
            lines = ["", f" {area['name']} -- {len(recs)} files",
                     " " + "=" * (len(area["name"]) + 12)]
            for i, r in enumerate(recs, 1):
                kb = max(1, r.get("size_bytes", 0) // 1024)
                lines.append(f" {i}. {r['name']} ({kb}k) by {r['uploader']}")
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
            if u == self._k("UPLOAD"):
                await self._upload(session, area)
            elif u == self._k("DOWNLOAD"):
                num = await self._ask(session, "Download #: ")
                await self._download(session, area, int(num) if num.isdigit() else 0)
            elif u == self._k("DELETE"):
                await self._delete(session, area)
            elif cmd.isdigit():
                await self._describe(session, area, int(cmd))

    async def _upload(self, session, area: dict):
        name = await self._ask(session, "Filename: ")
        if not name or "/" in name or "\\" in name or ".." in name:
            await self.bbs.send(session, "\r\nInvalid filename.\r\n")
            return
        size_s = await self._ask(session, "Size in bytes: ")
        size = int(size_s) if size_s.isdigit() else 0
        desc = await self._ask(session, "Description: ")
        rec = self.store.add_file(area["id"], name, size,
                                  session.user.username, desc)
        # Reserve the byte-store slot so a future transfer protocol has a home.
        store_dir = self.bbs.storage.dir("files") / area["id"] / "store"
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / f"{rec['id']}_{name}").touch()
        self.bbs.events.emit("files:upload",
                             {"session": session, "file": rec})
        await self.bbs.send(session, f"\r\nRegistered as #{rec['id']}.\r\n")

    async def _download(self, session, area: dict, fid: int):
        rec = self.store.get_file(area["id"], fid)
        if not rec:
            await self.bbs.send(session, "\r\nNo such file.\r\n")
            return
        self.bbs.events.emit("files:download", {"session": session, "file": rec})
        await self.bbs.send(
            session,
            f"\r\n{rec['name']}: transfer protocols (X/Y/Zmodem) "
            "not yet implemented.\r\nBytes would serve from "
            f"data/files/{area['id']}/store/{rec['id']}_{rec['name']}\r\n",
        )

    async def _delete(self, session, area: dict):
        num = await self._ask(session, "Delete #: ")
        if not num.isdigit():
            await self.bbs.send(session, "\r\nInvalid.\r\n")
            return
        rec = self.store.get_file(area["id"], int(num))
        if not rec:
            await self.bbs.send(session, "\r\nNo such file.\r\n")
            return
        if not self.store.can_delete(session.user, rec):
            await self.bbs.send(session, "\r\nNot allowed.\r\n")
            return
        self.store.delete_file(area["id"], int(num))
        self.bbs.events.emit("files:delete", {"session": session, "file": rec})
        await self.bbs.send(session, f"\r\nDeleted #{num}.\r\n")

    async def _describe(self, session, area: dict, fid: int):
        rec = self.store.get_file(area["id"], fid)
        if not rec:
            await self.bbs.send(session, "\r\nNo such file.\r\n")
            return
        await self.bbs.send(
            session,
            f"\r\n#{rec['id']} {rec['name']} ({rec.get('size_bytes', 0)} bytes)\r\n"
            f"Uploaded by {rec['uploader']} on {rec['timestamp'][:19]}\r\n"
            f"{rec.get('description', '')}\r\n",
        )

    def _k(self, name: str) -> str:
        return self.keys.get(name, "?")

    async def handle_command(self, session, command) -> bool:
        return (command or "").strip().upper() != self._k("QUIT")

    async def _ask(self, session, prompt: str) -> str:
        await self.bbs.send(session, prompt)
        from core import runner

        text = await runner.read_command(self.bbs, session)
        if text is None:
            return ""
        return text.strip()

    async def _get_key(self, session) -> str | None:
        from core import runner

        return await runner.read_key(self.bbs, session)


__all__ = ["FilesPlugin"]
