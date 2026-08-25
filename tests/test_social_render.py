"""B3 — Social two-pane renderer (boards-unification §B3).

Layout contract: every emitted line is exactly 79 visible columns.
Sidebar cell 22 cols | thread pane 54 cols inside the standard chrome.
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

from core.user import User
from plugins.mainmenu.social import render_social

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def vis(line: str) -> str:
    return _ANSI_RE.sub("", line)


class StubConvs:
    """Canned conversations service — deterministic golden material."""

    def __init__(self):
        self.index = [
            {"id": "general-discussion", "kind": "board", "title": "General Discussion",
             "created": "2026-08-20T10:00:00+00:00", "requires": [],
             "participants": [], "message_count": 3,
             "last_message_at": "2026-08-22T20:27:00+00:00"},
            {"id": "dm-ana", "kind": "dm", "title": "Ana",
             "created": "2026-08-21T10:00:00+00:00", "requires": [],
             "participants": ["ana", "dave"], "message_count": 1,
             "last_message_at": "2026-08-23T09:00:00+00:00"},
        ]
        self.msgs = {
            "general-discussion": [
                {"id": 1, "author": "dave", "body": "Test 123",
                 "created": "2026-08-22T20:27:21+00:00"},
                {"id": 2, "author": "api_test",
                 "body": "This body line is deliberately far longer than fifty one "
                         "columns so the wrap helper must break it.",
                 "created": "2026-08-23T10:00:00+00:00"},
            ],
            "dm-ana": [
                {"id": 1, "author": "ana", "body": "hey!",
                 "created": "2026-08-23T09:00:00+00:00"},
            ],
        }
        self.unread = {("dave", "dm-ana"): 2, ("dave", "general-discussion"): 2}

    async def list_conversations(self, *, kind=None, visible_to=None):
        out = [c for c in self.index if c["kind"] == kind]
        if visible_to is None:
            out = [c for c in out if c["kind"] not in ("dm", "group")]
        else:
            out = [c for c in out
                   if c["kind"] not in ("dm", "group")
                   or visible_to.username in c.get("participants", [])]
        return list(out)

    async def list_messages(self, conv_id):
        return list(self.msgs.get(conv_id, []))

    async def get_conversation(self, conv_id):
        return next((c for c in self.index if c["id"] == conv_id), None)

    async def unread_count(self, username, conv_id):
        return self.unread.get((username, conv_id), 0)


def _session(user=None, h=24, plain=True, sel=0):
    return SimpleNamespace(
        terminal_type="UNKNOWN" if plain else "ANSI-BBS",
        terminal_height=h,
        user=user,
        _pim_selected=sel,
    )


def _all_lines_79(lines):
    for ln in lines:
        v = vis(ln)
        if not v.startswith(("│", "+", "└")):
            continue  # tab bar above / hint below are not box rows
        assert len(v) == 79, f"bad width {len(v)}: {ln!r}"


def test_plain_layout_widths_and_rows():
    async def _a():
        s = _session(User(username="dave"), sel=2)  # board selected
        out = await render_social(StubConvs(), s)
        lines = out.split("\r\n")
        _all_lines_79(lines)
        joined = out
        # sidebar furniture
        assert "DMs" in joined and "+ new thread" in joined
        # board row selected marker + unread star, title capped at 15
        assert "► General Discuss" in joined and "*" in joined
        # thread pane header + message meta line
        assert "(2 msgs)" in joined
        assert "#1 [dave]" in joined and "08-22 20:27" in joined
        # long body got wrapped inside the pane, not truncated mid-word garbage
        assert "deliberately" in joined

    asyncio.run(_a())


def test_selection_moves_highlight_and_pane():
    async def _a():
        c = StubConvs()
        s0 = _session(User(username="dave"), sel=0)
        out0 = await render_social(c, s0)
        s1 = _session(User(username="dave"), sel=2)  # general-discussion row
        out1 = await render_social(c, s1)
        # DMs selected first: pane shows ana's DM (most recent dm)
        assert "ana" in out0
        # board selected: pane header switches to the board
        assert "(2 msgs)" in out1 and "Test 123" in out1

    asyncio.run(_a())


def test_ansi_variant_highlights_selected_row():
    async def _a():
        s = _session(User(username="dave"), plain=False, sel=0)
        out = await render_social(StubConvs(), s)
        assert "\x1b[7m" in out or "\x1b[2m" in out  # REVERSE or DIM used
        _all_lines_79(out.split("\r\n"))

    asyncio.run(_a())


def test_scroll_window_over_long_threads():
    async def _a():
        c = StubConvs()
        c.msgs["general-discussion"] = [
            {"id": n, "author": f"u{n}", "body": f"msg {n}",
             "created": "2026-08-22T20:00:00+00:00"}
            for n in range(1, 41)
        ]
        c.index[0]["message_count"] = 40
        s = _session(User(username="dave"), sel=2)
        bottom = await render_social(c, s)
        assert "msg 40" in bottom and "msg 1\r\n" not in bottom.replace("\r\n\r\n", "\r\n")
        # scroll up a page -> earliest messages appear
        s._social_scroll_up = 10_000  # clamp to top
        top = await render_social(c, s)
        assert "msg 1" in top

    asyncio.run(_a())


def test_empty_room_shows_placeholder():
    async def _a():
        c = StubConvs()
        c.index = [dict(c.index[0], message_count=0, last_message_at=None)]
        c.msgs["general-discussion"] = []
        s = _session(User(username="dave"), sel=1)
        out = await render_social(c, s)
        assert "(no messages yet" in out

    asyncio.run(_a())
