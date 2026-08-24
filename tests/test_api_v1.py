"""Integration tests for the One-API HTTP surface (plugins/api, /api/v1/...).

Covers:
* generic dispatch: auth, validation, permission mapping to status codes
* plane isolation: public listeners never expose sysop ops (schema or call)
* legacy endpoints still work
* parity invariant: every mgmt op is reachable from the terminal sysop menu
  mapping (registry <-> SysopPlugin letter map)
"""
from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import core.opdefs  # noqa: F401 - registers built-in operations
from core.events import EventBus
from core.ops import PLANE_MGMT, PLANE_PUBLIC, registry
from core.user import User, UserManager
from plugins.api import _make_handler_class
from plugins.api.handler import set_bbs


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSM:
    def __init__(self):
        self.sessions = {}
        self.max_nodes = 8

    @property
    def active_sessions(self):
        return []

    @property
    def active_count(self):
        return len(self.sessions)

    def get_all_sessions(self):
        return [s for s in self.sessions.values()]

    def get_session(self, sid):
        return self.sessions.get(sid)


class FakeServer:
    stop_called = False

    async def stop(self, message="bye"):
        self.stop_called = True
        self.message = message


class FakeBBS:
    def __init__(self, tmp_path):
        self.session_manager = FakeSM()
        self.users = UserManager(tmp_path / "users")
        self.server = FakeServer()
        self.plugins = []
        self.events = EventBus()
        self.config = {}
        self.sent = []

    async def send(self, session, text):
        self.sent.append(text)

    async def disconnect(self, session):
        self.session_manager.sessions.pop(getattr(session, "session_id", None), None)

    def get_plugin(self, name):
        return None


@pytest.fixture
def bbs(tmp_path):
    return FakeBBS(tmp_path)


@pytest.fixture(autouse=True)
def running_loop():
    """A real background event loop so run_coroutine_threadsafe works.

    In production the server's asyncio loop plays this role; tests need an
    equivalent pump or every dispatched op would hang forever.
    """
    global TEST_LOOP
    loop = asyncio.new_event_loop()
    TEST_LOOP = loop
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)
    loop.close()


TEST_LOOP = None


def start_server(bbs, *, mgmt=True):
    import plugins.api.handler as h

    set_bbs(bbs, keys=[], mgmt_plane=mgmt)
    h._loop = TEST_LOOP  # handler must dispatch onto our pump loop
    handler_cls = _make_handler_class(mgmt=mgmt)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]


def req(port, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    r = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=hdrs, method=method
    )
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


async def make_sysop(bbs):
    await bbs.users.create("dave", "hunter2", groups=["sysop"])


def login(bbs_port_unused, port, username="dave", password="hunter2"):
    body, status = req(port, "POST", "/api/v1/auth.login",
                       {"username": username, "password": password})
    assert status == 200, body
    return body["token"]


# ---------------------------------------------------------------------------
# Dispatch + auth
# ---------------------------------------------------------------------------

class TestV1Dispatch:
    def test_health_open_on_mgmt(self, bbs):
        httpd, port = start_server(bbs)
        try:
            body, status = req(port, "GET", "/api/v1/system.health")
            assert status == 200 and body["status"] == "running"
        finally:
            httpd.shutdown()

    def test_login_and_authenticated_call(self, bbs):
        import asyncio

        asyncio.run(make_sysop(bbs))
        httpd, port = start_server(bbs)
        try:
            token = login(0, port)
            body, status = req(port, "POST", "/api/v1/users.list", {},
                               headers={"Authorization": f"Bearer {token}"})
            assert status == 200
            names = [u["username"] for u in body["users"]]
            assert "dave" in names
            # sanitized: no password hashes anywhere
            assert all("password_hash" not in u for u in body["users"])
        finally:
            httpd.shutdown()

    def test_bad_credentials_rejected_403(self, bbs):
        import asyncio

        asyncio.run(make_sysop(bbs))
        httpd, port = start_server(bbs)
        try:
            body, status = req(port, "POST", "/api/v1/auth.login",
                               {"username": "dave", "password": "wrong"})
            assert status == 403
        finally:
            httpd.shutdown()

    def test_gated_op_without_token_403(self, bbs):
        httpd, port = start_server(bbs)
        try:
            body, status = req(port, "POST", "/api/v1/users.list", {})
            assert status == 403
            assert "error" in body
        finally:
            httpd.shutdown()

    def test_validation_error_400(self, bbs):
        import asyncio

        asyncio.run(make_sysop(bbs))
        httpd, port = start_server(bbs)
        try:
            token = login(0, port)
            # Authenticated sysop, but missing the required 'message' param.
            body, status = req(port, "POST", "/api/v1/system.broadcast", {},
                               headers={"Authorization": f"Bearer {token}"})
            assert status == 400
            assert "missing required" in body["error"]
        finally:
            httpd.shutdown()

    def test_unknown_param_400(self, bbs):
        httpd, port = start_server(bbs)
        try:
            body, status = req(port, "POST", "/api/v1/system.health", {"evil": True})
            assert status == 400
            assert "unknown" in body["error"]
        finally:
            httpd.shutdown()

    def test_unknown_operation_404(self, bbs):
        httpd, port = start_server(bbs)
        try:
            body, status = req(port, "POST", "/api/v1/no.suchthing", {})
            assert status == 404
        finally:
            httpd.shutdown()

    def test_shutdown_via_registry(self, bbs):
        import asyncio

        asyncio.run(make_sysop(bbs))
        httpd, port = start_server(bbs)
        try:
            token = login(0, port)
            body, status = req(port, "POST", "/api/v1/system.shutdown",
                               {"message": "test bye"},
                               headers={"Authorization": f"Bearer {token}"})
            assert status == 200 and body["status"] == "shutting_down"
            import time

            deadline = time.time() + 3
            while not bbs.server.stop_called and time.time() < deadline:
                time.sleep(0.05)
            assert bbs.server.stop_called
            assert bbs.server.message == "test bye"
        finally:
            httpd.shutdown()

    def test_shutdown_denied_without_token(self, bbs):
        httpd, port = start_server(bbs)
        try:
            body, status = req(port, "POST", "/api/v1/system.shutdown", {})
            assert status == 403
            assert not bbs.server.stop_called
        finally:
            httpd.shutdown()


# ---------------------------------------------------------------------------
# Plane isolation — the hard invariant
# ---------------------------------------------------------------------------

class TestPlaneIsolation:
    def test_public_schema_hides_sysop_ops(self, bbs):
        httpd, port = start_server(bbs, mgmt=False)
        try:
            body, status = req(port, "GET", "/api/v1/_schema")
            assert status == 200
            names = [o["name"] for o in body["operations"]]
            assert "sessions.kick" not in names      # sysop-gated
            assert "users.create" not in names       # sysop-gated
            assert "users.delete" not in names       # sysop-gated
            assert body["plane"] == "public"
        finally:
            httpd.shutdown()

    def test_public_call_to_sysop_op_is_invisible_404(self, bbs):
        httpd, port = start_server(bbs, mgmt=False)
        try:
            # Even with valid credentials this must be 404 (not 403): the
            # outside world cannot discover management capability.
            import asyncio

            asyncio.run(make_sysop(bbs))
            token = login(0, port)
            body, status = req(port, "POST", "/api/v1/sessions.kick",
                               {"session_id": "x"},
                               headers={"Authorization": f"Bearer {token}"})
            assert status == 404
        finally:
            httpd.shutdown()

    def test_mgmt_schema_shows_everything_public(self, bbs):
        httpd, port = start_server(bbs, mgmt=True)
        try:
            body, _ = req(port, "GET", "/api/v1/_schema")
            names = {o["name"] for o in body["operations"]}
            assert "sessions.kick" in names
            assert "users.delete" in names
            assert "boards.post" in names
        finally:
            httpd.shutdown()

    def test_registry_never_allows_sysop_on_public_plane(self):
        from core.ops import OpsRegistry

        reg = OpsRegistry()
        with pytest.raises(ValueError, match="public plane"):
            reg.register("x.y", requires=["sysop"],
                         planes=("mgmt", "public"), handler=lambda b, u, p: 1)


# ---------------------------------------------------------------------------
# Parity invariant — registry ops reachable from the terminal sysop menu
# ---------------------------------------------------------------------------

class TestTerminalParity:
    def test_every_menu_letter_maps_to_real_registry_op(self, bbs):
        from plugins.sysop import SysopPlugin

        plugin = SysopPlugin()
        plugin.on_load(bbs)
        # The render builds the letter map; exercise it.
        class S:
            terminal_width = 80

        plugin._render(S())
        assert plugin._letter_map, "menu must expose at least one op"
        for ch, op_name in plugin._letter_map.items():
            op = registry.get(op_name)
            assert op is not None, f"letter {ch} maps to unknown op {op_name}"
            assert "mgmt" in op.planes

    def test_terminal_menu_lists_core_sysop_ops(self, bbs):
        from plugins.sysop import SysopPlugin

        plugin = SysopPlugin()
        plugin.on_load(bbs)

        class S:
            terminal_width = 80

        rendered = plugin._render(S())
        for expected in ("users.list", "users.create", "users.delete",
                         "sessions.kick", "system.broadcast"):
            assert expected in rendered, f"{expected} missing from sysop menu"


# ---------------------------------------------------------------------------
# Legacy endpoints keep working
# ---------------------------------------------------------------------------

class TestLegacyEndpoints:
    def test_legacy_health_still_works(self, bbs):
        httpd, port = start_server(bbs)
        try:
            body, status = req(port, "GET", "/api/health")
            assert status == 200 and body["status"] == "running"
        finally:
            httpd.shutdown()

    def test_legacy_broadcast_still_works(self, bbs):
        import asyncio

        asyncio.run(make_sysop(bbs))
        httpd, port = start_server(bbs)
        try:
            token = login(0, port)
            body, status = req(port, "POST", "/api/broadcast", {"message": "hi"},
                               headers={"X-API-Key": ""})
            assert status == 200 and body["sent"] == 0
        finally:
            httpd.shutdown()


# ---------------------------------------------------------------------------
# Static dashboard
# ---------------------------------------------------------------------------

class TestAdminFiles:
    def test_index_served(self, bbs):
        httpd, port = start_server(bbs)
        try:
            r = urllib.request.Request(f"http://127.0.0.1:{port}/admin/")
            with urllib.request.urlopen(r, timeout=5) as resp:
                html = resp.read().decode()
                assert resp.headers["Content-Type"].startswith("text/html")
                assert "MODULO" in html
        finally:
            httpd.shutdown()

    def test_traversal_blocked(self, bbs):
        httpd, port = start_server(bbs)
        try:
            r = urllib.request.Request(f"http://127.0.0.1:{port}/admin/../handler.py")
            try:
                urllib.request.urlopen(r, timeout=5)
                raise AssertionError("traversal should have been blocked")
            except urllib.error.HTTPError as e:
                assert e.code == 404
        finally:
            httpd.shutdown()


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------

class TestTokens:
    def test_logout_revokes_token(self, bbs):
        import asyncio

        asyncio.run(make_sysop(bbs))
        httpd, port = start_server(bbs)
        try:
            token = login(0, port)
            body, status = req(port, "POST", "/api/v1/auth.logout", {"token": token})
            assert status == 200
            body, status = req(port, "POST", "/api/v1/users.list", {},
                               headers={"Authorization": f"Bearer {token}"})
            assert status == 403  # revoked
        finally:
            httpd.shutdown()
