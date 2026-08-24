"""Application object for Modulo BBS core.

The ``BBSApp`` is the central object that the transport server and every
plugin share. It owns the core services (event bus, user manager, session
manager), the list of loaded plugins, and a reference to the running server
so it can push bytes to a session (via ``bbs.send``). Plugins receive this
object from ``Plugin.on_load(bbs)``.

Per the plugin spec the plugin exposes the bus and manager as ``bbs.events``
and ``bbs.users``; the longer ``event_bus`` / ``user_manager`` / ``session_manager``
attribute names are kept on the same object for clarity.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.events import EventBus
from core.storage import PluginStorage
from core.user import UserManager
from server.session import Session, SessionManager, SessionState


class BBSApp:
    """Core application object shared by the server and all plugins."""

    def __init__(self, max_nodes: int = 8, users_dir=None, plugins=None,
                 config: dict | None = None):
        self.event_bus = EventBus()
        self.session_manager = SessionManager(max_nodes)
        self.user_manager = UserManager(users_dir)
        self.plugins: list[Any] = list(plugins) if plugins else []
        # Server configuration (loaded from config.yaml by run_server.py).
        # Plugins read it via ``bbs.config`` (e.g. logon_sequence).
        self.config: dict = dict(config) if config else {}
        # Per-plugin data directories: bbs.storage.dir("messageboard") ->
        # plugins/messageboard/data/ (created on demand).
        self.storage = PluginStorage()
        # Reference to the running transport server (telnet/SSH). Set when
        # the server is constructed so ``send`` can reuse its transport logic.
        self.server: Any = None

    # -- convenience aliases used by plugins -------------------------------

    @property
    def events(self) -> EventBus:
        """Plugins fire/subscribe events via ``bbs.events``."""
        return self.event_bus

    @property
    def users(self) -> UserManager:
        """Plugins manage accounts via ``bbs.users``."""
        return self.user_manager

    def get_plugin(self, name: str):
        """Return the first loaded plugin whose ``name`` is ``name`` or None."""
        for p in self.plugins:
            if getattr(p, "name", None) == name:
                return p
        return None

    def keys_for(self, plugin_name: str, defaults: dict[str, str]) -> dict[str, str]:
        """Load keybindings for a plugin (see core/keys.py for semantics).

        Plugins call this instead of importing the loader directly, so the
        storage location stays a core decision.
        """
        from core.keys import load_keys

        plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
        return load_keys(plugins_dir, plugin_name, defaults)

    # -- socket control ------------------------------------------------------
    #
    # Hard boundary: only core closes sockets. Plugins (sequencer, menu, any
    # third-party) request a disconnect via bbs.disconnect(session); they
    # never touch the writer directly.

    async def disconnect(self, session: Session) -> None:
        """Close ``session``'s socket and remove it from the session manager.

        This is a core-owned primitive. Setting the state to DISCONNECTED
        first guarantees ``session.is_active`` is False so every current
        plugin/transport loop stops immediately.
        """
        session.state = SessionState.DISCONNECTED
        writer = getattr(session, "writer", None)
        if writer is not None:
            try:
                writer.close()
                if hasattr(writer, "wait_closed"):
                    await writer.wait_closed()
            except Exception:  # noqa: BLE001 -- never let closing mask errors
                pass
        await self.session_manager.remove_session(session.session_id)

    async def send(self, session: Session, text: str) -> None:
        """Send ``text`` to ``session``.

        Prefers delegating to the running server's ``_send`` so codec
        selection (per-session CP437/UTF-8), ANSI stripping in plain-text
        mode, and writer lifecycle checks stay consistent. Falls back to
        writing directly to ``session.writer`` (tests / headless sessions
        that have no attached server).
        """
        if self.server is not None and hasattr(self.server, "_send"):
            await self.server._send(session, text)
            return
        writer = getattr(session, "writer", None)
        if writer is None:
            return
        from shared.codecs import encode_out

        writer.write(encode_out(text, getattr(session, "codec", "cp437")))
        await writer.drain()

    async def send_raw(self, session: Session, data: bytes) -> None:
        """Send raw bytes (e.g. telnet negotiation responses) to ``session``."""
        if self.server is not None and hasattr(self.server, "_send_raw"):
            await self.server._send_raw(session, data)
            return
        writer = getattr(session, "writer", None)
        if writer is None:
            return
        writer.write(data)
        await writer.drain()


__all__ = ["BBSApp"]