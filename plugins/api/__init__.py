"""HTTP API plugin for Modulo BBS.

Starts the stdlib ThreadingHTTPServer exposing:

* Legacy endpoints  /api/health, /api/sessions, /api/shutdown, /api/broadcast
* One-API dispatch  /api/v1/<op.name> + /api/v1/_schema (see docs/one-api.md)
* Static files      /admin/... -> plugins/api/admin/ (the web dashboard)

Plane selection (docs/one-api.md): a listener bound to a loopback address is
a *management* plane (sees sysop ops); any other bind address is a *public*
plane and never sees sysop-gated operations. config.yaml may run both::

    api:
      enabled: true
      host: "127.0.0.1"     # management plane
      port: 8080
      public_port: 8443     # optional second listener, always public-plane
"""
from __future__ import annotations

import logging
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core.opdefs  # noqa: F401 - importing registers built-in operations
from core.ops import registry
from plugins.api.handler import BBSAPIHandler, set_bbs
from plugins.base import Plugin

logger = logging.getLogger("bbs.api")

ADMIN_DIR = Path(__file__).resolve().parent / "admin"


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")


class AdminFileMixin(BaseHTTPRequestHandler):
    """Serves static dashboard files under /admin/ before API routing.

    Mixed in ahead of BBSAPIHandler; ``super().do_GET()`` resolves to the API
    router on the composed class.
    """

    def do_GET(self):  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        if path == "/admin" or path.startswith("/admin/"):
            return self._serve_admin(path)
        return super().do_GET()

    def _serve_admin(self, path: str):
        rel = path[len("/admin"):].lstrip("/") or "index.html"
        target = (ADMIN_DIR / rel).resolve()
        if not str(target).startswith(str(ADMIN_DIR.resolve())) or not target.is_file():
            return self._error("Not Found", 404)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _make_handler_class(*, mgmt: bool) -> type:
    """Compose a handler class whose plane is fixed per class.

    Plane must be per-class (not global) so mgmt + public listeners can run
    concurrently without racing shared state. ``_plane_mgmt`` is consulted
    by BBSAPIHandler._my_plane().
    """
    ns = {"_plane_mgmt": staticmethod(lambda: mgmt)}
    return type("BoundAPIHandler", (AdminFileMixin, BBSAPIHandler), ns)


class APIPlugin(Plugin):
    """HTTP control API + One-API dispatch + static admin dashboard."""

    name = "api"
    version = "2.0.0"
    description = "HTTP API: health/sessions/shutdown/broadcast + /api/v1 ops"
    menu_label = ""
    menu_key = ""
    menu_order = 0

    def __init__(self) -> None:
        self._httpds: list[ThreadingHTTPServer] = []
        self._threads: list[threading.Thread] = []

    def on_load(self, bbs) -> None:
        api_cfg = (bbs.config or {}).get("api", {})
        if not api_cfg.get("enabled", False):
            logger.debug("API plugin: disabled (api.enabled not set)")
            return

        host: str = api_cfg.get("host", "127.0.0.1")
        port: int = api_cfg.get("port", 8080)
        keys: list[dict] = api_cfg.get("keys", [])
        public_port = api_cfg.get("public_port")

        # Shared app/keys state (single process, one BBS).
        set_bbs(bbs, keys=keys, mgmt_plane=True)

        self._start_listener(host, port, mgmt=_is_loopback(host))
        if public_port:
            pub_host: str = api_cfg.get("public_host", "0.0.0.0")
            self._start_listener(pub_host, int(public_port), mgmt=False)

    def _start_listener(self, host: str, port: int, *, mgmt: bool) -> None:
        handler_cls = _make_handler_class(mgmt=mgmt)
        try:
            httpd = ThreadingHTTPServer((host, port), handler_cls)
        except OSError as exc:
            logger.error("API server failed to bind %s:%s — %s", host, port, exc)
            return
        t = threading.Thread(
            target=httpd.serve_forever,
            name=f"bbs-api-{'mgmt' if mgmt else 'public'}-{port}",
            daemon=True,
        )
        t.start()
        self._httpds.append(httpd)
        self._threads.append(t)
        logger.info(
            "API %s plane listening on %s:%s",
            "management" if mgmt else "public",
            host,
            port,
        )

    def on_unload(self) -> None:
        for httpd in self._httpds:
            httpd.shutdown()
        self._httpds.clear()
        self._threads.clear()


__all__ = ["APIPlugin", "registry"]
