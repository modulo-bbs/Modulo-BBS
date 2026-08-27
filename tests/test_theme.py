"""Tests for core/theme.py — file palettes and session resolution."""
from __future__ import annotations

from core.theme import (
    DEFAULT_THEME,
    load_palette,
    palette_for,
    parse_theme,
    resolve_theme_name,
    set_themes_dir,
    theme_aliases,
    theme_name_for,
    theme_names,
)
from core.user import User
from server.session import Session
from shared.telnet_protocol import ANSI

SHIPPED = ("classic", "amber", "green", "magenta", "matrix", "honey")


def _session(user=None):
    s = Session(session_id="t", node_id=1, address=("h", 1))
    s.user = user
    return s


def test_default_and_unknown_names_are_classic():
    assert DEFAULT_THEME == "classic"
    assert resolve_theme_name(None) == "classic"
    assert resolve_theme_name("") == "classic"
    assert resolve_theme_name("dark") == "classic"
    assert resolve_theme_name("AMBER") == "amber"


def test_no_user_is_classic():
    assert palette_for(None) == load_palette("classic")
    assert palette_for(_session(None)) == load_palette("classic")
    assert theme_name_for(_session(None)) == "classic"


def test_classic_matches_shipped_file():
    p = load_palette("classic")
    assert p.accent == ANSI.BRIGHT_CYAN
    assert p.success == ANSI.BRIGHT_GREEN
    assert p.warning == ANSI.BRIGHT_YELLOW
    assert p.error == ANSI.BRIGHT_RED
    assert p.muted == ANSI.BRIGHT_BLACK
    assert p.text == ANSI.BRIGHT_WHITE
    assert p.prompt == ANSI.BRIGHT_GREEN
    assert p.tab_fg == ANSI.BRIGHT_WHITE
    assert p.tab_bg == ANSI.BG_BLUE
    assert p.reset == ANSI.RESET


def test_shipped_presets_are_complete_and_distinct():
    names = theme_names()
    for n in SHIPPED:
        assert n in names
    signatures = {tuple(load_palette(n).tokens().values()) for n in SHIPPED}
    assert len(signatures) == len(SHIPPED)
    for name in SHIPPED:
        pal = load_palette(name)
        assert pal.name == name
        toks = pal.tokens()
        for role in ("ACCENT", "SUCCESS", "WARNING", "ERROR", "MUTED",
                     "TEXT", "PROMPT", "TAB_FG", "TAB_BG", "HIGHLIGHT"):
            assert toks[role], role
            assert toks[role].startswith("\033[")


def test_crt_monos_stay_in_one_phosphor():
    g = {ANSI.GREEN, ANSI.BRIGHT_GREEN, ANSI.BG_GREEN, ANSI.BLACK}
    a = {ANSI.YELLOW, ANSI.BRIGHT_YELLOW, ANSI.BG_YELLOW, ANSI.BLACK}
    for pal, allowed in ((load_palette("matrix"), g), (load_palette("honey"), a)):
        for role in ("accent", "success", "warning", "error", "muted",
                     "text", "prompt", "tab_fg", "tab_bg"):
            assert getattr(pal, role) in allowed, (pal.name, role)
            assert getattr(pal, role) != ANSI.DIM


def test_aliases_resolve_to_crt_names():
    assert resolve_theme_name("phosphor") == "matrix"
    assert resolve_theme_name("hacker") == "matrix"
    assert resolve_theme_name("ambercrt") == "honey"
    assert palette_for(
        _session(User(username="x", preferences={"theme": "hacker"}))
    ) == load_palette("matrix")
    assert theme_aliases()["phosphor"] == "matrix"


def test_session_pref_selects_palette():
    user = User(username="dave", preferences={"theme": "amber"})
    pal = palette_for(_session(user))
    assert pal == load_palette("amber")
    assert pal.accent == ANSI.BRIGHT_YELLOW
    assert pal.tab_bg == ANSI.BG_YELLOW


def test_parse_skips_comments_and_bad_lines():
    elems, aliases, order = parse_theme(
        "# hi\n"
        "order=42\n"
        "alias=ice\n"
        "alias=frost\n"
        "accent=11\n"
        "bogus\n"
        "error=nope\n"
        "highlight=4,7\n"
    )
    assert order == 42
    assert aliases == ["ice", "frost"]
    assert elems["accent"] == ANSI.BRIGHT_CYAN
    assert elems["_tab_fg"] == ANSI.RED
    assert elems["_tab_bg"] == ANSI.BG_WHITE


def test_partial_file_fills_classic_defaults(tmp_path):
    set_themes_dir(tmp_path)
    try:
        (tmp_path / "ice.theme").write_text(
            "highlight=4,7\ntext=7,0\nprompt=7,0\n", encoding="utf-8"
        )
        pal = load_palette("ice")
        assert pal.tab_fg == ANSI.RED
        assert pal.tab_bg == ANSI.BG_WHITE
        assert pal.text == ANSI.WHITE + ANSI.BG_BLACK
        assert pal.prompt == ANSI.WHITE + ANSI.BG_BLACK
        assert pal.accent == ANSI.BRIGHT_CYAN
        assert pal.error == ANSI.BRIGHT_RED
        assert "ice" in theme_names()
    finally:
        set_themes_dir(None)


def test_unknown_keys_become_tokens(tmp_path):
    set_themes_dir(tmp_path)
    try:
        (tmp_path / "x.theme").write_text("banner=14\naccent=11\n", encoding="utf-8")
        pal = load_palette("x")
        assert pal.extras["banner"] == ANSI.BRIGHT_YELLOW
        assert pal.tokens()["BANNER"] == ANSI.BRIGHT_YELLOW
    finally:
        set_themes_dir(None)


def test_new_file_is_picked_up_without_reload(tmp_path):
    set_themes_dir(tmp_path)
    try:
        assert "sunset" not in theme_names()
        (tmp_path / "sunset.theme").write_text("accent=12\n", encoding="utf-8")
        assert "sunset" in theme_names()
        assert load_palette("sunset").accent == ANSI.BRIGHT_RED
    finally:
        set_themes_dir(None)
