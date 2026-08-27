"""Tests for core/slash.py — /screen and the shared slash dispatcher."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.app import BBSApp
from server.session import Session


@pytest.fixture
def app(tmp_path):
    a = BBSApp(users_dir=tmp_path / "users")
    a.screens.plugins_root = tmp_path
    return a


class FakeWriter:
    def __init__(self):
        self.buffer = bytearray()

    def write(self, d):
        self.buffer.extend(d)

    async def drain(self):
        pass

    def is_closing(self):
        return False


def make_session():
    s = Session(session_id="t", node_id=1, address=("h", 1))
    s.writer = FakeWriter()
    return s


async def _real_user(app, username="tester", groups=("sysop",), **extra):
    """Create + return a real persisted user so users.update() works."""
    await app.users.create(username, password="pw-test-123")
    if groups != ("user",) or extra:
        await app.users.update(
            username,
            groups=list(groups),
            **{"preferences": extra.pop("preferences")} if "preferences" in extra else {},
        )
    return await app.users.get(username)


def run(coro):
    return asyncio.run(coro)


def text_of(session) -> str:
    return bytes(session.writer.buffer).decode("utf-8", errors="replace")


class TestScreenCommand:
    def test_screen_oneshot_shows_generated(self, app):
        app.screens.register_generator("mainmenu", "main", lambda session=None: "MENU BODY")
        s = make_session()

        from core.slash import handle_slash

        async def scenario():
            await handle_slash(app, s, "/screen mainmenu main")

        run(scenario())
        out = text_of(s)
        assert "MENU BODY" in out
        assert "generated defaults" in out

    def test_toggle_persists_and_bypasses_override(self, app, tmp_path):
        """/screen flips the preference; render() then serves generated."""
        d = tmp_path / "plugins" / "mainmenu" / "screens"
        d.mkdir(parents=True)
        (d / "main.txt").write_bytes(b"FANCY SKIN")

        from plugins.mainmenu import MainmenuPlugin

        MainmenuPlugin().on_load(app)

        # File wins by default.
        assert "FANCY SKIN" in app.screens.render(None, "mainmenu", "main")
        assert "FANCY SKIN" in app.screens.render(None, "mainmenu", "main")

        sysop = make_session()
        sysop.user = run(_real_user(app, "tester", ["sysop"]))
        run(_drive(app, sysop, "/screen"))  # toggle ON

        out = text_of(sysop)
        assert "Machine view ON" in out
        # Now render() bypasses the file for this user.
        rendered = app.screens.render(sysop, "mainmenu", "main")
        assert "FANCY SKIN" not in rendered
        assert "Main Menu" in rendered

        # Toggle OFF -> skin returns.
        sysop2 = make_session()
        sysop2.user = run(
            _real_user(app, "tester2", ["sysop"], preferences={"screen_mode": "generated"})
        )
        run(_drive(app, sysop2, "/screen"))
        assert "Machine view OFF" in text_of(sysop2)
        assert "FANCY SKIN" in app.screens.render(sysop2, "mainmenu", "main")

    def test_regular_user_can_toggle_too(self, app, tmp_path):
        """/screen is a general preference: plain output for anyone.

        A non-sysop toggling ON gets the same generated menu minus the
        commands their groups don't grant — nothing exposed, nothing hidden.
        """
        d = tmp_path / "plugins" / "mainmenu" / "screens"
        d.mkdir(parents=True)
        (d / "main.txt").write_bytes(b"FANCY SKIN")

        from plugins.mainmenu import MainmenuPlugin

        MainmenuPlugin().on_load(app)

        pleb = make_session()
        pleb.user = run(_real_user(app, "plain_jane", ["user"]))
        run(_drive(app, pleb, "/screen"))  # toggle ON

        out = text_of(pleb)
        assert "Machine view ON" in out

        rendered = app.screens.render(pleb, "mainmenu", "main")
        assert "FANCY SKIN" not in rendered          # skin bypassed
        assert "Main Menu" in rendered               # generated served
        assert "[I] System Info" in rendered         # theirs to use

    def test_screen_filters_by_permission(self, app, tmp_path):
        """Sysop sees [X]; regular user does not."""
        from plugins.mainmenu import MainmenuPlugin

        plugin = MainmenuPlugin()
        plugin.on_load(app)

        sysop = make_session()
        sysop.user = run(_real_user(app, "tester3", ["sysop"]))
        run(_drive(app, sysop, "/screen mainmenu main"))
        assert "[X] Shutdown" in text_of(sysop)

        pleb = make_session()
        pleb.user = run(_real_user(app, "pleb", ["user"]))
        run(_drive(app, pleb, "/screen mainmenu main"))
        assert "[X] Shutdown" not in text_of(pleb)

    def test_screen_beats_file_override(self, app, tmp_path):
        """Even with an override file installed, /screen shows generated."""
        d = tmp_path / "plugins" / "mainmenu" / "screens"
        d.mkdir(parents=True)
        (d / "main.txt").write_bytes(b"FANCY SKIN")

        from plugins.mainmenu import MainmenuPlugin

        MainmenuPlugin().on_load(app)

        # Normal render -> file wins.
        assert "FANCY SKIN" in app.screens.render(None, "mainmenu", "main")
        # /screen -> generated wins.
        s = make_session()
        run(_drive(app, s, "/screen mainmenu main"))
        assert "FANCY SKIN" not in text_of(s)

    def test_unknown_slash_gets_hint(self, app):
        s = make_session()
        run(_drive(app, s, "/bogus"))
        assert "Unknown command" in text_of(s)


class TestThemeCommand:
    def test_list_marks_current(self, app):
        s = make_session()
        s.user = run(_real_user(app, "themer", ["user"]))
        run(_drive(app, s, "/theme"))
        out = text_of(s)
        assert "classic *" in out
        assert "amber" in out
        assert "green" in out
        assert "magenta" in out
        assert "matrix" in out
        assert "honey" in out

    def test_set_persists_and_refreshes_session(self, app):
        s = make_session()
        s.user = run(_real_user(app, "painter", ["user"]))
        run(_drive(app, s, "/theme amber"))
        assert "Theme set to amber" in text_of(s)
        assert s.user.preferences.get("theme") == "amber"
        fresh = run(app.users.get("painter"))
        assert fresh.preferences.get("theme") == "amber"

    def test_unknown_theme_rejected(self, app):
        s = make_session()
        s.user = run(_real_user(app, "picker", ["user"]))
        run(_drive(app, s, "/theme neon"))
        assert "unknown theme" in text_of(s).lower()
        assert s.user.preferences.get("theme") != "neon"

    def test_alias_persists_canonical_name(self, app):
        s = make_session()
        s.user = run(_real_user(app, "neo", ["user"]))
        run(_drive(app, s, "/theme hacker"))
        assert s.user.preferences.get("theme") == "matrix"

    def test_double_slash_still_sets_theme(self, app):
        """PIM consumes the first `/`; typing `/theme` again must still work."""
        s = make_session()
        s.user = run(_real_user(app, "dbl", ["user"]))
        run(_drive(app, s, "//theme green"))
        assert s.user.preferences.get("theme") == "green"

    def test_bare_slash_lists_help(self, app):
        s = make_session()
        run(_drive(app, s, "/"))
        out = text_of(s)
        assert "/theme" in out
        assert "/ver" in out


class TestVerCommand:
    def test_ver_prints_version(self, app):
        from core.version import NAME, VERSION

        s = make_session()
        run(_drive(app, s, "/ver"))
        out = text_of(s)
        assert NAME in out
        assert VERSION in out

    def test_version_alias(self, app):
        from core.version import VERSION

        s = make_session()
        run(_drive(app, s, "/version"))
        assert VERSION in text_of(s)


class TestRegistry:
    def test_plugin_can_register_command(self, app):
        from core import slash

        hits = []

        async def handler(bbs, session, arg):
            hits.append(arg)
            await bbs.send(session, "did it")

        slash.register("demo", handler)
        s = make_session()

        async def scenario():
            await slash.handle_slash(app, s, "/demo stuff")

        asyncio.run(scenario())
        assert hits == ["stuff"]
        assert "did it" in text_of(s)


# -- helpers ---------------------------------------------------------------


async def _drive(app, session, line):
    from core.slash import handle_slash

    await handle_slash(app, session, line)


def _user_with(groups, preferences=None):
    from core.user import User

    return User(username="t", groups=groups, preferences=preferences or {})
