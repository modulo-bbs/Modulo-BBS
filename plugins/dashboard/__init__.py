"""Dashboard home-tab: digest rows collected from other plugins."""
from __future__ import annotations

import asyncio

from plugins.base import Plugin
from plugins.mainmenu import list_pane

from core import runner


class DashboardPlugin(Plugin):
    name = "dashboard"
    version = "1.0.0"
    description = "Home digest of unread mail, files, and bulletins."
    menu_label = ""
    menu_key = ""
    menu_order = 5
    home_label = "Dashboard"

    def __init__(self):
        self.bbs = None

    def on_load(self, bbs):
        self.bbs = bbs

    async def _collect(self, session) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for p in list(getattr(self.bbs, "plugins", []) or []):
            if p is self:
                continue
            fn = getattr(p, "home_digest", None)
            if not callable(fn):
                continue
            try:
                result = fn(session)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception:
                continue
            if not result:
                continue
            if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], str):
                rows.append((result[0], str(result[1])))
            elif isinstance(result, list):
                for item in result:
                    if isinstance(item, tuple) and len(item) == 2:
                        rows.append((str(item[0]), str(item[1])))
        return rows

    async def render_home_pane(self, session) -> str:
        digests = await self._collect(session)
        try:
            session._pim_dashboard_targets = [t for _, t in digests]
        except Exception:
            pass
        items = [text for text, _ in digests] or ["(nothing new)"]
        return list_pane(
            self.bbs,
            session,
            items,
            "  Arrows · WASD or 1/2/3 to switch tabs, Enter to open, Q to disconnect",
        )

    async def handle_home_key(self, session, key: str) -> bool:
        if key != "ENTER":
            return False
        targets = getattr(session, "_pim_dashboard_targets", None) or []
        if not targets:
            return True
        sel = int(getattr(session, "_pim_selected", 0) or 0)
        sel = max(0, min(sel, len(targets) - 1))
        jump = targets[sel]
        tabs = []
        try:
            from plugins.mainmenu.tabs import load_tabs, visible_tabs

            tabs = visible_tabs(load_tabs(self.bbs), getattr(session, "user", None))
        except Exception:
            tabs = []
        if any(t["id"] == jump for t in tabs):
            session._pim_active_tab = jump
            from plugins.social.social import forget_social_selection

            forget_social_selection(session)
            return True
        plugin = self.bbs.get_plugin(jump)
        if plugin is not None:
            await runner.run_plugin_flow(self.bbs, plugin, session)
        return True
