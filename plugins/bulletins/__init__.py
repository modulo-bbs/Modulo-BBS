"""Bulletins plugin: sysop-authored notices shown at logon, re-readable later."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from plugins.base import Plugin

logger = logging.getLogger("modulo.plugins.bulletins")

DEFAULT_KEYS = {"NEXT": "N", "PREVIOUS": "P", "QUIT": "Q"}


class BulletinsPlugin(Plugin):
    """Classic logon bulletins with seen-tracking and a main-menu reader."""

    name = "bulletins"
    version = "1.0.0"
    description = "Sysop bulletins shown new at logon."
    menu_label = "[B] Bulletins"
    menu_key = "B"
    menu_order = 40
    home_label = "Bulletins"

    def __init__(self):
        self.bbs = None
        self._keys = dict(DEFAULT_KEYS)

    # -- lifecycle -----------------------------------------------------------

    def on_load(self, bbs):
        self.bbs = bbs
        self._keys = bbs.keys_for("bulletins", DEFAULT_KEYS)
        self._data_dir()

    async def render_home_pane(self, session) -> str:
        from plugins.mainmenu import list_pane

        vis = self.visible_for(getattr(session, "user", None))
        items = [b["title"] for b in vis]
        return list_pane(
            self.bbs, session, items,
            "  Enter reads bulletins, arrows switch tabs, Q disconnects",
        )

    async def handle_home_key(self, session, key: str) -> bool:
        if key != "ENTER":
            return False
        await self.run_menu(session)
        return True

    def home_digest(self, session):
        from plugins.mainmenu import _elided

        user = getattr(session, "user", None)
        if user is None:
            return ("Bulletins: (no new)", "bulletins")
        ids = self.unseen(user)
        if ids:
            vis = {b["id"]: b for b in self.scan()}
            titles = [vis.get(i, {"title": i})["title"] for i in ids[:8]]
            return (_elided(f"Bulletins: ({len(ids)} new) ", titles, sep=" | ", width=74), "bulletins")
        return ("Bulletins: (no new)", "bulletins")

    def _data_dir(self) -> Path:
        d = self.bbs.storage.dir("bulletins")
        (d / "bulletins").mkdir(parents=True, exist_ok=True)
        return d

    def _content_dir(self) -> Path:
        return self._data_dir() / "bulletins"

    # -- catalog -------------------------------------------------------------

    def scan(self) -> list[dict]:
        """All bulletins sorted by id: {id, title, requires}."""
        out = []
        for p in sorted(self._content_dir().glob("*.txt")):
            bid = p.stem
            title, requires = bid, []
            meta = p.with_suffix(".meta.json")
            if meta.exists():
                try:
                    m = json.loads(meta.read_text(encoding="utf-8"))
                    title = m.get("title", title)
                    requires = [str(g).lower() for g in m.get("requires", [])]
                except (json.JSONDecodeError, OSError):
                    pass
            out.append({"id": bid, "title": title, "requires": requires})
        return out

    def visible_for(self, user) -> list[dict]:
        out = []
        for b in self.scan():
            req = b.get("requires") or []
            if not req:
                out.append(b)
            elif user is not None and user.can_access(req):
                out.append(b)
        return out

    def get(self, bulletin_id: str) -> str | None:
        p = self._content_dir() / f"{bulletin_id}.txt"
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")

    # -- seen tracking ---------------------------------------------------------

    def _seen_path(self) -> Path:
        return self._data_dir() / "seen.json"

    def _load_seen(self) -> dict[str, list[str]]:
        p = self._seen_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def unseen(self, user) -> list[str]:
        seen = set(self._load_seen().get(user.username, []))
        return [b["id"] for b in self.visible_for(user) if b["id"] not in seen]

    def mark_seen(self, user, ids: list[str]) -> None:
        data = self._load_seen()
        have = set(data.get(user.username, []))
        data[user.username] = sorted(have | set(ids))
        p = self._seen_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(p)

    # -- display ----------------------------------------------------------------

    async def _show(self, session, bid: str, title: str) -> bool:
        """Display one bulletin with paging. Returns False if user quit out."""
        text = self.get(bid)
        if text is None:
            return True
        self.bbs.events.emit("bulletins:read", {"session": session, "id": bid})
        h = max(int(getattr(session, "terminal_height", 24)), 4)
        lines = text.replace("\r\n", "\n").split("\n")
        for start in range(0, len(lines), h - 1):
            chunk = lines[start:start + h - 1]
            await self.bbs.send(session, "\r\n".join(chunk) + "\r\n")
            more = start + h - 1 < len(lines)
            if more:
                await self.bbs.send(
                    session, "\r\n-- More [Enter=next page, Q=stop] -- "
                )
                key = await self._read_key(session)
                await self.bbs.send(session, "\r\n")
                if key and key.upper() == "Q":
                    return False
        return True

    async def _read_key(self, session) -> str:
        from core import runner

        key = await runner.read_key(self.bbs, session)
        return key or ""

    # -- flows ---------------------------------------------------------------

    async def on_session_start(self, session) -> bool:
        """Logon step: show every accessible unseen bulletin, then mark seen."""
        if self.bbs is None:
            return True
        user = getattr(session, "user", None)
        ids = self.unseen(user) if user else []
        for bid in ids:
            meta = next((b for b in self.scan() if b["id"] == bid), {"id": bid, "title": bid})
            await self.bbs.send(session, f"\r\n=== {meta['title']} ===\r\n\r\n")
            if not await self._show(session, bid, meta["title"]):
                break
        if user is not None and ids:
            self.mark_seen(user, ids)
        return True

    async def run_menu(self, session) -> None:
        """Interactive re-read from the main menu. Never marks seen."""
        while getattr(session, "is_active", True):
            vis = self.visible_for(getattr(session, "user", None))
            lines = ["", " Bulletins", " ========="]
            for i, b in enumerate(vis, 1):
                lines.append(f" {i}. {b['title']}")
            lines.append("")
            await self.bbs.send(session, "\r\n".join(lines) + "\r\n")
            pick = await self._ask(session, f"{self._keys['QUIT']}=back #: ")
            if not pick or pick.upper() == self._keys["QUIT"]:
                return
            if pick.isdigit() and 1 <= int(pick) <= len(vis):
                b = vis[int(pick) - 1]
                await self.bbs.send(session, f"\r\n=== {b['title']} ===\r\n\r\n")
                await self._show(session, b["id"], b["title"])

    async def handle_command(self, session, command) -> bool:
        c = (command or "").strip().upper()
        if c == self._keys.get("QUIT"):
            return False
        if c == self._keys.get("NEXT"):
            await self.run_menu(session)
            return True
        return True

    async def _ask(self, session, prompt: str) -> str:
        await self.bbs.send(session, prompt)
        from core import runner

        text = await runner.read_command(self.bbs, session)
        if text is None:
            return ""
        return text.strip()


__all__ = ["BulletinsPlugin"]
