"""The screen service — one interpreter, every plugin (see docs/screens.md).

A *screen* is a named display file owned by a plugin:

    plugins/<plugin>/screens/<name>.ans   ← sysop override (CP437 + ANSI)
    plugins/<plugin>/screens/<name>.asc   ← sysop override (plain ASCII)
    plugins/<plugin>/screens/<name>.txt   ← shipped default (UTF-8)

Resolution is by extension priority per name: ``.ans`` → ``.asc`` → ``.txt``.
The extension says only how the bytes decode; every file goes through the
identical pipeline — read bytes (CRLF preserved), decode, substitute tokens,
return. Tokens are inline: ``{username}``, ``{time}``, ``{node}``, plus any
registered provider's namespaced tokens (``{boards.count}`` …). ANSI colour
constants from the classic ``{BRIGHT_CYAN}`` family also substitute anywhere.

When no file exists, a plugin-registered *generator* produces the screen in
code (the migration path for today's inline renderers): file beats generator,
always.

This service lives on ``bbs.screens`` next to ``bbs.users`` / ``bbs.events``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from shared.telnet_protocol import ANSI

logger = logging.getLogger("modulo.screens")

#: Resolution order and the codec each extension decodes with.
EXTENSION_CODECS: tuple[tuple[str, str], ...] = (
    (".ans", "cp437"),
    (".asc", "ascii"),
    (".txt", "utf-8"),
)

# --- ANSI token table ({RESET}, {BRIGHT_CYAN}, {CLEAR} …) --------------------

_ANSI_NAMES = [
    "RESET", "BOLD", "DIM", "UNDERLINE", "BLINK", "REVERSE",
    "BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE",
    "BRIGHT_BLACK", "BRIGHT_BLUE", "BRIGHT_CYAN", "BRIGHT_GREEN",
    "BRIGHT_MAGENTA", "BRIGHT_RED", "BRIGHT_WHITE", "BRIGHT_YELLOW",
    "BG_BLACK", "BG_BLUE", "BG_CYAN", "BG_GREEN", "BG_MAGENTA",
    "BG_RED", "BG_WHITE", "BG_YELLOW",
]
ANSI_TOKENS: dict[str, str] = {n: getattr(ANSI, n) for n in _ANSI_NAMES}
ANSI_TOKENS["CLEAR"] = ANSI.CLEAR_SCREEN
ANSI_TOKENS["HOME"] = "\x1b[H"


@dataclass
class TokenContext:
    """Everything a token provider may need for one render."""

    session: object = None
    bbs: object = None


#: A provider receives a TokenContext and returns str values.
TokenProvider = Callable[[TokenContext], dict[str, object]]


class ScreenService:
    """Registry + resolver + token engine. One instance per BBSApp."""

    def __init__(self, bbs, plugins_root: Path | None = None):
        self.bbs = bbs
        # Root that contains plugins/<name>/screens/ (and screens/ for logon).
        # Overridable so tests can point at a throwaway tree.
        self.plugins_root = (
            Path(plugins_root)
            if plugins_root
            else Path(__file__).resolve().parent.parent
        )
        self._generators: dict[tuple[str, str], Callable[[], str]] = {}
        self._providers: list[TokenProvider] = []
        self.register_core_tokens()

    # -- registration ---------------------------------------------------------

    def register_generator(
        self, plugin: str, name: str, fn: Callable[..., str]
    ) -> None:
        """Register the code fallback for screen ``<name>`` of ``<plugin>``.

        ``fn`` may take zero args or one (the session); one-arg generators
        can personalise their output (e.g. /screen permission filtering).
        """
        self._generators[(plugin, name)] = fn

    def register_provider(self, provider: TokenProvider) -> None:
        """Add a token provider (called on every render; keep it cheap)."""
        self._providers.append(provider)

    def register_core_tokens(self) -> None:
        """The standard token vocabulary available to every screen."""

        def core_tokens(ctx: TokenContext) -> dict[str, object]:
            session = ctx.session or {}
            user = getattr(session, "user", None)
            from datetime import datetime

            now = datetime.now().astimezone()
            connected = getattr(session, "connected_at", None)
            if connected is not None:
                try:
                    session_secs = int(now.timestamp() - float(connected))
                except (TypeError, ValueError):
                    session_secs = 0
            else:
                session_secs = 0
            mgr = getattr(ctx.bbs, "session_manager", None)
            return {
                "bbsname": "Modulo BBS",
                "version": "0.1-alpha",
                "time": now.strftime("%H:%M"),
                "date": now.strftime("%m/%d/%y"),
                "datetime": now.strftime("%m/%d/%y %H:%M"),
                "username": getattr(user, "username", "") or "-",
                "displayname": getattr(user, "shown_name", lambda: "")(),
                "node": getattr(session, "node_id", 0) or 0,
                "active": getattr(mgr, "active_count", 0),
                "maxnodes": getattr(mgr, "max_nodes", 0),
                "termwidth": getattr(session, "terminal_width", 80),
                "termheight": getattr(session, "terminal_height", 24),
                "sessiontime": f"{session_secs // 60:02d}:{session_secs % 60:02d}",
            }

        self.register_provider(core_tokens)

    # -- resolution ------------------------------------------------------------

    def _resolve(self, plugin: str, name: str) -> tuple[Path | None, str]:
        """Find the best file for this screen. Returns (path, codec)."""
        base = self.plugin_screens_dir(plugin)
        for ext, codec in EXTENSION_CODECS:
            path = base / f"{name}{ext}"
            if path.is_file():
                return path, codec
        return None, ""

    def plugin_screens_dir(self, plugin: str) -> Path:
        """Where this plugin's screens live.

        ``logon`` is special: its screens (splash/welcome) are board-global
        and live in the project-root ``screens/`` directory.
        """
        if plugin == "logon":
            return self.plugins_root / "screens"
        return self.plugins_root / "plugins" / plugin / "screens"

    def source_for(self, plugin: str, name: str) -> str:
        """Which source would win for this screen ('file:.ans'/'generator')."""
        path, _ = self._resolve(plugin, name)
        if path is not None:
            return f"file:{path.suffix}"
        if (plugin, name) in self._generators:
            return "generator"
        return "missing"

    def screen_names(self, plugin: str) -> list[str]:
        """Every screen name this plugin exposes (files ∪ generators)."""
        names: set[str] = set()
        d = self.plugin_screens_dir(plugin)
        if d.is_dir():
            for p in d.iterdir():
                for ext, _ in EXTENSION_CODECS:
                    if p.name.endswith(ext):
                        names.add(p.name[: -len(ext)])
        for owner, name in self._generators:
            if owner == plugin:
                names.add(name)
        return sorted(names)

    # -- rendering ---------------------------------------------------------------

    def render_default(self, session, plugin: str, name: str, **extra: object) -> str:
        """Render the *generated* screen even when a sysop file override exists.

        This is what ``/screen`` serves: the machine's own view of the
        interface, filtered to the caller's permissions. Never reads files.
        """
        gen = self._generators.get((plugin, name))
        if gen is None:
            return f"[no generated screen: {plugin}/{name}]"
        try:
            text = gen(session)
        except TypeError:
            # Zero-arg generator.
            text = gen()
        return self.substitute(text, session, **extra)

    def render(self, session, plugin: str, name: str, **extra: object) -> str:
        """Render screen ``name`` of ``plugin`` for ``session``.

        File beats generator; missing both returns a visible placeholder so a
        broken reskin is obvious rather than silent. ``**extra`` are ad-hoc
        tokens for this call only (highest precedence).
        """
        text = self._load(session, plugin, name)
        if text is None:
            gen = self._generators.get((plugin, name))
            if gen is not None:
                try:
                    text = gen(session)
                except TypeError:
                    text = gen()
            else:
                text = f"[missing screen: {plugin}/{name}]"
        return self.substitute(text, session, **extra)

    async def send(self, session, plugin: str, name: str, **extra: object) -> None:
        """Render and transmit in one step."""
        await self.bbs.send(session, self.render(session, plugin, name, **extra))

    def _load(self, session, plugin: str, name: str) -> str | None:
        path, codec = self._resolve(plugin, name)
        if path is None:
            return None
        try:
            raw = path.read_bytes()          # bytes! CRLF survives (Syncterm).
        except OSError:
            logger.warning("screen %s/%s unreadable", plugin, name)
            return None
        try:
            return raw.decode(codec, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")

    # -- tokens -----------------------------------------------------------------

    def substitute(self, text: str, session, **extra: object) -> str:
        """Swap ANSI constants + every provider's tokens + ad-hoc extras."""
        ctx = TokenContext(session=session, bbs=self.bbs)
        values: dict[str, object] = {}
        for provider in self._providers:
            try:
                values.update(provider(ctx) or {})
            except Exception:  # noqa: BLE001 - one bad provider can't kill a screen
                logger.exception("token provider failed")
        values.update(extra)
        values.update(ANSI_TOKENS)
        for key, val in values.items():
            text = text.replace("{" + key + "}", str(val))
        return text
