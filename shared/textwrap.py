"""Minimal text wrapping for BBS panes (boards-unification §B1).

Pure ``len()`` math over already-decoded str — CP437 glyphs are single
width once decoded, so no encoding awareness lives here. Greedy fill;
words longer than *width* are hard-broken; explicit newlines in the
input are preserved as line breaks. No ANSI awareness by design (callers
wrap plain text; styling is applied per-line afterwards).
"""
from __future__ import annotations


def wrap(text: str, width: int) -> list[str]:
    """Wrap *text* to at most *width* characters per line.

    Returns a list of lines (never empty; ``""`` yields ``[""]``).
    """
    if width < 1:
        width = 1

    lines: list[str] = []
    for raw in text.split("\n"):
        cur = ""
        for word in raw.split(" "):
            while len(word) > width:
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(word[:width])
                word = word[width:]
            if not word:
                continue
            if not cur:
                cur = word
            elif len(cur) + 1 + len(word) <= width:
                cur = f"{cur} {word}"
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def wrap_rows(text: str, width: int) -> list[tuple[int, int]]:
    """Soft-wrap *text* into fixed-box display rows, tracking offsets.

    Returns ``(start, length)`` pairs that partition *text* into contiguous,
    non-overlapping rows of at most *width* chars — ``text[start:start +
    length]`` is each displayed row. Greedy word wrap on spaces (spaces at a
    wrap boundary are dropped from the layout); words longer than *width*
    hard-break; an explicit newline ends the current row, and a trailing
    newline yields a final zero-length row so a caret can rest on the blank
    last line. Never empty: ``""`` yields ``[(0, 0)]``.
    """
    if width < 1:
        width = 1

    rows: list[tuple[int, int]] = []
    ls = 0
    n = len(text)
    while ls <= n:
        nl = text.find("\n", ls)
        le = n if nl == -1 else nl
        cur = ls
        while True:
            if le - cur <= width:
                rows.append((cur, le - cur))
                break
            brk = text.rfind(" ", cur + 1, cur + width + 1)
            seg = (brk - cur) if brk != -1 else width
            rows.append((cur, seg))
            cur += seg
            while cur < le and text[cur] == " ":
                cur += 1
        if nl == -1:
            break
        ls = nl + 1
    return rows
