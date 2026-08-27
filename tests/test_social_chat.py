"""B8 — Social chat: one-line prompt, Enter/ESC compose, overlay notepad.

Enter with text opens Post / Editor / Discard (Post default). Empty Enter,
wrap, or LF opens the notepad; ESC keeps the draft. Posting always goes
through the picker. Ctrl-S / Ctrl-E do not send.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core import runner
from core.app import BBSApp
from core.user import User
from plugins.mainmenu import (
    MainmenuPlugin,
    _collapse_overlay_spacing,
)
from plugins.modal import ModalPlugin
from plugins.modal.overlay import compact_overlay_geom, paint_overlay
from server.session import Session


class FakeWriter:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf.extend(data)

    def is_closing(self):
        return False

    async def drain(self):
        pass


def _app(tmp_path: Path):
    app = BBSApp(users_dir=tmp_path / "users")
    app.storage.plugins_dir = tmp_path / "plugins"
    app.screens.plugins_root = tmp_path
    from core.conversations import Conversations

    app.conversations = Conversations(app)
    from plugins.bulletins import BulletinsPlugin
    from plugins.dashboard import DashboardPlugin
    from plugins.files import FilesPlugin
    from plugins.social import SocialPlugin

    loaded = []
    for cls in (ModalPlugin, DashboardPlugin, SocialPlugin, FilesPlugin, BulletinsPlugin, MainmenuPlugin):
        inst = cls()
        inst.on_load(app)
        loaded.append(inst)
    app.plugins = loaded
    return app


def _session(user=None):
    s = Session(session_id="t", node_id=1, address=("h", 1))
    s.writer = FakeWriter()  # type: ignore[attr-defined]
    s.terminal_type = "UNKNOWN"
    s.terminal_width = 80
    s.terminal_height = 24
    s.user = user
    s.username = user.username if user else ""
    return s


def _seed(app):
    async def _a():
        await app.conversations.create_conversation(
            kind="board", title="General", created_by="dave", conv_id="b1")
        await app.conversations.post_message("b1", author="ana", body="hello room")

    asyncio.run(_a())


def _run_chat(s, app, conv, keys):
    p = app.get_plugin("social")
    orig = runner.read_key

    async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
        return next(keys)

    runner.read_key = fake_rk  # type: ignore[assignment]
    try:
        asyncio.run(p._social_chat(s, conv))
    finally:
        runner.read_key = orig  # type: ignore[assignment]


def _plain(s):
    import re as _re

    return _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "",
                   bytes(s.writer.buf).decode("cp437", errors="replace"))


def test_typing_at_prompt_posts_and_esc_exits(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    s.terminal_type = "ANSI-BBS"
    p = app.get_plugin("social")

    async def _a():
        keys = iter(["h", "i", " ", "t", "h", "e", "r", "e", "ENTER", "ENTER", "ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return next(keys)

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await p._social_chat(s, {"id": "b1", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]

        msgs = await app.conversations.list_messages("b1")
        assert msgs[-1]["body"] == "hi there"
        assert msgs[-1]["author"] == "dave"
        text = bytes(s.writer.buf).decode("cp437", errors="replace")
        import re as _re

        plain = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
        assert "\u250c" in text
        assert "> hi there" in plain
        assert "Post" in plain

    asyncio.run(_a())


def test_enter_at_end_always_tail_anchored(tmp_path):
    app = _app(tmp_path)

    async def _seed_many():
        await app.conversations.create_conversation(
            kind="board", title="General", created_by="dave", conv_id="big")
        for n in range(1, 41):
            await app.conversations.post_message("big", author=f"u{n}", body=f"msg {n}")

    asyncio.run(_seed_many())
    dave = User(username="dave", groups=[])
    s = _session(dave)
    p = app.get_plugin("social")

    async def _a():
        keys = iter(["ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return next(keys)

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await p._social_chat(s, {"id": "big", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]

        text = bytes(s.writer.buf).decode("cp437", errors="replace")
        assert "msg 40" in text
        assert "msg 1\n" not in text.replace("\r\n", "\n").split("msg 5")[0]

    asyncio.run(_a())


def test_up_scrolls_history_down_returns(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    p = app.get_plugin("social")

    async def _a():
        keys = iter(["UP", "UP", "DOWN", "ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return next(keys)

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await p._social_chat(s, {"id": "b1", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]
        text = bytes(s.writer.buf).decode("cp437", errors="replace")
        assert "history" in text

    asyncio.run(_a())


def test_echo_suppressed_during_chat_restored_after(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    p = app.get_plugin("social")

    async def _a():
        assert not getattr(s, "suppress_echo", False)
        keys = iter(["ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return next(keys)

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await p._social_chat(s, {"id": "b1", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]
        assert not getattr(s, "suppress_echo", False)

    asyncio.run(_a())


def test_entry_crlf_lf_does_not_open_editor(tmp_path):
    """Enter on the room list is CR; SyncTERM stashes the trailing LF.
    That leftover must not open the notepad as if it were empty Enter."""
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    s._line_buffer = "\n"
    _run_chat(s, app, {"id": "b1", "title": "General"}, iter(["ESC"]))
    assert "[2 lines]" not in _plain(s)
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1


def test_empty_enter_opens_editor(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(["ENTER"] + list("hi") + ["ESC", "ESC"]))
    text = _plain(s)
    assert "+----" in text or "\u250c" in bytes(s.writer.buf)
    assert "ESC back" in text
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1


def test_typed_enter_then_enter_posts(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(list("hi") + ["ENTER", "ENTER", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "hi"


def test_picker_down_opens_editor(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(list("hi") + ["ENTER", "DOWN", "ENTER"] + list("xy")
                   + ["ESC", "ENTER", "ENTER", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "hixy"


def test_picker_discard_clears_draft(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(list("hi") + ["ENTER", "DOWN", "DOWN", "ENTER", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1
    assert "> hi" in _plain(s)


def test_compose_picker_overlay_is_compact():
    """Three options: a small CUP box, not the notepad's full-screen geom."""
    from core.theme import load_palette

    s = _session()
    s.terminal_type = "ANSI-BBS"
    s.terminal_height = 24
    geom = compact_overlay_geom(s, n_rows=3, min_inner=28)
    top, L, wid, interior, inner_w = geom
    assert interior == 3
    assert wid < 50
    assert top >= 14
    assert L > 1
    pal = load_palette("classic")
    painted = paint_overlay(
        s, ["Post", "Editor", "Discard"], " arrows  Enter  ESC ", pal, geom=geom
    )
    assert painted.count("\x1b[") >= 5
    assert "\r\n" not in painted


def test_picker_esc_keeps_draft(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(list("hi") + ["ENTER", "ESC", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1
    assert "> hi" in _plain(s)


def test_lf_opens_editor_with_newline(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(["h", "i", "LF", "y", "o", "ESC", "ESC"]))
    text = _plain(s)
    assert "[2 lines]" in text
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1


def test_wrap_opens_editor_with_wrapping_char(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    keys = ["x"] * 78 + ["ESC", "ESC"]
    _run_chat(s, app, {"id": "b1", "title": "General"}, iter(keys))
    text = _plain(s)
    assert "[2 lines]" in text
    assert "  x" not in text.split("[2 lines]")[0]  # prompt never grew a wrap row
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1


def test_wrap_then_post_keeps_full_line(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    keys = ["x"] * 78 + ["ESC", "ENTER", "ENTER", "ESC"]
    _run_chat(s, app, {"id": "b1", "title": "General"}, iter(keys))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "x" * 78


def test_enter_posts_multiline_body_with_newlines_intact(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(["ENTER", "a", "ENTER", "b", "ESC", "ENTER", "ENTER", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "a\nb"
    assert msgs[-1]["author"] == "dave"


def test_newline_only_draft_is_not_posted(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(["LF", "ESC", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1


def test_crlf_trailing_lf_does_not_leak_into_next_draft(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)

    async def _a():
        keys = iter(["h", "i", "ENTER", "ENTER", "o", "ENTER", "ENTER", "ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            key = next(keys)
            if key == "ENTER":
                sess._line_buffer = "\n"
            return key

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await app.get_plugin("social")._social_chat(
                s, {"id": "b1", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]

    asyncio.run(_a())
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-2]["body"] == "hi"
    assert msgs[-1]["body"] == "o"


def test_no_emitted_segment_exceeds_79_visible_columns(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    body = ("OK, So here is another attempt at writing a long message "
            "first wraparound will be tested, and you see what happened.")
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(list(body) + ["ESC", "ESC"]))

    import re as _re

    raw = bytes(s.writer.buf).decode("cp437", errors="replace")
    W, H = 80, 24
    row = col = 1
    for tok in _re.split(r"(\x1b\[[0-9;]*[A-Za-z])", raw):
        if not tok:
            continue
        if tok.startswith("\x1b["):
            if tok[-1] == "H":
                p = tok[2:-1].split(";")
                row = int(p[0] or 1)
                col = int(p[1] or 1) if len(p) > 1 else 1
            continue
        for ch in tok:
            if ch == "\r":
                col = 1
            elif ch == "\n":
                row += 1
            elif ch == "\x1b":
                continue
            else:
                if col > W:
                    row += 1
                    col = 1
                col += 1
            if row > H:
                pytest.fail(
                    f"output scrolls a 24-row terminal at row={row} "
                    f"col={col} near {raw[max(0, raw.find(tok)-40):raw.find(tok)+20]!r}")


def test_collapsed_preview_after_editor(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    keys: list[str] = ["ENTER"]
    for n in range(1, 11):
        keys.extend(f"L{n:02d}")
        if n < 10:
            keys.append("ENTER")
    keys.extend(["ESC", "ESC"])
    _run_chat(s, app, {"id": "b1", "title": "General"}, iter(keys))
    text = _plain(s)
    assert "[10 lines]" in text
    assert "Ctrl-E" not in text


def test_notepad_save_collapses_blank_lines_to_single_spacing():
    assert _collapse_overlay_spacing("up\n\n\n\ndown") == "up\ndown"
    assert _collapse_overlay_spacing("up\n   \n\ndown") == "up\ndown"
    assert _collapse_overlay_spacing("keep\nthis") == "keep\nthis"
    assert _collapse_overlay_spacing("  indented") == "  indented"


def test_post_collapses_tomfoolery_blank_lines(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    keys = (
        ["ENTER"] + list("up") + ["ENTER"] * 8 + list("down")
        + ["ESC", "ENTER", "ENTER", "ESC"]
    )
    _run_chat(s, app, {"id": "b1", "title": "General"}, iter(keys))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "up\ndown"


def test_editor_esc_keeps_text_does_not_post(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    before = asyncio.run(app.conversations.list_messages("b1"))
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(list("hi") + ["ENTER", "DOWN", "ENTER"] + list("xy") + ["ESC", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs == before
    assert "> [1 lines]" in _plain(s) or "> hixy" in _plain(s)


def test_ctrl_s_does_not_post(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(["ENTER"] + list("xy") + ["CTRL_S", "ESC", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1
    assert "Ctrl-S save" not in _plain(s)
    assert "ESC back" in _plain(s)


def test_editor_esc_then_post(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(list("hi") + ["ENTER", "DOWN", "ENTER"] + list("xy")
                   + ["ESC", "ENTER", "ENTER", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "hixy"


def test_tall_draft_collapses_to_preview_then_posts(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    keys = ["ENTER"] + ["a"] * 240 + ["ESC", "ENTER", "ENTER", "ESC"]
    _run_chat(s, app, {"id": "b1", "title": "General"}, iter(keys))
    text = _plain(s)
    assert "[4 lines]" in text
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "a" * 240


def test_notepad_arrows_insert_midline(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    keys = ["ENTER"] + list("ac") + ["LEFT", "b", "ESC", "ENTER", "ENTER", "ESC"]
    _run_chat(s, app, {"id": "b1", "title": "General"}, iter(keys))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "abc"


def test_notepad_capacity_capped_to_box(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    s.terminal_height = 8
    p = app.get_plugin("social")

    async def _a():
        keys = iter(["x"] * 200 + ["ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            return next(keys)

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            posted, draft = await p._social_overlay_editor(
                s, {"id": "b1", "title": "General"}, "")
        finally:
            runner.read_key = orig  # type: ignore[assignment]
        assert posted is False
        assert draft == "x" * 152

    asyncio.run(_a())


def test_notepad_dead_session_exits_keeping_draft(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    s = _session(User(username="dave", groups=[]))
    p = app.get_plugin("social")

    async def _a():
        from server.session import SessionState

        s.state = SessionState.DISCONNECTED
        posted, draft = await p._social_overlay_editor(
            s, {"id": "b1", "title": "General"}, "kept")
        assert posted is False
        assert draft == "kept"

    asyncio.run(_a())


def test_notepad_enter_and_lf_insert_not_send(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    p = app.get_plugin("social")

    async def _a():
        keys = iter(["ENTER", "x", "LF", "ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            k = next(keys)
            if k == "ENTER":
                sess._line_buffer = "\n"
            return k

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            posted, draft = await p._social_overlay_editor(
                s, {"id": "b1", "title": "General"}, "")
        finally:
            runner.read_key = orig  # type: ignore[assignment]
        assert posted is False
        assert draft == "\nx\n"

    asyncio.run(_a())
