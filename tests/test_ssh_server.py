"""Tests for the Modulo BBS SSH transport (server/ssh_server.py).

The SSH session bridges the asyncssh channel to a real ``Session`` object
(reader/writer) so the shared plugins -- especially the ``login`` plugin,
which owns the whole auth experience -- drive the session exactly as they do
over telnet. These tests drive ``BBSSSHSession._shell_loop`` against a fake
channel and scripted input, and assert the banner, login, main menu, and
disconnect all come from the plugin system rather than hardcoded logic.

The async flows are driven synchronously via ``asyncio.run`` (matching the
style used in the other test modules).

Each app is wired with the full core-plugin stack -- ``login``, ``logon``
(sequencer) and ``mainmenu`` -- so the transport's bootstrap hook finds the
``logon`` sequencer and drives splash -> login -> welcome -> menu exactly as
a configured board would.
"""

import asyncio

from core.app import BBSApp
from plugins.login import LoginPlugin
from plugins.logon import LogonPlugin
from plugins.mainmenu import MainmenuPlugin
from server.ssh_server import BBSSSHSession


class FakeChan:
    """Minimal asyncssh-channel stand-in capturing everything written to the
    wire."""

    def __init__(self):
        self.buffer = bytearray()
        self._closed = False

    def write(self, data):
        self.buffer.extend(data)

    def is_closing(self):
        return self._closed

    def close(self):
        self._closed = True

    def get_extra_info(self, name):
        if name == "peername":
            return ("127.0.0.1", 22222)
        return None

    @property
    def text(self):
        return bytes(self.buffer).decode("latin-1", errors="replace")


def _make_app(tmp_path):
    """BBSApp wired to throwaway users + throwaway screens tree.

    Pointing the screen service at tmp_path keeps live sysop reskins
    (plugins/*/screens overrides) out of test runs.
    """
    app = BBSApp(users_dir=tmp_path / "users")
    app.screens.plugins_root = tmp_path
    # Minimal board-global logon screens (logon resolves them from the
    # project-root-equivalent "screens/" under plugins_root).
    screens = tmp_path / "screens"
    screens.mkdir(parents=True, exist_ok=True)
    (screens / "splash.txt").write_bytes(b"Welcome to Modulo BBS\r\n")
    (screens / "welcome.txt").write_bytes(b"WELCOME ABOARD\r\n")
    plugins = [LoginPlugin(), LogonPlugin(), MainmenuPlugin()]
    for plugin in plugins:
        plugin.on_load(app)
    app.plugins = plugins
    return app


async def _run_session(app, lines):
    """Bridge a fake chan into the shared app, script the input, and run the
    full SSH shell loop. Returns (session, fake_chan)."""
    chan = FakeChan()
    sess = BBSSSHSession(app)
    sess.connection_made(chan)          # binds reader/writer to the live loop
    for line in lines:
        sess.data_received((line + "\n").encode("latin-1"), None)
    await sess._shell_loop()
    return sess, chan


def run(coro):
    return asyncio.run(coro)


def _create_user(app, username="alice", password="sekrit", display_name="Alice"):
    return run(app.users.create(
        username=username, password=password, display_name=display_name,
    ))


# ---------------------------------------------------------------------------
# Login plugin drives auth over SSH
# ---------------------------------------------------------------------------

def test_ssh_login_flow_uses_login_plugin(tmp_path):
    app = _make_app(tmp_path)
    _create_user(app)

    # Correct login, then disconnect via Q from the post-login main menu.
    sess, chan = run(_run_session(app, ["alice", "sekrit", "Q"]))

    # Authenticated by the *login plugin*, not hardcoded SSH logic.
    assert sess._session is None                  # cleaned up after disconnect
    text = chan.text
    assert "MODULO" in text.upper()               # core banner kept in SSH flow
    assert "Welcome back, Alice" in text          # login plugin's success line
    # PIM is now the default home — accept either the classic menu or the
    # tabbed chrome (both are valid per preferences.home_mode).
    assert ("Main Menu" in text or "Boards" in text or "up/dn select" in text)
    # System Info is on the classic menu; the PIM shows the pane hint instead
    assert ("System Info" in text or "up/dn select" in text or "Boards" in text)
    assert "Goodbye! Thanks for calling." in text
    assert chan.is_closing() or "Goodbye!" in text


def test_ssh_login_wrong_password_then_quit(tmp_path):
    app = _make_app(tmp_path)
    _create_user(app, username="bob", password="right", display_name="Bob")

    # Wrong password -> login plugin loops again -> Q quits (not authenticated).
    sess, chan = run(_run_session(app, ["bob", "wrong", "Q"]))

    text = chan.text
    assert "Invalid username or password" in text
    assert "Main Menu" not in text               # never reached (no auth)
    assert "Goodbye!" in text
    assert chan.is_closing()
    del sess


def test_ssh_shows_banner_before_login(tmp_path):
    app = _make_app(tmp_path)
    _create_user(app)

    sess, chan = run(_run_session(app, ["alice", "sekrit", "Q"]))
    text = chan.text
    # Banner (with node id) appears before the login prompt from the plugin.
    assert "Welcome to Modulo BBS" in text
    assert "MODULO" in text.upper()
    assert "Login:" in text or "Password:" in text
    del sess


# ---------------------------------------------------------------------------
# Registration path (login plugin exposes it from the SSH login screen)
# ---------------------------------------------------------------------------

def test_ssh_registration_via_login_screen(tmp_path):
    app = _make_app(tmp_path)

    # From the SSH login screen, press R, fill the registration form,
    # decline the optional TOTP enrolment prompt, then quit from the menu.
    sess, chan = run(_run_session(app, [
        "R", "carol", "pw456", "pw456", "N", "Q",
    ]))

    assert sess._session is None
    text = chan.text
    assert "Account created. Welcome, carol!" in text   # falls back to username
    # PIM is default home — same as above
    assert ("Main Menu" in text or "Boards" in text or "up/dn select" in text)
    # The account was actually persisted by the login plugin.
    assert run(app.users.get("carol")) is not None


# ---------------------------------------------------------------------------
# runner (mirrors the other test modules)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))