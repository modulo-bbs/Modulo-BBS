"""Social home-tab plugin: rooms and threads."""
from __future__ import annotations

from plugins.base import Plugin
from plugins.social.social import render_social

from core import modal as core_modal
from core import runner
from core.theme import palette_for


def _collapse_overlay_spacing(text: str) -> str:
    """Save-time: collapse runs of blank lines to a single blank line.

    Double-spacing and above become one empty line between paragraphs.
    Leading/trailing blanks are dropped. Intra-line spaces are untouched.
    """
    lines: list[str] = []
    pending_blank = False
    for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if ln.strip() == "":
            if lines:
                pending_blank = True
            continue
        if pending_blank:
            lines.append("")
            pending_blank = False
        lines.append(ln)
    return "\n".join(lines)


class SocialPlugin(Plugin):
    name = "social"
    version = "1.0.0"
    description = "Boards and DMs as a two-pane Social surface."
    menu_label = ""
    menu_key = ""
    menu_order = 10
    home_label = "Social"

    def __init__(self):
        self.bbs = None

    def on_load(self, bbs):
        self.bbs = bbs

    async def render_home_pane(self, session) -> str:
        return await render_social(self.bbs.conversations, session)

    async def handle_home_key(self, session, key: str) -> bool:
        return await self._handle_social_key(session, key)

    async def home_digest(self, session):
        """Dashboard rows for DMs and boards with unread mail."""
        from plugins.mainmenu import _elided

        user = getattr(session, "user", None)
        rows = []
        try:
            if user is not None and getattr(user, "username", None):
                dms = await self.bbs.conversations.unread_conversations(
                    user.username, kind="dm", visible_to=user)
            else:
                dms = []
        except Exception:
            dms = []
        if dms:
            names, seen = [], set()
            uname = getattr(user, "username", "") if user else ""
            for c in dms:
                parts = c.get("participants", []) or []
                other = next((p for p in parts if p != uname), None) or c.get("title", c.get("id", "?"))
                if other not in seen:
                    names.append(str(other))
                    seen.add(other)
            rows.append((_elided(f"DMs: ({len(dms)} new) from ", names, sep=", ", width=74), "social"))
        else:
            rows.append(("DMs: (no new messages)", "social"))
        try:
            if user is not None and getattr(user, "username", None):
                boards = await self.bbs.conversations.unread_conversations(
                    user.username, kind="board", visible_to=user)
            else:
                boards = []
        except Exception:
            boards = []
        if boards:
            titles = [c.get("title", c.get("id", "?")) for c in boards[:8]]
            rows.append((_elided(f"Boards: ({len(boards)} new) ", titles, sep=" | ", width=74), "social"))
        else:
            rows.append(("Boards: (no new)", "social"))
        return rows

    async def _compose_picker(self, session) -> str | None:
        options = ("Post", "Open in full screen editor", "Discard draft")
        idx = await core_modal.choose(self.bbs, session, list(options), default=0)
        if idx is None:
            return None
        return ("post", "editor", "discard")[idx]

    async def _handle_social_key(self, session, key: str) -> bool:
        """Social tab keys (boards-unification §B4). Returns True if consumed.

        Browse keys (arrows/PgUp/PgDn/Space/ESC/B/ENTER/D) act immediately;
        N drops into LINE mode via ``read_command`` so a title prompt
        cannot leak a navigation keystroke into a draft.
        """
        from plugins.social.social import (
            forget_social_selection,
            remember_social_selection,
            set_social_selection,
            social_rooms,
        )

        user = getattr(session, "user", None)
        rooms = await social_rooms(self.bbs.conversations, user)
        n = len(rooms)
        sel = remember_social_selection(session, rooms)

        def _page() -> int:
            return max(6, int(getattr(session, "terminal_height", 24) or 24) - 6)

        if key in ("UP", "K") and n:
            set_social_selection(session, rooms, (sel - 1) % n)
            return True
        if key in ("DOWN", "J") and n:
            set_social_selection(session, rooms, (sel + 1) % n)
            return True
        if key in ("PGDN", "SPACE"):
            up = int(getattr(session, "_social_scroll_up", 0) or 0)
            session._social_scroll_up = max(0, up - _page())  # type: ignore[attr-defined]
            return True
        if key == "PGUP":
            up = int(getattr(session, "_social_scroll_up", 0) or 0)
            session._social_scroll_up = up + _page()  # type: ignore[attr-defined]
            return True
        if key == "ESC" or key == "B":
            # Back/up one level: Social → Dashboard. B is scoped to this
            # handler only — outside Social nothing changes.
            session._pim_active_tab = "dashboard"  # type: ignore[attr-defined]
            forget_social_selection(session)
            session._social_scroll_up = 0  # type: ignore[attr-defined]
            return True
        if key == "ENTER":
            # Same two-pane Threads surface; compose-focus on the
            # highlighted room. ESC returns to browsing rooms.
            conv = await self._social_target_conv(session, rooms, sel)
            if conv is None:
                await self.bbs.send(session, "\r\n(no room selected)\r\n")
                return True
            await self._social_thread(session, conv)
            return True
        if key == "N":
            await self._social_new_thread(session)
            return True
        if key == "D":
            await self._social_delete_thread(session, rooms, sel)
            return True
        return False

    async def _social_target_conv(self, session, rooms, sel):
        """Conversation behind the highlighted sidebar row."""
        room = rooms[sel] if sel < len(rooms) else None
        if room is None:
            return None
        if room.kind == "dms":
            dms = await self.bbs.conversations.list_conversations(
                kind="dm", visible_to=getattr(session, "user", None))
            dms.sort(key=lambda c: c.get("last_message_at") or c.get("created", ""),
                     reverse=True)
            return dms[0] if dms else None
        try:
            return await self.bbs.conversations.get_conversation(room.id)
        except Exception:
            return None

    async def _select_thread_room(self, session, conv: dict) -> None:
        """Keep the sidebar highlight on the thread we are composing in."""
        from plugins.social.social import set_social_selection, social_rooms

        rooms = await social_rooms(
            self.bbs.conversations, getattr(session, "user", None))
        cid = conv.get("id")
        kind = conv.get("kind")
        if not kind and cid:
            try:
                full = await self.bbs.conversations.get_conversation(cid)
                kind = (full or {}).get("kind")
            except Exception:
                kind = None
        for i, r in enumerate(rooms):
            if r.kind == "dms" and kind == "dm":
                set_social_selection(session, rooms, i)
                return
            if r.id == cid:
                set_social_selection(session, rooms, i)
                return

    async def _social_new_thread(self, session):
        """Compose mode: prompt for a title (capped), then create+select.

        Empty, whitespace-only, or ESC/control-only title silently aborts
        back to the Social list -- no conversation is created.
        """
        from core.conversations import SOCIAL_THREAD_TITLE_MAX, clean_title

        while True:
            await self.bbs.send(
                session,
                f"\r\nThread title (max {SOCIAL_THREAD_TITLE_MAX} chars): ",
            )
            raw = await runner.read_command(self.bbs, session)
            if raw is None:
                return
            # ESC here means "cancel", not a one-character title. Storing the
            # raw byte gave us a board titled ESC that broke its sidebar row.
            title = clean_title(raw)
            if not title:
                return
            if len(title) > SOCIAL_THREAD_TITLE_MAX:
                await self.bbs.send(
                    session,
                    f"Too long ({len(title)} chars) - "
                    f"{SOCIAL_THREAD_TITLE_MAX} max.\r\n",
                )
                continue
            conv = await self.bbs.conversations.create_conversation(
                kind="board", title=title, created_by=session.user.username)
            from plugins.social.social import set_social_selection, social_rooms

            rooms = await social_rooms(self.bbs.conversations, session.user)
            for i, r in enumerate(rooms):
                if r.id == conv["id"]:
                    set_social_selection(session, rooms, i)
                    break
            session._social_scroll_up = 0  # type: ignore[attr-defined]
            await self.bbs.send(session, f"Thread '{title}' created.\r\n")
            return

    async def _social_delete_thread(self, session, rooms, sel) -> None:
        """D on the highlighted board: confirm, then delete if allowed.

        Sysops can remove any board. The author can remove their own only
        while nobody else has posted. The DMs aggregate is never deleted.
        """
        room = rooms[sel] if sel < len(rooms) else None
        user = getattr(session, "user", None)
        if room is None or room.kind != "board":
            await core_modal.notice(
                self.bbs, session, "The DMs row cannot be deleted.")
            return
        conv = await self.bbs.conversations.get_conversation(room.id)
        if conv is None:
            return
        title = conv.get("title") or room.title or room.id
        if not await self.bbs.conversations.can_delete_conversation(
                room.id, user):
            await core_modal.notice(
                self.bbs, session,
                "You can delete your own thread only before anyone else replies.",
            )
            return
        idx = await core_modal.choose(
            self.bbs, session,
            [f"Delete '{title}'", "Cancel"],
            default=1,
        )
        if idx != 0:
            return
        try:
            ok = await self.bbs.conversations.delete_conversation(
                room.id, by_user=user)
        except PermissionError:
            await core_modal.notice(
                self.bbs, session, "You cannot delete this thread.")
            return
        if not ok:
            return
        from plugins.social.social import set_social_selection, social_rooms

        leftover = await social_rooms(self.bbs.conversations, user)
        nxt = min(sel, max(0, len(leftover) - 1))
        set_social_selection(session, leftover, nxt)
        session._social_scroll_up = 0  # type: ignore[attr-defined]

    def _social_tab_bar(self, session) -> str:
        """Same home tab row mainmenu paints above the Social pane."""
        mm = self.bbs.get_plugin("mainmenu") if self.bbs is not None else None
        if mm is None:
            return " " * 79
        from plugins.mainmenu.tabs import load_tabs, visible_tabs

        user = getattr(session, "user", None)
        tabs = visible_tabs(load_tabs(self.bbs), user)
        return mm._render_tabs(session, tabs, "social")[:79].ljust(79)

    async def _social_thread(self, session, conv: dict) -> None:
        """Compose-focus on the highlighted thread. Same two-pane chrome.

        The prompt never wraps or grows. Empty Enter, Ctrl-Enter (LF), or
        typing past the wrap column opens the overlay notepad; they type
        through the transition. Leaving the notepad with a draft (and Enter
        on a one-line draft) always opens Post / Editor / Discard. ESC on
        the picker keeps the draft on the prompt; ESC on the prompt returns
        to browsing rooms. UP/DOWN scroll history (tail-anchored). Idle
        polls once a second so mail that arrived since last leave shows
        as *NEW*.
        """
        import time

        from plugins.social.social import THREAD_HINT, new_badge_from_id, render_social
        from shared.textwrap import wrap as _tw_wrap

        cid = conv["id"]
        uname = getattr(getattr(session, "user", None), "username", "") or ""
        h = int(getattr(session, "terminal_height", 24) or 24)
        w = 79
        inner = w - 2  # '> ' prefix; wrap column for the one-line prompt

        def phys_rows(d: str) -> int:
            n = 0
            for ln in (d or "").split("\n"):
                n += len(_tw_wrap(ln, inner) or [""])
            return n or 1

        def input_rows(d: str) -> list[str]:
            """One prompt row. Overflow (newlines or wrap) is ``[N lines]``."""
            if not d:
                return ["> "]
            n = phys_rows(d)
            if n <= 1 and "\n" not in d:
                return ["> " + d]
            return [f"> [{n} lines]"]

        def would_overflow(d: str, extra: str) -> bool:
            cand = d + extra
            return "\n" in cand or phys_rows(cand) > 1

        async def open_editor(start: str) -> str:
            _posted, new = await self._social_overlay_editor(session, conv, start)
            return new

        async def commit(body: str) -> None:
            collapsed = _collapse_overlay_spacing(body)
            if not collapsed.strip():
                return
            await self.bbs.conversations.post_message(
                cid, author=uname or "anonymous", body=collapsed)
            try:
                await self.bbs.conversations.mark_read(uname, cid)
            except Exception:
                pass

        def swallow_crlf() -> None:
            stash = getattr(session, "_line_buffer", "")
            if stash.startswith("\n"):
                session._line_buffer = stash[1:]

        prev_suppress = getattr(session, "suppress_echo", False)
        session.suppress_echo = True  # type: ignore[attr-defined]
        # The Enter that opened this chat was CR; SyncTERM's trailing LF is
        # still in the stash. Do not treat it as empty-Enter / LF → notepad.
        stash = getattr(session, "_line_buffer", "")
        if stash.startswith("\n"):
            session._line_buffer = stash[1:]
        try:
            try:
                msgs = await self.bbs.conversations.list_messages(cid)
            except Exception:
                msgs = []
            try:
                watermark = await self.bbs.conversations.get_last_read(uname, cid)
            except Exception:
                watermark = 0
            new_from = new_badge_from_id(watermark)
            try:
                await self.bbs.conversations.mark_read(uname, cid)
            except Exception:
                pass
            await self._select_thread_room(session, conv)
            scroll_back = 0
            draft = ""
            rows = input_rows(draft)
            last_fp = None
            pending_offer = False
            last_key_at = time.monotonic()
            await self.bbs.send(session, "\x1b[2J\x1b[H")

            async def after_editor_or_picker(new_draft: str) -> None:
                nonlocal draft, rows, last_fp, scroll_back, msgs
                draft = new_draft
                rows = input_rows(draft)
                last_fp = None
                try:
                    fresh = await self.bbs.conversations.list_messages(cid)
                except Exception:
                    fresh = msgs
                msgs = fresh

            async def offer_draft() -> None:
                """Post / Editor / Discard. After the notepad, paint first."""
                nonlocal draft, scroll_back, last_fp, pending_offer
                if not draft.strip():
                    return
                choice = await self._compose_picker(session)
                if choice == "post":
                    await commit(draft)
                    scroll_back = 0
                    await after_editor_or_picker("")
                elif choice == "editor":
                    await after_editor_or_picker(await open_editor(draft))
                    if draft.strip():
                        pending_offer = True
                elif choice == "discard":
                    await after_editor_or_picker("")
                else:
                    last_fp = None

            while getattr(session, "is_active", True):
                try:
                    msgs = await self.bbs.conversations.list_messages(cid)
                except Exception:
                    msgs = []
                fp = (len(msgs), max((int(m.get("id", 0)) for m in msgs), default=0), len(rows))
                dirty = fp != last_fp
                last_fp = fp

                if dirty:
                    new_count = (
                        sum(
                            1 for m in msgs
                            if int(m.get("id", 0)) >= new_from
                            and str(m.get("author", "")) != uname
                        )
                        if new_from else 0
                    )
                    status_parts = []
                    if scroll_back:
                        status_parts.append(
                            f"history: {scroll_back} newer hidden - DOWN/PgDn")
                    if new_count:
                        status_parts.append(f"{new_count} NEW")
                    status = "  ".join(status_parts)
                    session._pim_active_tab = "social"  # type: ignore[attr-defined]
                    await self._select_thread_room(session, conv)
                    pane = await render_social(
                        self.bbs.conversations, session,
                        compact=False,
                        new_from_id=new_from,
                        scroll_up=scroll_back,
                        hint=THREAD_HINT,
                        status=status,
                    )
                    tab = self._social_tab_bar(session)
                    frame = [tab] + pane.split("\r\n")
                    while len(frame) < h - len(rows):
                        frame.append(" " * w)
                    frame = frame[: h - len(rows)]
                    frame.extend(rows)
                    await self.bbs.send(
                        session,
                        "\x1b[2J\x1b[H" + "\x1b[K\r\n".join(frame[:h]),
                    )

                if pending_offer:
                    pending_offer = False
                    await offer_draft()
                    continue

                key = await runner.read_key(
                    self.bbs, session,
                    timeout=1.0, preserve_case=True, idle_on_timeout=False,
                )
                if key is None:
                    if time.monotonic() - last_key_at > runner.IDLE_TIMEOUT:
                        break
                    continue
                last_key_at = time.monotonic()

                async def redraw_input() -> None:
                    start = h - max(len(rows), 1) + 1
                    await self.bbs.send(
                        session, f"\x1b[{start};1H\x1b[J" + "\r\n".join(rows))

                if key == "ESC":
                    break
                if key == "ENTER":
                    swallow_crlf()
                    if not draft.strip():
                        await after_editor_or_picker(await open_editor(draft))
                        pending_offer = bool(draft.strip())
                    else:
                        await offer_draft()
                elif key == "LF":
                    await after_editor_or_picker(await open_editor(draft + "\n"))
                    pending_offer = bool(draft.strip())
                elif key == "BACKSPACE":
                    if draft:
                        draft = draft[:-1]
                        rows = input_rows(draft)
                        await redraw_input()
                elif key == "UP":
                    scroll_back = min(len(msgs), scroll_back + 1)
                    last_fp = None
                elif key == "DOWN":
                    scroll_back = max(0, scroll_back - 1)
                    last_fp = None
                elif key == "PGUP":
                    scroll_back = min(len(msgs), scroll_back + max(5, h - 6))
                    last_fp = None
                elif key == "PGDN":
                    scroll_back = max(0, scroll_back - max(5, h - 6))
                    last_fp = None
                elif key == "SPACE" or (len(key) == 1 and key.isprintable()):
                    extra = " " if key == "SPACE" else key
                    if would_overflow(draft, extra):
                        await after_editor_or_picker(
                            await open_editor(draft + extra))
                        pending_offer = bool(draft.strip())
                    else:
                        draft += extra
                        rows = input_rows(draft)
                        await redraw_input()
                # CTRL_E / CTRL_S / anything else: ignore

            try:
                await self.bbs.conversations.mark_read(uname, cid)
            except Exception:
                pass
        finally:
            session.suppress_echo = prev_suppress  # type: ignore[attr-defined]

    async def _social_overlay_editor(self, session, conv: dict, draft: str) -> tuple[bool, str]:
        """Overlay notepad. ESC keeps the current text and returns to chat.

        Arrows move the caret; Enter and LF insert a line; capacity is
        capped to the box. Never posts — posting is the compose picker's job.
        Returns (False, draft); the bool is leftover for call-site symmetry.
        """
        import time

        from shared.textwrap import wrap_rows

        def _caret_cell(rows: list[tuple[int, int]], off: int) -> tuple[int, int]:
            for i, (st, ln) in enumerate(rows):
                if off <= st + ln:
                    return i, off - st
            st, ln = rows[-1]
            return len(rows) - 1, min(off - st, ln)

        h = int(getattr(session, "terminal_height", 24) or 24)
        L, R = 1, 79                      # border columns (inclusive)
        top, bot = 2, max(4, h - 3)       # border rows (inclusive)
        Wid = R - L - 1                   # interior columns
        H = bot - top - 1                 # interior rows — hard capacity
        inner_w = Wid - 1                 # text column minus gutter space
        is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
        pal = palette_for(session)
        G = "" if is_plain else pal.success
        RST = "" if is_plain else pal.reset
        if is_plain:
            tl, tr, bl, br, hb, vb = "+", "+", "+", "+", "-", "|"
        else:
            tl, tr, bl, br, hb, vb = "┌", "┐", "└", "┘", "─", "│"

        def render(text: str, off: int) -> str:
            rows = wrap_rows(text, inner_w)
            parts = [f"\x1b[{top};{L}H", f"{G}{tl}{hb * Wid}{tr}{RST}"]
            for i in range(H):
                body = ""
                if i < len(rows):
                    st, ln = rows[i]
                    body = text[st:st + ln]
                parts.append("\r\n" + f"{G}{vb}{RST}"
                             + (" " + body).ljust(Wid) + f"{G}{vb}{RST}")
            hint = " ESC back "
            pad_l = max(0, (Wid - len(hint)) // 2)
            pad_r = max(0, Wid - pad_l - len(hint))
            parts.append(
                "\r\n" + f"{G}{bl}{hb * pad_l}{RST}{hint}{G}{hb * pad_r}{br}{RST}"
            )
            r, c = _caret_cell(rows, min(off, len(text)))
            # Col L is the left border, L+1 the gutter space, text at L+2.
            parts.append(f"\x1b[{top + 1 + r};{L + 2 + c}H")
            return "".join(parts)

        text, off = draft, len(draft)

        def fits(cand: str) -> bool:
            return len(wrap_rows(cand, inner_w)) <= H

        last_key_at = time.monotonic()
        await self.bbs.send(session, render(text, off))
        while getattr(session, "is_active", True):
            key = await runner.read_key(
                self.bbs, session, timeout=1.0,
                preserve_case=True, idle_on_timeout=False)
            if key is None:
                if time.monotonic() - last_key_at > runner.IDLE_TIMEOUT:
                    return False, text  # idle: back to chat, draft kept
                continue
            last_key_at = time.monotonic()
            rows = wrap_rows(text, inner_w)

            if key == "ESC":
                return False, text          # keep current text; posting is the picker
            if key in ("ENTER", "LF"):
                stash = getattr(session, "_line_buffer", "")
                if stash.startswith("\n"):
                    session._line_buffer = stash[1:]
                cand = text[:off] + "\n" + text[off:]
                if fits(cand):
                    text, off = cand, off + 1
            elif key == "BACKSPACE":
                if off:
                    text, off = text[:off - 1] + text[off:], off - 1
            elif key == "LEFT":
                off = max(0, off - 1)
            elif key == "RIGHT":
                off = min(len(text), off + 1)
            elif key in ("UP", "DOWN"):
                r, c = _caret_cell(rows, off)
                nr = r - 1 if key == "UP" else r + 1
                if 0 <= nr < len(rows):
                    st, ln = rows[nr]
                    off = st + min(c, ln)
            elif key == "SPACE":
                cand = text[:off] + " " + text[off:]
                if fits(cand):
                    text, off = cand, off + 1
            elif len(key) == 1 and key.isprintable():
                cand = text[:off] + key + text[off:]
                if fits(cand):
                    text, off = cand, off + 1
            # anything else: ignore
            await self.bbs.send(session, render(text, off))
        return False, text



__all__ = ["SocialPlugin", "_collapse_overlay_spacing"]
