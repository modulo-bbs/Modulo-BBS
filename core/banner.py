"""Shared MODULO banner rendering for the BBS.

The MODULO block-letter splash is presented during the logon sequence
(``screens/splash.txt``) and this module owns both the block-art generation
(the static, sysop-editable art embedded in the screen file) and the live
banner string used as a fallback when the splash screen is missing.

It also exposes a small ANSI-token substitution map (``{BRIGHT_CYAN}`` etc.)
and a ``substitute_tokens`` helper so screen templates can stay readable
while still using the canonical ANSI constants from
``shared.telnet_protocol.ANSI`` -- mirroring the ``{TOKEN}`` convention the
login plugin already uses for its own screens.
"""

from __future__ import annotations

import sys

from core.version import display
from shared.telnet_protocol import ANSI
from tools.blockletters import render as block_render

# ANSI placeholder tokens recognised inside screen template files: a
# ``{NAME}`` written in a screen is replaced with the matching escape code.
_ANSI_NAMES = [
    "RESET", "BOLD", "DIM", "UNDERLINE", "BLINK", "REVERSE",
    "BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE",
    "BRIGHT_BLACK", "BRIGHT_BLUE", "BRIGHT_CYAN", "BRIGHT_GREEN",
    "BRIGHT_MAGENTA", "BRIGHT_RED", "BRIGHT_WHITE", "BRIGHT_YELLOW",
    "BG_BLACK", "BG_BLUE", "BG_CYAN", "BG_GREEN", "BG_MAGENTA",
    "BG_RED", "BG_WHITE", "BG_YELLOW",
    "CLEAR_SCREEN", "CLEAR_LINE",
]
_ANSI_TOKENS: dict[str, str] = {name: getattr(ANSI, name) for name in _ANSI_NAMES}
_ANSI_TOKENS["CLEAR"] = ANSI.CLEAR_SCREEN      # screen-clear shortcut
_ANSI_TOKENS["HOME"] = "\x1b[H"                # cursor home (1;1)


def substitute_tokens(text: str, session=None, **values: object) -> str:
    """Replace ``{ANSI_NAME}`` and runtime ``{KEY}`` placeholders in ``text``.

    Semantic theme roles first, then literal ANSI names, then keyword
    values (e.g. ``NODE``, ``ACTIVE``), so a template can reference any.
    """
    from core.theme import palette_for

    for token, code in palette_for(session).tokens().items():
        text = text.replace("{" + token + "}", code)
    for token, code in _ANSI_TOKENS.items():
        text = text.replace("{" + token + "}", code)
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def banner_art_text() -> str:
    """The static MODULO block-letters as a single LF-separated string.

    This is the block art embedded in ``screens/splash.txt``; keeping the
    generator here means regenerating the screen is just a copy/paste.
    """
    return block_render("MODULO", size="small", fill="#", blank=" ")


def banner_lines(session, active_count: int, max_nodes: int) -> list[str]:
    """The live, ANSI-coloured MODULO banner as a list of display lines.

    Mirrors the banner the telnet server used to render before the logon
    sequencer took over. Lines carry ``\\r\\n`` endings when joined by
    :func:`render_banner`.
    """
    from core.theme import palette_for

    w = min(getattr(session, "terminal_width", 80), 60)
    bar = "=" * w
    p = palette_for(session)
    C = p.accent
    B = ANSI.BOLD
    G = p.success
    W = p.text
    D = p.muted
    R = p.reset

    lines = []
    lines.append(C + B + bar + R)
    for art in banner_art_text().split("\n"):
        lines.append(C + B + art + R)
    lines.append(C + B + bar + R)
    lines.append("")
    lines.append(
        D + f"  Node {getattr(session, 'node_id', 0)} | "
        f"{session.terminal_type} ({session.terminal_width}x{session.terminal_height})" + R
    )
    lines.append("")
    lines.append(W + "  Welcome to Modulo BBS" + R)
    lines.append(D + "  A retro bulletin board system with a modern twist." + R)
    lines.append(D + "  Version " + display() + " | Python " + sys.version.split()[0] + R)
    lines.append("")
    lines.append(G + f"  Active nodes: {active_count}/{max_nodes}" + R)
    lines.append("")
    return lines


def render_banner(session, active_count: int, max_nodes: int) -> str:
    """Full banner as a ``\\r\\n``-terminated string.

    Used as the splash fallback when ``screens/splash.txt`` is missing. ``\\r\\n``
    line endings are required so terminals return to column 0 (bare LF causes
    staircase wrapping in SyncTERM).
    """
    return "\r\n".join(banner_lines(session, active_count, max_nodes))