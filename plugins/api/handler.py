"""Lightweight HTTP API handler for Modulo BBS.

Uses only stdlib (http.server + json).  Routes:

    GET  /api/health      → server status + node counts
    GET  /api/sessions    → active session list
    POST /api/shutdown    → graceful shutdown (broadcasts goodbye)
    POST /api/broadcast   → send a message to every connected user

Auth: ``X-API-Key`` header checked against configured keys.
If no keys are configured the API is open (local-dev mode).
"""
from __future__ import annotations

import asyncio
import json
import logging
from http.server import BaseHTTPRequestHandler
from typing import Any

logger = logging.getLogger("bbs.api")

# Set once during Plugin.on_load; the handler reads these at request time.
_bbs: Any = None
_api_keys: set[str] = set()
_loop: asyncio.AbstractEventLoop | None = None


def set_bbs(bbs: Any, keys: list[dict] | None = None) -> None:
    """Inject the BBS application object and API key list."""
    global _bbs, _api_keys, _loop  # noqa: PLW0603
    _bbs = bbs
    _api_keys = {k["key"] for k in (keys or []) if k.get("key")}
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            _loop = asyncio.get_event_loop()
        except RuntimeError:
            _loop = None


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class BBSAPIHandler(BaseHTTPRequestHandler):
    """Handle BBS control API requests (one instance per connection)."""

    # Silence the default stderr logging.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug(format, *args)

    # -- auth ----------------------------------------------------------------

    def _check_auth(self) -> bool:
        """Return True if the request carries a valid API key (or none configured)."""
        if not _api_keys:
            return True
        return self.headers.get("X-API-Key", "") in _api_keys

    # -- helpers -------------------------------------------------------------

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg: str, status: int = 400) -> None:
        self._json({"error": msg}, status)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}

    # -- routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — stdlib naming
        if not self._check_auth():
            return self._error("Unauthorized", 401)
        if self.path == "/api/health":
            return self._health()
        if self.path == "/api/sessions":
            return self._sessions()
        self._error("Not Found", 404)

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            return self._error("Unauthorized", 401)
        if self.path == "/api/shutdown":
            return self._shutdown()
        if self.path == "/api/broadcast":
            return self._broadcast()
        self._error("Not Found", 404)

    # -- endpoint implementations --------------------------------------------

    def _health(self) -> None:
        if _bbs is None:
            return self._error("BBS not initialised", 503)
        mgr = _bbs.session_manager
        self._json({
            "status": "running",
            "name": "Modulo BBS",
            "version": "0.1-alpha",
            "nodes": {
                "active": mgr.active_count,
                "max": mgr.max_nodes,
            },
            "plugins": [p.name for p in _bbs.plugins],
        })

    def _sessions(self) -> None:
        if _bbs is None:
            return self._error("BBS not initialised", 503)
        sessions = _bbs.session_manager.get_all_sessions()
        self._json({"sessions": sessions, "count": len(sessions)})

    def _shutdown(self) -> None:
        if _bbs is None:
            return self._error("BBS not initialised", 503)
        body = self._read_body()
        message = body.get("message", "Server shutting down. Goodbye!")

        if _loop is None or _loop.is_closed():
            return self._error("Event loop unavailable", 503)

        _loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(_do_shutdown(message))
        )
        self._json({"status": "shutting_down", "message": message})

    def _broadcast(self) -> None:
        if _bbs is None:
            return self._error("BBS not initialised", 503)
        body = self._read_body()
        message = body.get("message", "")
        if not message:
            return self._error("Missing 'message' field")

        if _loop is None or _loop.is_closed():
            return self._error("Event loop unavailable", 503)

        _loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(_do_broadcast(message))
        )
        self._json({"status": "broadcasting", "message": message})


# ---------------------------------------------------------------------------
# Async helpers (scheduled on the event loop from handler threads)
# ---------------------------------------------------------------------------

async def _do_broadcast(message: str) -> None:
    if _bbs is None:
        return
    for session in list(_bbs.session_manager.active_sessions):
        try:
            await _bbs.send(session, f"\r\n[Broadcast] {message}\r\n")
        except Exception:  # noqa: BLE001
            pass


async def _do_shutdown(message: str) -> None:
    if _bbs is None:
        return
    # Notify all connected users first.
    for session in list(_bbs.session_manager.active_sessions):
        try:
            await _bbs.send(session, f"\r\n\r\n{message}\r\n")
        except Exception:  # noqa: BLE001
            pass
    # Brief pause so messages flush before the transport closes.
    await asyncio.sleep(0.5)
    if _bbs.server:
        await _bbs.server.stop(message)
