"""Tests for core role resolution (``bbs.plugin_for`` / ``role_plugin_name``)."""

from plugins.base import Plugin

from core.app import BBSApp


class _Named(Plugin):
    def __init__(self, name: str):
        self.name = name


def _app(plugins=None, config=None) -> BBSApp:
    app = BBSApp(config=config or {})
    app.plugins = list(plugins or [])
    return app


def test_plugin_for_defaults_to_role_name():
    modal = _Named("modal")
    app = _app([modal])
    assert app.plugin_for("modal") is modal
    assert app.role_plugin_name("modal") == "modal"


def test_plugin_for_follows_config_map():
    awesome = _Named("awesomemodal")
    leftover = _Named("modal")
    app = _app([leftover, awesome], config={"modal": "awesomemodal"})
    assert app.plugin_for("modal") is awesome
    assert app.get_plugin("modal") is leftover


def test_plugin_for_ignores_nested_config_blocks():
    login = _Named("login")
    app = _app([login], config={"server": {"host": "127.0.0.1"}, "login": "login"})
    assert app.plugin_for("login") is login
    assert app.plugin_for("server") is None


def test_logon_role_accepts_legacy_logon_plugin_key():
    alt = _Named("wizard")
    app = _app([alt], config={"logon_plugin": "wizard"})
    assert app.role_plugin_name("logon") == "wizard"
    assert app.plugin_for("logon") is alt


def test_plugin_for_missing_returns_none():
    app = _app([])
    assert app.plugin_for("modal") is None
    assert app.plugin_for("") is None
