"""Tests for the PIM tab registry (build-plan § Step 6)."""
from __future__ import annotations

import json
from pathlib import Path

from core.app import BBSApp
from core.user import User
from plugins.mainmenu.tabs import DEFAULT_TABS, load_tabs, visible_tabs


def _app(tmp_path: Path) -> BBSApp:
    app = BBSApp(users_dir=tmp_path / "users")
    app.storage.plugins_dir = tmp_path / "plugins"
    return app


def test_default_tabs_load(tmp_path):
    app = _app(tmp_path)
    tabs = load_tabs(app)
    assert len(tabs) == len(DEFAULT_TABS)
    assert [t["id"] for t in tabs] == ["dashboard", "social", "files", "bulletins"]
    assert tabs[0]["key"] == "1"
    assert tabs[1]["id"] == "social" and tabs[1]["key"] == "2"
    # B5: Boards and DMs are gone as tabs — DMs live inside Social (OQ2).
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
    # anonymous sees only public
    assert len(visible_tabs(tabs, None)) == 1


def test_sysop_override_file(tmp_path):
    app = _app(tmp_path)
    md = app.storage.dir("mainmenu")
    override = [
        {"id": "a", "label": "A", "kind": "board", "key": "1", "requires": []},
        {"id": "b", "label": "B", "kind": "dm", "key": "2", "requires": []},
    ]
    (md / "tabs.json").write_text(json.dumps(override), encoding="utf-8")
    tabs = load_tabs(app)
    assert [t["id"] for t in tabs] == ["a", "b"]


def test_plugin_contributed_tab(tmp_path):
    app = _app(tmp_path)
    import plugins.mainmenu.tabs as tm
    orig = tm.DEFAULT_TABS
    tm.DEFAULT_TABS = orig[:2]  # leave room for contributed
    try:
        class FakePlugin:
            pim_tab = {"id": "extra", "label": "Extra", "kind": "all", "key": "9", "requires": []}

        app.plugins = [FakePlugin()]  # type: ignore[assignment]
        tabs = load_tabs(app)
        assert any(t["id"] == "extra" for t in tabs)
    finally:
        tm.DEFAULT_TABS = orig


def test_truncates_to_five(tmp_path):
    app = _app(tmp_path)

    class Many:
        pass

    # inject 10 fake plugins each contributing a tab
    plugins = []
    for i in range(10):
        p = Many()
        p.pim_tab = {"id": f"x{i}", "label": f"X{i}", "kind": "all", "key": str(i), "requires": []}  # type: ignore[attr-defined]
        plugins.append(p)
    app.plugins = plugins
    tabs = load_tabs(app)
    assert len(tabs) <= 5
