"""B3 — Social two-pane renderer (boards-unification §B3).

Layout contract: every emitted line is exactly 79 display columns.
Sidebar cell 22 cols | thread pane 54 cols (CP437 / 1-cell glyphs).
UTF-8 Ambiguous box drawing is 2 cells; pane shrinks so the box still
stacks on the same screen columns.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.user import User
from plugins.social.social import focus_arrows, gutter_stack, render_social
from shared.visible import at_display, display_width, strip_ansi


def vis(line: str) -> str:
    return strip_ansi(line) if line else ""


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
        self.last_read = {}

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

    async def get_last_read(self, username, conv_id):
        return self.last_read.get((username, conv_id), 0)


def _session(user=None, h=24, plain=True, sel=0):
    return SimpleNamespace(
        terminal_type="UNKNOWN" if plain else "ANSI-BBS",
        terminal_height=h,
        user=user,
        _pim_selected=sel,
    )


def _all_lines_79(lines, wide=False):
    for ln in lines:
        v = vis(ln)
        if not v.startswith(("│", "+", "└")):
            continue  # tab bar above / hint below are not box rows
        w = display_width(ln, wide_ambiguous=wide)
        assert w == 79, f"bad width {w} (codepoints {len(v)}): {v!r}"


def test_plain_layout_widths_and_rows():
    async def _a():
        s = _session(User(username="dave"), sel=2)  # board selected
        out = await render_social(StubConvs(), s)
        lines = out.split("\r\n")
        _all_lines_79(lines)
        joined = out
        # sidebar furniture: DMs pinned, then separator, then rooms.
        # "+ new thread" is not a row -- N in the hint creates threads.
        assert "DMs" in joined
        assert "+ new thread" not in joined
        assert "N new thread" in joined
        # board row selected marker + unread star, title capped at 15
        assert "> General Discuss" in joined and "*" in joined
        # never opened: star only, no *NEW* on the old posts
        assert "*NEW*" not in joined
        # thread pane: compact bubbles, author-only title bars (B8)
        assert "General Discuss" in joined
        assert "dave" in joined and "api_test" in joined
        # long body got wrapped inside the pane, not truncated mid-word garbage
        assert "deliberately" in joined
        # DMs is immediately followed by the separator, not an action row
        wide = False
        side = []
        for ln in lines:
            v = vis(ln)
            if v.startswith("│") and display_width(ln, wide_ambiguous=wide) == 79:
                side.append(v[1:23])
        dms_i = next(i for i, cell in enumerate(side) if "DMs" in cell)
        assert dms_i + 1 < len(side)
        sep = side[dms_i + 1]
        assert set(sep.strip()) <= set("-─ ")
        assert "new" not in sep.lower()

    asyncio.run(_a())


def test_preview_new_badge_only_on_mail_since_last_leave():
    async def _a():
        c = StubConvs()
        c.last_read = {("dave", "general-discussion"): 1}
        c.unread[("dave", "general-discussion")] = 1
        s = _session(User(username="dave"), sel=2)
        out = await render_social(c, s)
        assert "*NEW*" in out
        assert "api_test" in out
        # dave's older post is below the watermark — no badge on that body
        dave_block = out.split("api_test")[0]
        assert "*NEW*" not in dave_block

    asyncio.run(_a())


def test_selection_follows_room_when_activity_reorders():
    """Hover stays on the room, not the slot, when another board jumps to top."""

    async def _a():
        c = StubConvs()
        c.index.append({
            "id": "newtest-1", "kind": "board", "title": "newtest 1",
            "created": "2026-08-20T11:00:00+00:00", "requires": [],
            "participants": [], "message_count": 1,
            "last_message_at": "2026-08-24T12:00:00+00:00",
        })
        c.msgs["newtest-1"] = [
            {"id": 1, "author": "dave", "body": "quiet thread",
             "created": "2026-08-24T12:00:00+00:00"},
        ]
        # DMs, newtest 1 (newer), General Discussion (older)
        s = _session(User(username="dave"), sel=1)
        first = await render_social(c, s)
        assert "quiet thread" in first
        assert getattr(s, "_social_selected_id", "") == "newtest-1"
        # General gets newer mail and sorts above newtest; index 1 would be General
        c.index[0]["last_message_at"] = "2026-08-25T09:00:00+00:00"
        c.msgs["general-discussion"].append({
            "id": 3, "author": "api_test", "body": "hello from General",
            "created": "2026-08-25T09:00:00+00:00",
        })
        c.index[0]["message_count"] = 4
        again = await render_social(c, s)
        assert "quiet thread" in again
        assert "hello from General" not in again
        assert s._pim_selected == 2
        assert s._social_selected_id == "newtest-1"

    asyncio.run(_a())


def _mid_gutter(out: str, wide: bool = False) -> str:
    """Visible chars of the divider between sidebar and pane."""
    gutter_col = 24 if wide else 23
    chars = []
    for ln in out.split("\r\n"):
        v = vis(ln)
        if not v.startswith("│"):
            continue
        if display_width(ln, wide_ambiguous=wide) != 79:
            continue
        chars.append(at_display(v, gutter_col, wide_ambiguous=wide))
    return "".join(chars)


def test_gutter_stack_points_at_the_column_you_can_enter():
    rooms = gutter_stack(9, True, "<", ">", "|")
    assert "".join(rooms) == "|>ENTER>|"
    thread = gutter_stack(9, False, "<", ">", "|")
    assert "".join(thread) == "||<ESC<||"
    assert gutter_stack(3, True, "<", ">", "|") == [">", "E", "N"]


def test_focus_arrows_follow_codec():
    s = _session(User(username="dave"))
    assert focus_arrows(s, True) == ("<", ">")
    s.codec = "utf-8"
    assert focus_arrows(s, False) == ("←", "→")
    s.codec = "cp437"
    assert focus_arrows(s, False) == ("<", ">")


def test_utf8_gutter_uses_unicode_arrows():
    async def _a():
        s = _session(User(username="dave"), plain=False, sel=0)
        s.codec = "utf-8"
        mid = _mid_gutter(await render_social(StubConvs(), s), wide=False)
        assert "→ENTER→" in mid

    asyncio.run(_a())


def test_browse_gutter_points_right_with_enter():
    async def _a():
        s = _session(User(username="dave"), sel=0)
        mid = _mid_gutter(await render_social(StubConvs(), s))
        assert ">ENTER>" in mid
        assert "<" not in mid

    asyncio.run(_a())


def test_thread_gutter_points_left_with_esc():
    async def _a():
        s = _session(User(username="dave"), sel=0)
        mid = _mid_gutter(
            await render_social(StubConvs(), s, compact=False)
        )
        assert "<ESC<" in mid
        assert ">" not in mid

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
        from core.theme import load_palette

        s = _session(User(username="dave"), plain=False, sel=0)
        out = await render_social(StubConvs(), s)
        pal = load_palette("classic")
        assert pal.tab_bg in out  # highlight=15,1 → blue selection bar
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


def test_pane_leaves_rows_for_tab_bar_and_prompt():
    """Social pane + tab bar + trailing CRLF must not fill 24 rows.

    `_show_menu` paints the tab bar, then this pane, then a trailing CRLF,
    then CUPs the prompt onto the last line. A 23-line pane scrolled
    SyncTERM and the tab bar disappeared.
    """
    async def _a():
        s = _session(User(username="dave"), h=24, sel=1)
        out = await render_social(StubConvs(), s)
        lines = out.split("\r\n")
        assert len(lines) == 22, f"expected 22 pane lines, got {len(lines)}"
        _all_lines_79(lines)

    asyncio.run(_a())


def test_utf8_box_bars_stack_on_dash_and_empty_rows():
    """Frame bars sit on the same columns on dash rows and empty rows."""

    async def _a():
        c = StubConvs()
        c.index.append({
            "id": "newtest-1", "kind": "board", "title": "newtest 1",
            "created": "2026-08-20T11:00:00+00:00", "requires": [],
            "participants": [], "message_count": 1,
            "last_message_at": "2026-08-21T12:00:00+00:00",
        })
        c.msgs["newtest-1"] = [
            {"id": 1, "author": "dave", "body": "hello",
             "created": "2026-08-21T12:00:00+00:00"},
        ]
        s = _session(User(username="dave"), plain=False, sel=1)
        s.codec = "utf-8"
        s.terminal_type = "xterm-256color"
        out = await render_social(c, s)
        lines = out.split("\r\n")
        _all_lines_79(lines, wide=False)
        box = [vis(ln) for ln in lines if vis(ln).startswith("│")]
        last_topic = next(i for i, v in enumerate(box) if "newtest" in v)
        empty_after = last_topic + 1
        sep = next(v for v in box if "─" in v[1:12])
        for row in (box[0], sep, box[last_topic], box[empty_after]):
            assert display_width(row, wide_ambiguous=False) == 79
            assert at_display(row, 0, wide_ambiguous=False) == "│"
            assert at_display(row, 23, wide_ambiguous=False) in "│→←ENTER"
            assert at_display(row, 78, wide_ambiguous=False) == "│"
            sid = vis(row)[1:23]
            assert len(sid) == 22, sid

    asyncio.run(_a())


def test_utf8_two_cell_box_bars_stack_when_probed_wide():
    """xterm.js-style Ambiguous=2: layout shrinks so │ still stack at 79 display."""

    async def _a():
        c = StubConvs()
        c.index.append({
            "id": "newtest-1", "kind": "board", "title": "newtest 1",
            "created": "2026-08-20T11:00:00+00:00", "requires": [],
            "participants": [], "message_count": 1,
            "last_message_at": "2026-08-21T12:00:00+00:00",
        })
        c.msgs["newtest-1"] = [
            {"id": 1, "author": "dave", "body": "hello",
             "created": "2026-08-21T12:00:00+00:00"},
        ]
        s = _session(User(username="dave"), plain=False, sel=1)
        s.codec = "utf-8"
        s.wide_ambiguous = True
        s.terminal_type = "xterm-256color"
        out = await render_social(c, s)
        lines = out.split("\r\n")
        _all_lines_79(lines, wide=True)
        box = [vis(ln) for ln in lines if vis(ln).startswith("│")]
        last_topic = next(i for i, v in enumerate(box) if "newtest" in v)
        empty_after = last_topic + 1
        sep = next(v for v in box if "─" in v[1:8])
        for row in (box[0], sep, box[last_topic], box[empty_after]):
            assert display_width(row, wide_ambiguous=True) == 79
            assert at_display(row, 0, wide_ambiguous=True) == "│"
            assert at_display(row, 24, wide_ambiguous=True) in "│→←ENTER"
            assert at_display(row, 77, wide_ambiguous=True) == "│"

    asyncio.run(_a())
