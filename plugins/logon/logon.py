"""Logon sequencer plugin -- the orchestrator of the logon experience.

The order of what a caller sees (splash, login, welcome, menu ...) is data,
not code: the sysop configures ``logon_sequence`` in config.yaml and this
plugin runs each step in order. Every step is pluggable:

    - ``screen:<file>``  display ``screens/<file>``, no input
    - ``plugin:<name>``  run the named plugin's session flow

The sequencer is itself an ordinary plugin -- it needs no special privileges,
so it gets none. Because it is nothing but orchestration, a sysop can point
the core's ``logon_plugin`` config key at any orchestrator (a wizard-style
onboarding, straight-to-chat, kiosk mode) without touching core.

Each executed step emits a ``logon:step`` event with ``{session, step, result}``
giving instrumentation per-step visibility even if a later step fails.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from plugins.base import Plugin

logger = logging.getLogger("modulo.plugins.logon")

# Session state (guarded so this module imports standalone too).
try:  # pragma: no cover - guard for environments without server.session
    from server.session import SessionState
except Exception:  # noqa: BLE001
    SessionState = None

# --- paths -------------------------------------------------------------------

_PLUGIN_DIR = Path(__file__).resolve().parent.parent    # plugins/
_PROJECT_ROOT = _PLUGIN_DIR.parent                       # project root
SCREENS_DIR = _PROJECT_ROOT / "screens"                  # sysop screens dir


class LogonPlugin(Plugin):
    """Executes the sysop-configurable ``logon_sequence`` one step at a time."""

    name = "logon"
    version = "1.0.0"
    description = "Runs the sysop-configurable logon sequence (splash/login/menu)."
    menu_label = ""                   # the sequencer is not a menu item
    menu_key = ""
    menu_order = 0

    # Default sequence when config.yaml omits ``logon_sequence``. Mirrors the
    # banner -> login -> welcome -> menu flow the transports used to hardcode.
    DEFAULT_SEQUENCE = [
        "screen:splash.txt",
        "plugin:login",
        "screen:welcome.txt",
        "plugin:mainmenu",
    ]

    def __init__(self):
        self.bbs = None
        self.screens_dir = SCREENS_DIR

    # -- config ----------------------------------------------------------------

    def _sequence(self) -> list[str]:
        """The configured logon_sequence, or the DEFAULT_SEQUENCE."""
        seq = self.bbs.config.get("logon_sequence") if self.bbs else None
        if not seq:
            return list(self.DEFAULT_SEQUENCE)
        if isinstance(seq, str):
            seq = [seq]
        return [s for s in seq if isinstance(s, str)]

    # -- lifecycle -------------------------------------------------------------

    def on_load(self, bbs):
        self.bbs = bbs

    async def on_session_start(self, session):
        """Run every step of the logon sequence in order."""
        if self.bbs is None:
            return
        if SessionState is not None:
            session.state = SessionState.LOGIN

        for step in self._sequence():
            if not getattr(session, "is_active", True):
                break
            step = step.strip()
            if not step:
                continue
            if step.startswith("screen:"):
                await self._run_screen(session, step[len("screen:"):])
            elif step.startswith("plugin:"):
                await self._run_plugin_step(session, step[len("plugin:"):])
            else:
                logger.warning("Unknown logon step %r; skipping", step)
                self._emit(session, step, "unknown")

        # If the sequence finished with the session still open, nothing else
        # will drive it -- close rather than leaving the session hanging.
        if getattr(session, "is_active", False):
            await self.bbs.disconnect(session)

    # -- steps -----------------------------------------------------------------

    async def _run_screen(self, session, filename: str):
        """Display a logon screen via the core screen service.

        The service resolves the best existing variant (``.ans`` > ``.asc``
        > ``.txt``) from ``screens/`` and substitutes tokens ({NODE},
        {NAME}, {ACTIVE}, {ACCENT} …).
        """
        stem = filename.rsplit(".", 1)[0]
        svc = getattr(self.bbs, "screens", None)
        if svc is not None:
            await svc.send(session, self.name, stem, **self._placeholders(session))
            self._emit(session, f"screen:{filename}", "displayed")
            return
        # Fallback (service unavailable): legacy direct read.
        path = SCREENS_DIR / filename
        if not path.is_file():
            logger.warning("logon screen %r not found in %s", filename, SCREENS_DIR)
            self._emit(session, f"screen:{filename}", "missing")
            return
        text = path.read_bytes().decode("utf-8", errors="replace")
        from core import banner as _banner

        text = _banner.substitute_tokens(text, session=session, **self._placeholders(session))
        await self.bbs.send(session, text)
        self._emit(session, f"screen:{filename}", "displayed")

    async def _run_plugin_step(self, session, name: str):
        """Run a named plugin's session flow (its ``on_session_start``)."""
        plugin = self.bbs.get_plugin(name)
        if plugin is None:
            logger.warning("logon step references missing plugin %r", name)
            self._emit(session, f"plugin:{name}", "missing")
            return
        try:
            result = plugin.on_session_start(session)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception:  # noqa: BLE001
            logger.exception("logon plugin %r crashed during its session flow", name)
            self._emit(session, f"plugin:{name}", "error")
            return
        self._emit(session, f"plugin:{name}", result)

        # A flow step that returned falsy without the user authenticating means
        # the caller declined / quit -- end the logon cleanly. (True/None steps
        # such as login success or the main menu keep the sequence going.)
        if (
            result is not None
            and not bool(result)
            and not getattr(session, "authenticated", False)
            and getattr(session, "is_active", False)
        ):
            await self.bbs.send(session, "\r\nGoodbye! Thanks for calling.\r\n")
            await self.bbs.disconnect(session)

    # -- helpers ---------------------------------------------------------------

    def _placeholders(self, session) -> dict[str, object]:
        """Runtime values substituted into screen templates."""
        mgr = self.bbs.session_manager
        name = ""
        user = getattr(session, "user", None)
        if getattr(session, "authenticated", False) and user is not None:
            name = getattr(user, "display_name", "") or getattr(session, "username", "")
        from core.version import display

        return {
            "NODE": getattr(session, "node_id", 0),
            "TTERM": getattr(session, "terminal_type", "UNKNOWN"),
            "TW": getattr(session, "terminal_width", 80),
            "TH": getattr(session, "terminal_height", 24),
            "VERSION": display(),
            "PYTHON": sys.version.split()[0],
            "ACTIVE": mgr.active_count,
            "MAXNODES": mgr.max_nodes,
            "NAME": name,
        }

    def _emit(self, session, step: str, result) -> None:
        """Fire ``logon:step`` with ``{session, step, result}``."""
        if self.bbs is not None:
            self.bbs.events.emit(
                "logon:step", {"session": session, "step": step, "result": result}
            )


__all__ = ["LogonPlugin", "SCREENS_DIR"]