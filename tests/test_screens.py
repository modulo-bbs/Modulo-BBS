"""Tests for the core screen service (core/screens.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.app import BBSApp
from core.screens import ANSI_TOKENS, ScreenService


@pytest.fixture
def app(tmp_path):
    a = BBSApp(users_dir=tmp_path / "users")
    # Point the service at the tmp tree so tests write screens there.
    a.screens.plugins_root = tmp_path
    return a


def make_screens(tmp_path: Path, plugin: str, files: dict[str, bytes]) -> Path:
    d = tmp_path / "plugins" / plugin / "screens"
    d.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (d / name).write_bytes(data)
    return d


class TestResolution:
    def test_txt_used_when_alone(self, app, tmp_path):
        make_screens(tmp_path, "demo", {"main.txt": b"TXT"})
        assert app.screens.render(None, "demo", "main") == "TXT"

    def test_ans_beats_asc_beats_txt(self, app, tmp_path):
        make_screens(
            tmp_path,
            "demo",
            {"main.txt": b"TXT", "main.asc": b"ASC", "main.ans": b"ANS"},
        )
        assert app.screens.render(None, "demo", "main") == "ANS"

        # Remove .ans → .asc wins
        (tmp_path / "plugins/demo/screens/main.ans").unlink()
        assert app.screens.render(None, "demo", "main") == "ASC"

        # Remove .asc → .txt
        (tmp_path / "plugins/demo/screens/main.asc").unlink()
        assert app.screens.render(None, "demo", "main") == "TXT"

    def test_missing_screen_placeholder(self, app):
        out = app.screens.render(None, "ghost", "nope")
        assert "[missing screen: ghost/nope]" in out

    def test_generator_fallback(self, app, tmp_path):
        app.screens.register_generator("demo", "menu", lambda: "GENERATED")
        assert app.screens.render(None, "demo", "menu") == "GENERATED"

    def test_file_beats_generator(self, app, tmp_path):
        app.screens.register_generator("demo", "menu", lambda: "GENERATED")
        make_screens(tmp_path, "demo", {"menu.txt": b"FROM FILE"})
        assert app.screens.render(None, "demo", "menu") == "FROM FILE"

    def test_source_for(self, app, tmp_path):
        app.screens.register_generator("demo", "gen", lambda: "")
        assert app.screens.source_for("demo", "gen") == "generator"
        assert app.screens.source_for("demo", "nothing") == "missing"
        make_screens(tmp_path, "demo", {"here.ans": b"x"})
        assert app.screens.source_for("demo", "here") == "file:.ans"


class TestDecoding:
    def test_ans_decodes_cp437(self, app, tmp_path):
        raw = "─│┌┐".encode("cp437")
        make_screens(tmp_path, "demo", {"box.ans": raw})
        out = app.screens.render(None, "demo", "box")
        assert out == "─│┌┐"

    def test_crlf_survives_every_extension(self, app, tmp_path):
        for ext in (".ans", ".asc", ".txt"):
            make_screens(tmp_path, f"p{ext[1]}", {f"s{ext}": b"A\r\nB\r\n"})
            out = app.screens.render(None, f"p{ext[1]}", "s")
            assert "\r\n" in out, ext

    def test_bad_bytes_replace_not_crash(self, app, tmp_path):
        make_screens(tmp_path, "demo", {"bad.ans": b"\xff\xfe\x81"})
        out = app.screens.render(None, "demo", "bad")
        assert isinstance(out, str)  # never raises


class TestTokens:
    def _screen(self, app, tmp_path, body: bytes):
        make_screens(tmp_path, "demo", {"t.txt": body})

    def test_ansi_tokens(self, app, tmp_path):
        self._screen(app, tmp_path, b"{BRIGHT_CYAN}hi{RESET}")
        out = app.screens.render(None, "demo", "t")
        assert out == ANSI_TOKENS["BRIGHT_CYAN"] + "hi" + ANSI_TOKENS["RESET"]

    def test_accent_follows_session_theme(self, app, tmp_path):
        from core.theme import load_palette
        from core.user import User
        from server.session import Session
        from shared.telnet_protocol import ANSI

        self._screen(app, tmp_path, b"{ACCENT}hi{RESET}")
        anon = app.screens.render(None, "demo", "t")
        assert anon == load_palette("classic").accent + "hi" + ANSI.RESET

        s = Session(session_id="t", node_id=1, address=("h", 1))
        s.user = User(username="dave", preferences={"theme": "amber"})
        out = app.screens.render(s, "demo", "t")
        assert out == load_palette("amber").accent + "hi" + ANSI.RESET
        assert load_palette("amber").accent == ANSI.BRIGHT_YELLOW

    def test_literal_bright_cyan_not_remapped(self, app, tmp_path):
        """Semantic roles follow the theme; {BRIGHT_CYAN} stays actual cyan."""
        from core.user import User
        from server.session import Session

        self._screen(app, tmp_path, b"{BRIGHT_CYAN}x{ACCENT}")
        s = Session(session_id="t", node_id=1, address=("h", 1))
        s.user = User(username="dave", preferences={"theme": "amber"})
        out = app.screens.render(s, "demo", "t")
        assert out.startswith(ANSI_TOKENS["BRIGHT_CYAN"])
        from core.theme import load_palette
        assert out.endswith(load_palette("amber").accent)

    def test_core_tokens(self, app, tmp_path):
        self._screen(app, tmp_path, b"{bbsname}|{time}|{date}")
        out = app.screens.render(None, "demo", "t")
        parts = out.split("|")
        assert parts[0] == "Modulo BBS"
        assert len(parts[1]) == 5 and ":" in parts[1]      # HH:MM
        assert len(parts[2]) == 8 and parts[2].count("/") == 2  # MM/DD/YY

    def test_session_tokens(self, app, tmp_path):
        from server.session import Session

        s = Session(session_id="t", node_id=3, address=("h", 1))
        self._screen(app, tmp_path, b"node={node} user={username}")
        out = app.screens.render(s, "demo", "t")
        assert out == "node=3 user=-"   # unauthenticated -> dash

    def test_extra_kwargs_highest_precedence(self, app, tmp_path):
        self._screen(app, tmp_path, b"{secret}")
        out = app.screens.render(None, "demo", "t", secret="XYZZY")
        assert out == "XYZZY"

    def test_custom_provider(self, app, tmp_path):
        calls = []

        def provider(ctx):
            calls.append(ctx)
            return {"boards.count": 7}

        app.screens.register_provider(provider)
        self._screen(app, tmp_path, b"{boards.count} boards")
        assert app.screens.render(None, "demo", "t") == "7 boards"
        assert calls  # provider actually ran with a context

    def test_broken_provider_doesnt_kill_render(self, app, tmp_path):
        app.screens.register_provider(lambda ctx: 1 / 0)  # type: ignore[arg-type]
        self._screen(app, tmp_path, b"still works")
        assert app.screens.render(None, "demo", "t") == "still works"


class TestInventory:
    def test_screen_names_union(self, app, tmp_path):
        make_screens(
            tmp_path,
            "demo",
            {"a.ans": b"", "a.txt": b"", "b.asc": b""},
        )
        app.screens.register_generator("demo", "g", lambda: "")
        names = app.screens.screen_names("demo")
        assert names == ["a", "b", "g"]

    def test_logon_screens_dir_is_project_root(self, app):
        d = app.screens.plugin_screens_dir("logon")
        assert d.name == "screens" and "plugins" not in d.parts


class TestSend:
    def test_send_transmits_rendered(self, app, tmp_path):
        make_screens(tmp_path, "demo", {"hi.txt": b"hello {username}"})

        class FakeWriter:
            def __init__(self):
                self.buffer = bytearray()

            def write(self, d):
                self.buffer.extend(d)

            async def drain(self):
                pass

            def is_closing(self):
                return False

        from server.session import Session

        s = Session(session_id="t", node_id=1, address=("h", 1))
        s.writer = FakeWriter()

        import asyncio

        asyncio.run(app.screens.send(s, "demo", "hi"))
        assert bytes(s.writer.buffer) == b"hello -\r\n" or b"hello" in (
            bytes(s.writer.buffer)
        )


_THEME_ROLES = (
    "ACCENT", "SUCCESS", "WARNING", "ERROR", "MUTED", "FRAME", "TEXT", "TAB_FG", "TAB_BG",
)


class TestShippedScreens:
    """Production .txt templates must decode through ScreenService, not leak
    ``{ACCENT}`` as literal text the way a stale server did on login."""

    def test_login_and_logon_txt_leave_no_role_tokens(self, tmp_path):
        from core.app import BBSApp

        # Fresh app keeps the real plugins_root (repo), unlike the fixture.
        app = BBSApp(users_dir=tmp_path / "users")
        shipped = (
            ("logon", "splash"),
            ("logon", "welcome"),
            ("login", "login"),
            ("login", "register"),
            ("login", "totp_setup"),
            ("login", "totp_verify"),
        )
        for plugin, name in shipped:
            extra = (
                {"SECRET": "X", "USERNAME": "dave"}
                if name == "totp_setup"
                else {}
            )
            out = app.screens.render(None, plugin, name, **extra)
            for tok in _THEME_ROLES:
                assert "{" + tok + "}" not in out, f"{plugin}/{name} leaked {{{tok}}}"
            assert "\x1b[" in out

