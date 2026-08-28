"""Display-column math for 79-col BBS rows.

Box drawing is Unicode East Asian Width *Ambiguous*: one cell on Linux
glibc / SyncTERM, two cells on some xterm.js terminals. Padding by
codepoint then wraps the dash-heavy row under the last topic (~18 extra
cells) and the next line looks like a short ``│   │``.

:func:`wide_ambiguous_for` follows ``session.wide_ambiguous``, set at
login by a DSR probe of ``│``. Callers pass that into fit/fill so both
kinds of terminal get 79 *display* columns.
"""
from __future__ import annotations

import re
import unicodedata

# CSI including private (?), SCS, OSC … ST/BEL. Bare ESC is dropped later.
_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-9;?]*[A-Za-z]"
    r"|][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|[()][AB012]"
    r")"
)


def strip_ansi(s: str) -> str:
    """Drop SGR/CSI/OSC. Keep CP437 C0 arrows (0x18-0x1B); drop other C0."""
    out = _ANSI_RE.sub("", s or "")
    keep = {0x18, 0x19, 0x1A, 0x1B}
    result = []
    i = 0
    n = len(out)
    while i < n:
        o = ord(out[i])
        if o == 0x1B and i + 1 < n and out[i + 1] == "[":
            i += 1
            continue
        if o < 32 and o not in keep:
            i += 1
            continue
        result.append(out[i])
        i += 1
    return "".join(result)


def sanitize_cell(text: str) -> str:
    """Strip control bytes that a terminal would read as an escape sequence.

    For *data* going into a box-drawn cell (titles, authors, bodies), not
    for chrome. Colour we generate ourselves is kept; a stray ESC from the
    store is not, because the terminal eats the rest of the row and the
    frame's divider jumps left (2026-08-28: a board titled ESC).
    """
    if not text:
        return ""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            m = _ANSI_RE.match(text, i)
            if m:  # our own SGR/CSI — keep it, it paints no columns
                out.append(m.group(0))
                i = m.end()
                continue
            i += 1  # bare ESC from data: drop it
            continue
        if ch.isprintable() or ch == " ":
            out.append(ch)
        i += 1
    return "".join(out)


def wide_ambiguous_for(session, is_plain: bool | None = None) -> bool:
    """True when this session's box-drawing glyphs occupy two cells.

    Set by :func:`shared.codecs.probe_ambiguous_width` after UTF-8 login.
    Unprobed sessions (tests, CP437) stay 1-cell so Linux glibc terminals
    never get the short-row leftover paint.
    """
    if is_plain is None:
        is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
    if is_plain:
        return False
    return bool(getattr(session, "wide_ambiguous", False))


def char_width(ch: str, *, wide_ambiguous: bool = False) -> int:
    if not ch:
        return 0
    o = ord(ch)
    if o in (0x18, 0x19, 0x1A, 0x1B):
        return 1  # CP437 arrows (0x1B is also ESC; CSI is stripped first)
    if o < 32:
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F") or (wide_ambiguous and eaw == "A"):
        return 2
    return 1


def display_width(s: str, *, wide_ambiguous: bool = False) -> int:
    return sum(char_width(ch, wide_ambiguous=wide_ambiguous) for ch in strip_ansi(s))


def fit_display(s: str, width: int, *, wide_ambiguous: bool = False) -> str:
    """Pad or clip *s* to exactly *width* display columns.

    ANSI is kept when the visible text already fits; overflow strips
    colour and clips. A leftover 1-col gap (2-wide glyph won't fit) is
    a space so the next ``│`` still lands on the same screen column.
    """
    if width < 0:
        width = 0
    vis = strip_ansi(s)
    dw = display_width(vis, wide_ambiguous=wide_ambiguous)
    if dw <= width:
        return s + " " * (width - dw)
    out = []
    used = 0
    for ch in vis:
        w = char_width(ch, wide_ambiguous=wide_ambiguous)
        if w <= 0:
            continue
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out) + " " * (width - used)


def fill_display(ch: str, width: int, *, wide_ambiguous: bool = False) -> str:
    """Repeat *ch* up to *width* display columns, space-pad the remainder."""
    if width <= 0:
        return ""
    cw = char_width(ch, wide_ambiguous=wide_ambiguous) or 1
    n = width // cw
    s = ch * n
    return s + " " * (width - display_width(s, wide_ambiguous=wide_ambiguous))


def hline(left: str, fill: str, right: str, width: int, *, wide_ambiguous: bool = False) -> str:
    """``left`` + fill + ``right`` occupying exactly *width* display columns."""
    ends = display_width(left, wide_ambiguous=wide_ambiguous) + display_width(
        right, wide_ambiguous=wide_ambiguous
    )
    inner = max(0, width - ends)
    return left + fill_display(fill, inner, wide_ambiguous=wide_ambiguous) + right


def overlay_display(
    base: str, at: int, text: str, width: int, *, wide_ambiguous: bool = False
) -> str:
    """Place *text* at display column *at* of *base*; result is *width* cols."""
    tw = display_width(text, wide_ambiguous=wide_ambiguous)
    left = fit_display(base, at, wide_ambiguous=wide_ambiguous)
    rest_from = at + tw
    skipped = 0
    rest_chars = []
    for ch in strip_ansi(base):
        w = char_width(ch, wide_ambiguous=wide_ambiguous)
        if skipped >= rest_from:
            rest_chars.append(ch)
        skipped += w
    right = fit_display("".join(rest_chars), max(0, width - at - tw), wide_ambiguous=wide_ambiguous)
    return fit_display(left + text + right, width, wide_ambiguous=wide_ambiguous)


def center_display(s: str, width: int, *, wide_ambiguous: bool = False) -> str:
    sw = display_width(s, wide_ambiguous=wide_ambiguous)
    pad = max(0, width - sw)
    left = pad // 2
    return " " * left + s + " " * (pad - left)


def at_display(s: str, col: int, *, wide_ambiguous: bool = False) -> str:
    """Glyph occupying display column *col* (0-based), or ''."""
    d = 0
    for ch in strip_ansi(s):
        w = char_width(ch, wide_ambiguous=wide_ambiguous)
        if w <= 0:
            continue
        if d <= col < d + w:
            return ch
        d += w
    return ""


def slice_display(s: str, start: int, end: int, *, wide_ambiguous: bool = False) -> str:
    """Glyphs whose display span overlaps ``[start, end)``."""
    out = []
    d = 0
    for ch in strip_ansi(s):
        w = char_width(ch, wide_ambiguous=wide_ambiguous)
        if w <= 0:
            continue
        if d + w > start and d < end:
            out.append(ch)
        d += w
        if d >= end:
            break
    return "".join(out)
