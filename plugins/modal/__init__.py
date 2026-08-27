"""Modal core-plugin: pickers and notices other plugins call.

Callers use ``await core.modal.choose(bbs, session, labels)`` so a missing
plugin falls back. Swap the directory with a one-line role map in config.yaml.
"""
from __future__ import annotations

from plugins.base import Plugin
from plugins.modal.overlay import compact_overlay_geom, overlay_geom, paint_overlay

from core import runner
from core.theme import palette_for


class ModalPlugin(Plugin):
    name = "modal"
    version = "1.0.0"
    description = "Picker and notice overlays used by the rest of the board."
    menu_label = ""
    menu_key = ""
    menu_order = 0

    def __init__(self):
        self.bbs = None

    def on_load(self, bbs):
        self.bbs = bbs

    async def choose(
        self,
        session,
        labels: list[str],
        *,
        default: int = 0,
        hint: str | None = None,
        compact: bool = True,
    ) -> int | None:
        """Show *labels*; return the selected index, or None on ESC.

        Arrows / J / K move; Enter selects; ESC or Q cancels. Compact boxes
        sit low-center; ``compact=False`` uses the large overlay.
        """
        names = [str(x) for x in (labels or [])]
        if not names:
            return None
        idx = max(0, min(int(default), len(names) - 1))
        is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
        pal = palette_for(session)
        hint = hint if hint is not None else " arrows  Enter  ESC "
        if compact:
            min_inner = max(len(n) + 2 for n in names)
            min_inner = max(min_inner, len(hint) - 1)
            geom = compact_overlay_geom(
                session, n_rows=len(names), min_inner=min_inner
            )
        else:
            geom = overlay_geom(session)
        _top, _L, _wid, _interior, inner_w = geom

        while getattr(session, "is_active", True):
            rows: list[str] = []
            for i, name in enumerate(names):
                label = name[:inner_w].ljust(inner_w)
                if is_plain:
                    prefix = "> " if i == idx else "  "
                    rows.append(f"{prefix}{name}"[:inner_w].ljust(inner_w))
                elif i == idx:
                    rows.append(f"{pal.tab_fg}{pal.tab_bg}{label}{pal.reset}")
                else:
                    rows.append(f"{pal.text}{label}{pal.reset}")
            await self.bbs.send(
                session,
                paint_overlay(session, rows, hint, pal, geom=geom),
            )
            key = await runner.read_key(self.bbs, session)
            if key is None:
                return None
            if key in ("UP", "K"):
                idx = max(0, idx - 1)
            elif key in ("DOWN", "J"):
                idx = min(len(names) - 1, idx + 1)
            elif key == "ENTER":
                stash = getattr(session, "_line_buffer", "")
                if stash.startswith("\n"):
                    session._line_buffer = stash[1:]
                return idx
            elif key in ("ESC", "Q"):
                return None
        return None

    async def notice(self, session, body: str) -> None:
        """Bordered info box; any key dismisses."""
        from shared.codecs import _ANSI_RE
        from shared.textwrap import wrap

        _top, _L, _wid, interior, inner_w = overlay_geom(session)
        pal = palette_for(session)
        text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
        raw = [ln.rstrip() for ln in text.split("\n")]
        while raw and not raw[0].strip():
            raw.pop(0)
        while raw and not raw[-1].strip():
            raw.pop()

        lines: list[str] = []
        for ln in raw:
            plain = _ANSI_RE.sub("", ln)
            if len(plain) <= inner_w:
                lines.append(ln)
            else:
                lines.extend(wrap(plain, inner_w))
        if len(lines) > interior:
            lines = lines[: interior - 1] + ["…"]
        await self.bbs.send(
            session, paint_overlay(session, lines, " any key dismiss ", pal)
        )
        await runner.read_key(self.bbs, session)
