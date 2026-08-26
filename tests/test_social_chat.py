"""B8 — Social chat mode (Telegram-style): type at the prompt, Enter sends,
UP/DOWN scrolls history, polling picks up other nodes' messages, NEW badges
mark arrivals since entry. Replaces the classic reader on Social rooms.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core import runner
from core.app import BBSApp
from core.user import User
from plugins.mainmenu import MainmenuPlugin
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
    p = MainmenuPlugin()
    p.on_load(app)
    app.plugins = [p]
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


def test_typing_at_prompt_posts_and_esc_exits(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    s.terminal_type = "ANSI-BBS"  # exercise the CP437 box-drawing path
    p = app.get_plugin("mainmenu")

    async def _a():
        keys = iter(["h", "i", " ", "t", "h", "e", "r", "e", "ENTER", "ESC"])
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
        assert "\u250c" in text          # bubbles drawn
        assert "> hi there" in plain     # draft echoed at prompt

    asyncio.run(_a())


def test_enter_at_end_always_tail_anchored(tmp_path):
    """Long thread: entering lands on the newest messages, not the top."""
    app = _app(tmp_path)

    async def _seed_many():
        await app.conversations.create_conversation(
            kind="board", title="General", created_by="dave", conv_id="big")
        for n in range(1, 41):
            await app.conversations.post_message("big", author=f"u{n}", body=f"msg {n}")

    asyncio.run(_seed_many())
    dave = User(username="dave", groups=[])
    s = _session(dave)
    p = app.get_plugin("mainmenu")

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
    p = app.get_plugin("mainmenu")

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
        assert "history" in text   # status line shows hidden/newer count

    asyncio.run(_a())


def test_echo_suppressed_during_chat_restored_after(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    p = app.get_plugin("mainmenu")

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


# -- multi-line compose (B8 part 4: Ctrl-Enter newline, growing input) -------


def _run_chat(s, app, conv, keys):
    p = app.get_plugin("mainmenu")
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


def test_lf_inserts_newline_and_input_area_grows(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(["h", "i", "LF", "y", "o", "ESC"]))
    text = _plain(s)
    assert "> hi" in text          # first physical input row
    assert "  yo" in text          # newline opened a second input row
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1          # ESC never posts


def test_long_line_wraps_into_extra_row_at_77_cols(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    keys = ["x"] * 78 + ["ESC"]
    _run_chat(s, app, {"id": "b1", "title": "General"}, iter(keys))
    text = _plain(s)
    # '> ' + 77 wrapped columns, continuation on its own '  '-prefixed row
    assert "> " + "x" * 77 + "\r\n  x" in text


def test_enter_posts_multiline_body_with_newlines_intact(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(["a", "LF", "b", "ENTER", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "a\nb"
    assert msgs[-1]["author"] == "dave"


def test_newline_only_draft_is_not_posted(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(["LF", "ENTER", "ESC"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert len(msgs) == 1          # only the seeded message


def test_crlf_trailing_lf_does_not_leak_into_next_draft(tmp_path):
    """Real CRLF clients leave '\n' in the stash after Enter's '\r'; it must
    be swallowed on send, or the next draft starts with a phantom newline."""
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)

    async def _a():
        keys = iter(["h", "i", "ENTER", "o", "ENTER", "ESC"])
        orig = runner.read_key

        async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
            key = next(keys)
            # simulate a CRLF client: Enter's \r consumed, \n stashed behind
            if key == "ENTER":
                sess._line_buffer = "\n"
            return key

        runner.read_key = fake_rk  # type: ignore[assignment]
        try:
            await app.get_plugin("mainmenu")._social_chat(
                s, {"id": "b1", "title": "General"})
        finally:
            runner.read_key = orig  # type: ignore[assignment]

    asyncio.run(_a())
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-2]["body"] == "hi"
    assert msgs[-1]["body"] == "o"          # not "\no"


def test_no_emitted_segment_exceeds_79_visible_columns(tmp_path):
    """Dave's SyncTERM repro (B8 part 4 regression): the pre-part-4 redraw
    echoed the whole draft on ONE line, so past end-of-line the terminal
    autowrapped it — every keystroke scrolled the screen and duplicated the
    row. Walk the raw output through a minimal 80x24 terminal model and
    assert nothing we send ever forces a wrap/scroll.
    """
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    body = ("OK, So here is another attempt at writing a long message "
            "first wraparound will be tested, and you see what happened.")
    _run_chat(s, app, {"id": "b1", "title": "General"},
              iter(list(body) + ["ESC"]))

    import re as _re

    raw = bytes(s.writer.buf).decode("cp437", errors="replace")
    W, H = 80, 24
    row = col = 1
    csi = None
    for tok in _re.split(r"(\x1b\[[0-9;]*[A-Za-z])", raw):
        if not tok:
            continue
        if tok.startswith("\x1b["):
            if tok[-1] == "H":
                p = tok[2:-1].split(";")
                row = int(p[0] or 1)
                col = int(p[1] or 1) if len(p) > 1 else 1
            elif tok[-1] == "J":
                pass  # erase display/from-cursor: no cursor movement
            elif tok[-1] == "K":
                pass  # erase line
            # SGR (m) and anything else: no movement
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
                    row += 1  # pending autowrap fires on next printable
                    col = 1
                col += 1
            if row > H:
                pytest.fail(
                    f"output scrolls a 24-row terminal at row={row} "
                    f"col={col} near {raw[max(0, raw.find(tok)-40):raw.find(tok)+20]!r}")


def test_input_rows_clipped_to_cap_keeping_latest_lines(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)             # 24 rows -> cap 18
    keys: list[str] = []
    for n in range(1, 26):
        keys.extend(f"L{n:02d}")
        if n < 25:
            keys.append("LF")
    _run_chat(s, app, {"id": "b1", "title": "General"}, iter(keys + ["ESC"]))
    raw = bytes(s.writer.buf).decode("cp437", errors="replace")
    # final input-area state = everything after the last erase-to-end
    import re as _re

    final = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw.rsplit("\x1b[J", 1)[-1])
    assert "L25" in final          # newest lines survive
    assert "L08" in final          # 25 logical lines - cap 18 -> starts at L08
    assert "L07" not in final      # oldest overflow clipped
    assert "> L08" not in final    # prompt marker scrolled off with its line


def _run_chat_with_editor(s, app, conv, keys, lines):
    """Drive _social_chat with scripted read_key AND read_command streams."""
    p = app.get_plugin("mainmenu")
    orig_rk, orig_rc = runner.read_key, runner.read_command

    async def fake_rk(bbs, sess, timeout=runner.IDLE_TIMEOUT, **kw):
        return next(keys)

    async def fake_rc(bbs, sess, timeout=runner.IDLE_TIMEOUT):
        return next(lines)

    runner.read_key, runner.read_command = fake_rk, fake_rc  # type: ignore[assignment]
    try:
        asyncio.run(p._social_chat(s, conv))
    finally:
        runner.read_key, runner.read_command = orig_rk, orig_rc  # type: ignore[assignment]


def test_ctrl_e_opens_full_editor_and_sends_multiline(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    _run_chat_with_editor(
        s, app, {"id": "b1", "title": "General"},
        iter(["CTRL_E", "ESC"]), iter(["hello", "", "world", "/S"]))
    text = _plain(s)
    assert "EDITOR" in text and "/S=send" in text
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "hello\n\nworld"   # blank line preserved
    assert msgs[-1]["author"] == "dave"


def test_full_editor_abort_restores_entry_draft(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    dave = User(username="dave", groups=[])
    s = _session(dave)
    # draft "hi" before the editor; editor types "oops" then aborts;
    # back in chat, ENTER must post the ENTRY draft, not the edit.
    _run_chat_with_editor(
        s, app, {"id": "b1", "title": "General"},
        iter(["h", "i", "CTRL_E", "ENTER", "ESC"]),
        iter(["oops", "/A"]))
    msgs = asyncio.run(app.conversations.list_messages("b1"))
    assert msgs[-1]["body"] == "hi"


def test_full_editor_eof_keeps_draft(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    s = _session(User(username="dave", groups=[]))
    p = app.get_plugin("mainmenu")

    async def _a():
        orig = runner.read_command

        async def fake_rc(bbs, sess, timeout=runner.IDLE_TIMEOUT):
            return None  # disconnect mid-compose

        runner.read_command = fake_rc  # type: ignore[assignment]
        try:
            posted, draft = await p._social_full_editor(
                s, {"id": "b1", "title": "General"}, "seed text")
        finally:
            runner.read_command = orig  # type: ignore[assignment]
        assert posted is False
        assert draft == "seed text"
        assert "EDITOR" in bytes(s.writer.buf).decode("cp437", errors="replace")

    asyncio.run(_a())
