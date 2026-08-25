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
