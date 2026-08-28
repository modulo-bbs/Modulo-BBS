"""Chat bubble renderer (boards-unification B8 — Dave's Telegram-style chat).

Pure function: messages in, display rows out. CP437-safe box drawing,
every emitted row exactly ``width`` visible columns, ANSI-colored unless
plain. Own messages align right in the theme accent, others align left in
success; messages that arrived after the viewer last left the room carry a
NEW tag (warning colour on ANSI). Pass ``palette=`` to recolor; default
is the classic cyan/green look so tests stay stable.
"""
from __future__ import annotations

import re

from core.theme import Palette, load_palette
from shared.textwrap import wrap
from shared.visible import display_width, fit_display, hline

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _stamp(created: str) -> str:
    return (created or "")[5:16].replace("T", " ")


def render_bubbles(
    msgs: list[dict],
    width: int,
    *,
    username: str = "",
    new_from_id: int = 0,
    plain: bool = False,
    compact: bool = False,
    palette: Palette | None = None,
    wide_ambiguous: bool = False,
) -> list[str]:
    """Render *msgs* (oldest first) as stacked chat bubbles.

    ``new_from_id`` marks the first id considered new since last leave
    (badge on other people's messages only; 0 = none). ``compact`` summarizes:
    author-only title bar, first body line only, no gap rows.
    ``width`` is display columns (UTF-8 Ambiguous glyphs may be 2).
    """
    if width < 9:
        width = 9

    pal = palette or load_palette("classic")

    if plain:
        TL, TR, BL, BR, H, V = "+", "+", "+", "+", "-", "|"
    else:
        TL, TR, BL, BR, H, V = "\u250c", "\u2510", "\u2514", "\u2518", "\u2500", "\u2502"

    C_ME = "" if plain else pal.accent
    C_OTHER = "" if plain else pal.success
    C_NEW = "" if plain else pal.warning
    RST = "" if plain else pal.reset

    def _dw(s: str) -> int:
        return display_width(s, wide_ambiguous=wide_ambiguous)

    def _fit(s: str, n: int) -> str:
        return fit_display(s, n, wide_ambiguous=wide_ambiguous)

    rows: list[str] = []
    for m in msgs:
        author = str(m.get("author", "?"))
        mine = author == username
        is_new = int(m.get("id", 0)) >= new_from_id > 0 and not mine
        new_txt = " *NEW*" if is_new else ""

        # --- bubble content -------------------------------------------------
        title = author + ("" if compact else f" {_stamp(m.get('created', ''))}") + new_txt
        vpad = _dw(V) + 1 + 1 + _dw(V)
        body_all = wrap((m.get("body", "") or "").replace("\r", ""), max(4, width - vpad))
        if compact and len(body_all) > 1:
            body_all = [body_all[0].rstrip() + ".."]

        title_prefix = f"{TL}{H} "
        title_min = _dw(title_prefix) + _dw(title) + 1 + _dw(TR)
        body_min = max((_dw(l) + vpad) for l in body_all) if body_all else vpad
        bw = min(width, max(title_min, body_min, vpad + 1))
        inner_w = max(1, bw - vpad)

        pad_title = max(1, bw - _dw(title_prefix) - _dw(title) - _dw(TR))
        plain_rows = [f"{title_prefix}{title}{' ' * pad_title}{TR}"]
        shown = [body_all[0]] if compact else body_all
        for bl in shown:
            plain_rows.append(f"{V} {_fit(bl, inner_w)} {V}")
        plain_rows.append(hline(BL, H, BR, bw, wide_ambiguous=wide_ambiguous))
        plain_rows = [_fit(pr, bw) for pr in plain_rows]

        # --- alignment + color ----------------------------------------------
        pad = (width - bw) if mine else 0
        color = C_ME if mine else C_OTHER
        for pr in plain_rows:
            if new_txt and pr is plain_rows[0]:
                at = pr.index(new_txt)
                row = (
                    f"{color}{pr[:at]}"
                    f"{C_NEW}{new_txt}"
                    f"{RST}{color}{pr[at + len(new_txt):]}{RST}"
                )
            elif plain:
                row = pr
            else:
                row = f"{color}{pr}{RST}"
            rows.append(" " * pad + row)

        if not compact:
            rows.append(" " * width)

    while rows and not strip_vis(rows[-1]).strip():
        rows.pop()
    return [_fit(r, width) for r in rows]


def strip_vis(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _vis(s: str) -> str:
    return strip_vis(s)
