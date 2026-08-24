"""HTTP layer for the Modulo BBS API plugin.

Two jobs, both generic (no per-endpoint business logic):

1. Legacy endpoints (/api/health etc.) — kept for compatibility.
2. One-API dispatch (/api/v1/...) — thin adapter over core.ops.registry:
   POST /api/v1/<op.name>  JSON body -> ops.call()
   GET  /api/v1/_schema    self-description, filtered by plane

Plane selection is deployment-driven: the listener bound to loopback serves
both planes ("mgmt"); any other bind address is treated as public-only and
never sees sysop ops — enforced again here even though registration already
forbids it.

Auth for /api/v1: Bearer token from auth.login (user accounts). The legacy
X-API-Key header continues to work on legacy endpoints only.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any

from core.ops import (
    PLANE_MGMT,
    PLANE_PUBLIC,
    OpsError,
    PermissionDeniedError,
    UnknownOperation,
    ValidationError,
    registry,
)

logger = logging.getLogger("bbs.api")

# Set once during Plugin.on_load; handlers read these at request time.
_bbs: Any = None
_legacy_keys: set[str] = set()   # legacy X-API-Key allowlist (legacy endpoints)
_loop: asyncio.AbstractEventLoop | None = None
_mgmt_plane = True               # False when bound to a non-loopback address


def set_bbs(bbs: Any, keys: list[dict] | None = None, mgmt_plane: bool = True) -> None:
    """Inject the BBS application object, legacy keys, and plane mode."""
    global _bbs, _legacy_keys, _loop, _mgmt_plane  # noqa: PLW0603
    _bbs = bbs
    _legacy_keys = {k["key"] for k in (keys or []) if isinstance(k, dict) and k.get("key")}
    _mgmt_plane = mgmt_plane
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            _loop = asyncio.get_event_loop()
        except RuntimeError:
            _loop = None


def _error_payload(msg: str) -> dict:
    return {"error": msg}


class BBSAPIHandler(BaseHTTPRequestHandler):
    """Handle HTTP requests; one instance per connection."""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug(format, *args)

    # -- responses ------------------------------------------------------------

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, msg: str, status: int = 400) -> None:
        self._json(_error_payload(msg), status)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raise ValidationError("body must be valid JSON")
        if not isinstance(parsed, dict):
            raise ValidationError("body must be a JSON object")
        return parsed

    # -- auth -----------------------------------------------------------------

    def _bearer_user(self):
        """Resolve Authorization: Bearer <token> -> User | None."""
        from core.opdefs import resolve_token

        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        if _loop is None or _loop.is_closed() or not _loop.is_running():
            return None
        raw = header[len("Bearer "):].strip()
        username = resolve_token(raw)
        if username is None or _bbs is None:
            return None
        fut = asyncio.run_coroutine_threadsafe(
            _bbs.users.get(username), _loop
        )
        try:
            return fut.result(timeout=5)
        except Exception:  # noqa: BLE001
            return None

    def _legacy_auth_ok(self) -> bool:
        if not _legacy_keys:
            return True
        return self.headers.get("X-API-Key", "") in _legacy_keys

    # -- routing ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/api/v1/_schema":
                return self._schema()
            if path.startswith("/api/v1/"):
                op_name = path[len("/api/v1/"):]
                # Read-style ops may be invoked via GET with query params.
                return self._dispatch(op_name, self._query_params(), allow_get=True)
            if path == "/api/health":
                return self._legacy_health()
            if path == "/api/sessions":
                return self._legacy_sessions()
            self._error("Not Found", 404)
        except (ValidationError, PermissionDeniedError, UnknownOperation, OpsError) as e:
            self._map_op_error(e)
        except Exception as e:  # noqa: BLE001
            logger.exception("GET %s failed", path)
            self._error(f"internal error: {e}", 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        try:
            body = self._read_body()
            if path.startswith("/api/v1/"):
                return self._dispatch(path[len("/api/v1/"):], body)
            if path == "/api/shutdown":
                return self._legacy_shutdown(body)
            if path == "/api/broadcast":
                return self._legacy_broadcast(body)
            self._error("Not Found", 404)
        except (ValidationError, PermissionDeniedError, UnknownOperation, OpsError) as e:
            self._map_op_error(e)
        except Exception as e:  # noqa: BLE001
            logger.exception("POST %s failed", path)
            self._error(f"internal error: {e}", 500)

    def _query_params(self) -> dict:
        qs = urllib.parse.urlparse(self.path).query
        out: dict[str, str] = {}
        for part in qs.split("&"):
            if "=" in part:
                k, _, v = part.partition("=")
                out[urllib.parse.unquote(k)] = urllib.parse.unquote(v)
        return out

    def _map_op_error(self, exc: Exception) -> None:
        if isinstance(exc, UnknownOperation):
            return self._error(str(exc), 404)
        if isinstance(exc, PermissionDeniedError):
            return self._error(str(exc), 403)
        if isinstance(exc, (ValidationError, OpsError)):
            return self._error(str(exc), 400)
        self._error(str(exc), 500)

    # -- /api/v1 dispatch -------------------------------------------------------

    def _my_plane(self) -> str:
        """This listener's plane; per-class, never global state."""
        getter = getattr(self, "_plane_mgmt", None)
        is_mgmt = getter() if callable(getter) else True
        return PLANE_MGMT if is_mgmt else PLANE_PUBLIC

    def _schema(self) -> None:
        self._json(registry.schema(plane=self._my_plane()))

    def _dispatch(self, op_name: str, params: dict, allow_get: bool = False) -> None:
        op = registry.get(op_name)
        if op is None:
            return self._error(f"no such operation: {op_name}", 404)
        # Plane check: this listener's plane must carry the operation.
        if self._my_plane() not in op.planes:
            return self._error("no such operation", 404)  # invisible, not forbidden
        if allow_get and (op.params or op.requires):
            # Only open, paramless-ish reads are comfortable over GET.
            if op.params:
                return self._error("use POST for operations with required params", 405)
        user = self._bearer_user()
        if _loop is None or _loop.is_closed() or not _loop.is_running():
            return self._error("event loop unavailable", 503)
        fut = asyncio.run_coroutine_threadsafe(
            registry.call(_bbs, user, op_name, params), _loop
        )
        try:
            result = fut.result(timeout=30)
        except concurrent.futures.TimeoutError:
            return self._error("operation timed out", 504)
        except concurrent.futures.CancelledError:
            return self._error("event loop unavailable", 503)
        except Exception as e:  # noqa: BLE001
            return self._map_op_error(e)
        self._json(result)

    # -- legacy endpoints --------------------------------------------------------

    # Legacy endpoints predate user-token auth; their X-API-Key allowlist IS
    # the credential. A valid legacy key (or open dev mode) is therefore
    # treated as a sysop-equivalent machine identity for the registry call.

    def _legacy_principal(self):
        from core.user import SYSOP_GROUP, User

        return User(
            username="api-key",
            display_name="API Key",
            password_hash="",
            groups=[SYSOP_GROUP],
        )

    def _legacy_health(self) -> None:
        if not self._legacy_auth_ok():
            return self._error("Unauthorized", 401)
        if _bbs is None:
            return self._error("BBS not initialised", 503)
        mgr = _bbs.session_manager
        self._json({
            "status": "running",
            "name": "Modulo BBS",
            "version": "0.1-alpha",
            "nodes": {"active": mgr.active_count, "max": mgr.max_nodes},
            "plugins": [p.name for p in _bbs.plugins],
        })

    def _legacy_sessions(self) -> None:
        if not self._legacy_auth_ok():
            return self._error("Unauthorized", 401)
        if _bbs is None:
            return self._error("BBS not initialised", 503)
        sessions = _bbs.session_manager.get_all_sessions()
        self._json({"sessions": sessions, "count": len(sessions)})

    def _legacy_shutdown(self, body: dict) -> None:
        if not self._legacy_auth_ok():
            return self._error("Unauthorized", 401)
        if _bbs is None:
            return self._error("BBS not initialised", 503)
        message = body.get("message") or "Server shutting down. Goodbye!"
        if _loop is None or _loop.is_closed() or not _loop.is_running():
            return self._error("Event loop unavailable", 503)
        fut = asyncio.run_coroutine_threadsafe(
            registry.call(_bbs, self._legacy_principal(), "system.shutdown",
                          {"message": message}),
            _loop,
        )
        try:
            result = fut.result(timeout=10)
        except Exception as e:  # noqa: BLE001
            return self._error(str(e), 500)
        self._json(result)

    def _legacy_broadcast(self, body: dict) -> None:
        if not self._legacy_auth_ok():
            return self._error("Unauthorized", 401)
        if _bbs is None:
            return self._error("BBS not initialised", 503)
        message = body.get("message", "")
        if not message:
            return self._error("Missing 'message' field")
        if _loop is None or _loop.is_closed() or not _loop.is_running():
            return self._error("Event loop unavailable", 503)
        fut = asyncio.run_coroutine_threadsafe(
            registry.call(_bbs, self._legacy_principal(), "system.broadcast",
                          {"message": message}),
            _loop,
        )
        try:
            result = fut.result(timeout=10)
        except Exception as e:  # noqa: BLE001
            return self._error(str(e), 500)
        self._json(result)


# Static file serving for the web dashboard lives in the plugin __init__
# (it subclasses this handler once at startup).
