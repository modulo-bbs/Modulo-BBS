"""Named per-user colour palettes, loaded from ``themes/*.theme``.

A theme is a string on ``User.preferences["theme"]``. Plugins and the
screen engine resolve it through :func:`palette_for` from the session's
bound user. Anonymous sessions (no user yet) get **classic**.

Files are DOS colour numbers, one ``key=fg`` or ``key=fg,bg`` per line.
Missing keys fall back to classic defaults. The directory is re-read on
use (mtime), so a sysop can drop or edit a file without a restart.

How to write a file, the colour chart, and caller UX: ``docs/themes.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from shared.telnet_protocol import ANSI

logger = logging.getLogger("modulo.theme")

DEFAULT_THEME = "classic"

# IBM/DOS 16-colour: 0-7 dim, 8-15 bright. Backgrounds are 0-7.
_FG = (
    ANSI.BLACK, ANSI.BLUE, ANSI.GREEN, ANSI.CYAN,
    ANSI.RED, ANSI.MAGENTA, ANSI.YELLOW, ANSI.WHITE,
    ANSI.BRIGHT_BLACK, ANSI.BRIGHT_BLUE, ANSI.BRIGHT_GREEN, ANSI.BRIGHT_CYAN,
    ANSI.BRIGHT_RED, ANSI.BRIGHT_MAGENTA, ANSI.BRIGHT_YELLOW, ANSI.BRIGHT_WHITE,
)
_BG = (
    ANSI.BG_BLACK, ANSI.BG_BLUE, ANSI.BG_GREEN, ANSI.BG_CYAN,
    ANSI.BG_RED, ANSI.BG_MAGENTA, ANSI.BG_YELLOW, ANSI.BG_WHITE,
)

# Friendly names that fold into a Palette field.
_KEY_ALIASES = {
    "title": "accent",
    "hint": "success",
}

_META = {"order", "alias", "aliases", "name", "label"}

# Classic look, as numbers. Every missing file key lands here.
_CLASSIC_NUMS: dict[str, tuple[int, int | None]] = {
    "accent": (11, None),      # bright cyan
    "success": (10, None),     # bright green
    "warning": (14, None),     # bright yellow
    "error": (12, None),       # bright red
    "muted": (8, None),        # dark gray (DOS stand-in for dim)
    "frame": (8, None),        # box drawing: tab bars, pane borders, rules
    "inactive": (8, None),    # unfocused region chrome (idle pane)
    "active": (11, None),     # focused region chrome (classic cyan)
    "text": (15, None),        # bright white
    "prompt": (10, None),      # same as success — the `>` line
    "highlight": (15, 1),      # bright white on blue
}

_DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "themes"
_root: Path | None = None
_cache: dict[str, tuple[float, object]] = {}  # stem -> (mtime, _Loaded)


@dataclass(frozen=True)
class Palette:
    """One named set of semantic ANSI roles, plus any extra file keys."""

    name: str
    accent: str      # headers, titles, me-bubbles
    success: str     # ok / hints / other-bubbles
    warning: str     # NEW / caution
    error: str       # failures
    muted: str       # inactive tab labels, dim text
    frame: str       # box drawing: tab bars, pane borders, rules
    inactive: str    # unfocused region chrome (idle pane, idle widget)
    active: str      # focused region chrome (the pane that has the keys)
    text: str        # emphasized body
    prompt: str      # the `>` prompt
    tab_fg: str      # active PIM tab / list selection foreground
    tab_bg: str      # active PIM tab / list selection background
    extras: dict[str, str] = field(default_factory=dict)
    reset: str = ANSI.RESET

    def tokens(self) -> dict[str, str]:
        """Uppercase ``{ACCENT}`` … plus extras (``{BANNER}`` etc.)."""
        out = {
            "ACCENT": self.accent,
            "SUCCESS": self.success,
            "WARNING": self.warning,
            "ERROR": self.error,
            "MUTED": self.muted,
            "FRAME": self.frame,
            "INACTIVE": self.inactive,
            "ACTIVE": self.active,
            "TEXT": self.text,
            "PROMPT": self.prompt,
            "TAB_FG": self.tab_fg,
            "TAB_BG": self.tab_bg,
            "HIGHLIGHT": self.tab_fg + self.tab_bg,
        }
        reserved = set(out)
        for key, sgr in self.extras.items():
            token = key.upper()
            if token not in reserved:
                out[token] = sgr
        return out


@dataclass(frozen=True)
class _Loaded:
    palette: Palette
    aliases: tuple[str, ...]
    order: int


def themes_dir() -> Path:
    """Directory scanned for ``*.theme`` files."""
    return _root if _root is not None else _DEFAULT_ROOT


def set_themes_dir(path: Path | str | None) -> None:
    """Point the loader at *path*, or ``None`` to restore the shipped folder."""
    global _root
    _root = Path(path) if path is not None else None
    _cache.clear()


def _sgr(fg: int, bg: int | None) -> str:
    fg = max(0, min(15, fg))
    out = _FG[fg]
    if bg is not None:
        out += _BG[max(0, min(7, bg))]
    return out


def _bg_only(bg: int) -> str:
    return _BG[max(0, min(7, bg))]


def _classic_sgr() -> dict[str, str]:
    out = {k: _sgr(fg, bg) for k, (fg, bg) in _CLASSIC_NUMS.items()}
    hfg, hbg = _CLASSIC_NUMS["highlight"]
    out["_tab_fg"] = _sgr(hfg, None)
    out["_tab_bg"] = _bg_only(hbg if hbg is not None else 1)
    return out


def parse_theme(text: str) -> tuple[dict[str, str], list[str], int]:
    """Parse a theme file body.

    Returns ``(elements, aliases, order)``. *elements* maps lowercase keys
    to SGR strings (unknown colour keys included). Invalid lines are skipped.
    """
    elements: dict[str, str] = {}
    aliases: list[str] = []
    order = 100
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if not key:
            continue
        if key == "order":
            try:
                order = int(val, 10)
            except ValueError:
                pass
            continue
        if key in ("alias", "aliases"):
            for a in val.replace(",", " ").split():
                a = a.strip().lower()
                if a:
                    aliases.append(a)
            continue
        if key in ("name", "label"):
            continue
        pair = _parse_pair(val)
        if pair is None:
            logger.warning("theme: skip bad colour %r=%r", key, val)
            continue
        fg, bg = pair
        elements[key] = _sgr(fg, bg)
        if key == "highlight":
            elements["_tab_fg"] = _sgr(fg, None)
            elements["_tab_bg"] = _bg_only(bg if bg is not None else 1)
    return elements, aliases, order


def _parse_pair(raw: str) -> tuple[int, int | None] | None:
    parts = [p.strip() for p in raw.split(",")]
    if not parts or not parts[0]:
        return None
    try:
        fg = int(parts[0], 10)
    except ValueError:
        return None
    fg = max(0, min(15, fg))
    bg: int | None = None
    if len(parts) > 1 and parts[1] != "":
        try:
            bg = max(0, min(15, int(parts[1], 10))) & 7
        except ValueError:
            return None
    return fg, bg


def _palette_from_elements(name: str, elements: dict[str, str]) -> Palette:
    base = _classic_sgr()
    base.update(elements)
    # Fold title/hint before reading fields.
    if "title" in elements and "accent" not in elements:
        base["accent"] = elements["title"]
    if "hint" in elements and "success" not in elements:
        base["success"] = elements["hint"]
    tab_fg = base.get("_tab_fg") or _sgr(15, None)
    tab_bg = base.get("_tab_bg") or _bg_only(1)
    if "highlight" in elements and "_tab_fg" not in elements:
        # highlight=fg only: fg on default blue bg
        tab_fg = elements["highlight"]
    extras = {
        k: v for k, v in elements.items()
        if k not in _CLASSIC_NUMS and k not in _KEY_ALIASES
        and not k.startswith("_") and k != "highlight"
    }
    return Palette(
        name=name,
        accent=base["accent"],
        success=base["success"],
        warning=base["warning"],
        error=base["error"],
        muted=base["muted"],
        frame=base.get("frame") or base["muted"],
        inactive=base.get("inactive") or base.get("frame") or base["muted"],
        active=base.get("active") or base["accent"],
        text=base["text"],
        prompt=base.get("prompt") or base["success"],
        tab_fg=tab_fg,
        tab_bg=tab_bg,
        extras=extras,
    )


def _scan_files() -> dict[str, Path]:
    root = themes_dir()
    found: dict[str, Path] = {}
    if not root.is_dir():
        return found
    for path in root.glob("*.theme"):
        stem = path.stem.strip().lower()
        if stem and stem.isascii() and all(c.isalnum() or c in "-_" for c in stem):
            found[stem] = path
    return found


def _load_path(name: str, path: Path | None) -> _Loaded:
    mtime = path.stat().st_mtime if path is not None and path.is_file() else 0.0
    cached = _cache.get(name)
    if cached is not None and cached[0] == mtime:
        return cached[1]  # type: ignore[return-value]
    text = ""
    if path is not None and path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("theme: cannot read %s", path)
    elements, aliases, order = parse_theme(text)
    loaded = _Loaded(
        palette=_palette_from_elements(name, elements),
        aliases=tuple(aliases),
        order=order,
    )
    _cache[name] = (mtime, loaded)
    return loaded


def load_palette(name: str | None) -> Palette:
    """Load ``name.theme``, or classic defaults if missing/unknown."""
    key = (name or DEFAULT_THEME).strip().lower() or DEFAULT_THEME
    files = _scan_files()
    alias_map = _alias_map(files)
    key = alias_map.get(key, key)
    if key not in files and key != DEFAULT_THEME:
        key = DEFAULT_THEME
    path = files.get(key)
    return _load_path(key, path).palette


def _alias_map(files: dict[str, Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, path in files.items():
        loaded = _load_path(name, path)
        for a in loaded.aliases:
            if a and a not in files:
                out[a] = name
    return out


def theme_names() -> list[str]:
    """Picker order: ``order=`` then name. Classic is always present."""
    files = _scan_files()
    items: list[tuple[int, str]] = []
    for name, path in files.items():
        items.append((_load_path(name, path).order, name))
    names = {n for _, n in items}
    if DEFAULT_THEME not in names:
        items.append((0, DEFAULT_THEME))
    items.sort()
    return [n for _, n in items]


def theme_aliases() -> dict[str, str]:
    """Nickname → canonical file stem."""
    return dict(_alias_map(_scan_files()))


def resolve_theme_name(raw) -> str:
    """Canonical theme name, or :data:`DEFAULT_THEME` if unknown/unset."""
    if isinstance(raw, str):
        key = raw.strip().lower()
        files = _scan_files()
        if key in files:
            return key
        alias = _alias_map(files).get(key)
        if alias is not None:
            return alias
    return DEFAULT_THEME


def theme_name_for(session) -> str:
    """Theme name stored on ``session.user.preferences``, else classic."""
    user = getattr(session, "user", None) if session is not None else None
    prefs = getattr(user, "preferences", None) or {}
    return resolve_theme_name(prefs.get("theme"))


def palette_for(session) -> Palette:
    """Palette for this session. No user / unknown name → classic."""
    return load_palette(theme_name_for(session))
