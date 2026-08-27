"""Tests for the PIM tab registry (home file + loaded plugins)."""
from __future__ import annotations

from pathlib import Path

from core.app import BBSApp
from core.user import User
from plugins.base import Plugin
from plugins.mainmenu.tabs import DEFAULT_HOME, load_home_names, load_tabs, visible_tabs


class _Named(Plugin):
    def __init__(self, name: str, label: str, requires=None):
        self.name = name
        self.home_label = label
        self.menu_requires = requires


def _app(tmp_path: Path) -> BBSApp:
    app = BBSApp(users_dir=tmp_path / "users")
    app.storage.plugins_dir = tmp_path / "plugins"
    return app


def _stock(app):
    app.plugins = [
        _Named("dashboard", "Dashboard"),
        _Named("social", "Social"),
        _Named("files", "Files"),
        _Named("bulletins", "Bulletins"),
    ]


def test_default_tabs_load(tmp_path):
    app = _app(tmp_path)
    _stock(app)
    tabs = load_tabs(app)
    assert [t["id"] for t in tabs] == ["dashboard", "social", "files", "bulletins"]
    assert tabs[0]["key"] == "1"
    assert tabs[1]["id"] == "social" and tabs[1]["key"] == "2"
    assert all(t["id"] not in ("boards", "dms") for t in tabs)


def test_visible_tabs_gating():
    pleb = User(username="pleb", groups=[])
    sysop = User(username="sysop", groups=["sysop"])
    tabs = [
        {"id": "boards", "label": "Boards", "kind": "board", "key": "1", "requires": []},
        {"id": "admin", "label": "Admin", "kind": "board", "key": "9", "requires": ["sysop"]},
    ]
    assert len(visible_tabs(tabs, pleb)) == 1
    assert len(visible_tabs(tabs, sysop)) == 2
    assert len(visible_tabs(tabs, None)) == 1


def test_home_file_reorder_and_omit(tmp_path):
    app = _app(tmp_path)
    _stock(app)
    md = app.storage.dir("mainmenu")
    (md / "home").write_text("social\nfiles\n", encoding="utf-8")
    tabs = load_tabs(app)
    assert [t["id"] for t in tabs] == ["social", "files"]
    assert tabs[0]["key"] == "1"


def test_home_skips_missing_plugin(tmp_path):
    app = _app(tmp_path)
    app.plugins = [_Named("social", "Social")]
    md = app.storage.dir("mainmenu")
    (md / "home").write_text("dashboard\nsocial\nfiles\n", encoding="utf-8")
    tabs = load_tabs(app)
    assert [t["id"] for t in tabs] == ["social"]


def test_unlisted_plugin_does_not_appear(tmp_path):
    app = _app(tmp_path)
    _stock(app)
    app.plugins.append(_Named("classifieds", "Classifieds"))
    tabs = load_tabs(app)
    assert all(t["id"] != "classifieds" for t in tabs)


def test_truncates_to_five(tmp_path):
    app = _app(tmp_path)
    names = [f"x{i}" for i in range(10)]
    app.plugins = [_Named(n, n.upper()) for n in names]
    md = app.storage.dir("mainmenu")
    (md / "home").write_text("\n".join(names) + "\n", encoding="utf-8")
    tabs = load_tabs(app)
    assert len(tabs) <= 5
    assert [t["id"] for t in tabs] == names[:5]


def test_default_home_names_when_file_missing(tmp_path):
    app = _app(tmp_path)
    assert load_home_names(app) == DEFAULT_HOME
