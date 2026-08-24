"""Tests for the HTTP API plugin (legacy surface + lifecycle)."""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest

from plugins.api.handler import BBSAPIHandler, set_bbs

TEST_LOOP = None


@pytest.fixture(autouse=True)
def _pump_loop():
    """Legacy shutdown/broadcast now dispatch through the ops registry,
    which needs a running event loop to schedule onto."""
    global TEST_LOOP
    loop = asyncio.new_event_loop()
    TEST_LOOP = loop
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)
    loop.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakePlugin:
    """Minimal plugin stub with a .name attribute."""
    def __init__(self, name: str):
        self.name = name


class _FakeSessionManager:
    """Minimal session manager for testing."""

    def __init__(self):
        self.sessions: dict = {}
        self.max_nodes = 8

    @property
    def active_sessions(self):
        return list(self.sessions.values())

    @property
    def active_count(self):
        return len(self.sessions)

    def get_all_sessions(self):
        return [{"session_id": s["session_id"], "node": s["node"]} for s in self.sessions.values()]


class _FakeServer:
    """Mock transport server with an async stop()."""

    def __init__(self):
        self.stop_called = False
        self.stop_message = None

    async def stop(self, message="BBS shutting down. Goodbye!"):
        self.stop_called = True
        self.stop_message = message


class _FakeBBS:
    """Minimal BBS application object for API tests."""

    def __init__(self):
        self.session_manager = _FakeSessionManager()
        self.server = _FakeServer()
        self.plugins = [_FakePlugin("login"), _FakePlugin("mainmenu")]
        self.config = {}
        self.sent: list[tuple] = []
        # Legacy shutdown/broadcast dispatch through the ops registry, which
        # emits audit events on the bus.
        from core.events import EventBus

        self.events = EventBus()

    async def send(self, session, text):
        self.sent.append((session, text))


@pytest.fixture
def bbs():
    return _FakeBBS()


@pytest.fixture
def api_server(bbs):
    """Start a ThreadingHTTPServer on a free port for testing."""
    import plugins.api.handler as h

    set_bbs(bbs, keys=[])
    h._loop = TEST_LOOP
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), BBSAPIHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, port, bbs
    httpd.shutdown()
    h._bbs = None
    h._api_keys = set()
    h._loop = None


def _get(port, path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read()), resp.status


def _get_err(port, path, headers=None):
    """GET that expects a non-2xx response. Returns (body, status_code)."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read()), exc.code


def _post(port, path, data=None, headers=None):
    body = json.dumps(data or {}).encode()
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, headers=hdrs, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read()), resp.status


def _post_err(port, path, data=None, headers=None):
    """POST that expects a non-2xx response. Returns (body, status_code)."""
    body = json.dumps(data or {}).encode()
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, headers=hdrs, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read()), exc.code


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_running(self, api_server):
        httpd, port, bbs = api_server
        data, status = _get(port, "/api/health")
        assert status == 200
        assert data["status"] == "running"
        assert data["name"] == "Modulo BBS"
        assert "version" in data
        assert data["nodes"]["active"] == 0
        assert data["nodes"]["max"] == 8
        assert isinstance(data["plugins"], list)

    def test_includes_loaded_plugins(self, api_server):
        httpd, port, bbs = api_server
        data, _ = _get(port, "/api/health")
        names = data["plugins"]
        assert "login" in names
        assert "mainmenu" in names


# ---------------------------------------------------------------------------
# Sessions endpoint
# ---------------------------------------------------------------------------

class TestSessions:
    def test_empty_sessions(self, api_server):
        httpd, port, bbs = api_server
        data, status = _get(port, "/api/sessions")
        assert status == 200
        assert data["sessions"] == []
        assert data["count"] == 0

    def test_with_active_sessions(self, api_server):
        httpd, port, bbs = api_server
        bbs.session_manager.sessions["s1"] = {"session_id": "s1", "node": 1}
        bbs.session_manager.sessions["s2"] = {"session_id": "s2", "node": 2}
        data, _ = _get(port, "/api/sessions")
        assert data["count"] == 2
        assert len(data["sessions"]) == 2


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_no_keys_configured_allows_access(self, api_server):
        httpd, port, bbs = api_server
        data, status = _get(port, "/api/health")
        assert status == 200

    def test_valid_key_accepted(self):
        bbs = _FakeBBS()
        set_bbs(bbs, keys=[{"name": "test", "key": "secret-123"}])
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), BBSAPIHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            data, status = _get(port, "/api/health", headers={"X-API-Key": "secret-123"})
            assert status == 200
        finally:
            httpd.shutdown()
            import plugins.api.handler as h
            h._bbs = None
            h._api_keys = set()
            h._loop = None

    def test_invalid_key_rejected(self):
        bbs = _FakeBBS()
        set_bbs(bbs, keys=[{"name": "test", "key": "secret-123"}])
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), BBSAPIHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            data, status = _get_err(port, "/api/health", headers={"X-API-Key": "wrong"})
            assert status == 401
            assert "error" in data
        finally:
            httpd.shutdown()
            import plugins.api.handler as h
            h._bbs = None
            h._api_keys = set()
            h._loop = None

    def test_missing_key_rejected(self):
        bbs = _FakeBBS()
        set_bbs(bbs, keys=[{"name": "test", "key": "secret-123"}])
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), BBSAPIHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            data, status = _get_err(port, "/api/health")
            assert status == 401
        finally:
            httpd.shutdown()
            import plugins.api.handler as h
            h._bbs = None
            h._api_keys = set()
            h._loop = None


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

class TestBroadcast:
    def test_broadcast_sends_to_all_sessions(self, api_server):
        httpd, port, bbs = api_server
        data, status = _post(port, "/api/broadcast", {"message": "Hello everyone"})
        assert status == 200
        assert data["sent"] == 0  # no active sessions
        # Allow the registry's audit-event task to run; nothing should explode.
        time.sleep(0.1)

    def test_broadcast_empty_message_rejected(self, api_server):
        httpd, port, bbs = api_server
        data, status = _post_err(port, "/api/broadcast", {"message": ""})
        assert status == 400


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

class TestShutdown:
    def test_shutdown_returns_shutting_down(self, api_server):
        httpd, port, bbs = api_server
        data, status = _post(port, "/api/shutdown")
        assert status == 200
        assert data["status"] == "shutting_down"

    def test_shutdown_with_custom_message(self, api_server):
        httpd, port, bbs = api_server
        data, _ = _post(port, "/api/shutdown", {"message": "Maintenance break"})
        assert data["message"] == "Maintenance break"
        # The async shutdown task is scheduled on the event loop but
        # won't complete here (no running loop in the test thread).
        # Verify the response is correct; the actual stop() call is
        # tested via integration / manual verification.


# ---------------------------------------------------------------------------
# 404 and routing
# ---------------------------------------------------------------------------

class TestRouting:
    def test_get_unknown_route(self, api_server):
        httpd, port, bbs = api_server
        data, status = _get_err(port, "/api/unknown")
        assert status == 404

    def test_post_unknown_route(self, api_server):
        httpd, port, bbs = api_server
        data, status = _post_err(port, "/api/unknown")
        assert status == 404


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------

class TestPluginLifecycle:
    def test_disabled_plugin_loads_without_server(self):
        from plugins.api import APIPlugin

        bbs = _FakeBBS()
        bbs.config = {"api": {"enabled": False}}
        plugin = APIPlugin()
        plugin.on_load(bbs)
        assert plugin._httpds == []

    def test_enabled_plugin_starts_server(self):
        from plugins.api import APIPlugin

        bbs = _FakeBBS()
        bbs.config = {"api": {"enabled": True, "port": 0, "host": "127.0.0.1"}}
        plugin = APIPlugin()
        plugin.on_load(bbs)
        assert len(plugin._httpds) == 1
        assert plugin._threads
        plugin.on_unload()
        assert plugin._httpds == []

    def test_bind_failure_logged_not_raised(self):
        """Port already in use → plugin loads but no listener is created."""
        from http.server import ThreadingHTTPServer as RealHTTPServer
        from plugins.api import APIPlugin

        bbs = _FakeBBS()
        bbs.config = {"api": {"enabled": True, "port": 19999, "host": "127.0.0.1"}}

        # Patch the constructor to simulate a bind failure.
        orig_init = RealHTTPServer.__init__
        def _fail_init(self, *a, **kw):
            raise OSError("Address already in use")
        RealHTTPServer.__init__ = _fail_init
        try:
            plugin = APIPlugin()
            plugin.on_load(bbs)  # should not raise
            assert plugin._httpds == []
        finally:
            RealHTTPServer.__init__ = orig_init
