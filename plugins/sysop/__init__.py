"""The sysop terminal menu — a thin, generic client of the ops registry.

Per the One-API principle (docs/one-api.md) this plugin contains no business
logic of its own: it renders groups of operations from the registry and calls
``registry.call()``. Anything registered with ``requires=["sysop"]`` shows up
here automatically under its section prefix ("users.", "sessions.", ...),
which is exactly the parity guarantee: an op that exists is operable from
the terminal without bespoke terminal code.

Sections are curated by SECTION_TITLES; unlisted sections still render in a
generic group so newly registered ops never silently vanish.
"""

from __future__ import annotations

import asyncio

from core.ops import PermissionDeniedError, ValidationError, registry
from plugins.base import Plugin
from shared.telnet_protocol import ANSI

from core import runner

try:  # pragma: no cover - environments without server.session
    from server.session import SessionState
except Exception:  # noqa: BLE001
    SessionState = None


SECTION_TITLES = {
    "users": "USER ACCOUNTS",
    "sessions": "ACTIVE SESSIONS",
    "system": "SYSTEM",
}

# Ops that need interactive prompting get a friendly label + prompt spec.
PROMPTS = {
    "users.create": [("username", "Username"), ("password", "Password"),
                     ("display_name", "Display name"), ("groups", "Groups")],
    "users.update": [("username", "Username to edit"), ("password", "New password"),
                     ("groups", "Groups (blank=keep)")],
    "users.delete": [("username", "Username to DELETE")],
    "users.get": [("username", "Username")],
    "sessions.kick": [("session_id", "Session id (see list)")],
    "system.broadcast": [("message", "Broadcast text")],
}


class SysopPlugin(Plugin):
    """[S] Sysop Menu — registry-driven management console."""

    name = "sysop"
    version = "1.0.1"
    description = "Sysop management menu (One-API client)"
    menu_label = "[S] Sysop Menu"
    menu_key = "S"
    menu_order = 90
    menu_requires = ["sysop"]  # hidden from non-sysops in menus

    def __init__(self):
        self.bbs = None

    def on_load(self, bbs):
        self.bbs = bbs

    async def on_session_start(self, session) -> None:
        # Defense in depth: even if reached via a stale hotkey or a custom
        # menu, a non-sysop gets bounced before any op can run.
        user = getattr(session, "user", None)
        if user is None or not user.in_group("sysop"):
            await self.bbs.send(session, "\r\nAccess denied.\r\n")
            return
        if SessionState is not None:
            session.state = SessionState.MAIN_MENU
        self.bbs.events.emit("menu:open", {"session": session, "menu_name": "sysop"})
        await self._loop(session)

    async def _loop(self, session):
        while getattr(session, "is_active", True):
            await self.bbs.send(session, self._render(session))
            key = await runner.read_key(self.bbs, session)
            if key is None:
                return
            if key == "Q":
                return
            op_name = self._key_to_op(key)
            if op_name is None:
                await self.bbs.send(session, "\r\nInvalid selection.\r\n")
                continue
            await self._run_op(session, op_name)

    # -- rendering -----------------------------------------------------------------

    def _ops_by_section(self) -> dict[str, list]:
        out: dict[str, list] = {}
        for name in registry.names():
            op = registry.get(name)
            if op is None or "mgmt" not in op.planes:
                continue
            section = name.split(".", 1)[0]
            if section == "auth" or name == "boards.delete_message":
                continue
            out.setdefault(section, []).append(op)
        for ops in out.values():
            ops.sort(key=lambda o: o.name)
        return out

    def _key_to_op(self, key: str) -> str | None:
        """Deterministic single-key mapping over sorted op names."""
        idx = ord(key) - ord("A")
        flat: list[str] = []
        for section in sorted(self._ops_by_section()):
            flat.extend(op.name for op in self._ops_by_section()[section])
        if 0 <= idx < len(flat):
            return flat[idx]
        return None

    def _render(self, session) -> str:
        w = min(getattr(session, "terminal_width", 80), 60)
        C, B, W, R = ANSI.BRIGHT_CYAN, ANSI.BOLD, ANSI.BRIGHT_WHITE, ANSI.RESET
        lines = [C + B + "=" * w + R, C + B + "  SysOp Menu" + R, C + B + "=" * w + R, ""]

        letter = 0
        self._letter_map: dict[str, str] = {}
        sections = self._ops_by_section()
        for section in sorted(sections):
            title = SECTION_TITLES.get(section, section.upper())
            lines.append(C + f"  -- {title} --" + R)
            for op in sections[section]:
                ch = chr(ord("A") + letter)
                self._letter_map[ch] = op.name
                desc = op.description or ""
                lines.append(W + f"  [{ch}] {op.name:<22}" + C + f" {desc[:34]}" + R)
                letter += 1
            lines.append("")

        lines.append(W + "  [Q] Back to Main Menu" + R)
        lines.append("")
        lines.append(W + "  Select: " + R)
        return "\r\n".join(lines)

    # -- execution -----------------------------------------------------------------

    async def _run_op(self, session, op_name: str):
        op = registry.get(op_name)
        if op is None:
            return
        params = await self._collect_params(session, op_name, op.params, op.optional)
        if params is None:
            return  # cancelled
        try:
            result = await registry.call(self.bbs, session.user, op_name, params)
        except ValidationError as e:
            await self.bbs.send(session, f"\r\n! {e}\r\n")
            await self._pause(session)
            return
        except PermissionDeniedError as e:
            await self.bbs.send(
                session,
                f"\r\n! Denied: {e}\r\n"
                "! (your account lacks the required group)\r\n",
            )
            await self._pause(session)
            return
        await self._show_result(session, result)

    async def _pause(self, session):
        """Hold output on screen until a key is pressed."""
        await self.bbs.send(session, "\r\n[Press any key] ")
        await runner.read_key(self.bbs, session)

    async def _collect_params(self, session, op_name, required, optional):
        """Interactive prompts per PROMPTS spec; generic fallback for others."""
        fields = list(PROMPTS.get(op_name, [(name, name.replace("_", " ").title())
                                            for name in required]))
        params: dict = {}

        # Special case: users.update / users.get benefit from listing users first.
        if op_name in ("users.update", "users.delete"):
            await self._show_users(session)

        for field, label in fields:
            text = await self._ask(session, f"{label}: ")
            if text.strip().upper() == "/A":
                await self.bbs.send(session, "\r\nCancelled.\r\n")
                return None
            if text.strip():
                params[field] = text.strip()
        # Fill required fields that were skipped by blanks -> error later via
        # validation. Optional numeric coercion for boards.* ids:
        if op_name == "boards.delete_message" and "id" in params:
            try:
                params["id"] = int(params["id"])
            except ValueError:
                pass
        return params

    async def _ask(self, session, prompt: str) -> str:
        await self.bbs.send(session, prompt)
        line = await runner.read_command(self.bbs, session)
        return (line or "").strip()

    async def _show_users(self, session):
        try:
            result = await registry.call(self.bbs, session.user, "users.list", {})
        except Exception:  # noqa: BLE001
            return
        rows = result.get("users", [])
        lines = ["", " Current accounts:"]
        for u in rows:
            lines.append(f"   {u['username']:<14} {','.join(u['groups'])}")
        await self.bbs.send(session, "\r\n".join(lines) + "\r\n")

    async def _show_result(self, session, result):
        import json as _json

        text = _json.dumps(result, indent=2, default=str)
        # Keep terminal lines short (Syncterm).
        out_lines = []
        for ln in text.splitlines():
            while len(ln) > 58:
                out_lines.append(ln[:58])
                ln = " " + ln[58:]
            out_lines.append(ln)
        await self.bbs.send(session, "\r\n" + "\r\n".join(out_lines) + "\r\n")
        await self._pause(session)


__all__ = ["SysopPlugin"]
