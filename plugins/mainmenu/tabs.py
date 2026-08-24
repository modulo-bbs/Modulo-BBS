"""Tab registry for the PIM home (see docs/build-plan.md Phase 1, Step 6).

Tabs are filtered views of the same ``core/conversations.py`` engine.
A tab is: {id, label, kind, key, requires} where kind filters
conversations (board|channel|dm|group|all) and requires is a group gate
via ``user.can_access()``.  Sysops can override via
``plugins/mainmenu/data/tabs.json``; plugins can contribute by setting
``pim_tab = {...}`` on their Plugin class (collected at on_load).
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_TABS: list[dict] = [
    {"id": "boards", "label": "Boards", "kind": "board", "key": "1", "requires": []},
    {"id": "dms", "label": "DMs", "kind": "dm", "key": "2", "requires": []},
    {"id": "mentions", "label": "Mentions", "kind": "all", "key": "3", "requires": []},
]

# Keys reserved for tab switching — never used for board selection
# inside the PIM (that uses up/dn + enter per build-plan § Risks).
TAB_KEYS = {t["key"] for t in DEFAULT_TABS}


def _validate_tab(t: dict) -> dict | None:
    if not isinstance(t, dict):
        return None
    for k in ("id", "label", "kind", "key"):
        if k not in t or not isinstance(t[k], str) or not t[k].strip():
            return None
    if t["kind"] not in ("board", "channel", "dm", "group", "all"):
        return None
    req = t.get("requires", [])
    if not isinstance(req, list):
        return None
    return {
        "id": t["id"].strip(),
        "label": t["label"].strip(),
        "kind": t["kind"].strip(),
        "key": t["key"].strip(),
        "requires": [str(x).strip() for x in req if str(x).strip()],
    }


def load_tabs(bbs) -> list[dict]:
    """Load the active tab list for this BBS instance.

    Precedence: DEFAULT_TABS + plugin-contributed pim_tab entries,
    overridden entirely if ``plugins/mainmenu/data/tabs.json`` exists and
    parses as a list.  Invalid entries are dropped (never crash boot).
    """
    tabs: list[dict] = [dict(t) for t in DEFAULT_TABS]

    # plugin-contributed tabs (e.g. files plugin wants a Files branch)
    try:
        for p in getattr(bbs, "plugins", []) or []:
            extra = getattr(p, "pim_tab", None)
            if isinstance(extra, dict):
                v = _validate_tab(extra)
                if v is not None and v["id"] not in {t["id"] for t in tabs}:
                    tabs.append(v)
    except Exception:
        pass

    # sysop override — a JSON list of tab objects
    try:
        override_path = bbs.storage.dir("mainmenu") / "tabs.json"
        if override_path.is_file():
            raw = json.loads(override_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                validated = [v for t in raw if (v := _validate_tab(t)) is not None]
                if validated:
                    tabs = validated
    except Exception:
        pass

    # truncate to 5 for 80-col fit (build-plan § Risks: max tabs before wrap)
    return tabs[:5]


def visible_tabs(tabs: list[dict], user) -> list[dict]:
    """Filter tabs by the caller's groups. Anonymous sees only requires=[] tabs."""
    if user is None:
        return [t for t in tabs if not t.get("requires")]
    return [t for t in tabs if not t.get("requires") or user.can_access(t["requires"])]
