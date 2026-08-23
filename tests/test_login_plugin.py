"""Tests for the Modulo BBS login plugin.

Covers the plugin contract (metadata, on_load), the interactive login flow,
registration flow, and TOTP manager/setup/verify. All flows run against a
fake in-memory session + a throwaway users/totp directory, so the real
``plugins/login/data/`` and root ``users/`` directories are never touched.

The async flows are driven synchronously via ``asyncio.run`` so no additional
async test plugin is required -- matching the style used in tests/test_user.py.
"""

import asyncio

import pyotp

from core import EventBus, UserExistsError, UserManager
from plugins.base import Plugin
from plugins.login import LoginPlugin
from plugins.login.login import LoginFlow, Terminal, ScreenLoader
from plugins.login.registration import RegistrationFlow
from plugins.login.totp import TOTPFlow, TOTPManager
from server.session import Session


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeWriter:
    """Minimal stream writer capturing everything written to the wire."""

    def __init__(self):
        self.buffer = bytearray()

    def write(self, data):
        self.buffer.extend(data)

    async def drain(self):
        pass

    def is_closing(self):
        return False


class ScriptReader:
    """Session reader that returns a scripted sequence of CRLF-terminated lines.

    Returning ``b""`` signals EOF (disconnect), which the flows treat as a
    clean end -- so a script that runs out of input never hangs a test.
    """

    def __init__(self, inputs):
        self.inputs = list(inputs)
        self.line_calls = 0

    async def readline(self):
        if not self.inputs:
            return b""                       # EOF
        self.line_calls += 1
        return self.inputs.pop(0).encode("latin-1", errors="replace")

    async def read(self, n=-1):
        """read_command-compatible chunked read: one line + CR terminator."""
        if not self.inputs:
            return b""                       # EOF
        self.line_calls += 1
        return self.inputs.pop(0).encode("latin-1", errors="replace") + b"\r"


class FakeBBS:
    """Minimal core object exposing what the plugin consumes: ``events``,
    ``users`` and ``send``."""

    def __init__(self, users_dir):
        self.events = EventBus()
        self.users = UserManager(users_dir)
        self.sent = []

    async def send(self, session, text):
        self.sent.append(text)
        writer = getattr(session, "writer", None)
        if writer is not None:
            writer.write(text.encode("latin-1", errors="replace"))
            await writer.drain()


def make_session(inputs=None):
    return Session(
        session_id="test1",
        node_id=1,
        address=("127.0.0.1", 0),
        reader=ScriptReader(inputs or []),
        writer=FakeWriter(),
    )


def capture(bus, event):
    """Subscribe a handler to ``event``; returns the list of fired payloads."""
    hits = []

    async def handler(data):
        hits.append(data)

    bus.on(event, handler)
    return hits


def run(coro):
    return asyncio.run(coro)


def run_command_in_loop(fn, *args):
    """Invoke a synchronous ``fn`` inside a live event loop.

    Needed for synchronous hooks like ``handle_command`` that emit events:
    ``EventBus.emit`` schedules handler tasks on the running loop, so they only
    execute while a loop is active.
    """

    async def _runner():
        result = fn(*args)
        await asyncio.sleep(0.05)     # let emitted background handlers run
        return result

    return asyncio.run(_runner())


# ---------------------------------------------------------------------------
# Plugin <-> screen plumbing helpers
# ---------------------------------------------------------------------------

def _make_env(tmp_path):
    """Return (bbs, totp, screens) wired to throwaway paths."""
    bbs = FakeBBS(tmp_path / "users")
    totp = TOTPManager(tmp_path / "totp_secrets.json")
    screens = ScreenLoader(bbs)
    return bbs, totp, screens


def _precreate(bbs, username="alice", password="sekrit"):
    return run(bbs.users.create(
        username=username, password=password, display_name="Alice",
    ))


# ---------------------------------------------------------------------------
# Plugin contract
# ---------------------------------------------------------------------------

def test_login_plugin_is_a_plugin_and_has_metadata():
    p = LoginPlugin()
    assert isinstance(p, LoginPlugin)
    assert isinstance(p, Plugin)
    assert p.name == "login"
    assert p.version == "1.0.0"
    assert p.description
    assert isinstance(p.menu_order, int)


def test_on_load_stores_bbs_reference(tmp_path):
    bbs = FakeBBS(tmp_path / "users")
    p = LoginPlugin()
    assert p.bbs is None
    p.on_load(bbs)
    assert p.bbs is bbs


def test_logout_command_emits_user_logout(tmp_path):
    bbs, totp, _ = _make_env(tmp_path)
    user = _precreate(bbs)
    p = LoginPlugin()
    p.on_load(bbs)
    hits = capture(bbs.events, "user:logout")

    s = make_session()
    s.user = user
    s.username = user.username
    s.authenticated = True

    # handle_command is synchronous, but it emits events that are scheduled
    # on the running loop -- so invoke it inside a live event loop.
    result = run_command_in_loop(p.handle_command, s, "Q")
    assert result is False                      # return to menu
    assert s.authenticated is False
    assert s.user is None
    assert len(hits) == 1
    assert hits[0]["user"].username == "alice"


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

def test_login_flow_success(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    _precreate(bbs)
    s = make_session(inputs=["alice", "sekrit"])
    logins = capture(bbs.events, "user:login")

    result = run(LoginFlow(bbs, totp, screens).run(s))
    assert result is True
    assert s.authenticated is True
    assert s.user is not None and s.user.username == "alice"
    assert s.username == "alice"
    # Success fired user:login exactly once.
    assert len(logins) == 1
    assert logins[0]["session"] is s
    assert logins[0]["user"].username == "alice"
    # Credentials were prompted in order.
    prompts = "".join(bbs.sent)
    assert "Username" not in prompts          # login prompts are bare
    got = [x for x in bbs.sent if "Password:" in x]
    assert got


def test_login_flow_wrong_password_then_quit(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    _precreate(bbs)
    # wrong password -> loop back to login -> Q quits to the menu
    s = make_session(inputs=["alice", "wrong", "Q"])
    failed = capture(bbs.events, "auth:login_failed")

    result = run(LoginFlow(bbs, totp, screens).run(s))
    assert result is False                     # user quit -> return to menu
    assert s.authenticated is False
    assert s.user is None
    assert len(failed) == 1
    assert failed[0]["username"] == "alice"
    assert "Invalid" in "".join(bbs.sent)


def test_login_flow_unknown_user_fails(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    s = make_session(inputs=["ghost", "sekrit", "Q"])
    failed = capture(bbs.events, "auth:login_failed")

    result = run(LoginFlow(bbs, totp, screens).run(s))
    assert result is False
    assert len(failed) == 1
    assert failed[0]["username"] == "ghost"
    assert s.authenticated is False


def test_login_flow_register_shortcut(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    # From the login screen, press R to register, fill the form (no TOTP).
    s = make_session(inputs=[
        "R", "bob", "pw123", "pw123", "n",
    ])
    registered = capture(bbs.events, "auth:register")

    result = run(LoginFlow(bbs, totp, screens).run(s))
    assert result is True                     # registered -> auto-login
    assert s.authenticated is True
    assert s.user.username == "bob"
    assert len(registered) == 1
    assert registered[0]["user"].username == "bob"


def test_login_flow_q_returns_to_menu(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    s = make_session(inputs=["Q"])
    result = run(LoginFlow(bbs, totp, screens).run(s))
    assert result is False
    assert s.authenticated is False


# ---------------------------------------------------------------------------
# Registration flow
# ---------------------------------------------------------------------------

def test_registration_creates_user_and_emits_event(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    s = make_session(inputs=["carol", "pw456", "pw456", "n"])
    registered = capture(bbs.events, "auth:register")

    result = run(RegistrationFlow(bbs, totp, screens).run(s))
    assert result is True
    assert s.authenticated is True
    assert s.user.username == "carol"
    assert len(registered) == 1

    # Persisted for real (verify via the manager, not just session state).
    fetched = run(bbs.users.get("carol"))
    assert fetched is not None
    assert fetched.display_name == ""          # no longer collected at signup
    assert fetched.location == ""              # ditto
    assert fetched.email == ""                 # ditto
    assert fetched.shown_name() == "carol"      # falls back to username
    assert fetched.verify_password("pw456")
    assert not fetched.verify_password("nope")


def test_registration_password_mismatch_retries(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    # mismatched confirm -> retry -> success on second attempt
    s = make_session(inputs=["dave", "pw1", "pw2", "dave", "pw1", "pw1", "n"])
    registered = capture(bbs.events, "auth:register")

    result = run(RegistrationFlow(bbs, totp, screens).run(s))
    assert result is True
    assert len(registered) == 1
    assert run(bbs.users.get("dave")) is not None


def test_registration_rejects_duplicate_username(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    _precreate(bbs)                            # alice already exists
    # first attempt fills a full form for "alice" (rejected because it exists),
    # then a second full form for "erin" succeeds.
    s = make_session(inputs=[
        "alice", "x1", "x1",
        "erin", "x1", "x1", "n",
    ])
    registered = capture(bbs.events, "auth:register")

    result = run(RegistrationFlow(bbs, totp, screens).run(s))
    assert result is True
    assert registered[0]["user"].username == "erin"
    assert "already taken" in "".join(bbs.sent).lower()


def test_registration_validate_username(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    # uppercase / bad characters are rejected (full form consumed), then a
    # valid one ("frank") succeeds.
    s = make_session(inputs=[
        "Bad Name!", "x", "x",
        "frank", "x", "x", "n",
    ])
    registered = capture(bbs.events, "auth:register")
    result = run(RegistrationFlow(bbs, totp, screens).run(s))
    assert result is True
    assert registered[0]["user"].username == "frank"
    assert "invalid" in "".join(bbs.sent).lower()


# ---------------------------------------------------------------------------
# TOTP: manager
# ---------------------------------------------------------------------------

def test_totp_manager_roundtrip(tmp_path):
    m = TOTPManager(tmp_path / "totp.json")
    secret = "JBSWY3DPEHPK3PXP"
    m.set_secret("alice", secret)
    assert m.has_secret("alice")
    assert m.get_secret("alice") == secret

    totp = pyotp.TOTP(secret)
    assert m.verify("alice", totp.now())
    assert not m.verify("alice", "000000")
    assert not m.verify("nobody", totp.now())
    assert not m.verify("alice", "")


def test_totp_manager_persists_and_reloads(tmp_path):
    path = tmp_path / "totp.json"
    m1 = TOTPManager(path)
    m1.set_secret("alice", "JBSWY3DPEHPK3PXP")
    m2 = TOTPManager(path)                     # fresh instance reloads from disk
    assert m2.has_secret("alice")
    assert m2.verify("alice", pyotp.TOTP("JBSWY3DPEHPK3PXP").now())

    assert m2.remove_secret("alice") is True
    assert not m2.has_secret("alice")


# ---------------------------------------------------------------------------
# TOTP: setup / verify flow
# ---------------------------------------------------------------------------

def test_totp_setup_with_valid_code(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    user = _precreate(bbs)
    secret = "JBSWY3DPEHPK3PXP"
    code = pyotp.TOTP(secret).now()
    s = make_session(inputs=[code])

    ok = run(TOTPFlow(bbs, totp, screens).setup(s, user, secret=secret))
    assert ok is True
    assert totp.has_secret("alice")
    assert totp.get_secret("alice") == secret
    assert "Two-factor authentication enabled" in "".join(bbs.sent)


def test_totp_setup_with_bad_code_does_not_store(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    user = _precreate(bbs)
    s = make_session(inputs=["000000"])

    ok = run(TOTPFlow(bbs, totp, screens).setup(s, user, secret="JBSWY3DPEHPK3PXP"))
    assert ok is False
    assert not totp.has_secret("alice")


def test_totp_setup_generates_secret_and_verifies(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    user = _precreate(bbs)
    # Use plugin-level setup with a generated secret computed in advance.
    p = LoginPlugin()
    p.on_load(bbs)
    p.totp = totp
    secret = "JBSWY3DPEHPK3PXP"
    code = pyotp.TOTP(secret).now()
    s = make_session(inputs=[code])

    ok = run(p.setup_totp(s, user, secret=secret))
    assert ok is True
    assert totp.has_secret("alice")
    # The rendered screen exposes the secret for manual entry.
    rendered = "".join(bbs.sent)
    assert "JBSWY3DPEHPK3PXP" in rendered


def test_login_flow_requires_totp_when_enrolled(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    user = _precreate(bbs)
    secret = "JBSWY3DPEHPK3PXP"
    totp.set_secret(user.username, secret)
    good_code = pyotp.TOTP(secret).now()

    # Correct password but TOTP enforced: bad code rejects, good code allows.
    s = make_session(inputs=["alice", "sekrit", good_code])
    logins = capture(bbs.events, "user:login")
    result = run(LoginFlow(bbs, totp, screens).run(s))
    assert result is True
    assert s.authenticated is True
    assert len(logins) == 1


def test_login_flow_totp_bad_code_denies(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    user = _precreate(bbs)
    totp.set_secret(user.username, "JBSWY3DPEHPK3PXP")
    s = make_session(inputs=["alice", "sekrit", "000000", "Q"])
    failed = capture(bbs.events, "auth:login_failed")
    result = run(LoginFlow(bbs, totp, screens).run(s))
    assert result is False
    assert s.authenticated is False
    assert failed[-1]["reason"] == "Invalid TOTP code"


# ---------------------------------------------------------------------------
# Terminal / EOF safety
# ---------------------------------------------------------------------------

def test_read_line_eof_returns_empty(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    s = make_session(inputs=[])               # immediate EOF
    tty = Terminal(bbs, s)
    assert run(tty.read_line("Login: ")) == ""


def test_screen_loader_substitutes_ansi_and_placeholders(tmp_path):
    bbs, totp, screens = _make_env(tmp_path)
    text = screens.render("login.txt")
    assert "\x1b[" in text                 # ANSI escape injected
    from shared.telnet_protocol import ANSI as A
    assert A.CLEAR_SCREEN in text

    setup = screens.render("totp_setup.txt", SECRET="ABC", USERNAME="dave")
    assert "ABC" in setup
    assert "otpauth://totp/ModuloBBS:dave?secret=ABC" in setup


# ---------------------------------------------------------------------------
# main / runner (mirrors the other test modules)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))