"""Board home strip — which plugins sit on the tab bar.

The list is a file at the tree root (``home``), next to ``config.yaml``.
It is not a mainmenu plugin file: swapping that plugin must not take the
board's feature list with it. One plugin name per line; ``#`` comments.
A replacement menu should call :func:`load_home_names` / :func:`load_tabs`
from here, not invent a second list.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("modulo.core.home")

DEFAULT_HOME: list[str] = ["dashboard", "social", "files", "bulletins"]
MAX_TABS = 5

# Kept so older tests/docs that mention DEFAULT_TABS still resolve a shape.
DEFAULT_TABS: list[dict] = [
    {"id": n, "label": n.title(), "kind": n, "key": str(i), "requires": []}
    for i, n in enumerate(DEFAULT_HOME, 1)
]


def home_file(bbs) -> Path | None:
    """``<board root>/home`` — parent of ``plugins/``."""
    try:
        storage = getattr(bbs, "storage", None) if bbs is not None else None
        if storage is None:
            return None
        return Path(storage.plugins_dir).resolve().parent / "home"
    except Exception:  # noqa: BLE001
        return None


def load_home_names(bbs) -> list[str]:
    """Plugin names for the home strip, factory default if the file is missing."""
    path = home_file(bbs)
    if path is not None and path.is_file():
        try:
            names = []
            seen = set()
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                name = line.split()[0].lower()
                if name in seen:
                    continue
                seen.add(name)
                names.append(name)
            if names:
                return names[:MAX_TABS]
        except Exception:  # noqa: BLE001
            logger.exception("failed to read home file %s", path)
    return list(DEFAULT_HOME)[:MAX_TABS]


def load_tabs(bbs) -> list[dict]:
    """Active tab list: {id, label, key, requires, plugin} for loaded names."""
    tabs: list[dict] = []
    if bbs is None:
        return tabs
    get = getattr(bbs, "get_plugin", None)
    for i, name in enumerate(load_home_names(bbs), 1):
        plugin = get(name) if callable(get) else None
        if plugin is None:
            logger.warning("home lists %r but that plugin is not loaded; skipping", name)
            continue
        label = (getattr(plugin, "home_label", None) or "").strip() or (
            getattr(plugin, "name", None) or name
        )
        req = getattr(plugin, "menu_requires", None) or []
        if not isinstance(req, list):
            req = []
        tabs.append({
            "id": name,
            "label": str(label),
            "kind": name,
            "key": str(i),
            "requires": [str(x).strip() for x in req if str(x).strip()],
            "plugin": plugin,
        })
        if len(tabs) >= MAX_TABS:
            break
    return tabs


def visible_tabs(tabs: list[dict], user) -> list[dict]:
    """Filter tabs by the caller's groups. Anonymous sees only requires=[] tabs."""
    if user is None:
        return [t for t in tabs if not t.get("requires")]
    return [t for t in tabs if not t.get("requires") or user.can_access(t["requires"])]
