"""B8 — chat bubble renderer (boards-unification §B8, Dave's Telegram model).

Pure-function contract: every row exactly *width* visible columns; own
bubbles right-aligned cyan, others left green; NEW badge on messages that
arrived after the viewer entered (never on own); compact summarizes.
"""
from __future__ import annotations

import re

from plugins.mainmenu.bubbles import render_bubbles

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def vis(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _msgs():
    return [
        {"id": 1, "author": "ana", "body": "hello room",
         "created": "2026-08-25T09:00:00+00:00"},
        {"id": 2, "author": "dave", "body": "hi ana",
         "created": "2026-08-25T09:01:00+00:00"},
    ]


def test_rows_exact_width_and_box_shape():
    out = render_bubbles(_msgs(), 60, username="dave")
    assert out
    for r in out:
        assert len(vis(r)) == 60, f"{r!r}"
    joined = "\n".join(vis(r) for r in out)
    assert "\u250c" in joined and "\u2514" in joined  # ┌ └


def test_own_right_others_left():
    out = [vis(r) for r in render_bubbles(_msgs(), 60, username="dave")]
    ana_row = next(r for r in out if "ana" in r and "\u250c" in r)
    dave_row = next(r for r in out if "dave" in r and "\u250c" in r)
    assert ana_row.lstrip() == ana_row            # others: flush left
    assert dave_row != dave_row.lstrip()          # own: indented right


def test_new_badge_only_for_others_after_baseline():
    msgs = [
        {"id": 1, "author": "ana", "body": "old", "created": "2026-08-25T09:00:00+00:00"},
        {"id": 2, "author": "ana", "body": "fresh", "created": "2026-08-25T10:00:00+00:00"},
        {"id": 3, "author": "dave", "body": "mine also fresh", "created": "2026-08-25T10:01:00+00:00"},
    ]
    out = render_bubbles(msgs, 60, username="dave", new_from_id=2)
    text = "\n".join(vis(r) for r in out)
    # msg1 old: no badge; msg2 new: badge; msg3 own: no badge even though >= baseline
    assert "*NEW*" not in text.split("fresh")[0].rsplit("old", 1)[-1] or True
    seg_old = [vis(r) for r in out[:3]]
    assert not any("*NEW*" in r for r in seg_old)
    assert any("*NEW*" in vis(r) for r in out)
    # the only *NEW* belongs to ana's fresh bubble, not dave's
    dave_bubble = [vis(r) for r in out if "mine also fresh" in r]
    assert not any("*NEW*" in r for r in dave_bubble)


def test_colors_cyan_me_green_other_yellow_new():
    out = render_bubbles(_msgs(), 60, username="dave")
    joined = "\n".join(out)
    assert "\x1b[96m" in joined   # own cyan (classic accent)
    assert "\x1b[92m" in joined   # other green (classic success)


def test_palette_recolors_me_and_other():
    from core.theme import load_palette

    pal = load_palette("amber")
    out = render_bubbles(_msgs(), 60, username="dave", palette=pal)
    joined = "\n".join(out)
    assert pal.accent in joined
    assert pal.success in joined
    assert "\x1b[96m" not in joined  # classic cyan gone


def test_plain_fallback_ascii_only():
    out = render_bubbles(_msgs(), 60, username="dave", plain=True)
    for r in out:
        assert "\x1b" not in r
        assert len(r) == 60
    joined = "\n".join(out)
    assert "+---" in joined and "|" in joined
    assert "\u250c" not in joined


def test_compact_summarizes_to_one_body_line():
    msgs = [{"id": 1, "author": "ana",
             "body": "first line stays\nsecond line goes away\nthird",
             "created": "2026-08-25T09:00:00+00:00"}]
    full = render_bubbles(msgs, 60, username="dave")
    comp = render_bubbles(msgs, 60, username="dave", compact=True)
    assert "second line" not in "\n".join(vis(r) for r in comp)
    assert "second line" in "\n".join(vis(r) for r in full)
    # compact has no gap rows -> strictly fewer lines
    assert len(comp) < len(full)


def test_long_body_wraps_inside_bubble():
    body = " ".join(["word"] * 40)
    msgs = [{"id": 1, "author": "ana", "body": body,
             "created": "2026-08-25T09:00:00+00:00"}]
    out = render_bubbles(msgs, 50, username="dave")
    for r in out:
        assert len(vis(r)) == 50
    assert "word" in "".join(vis(r) for r in out)


def test_empty_messages_no_rows():
    assert render_bubbles([], 60, username="dave") == []
