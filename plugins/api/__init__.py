"""HTTP control API plugin for Modulo BBS.

Starts a lightweight HTTP server (stdlib only) on a configurable port,
exposing health, session, and shutdown endpoints for external tooling.
No third-party frameworks — just ``http.server.ThreadingHTTPServer``.

Configuration (config.yaml)::

    api:
        enabled: true
        host: "127.0.0.1"
        port: 8080
        keys:
            - name: "admin"
              key: "your-secret-key-here"
"""
from __future__ import annotations

import logging
import threading
from http.server import ThreadingHTTPServer

from plugins.base import Plugin

logger = logging.getLogger("bbs.api")


class APIPlugin(Plugin):
    """Lightweight HTTP control API.

    Does not appear in the main menu — it is an infrastructure plugin
    started at load time when ``api.enabled`` is true in the config.
    """

    name = "api"
    version = "1.0.0"
    description = "HTTP control API (health, sessions, shutdown, broadcast)"
    menu_label = ""
    menu_key = ""
    menu_order = 0

    def __init__(self) -> None:
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def on_load(self, bbs) -> None:  # noqa: ARG002 — unused bbs in guard path
        api_cfg = (bbs.config or {}).get("api", {})
        if not api_cfg.get("enabled", False):
            logger.debug("API plugin: disabled (api.enabled not set)")
            return

        host: str = api_cfg.get("host", "127.0.0.1")
        port: int = api_cfg.get("port", 8080)
        keys: list[dict] = api_cfg.get("keys", [])

        from plugins.api.handler import BBSAPIHandler, set_bbs

        set_bbs(bbs, keys)

        try:
            self._httpd = ThreadingHTTPServer((host, port), BBSAPIHandler)
        except OSError as exc:
            logger.error("API server failed to bind %s:%s — %s", host, port, exc)
            return

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="bbs-api",
            daemon=True,
        )
        self._thread.start()
        logger.info("API server listening on %s:%s", host, port)

    def on_unload(self) -> None:
        if self._httpd is not None:
            logger.info("API server shutting down")
            self._httpd.shutdown()
            self._httpd = None
            self._thread = None
