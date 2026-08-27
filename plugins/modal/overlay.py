"""Overlay geometry and CUP paint for the modal plugin.

Callers do not import this — they go through ``choose`` / ``notice``.
"""
from __future__ import annotations


def overlay_geom(session) -> tuple[int, int, int, int, int]:
    """Notepad-style overlay: top, left, interior width, interior rows, text cols."""
    h = int(getattr(session, "terminal_height", 24) or 24)
    L, R = 1, 79
    top, bot = 2, max(4, h - 3)
    wid = R - L - 1
    interior = bot - top - 1
    return top, L, wid, interior, wid - 1


def compact_overlay_geom(
    session, n_rows: int, min_inner: int
) -> tuple[int, int, int, int, int]:
    """Small centered-low box: *n_rows* interior lines, fitted width.

    ANSI draw-over sits above the prompt so the screen stays visible around it.
    """
    h = int(getattr(session, "terminal_height", 24) or 24)
    inner_w = min(75, max(int(min_inner), 8))
    wid = inner_w + 1  # leading gutter space in paint_overlay
    interior = max(1, int(n_rows))
    box_h = interior + 2
    box_w = wid + 2
    L = max(1, (79 - box_w) // 2 + 1)
    top = max(2, h - box_h - 2)
    return top, L, wid, interior, inner_w


def paint_overlay(session, rows: list[str], hint: str, pal, geom=None) -> str:
    """CUP-positioned bordered box. *rows* are already inner-width (ANSI ok).

    Each row is placed with CUP so *geom* left/top stick (``\\r\\n`` would
    reset to column 1). Plain terminals skip CUP and emit a compact stack.
    """
    from shared.codecs import _ANSI_RE

    if geom is None:
        top, L, wid, interior, inner_w = overlay_geom(session)
    else:
        top, L, wid, interior, inner_w = geom
    is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
    G = "" if is_plain else pal.success
    RST = "" if is_plain else pal.reset
    if is_plain:
        tl, tr, bl, br, hb, vb = "+", "+", "+", "+", "-", "|"
    else:
        tl, tr, bl, br, hb, vb = "┌", "┐", "└", "┘", "─", "│"

    clipped = list(rows)
    if len(clipped) > interior:
        clipped = clipped[: interior - 1] + ["…"]

    def place(row: int, s: str) -> str:
        if is_plain:
            return "\r\n" + s
        return f"\x1b[{row};{L}H" + s

    parts = [place(top, f"{G}{tl}{hb * wid}{tr}{RST}")]
    for i in range(interior):
        cell = clipped[i] if i < len(clipped) else ""
        gutter = " " + cell
        vis = len(_ANSI_RE.sub("", gutter))
        if vis < wid:
            gutter = gutter + " " * (wid - vis)
        elif vis > wid:
            gutter = gutter[:wid]
        parts.append(place(top + 1 + i, f"{G}{vb}{RST}" + gutter + f"{G}{vb}{RST}"))
    hint = hint or ""
    pad_l = max(0, (wid - len(hint)) // 2)
    pad_r = max(0, wid - pad_l - len(hint))
    parts.append(
        place(
            top + 1 + interior,
            f"{G}{bl}{hb * pad_l}{RST}{hint}{G}{hb * pad_r}{br}{RST}",
        )
    )
    return "".join(parts)
