"""Social sidebar model (boards-unification §B2).

The two-pane Social surface replaces the Boards tab and absorbs DMs. This
module owns the *model*: which rooms exist for a viewer, in what order,
and what is unread. Pure data — rendering lives in the chrome, input in
the PIM dispatcher.

Sidebar shape:

    > DMs             N   <- pinned aggregate (all kind=dm convs)
    ──────────────────
      General Discus  *  <- kind=board rooms by recent activity
      Trading Post

New threads are created with N (see SOCIAL_HINT), not a fake sidebar row.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from core.conversations import SOCIAL_THREAD_TITLE_MAX as TITLE_MAX

DMS_ROOM_ID = "dms"  # sentinel id of the pinned DMs aggregate row

# Two-pane geometry: │(1) + sidebar(22) + │(1) + pane(54) + │(1) = 79 cols.
SID_CELL = 22
PANE_CELL = 54
SID_INNER = SID_CELL - 2   # text width after the padding spaces
PANE_INNER = PANE_CELL - 2

SOCIAL_HINT = "  Enter chat · Up/Dn rooms · N new thread · Space/PgUp/PgDn peek · ESC back"[:79]


@dataclass
class Room:
    """One selectable sidebar row."""

    id: str  # conversation id, or DMS_ROOM_ID for the pinned aggregate
    title: str  # display title, capped at TITLE_MAX
    kind: str  # "board" or "dms"
    unread: int  # unread messages for this viewer
    message_count: int
    last_activity: str  # ISO timestamp: last_message_at, else created


def _activity_key(conv: dict) -> str:
    return conv.get("last_message_at") or conv.get("created") or ""


def _cap(title: str) -> str:
    return (title or "")[:TITLE_MAX]


async def social_rooms(conversations, user) -> list[Room]:
    """Rooms for the Social sidebar, in display order.

    Pinned ``DMs`` aggregate first (authenticated viewers only), then all
    boards visible to *user* sorted by recent activity. Unread counts use
    the per-user last-read markers; anonymous viewers see none.
    """
    username = getattr(user, "username", "") or ""
    rooms: list[Room] = []

    if username:
        dms_total = 0
        try:
            dms = await conversations.list_conversations(kind="dm", visible_to=user)
            for c in dms:
                dms_total += await conversations.unread_count(username, c["id"])
        except Exception:
            dms_total = 0
        rooms.append(
            Room(
                id=DMS_ROOM_ID,
                title="DMs",
                kind="dms",
                unread=dms_total,
                message_count=0,
                last_activity="",
            )
        )

    try:
        boards = await conversations.list_conversations(kind="board", visible_to=user)
    except Exception:
        boards = []
    boards.sort(key=_activity_key, reverse=True)
    for c in boards:
        unread = 0
        if username:
            try:
                unread = await conversations.unread_count(username, c["id"])
            except Exception:
                unread = 0
        rooms.append(
            Room(
                id=c["id"],
                title=_cap(c.get("title", c.get("id", "?"))),
                kind="board",
                unread=unread,
                message_count=int(c.get("message_count", 0)),
                last_activity=_activity_key(c),
            )
        )
    return rooms


def other_participant(conv: dict, username: str) -> str:
    """Display name for a DM conversation: the other side's username."""
    parts = conv.get("participants") or []
    other = next((p for p in parts if p != username), None)
    return other or conv.get("title", conv.get("id", "?"))


# -- renderer (B3) -----------------------------------------------------------


def _activity_key_conv(conv: dict) -> str:
    return _activity_key(conv)


async def render_social(conversations, session) -> str:
    """The Social tab's pane band: room sidebar | live thread pane.

    Selection is ``session._pim_selected`` (index into ``social_rooms()``);
    changing rooms re-renders immediately and re-anchors the message
    scroll to the newest activity. Scroll position is stored as lines
    scrolled *up from the bottom* (0 = tail) in ``session._social_scroll_up``.
    """
    from core.theme import palette_for

    user = getattr(session, "user", None)
    h = int(getattr(session, "terminal_height", 24) or 24)
    is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
    pal = palette_for(session)
    username = getattr(user, "username", "") or ""

    rooms = await social_rooms(conversations, user)
    sel = int(getattr(session, "_pim_selected", 0) or 0)
    if rooms and sel >= len(rooms):
        sel = len(rooms) - 1
        try:
            session._pim_selected = sel
        except Exception:
            pass
    if sel < 0:
        sel = 0

    # Row budget: tab bar + funnel + bottom + hint + prompt = 5 chrome rows.
    # `_show_menu` sends tab_bar + pane + trailing CRLF, then CUPs the prompt
    # onto the last terminal line. Filling h-4 made the pane 23 lines; the
    # extra CRLF scrolled a 24-row SyncTERM and the tab bar vanished.
    content_rows = max(8, h - 5)
    sidebar_slots = content_rows - 2  # DMs row + separator
    pane_rows_n = content_rows - 2    # room header + separator

    # -- resolve highlighted room -> thread conversation ---------------------
    room = rooms[sel] if rooms and sel < len(rooms) else None
    thread_conv: dict | None = None
    msgs: list[dict] = []
    header_title = ""
    if room is not None:
        if room.kind == "dms":
            try:
                dms = await conversations.list_conversations(kind="dm", visible_to=user)
            except Exception:
                dms = []
            dms.sort(key=_activity_key_conv, reverse=True)
            if dms:
                thread_conv = dms[0]
                header_title = _cap(other_participant(thread_conv, username))
        else:
            try:
                thread_conv = await conversations.get_conversation(room.id)
            except Exception:
                thread_conv = None
            header_title = room.title
        if thread_conv is not None:
            try:
                msgs = await conversations.list_messages(thread_conv["id"])
            except Exception:
                msgs = []

    # -- scroll state (lines up from the bottom; reset on room change) -------
    cur_id = room.id if room else ""
    prev_id = getattr(session, "_social_room_id", None)
    up = int(getattr(session, "_social_scroll_up", 0) or 0)
    if prev_id != cur_id:
        up = 0
        try:
            session._social_scroll_up = 0
        except Exception:
            pass
    try:
        session._social_room_id = cur_id
    except Exception:
        pass

    # -- build message lines for the pane ------------------------------------
    # B8: compact bubbles (summarized) instead of the old #id [author] list.
    # Tail-anchored; _social_scroll_up now counts MESSAGES scrolled past.
    seen = getattr(session, "_social_seen", None)
    if not isinstance(seen, dict):
        seen = {}
        try:
            session._social_seen = seen
        except Exception:
            pass
    new_from = int(seen.get(cur_id, 0) or 0)

    from plugins.social.bubbles import render_bubbles as _render_bubbles

    up = max(0, int(up or 0))
    pane_rows_all: list[str] = []
    if thread_conv is not None and msgs:
        end = len(msgs) - up
        window = msgs[max(0, end - 40):max(0, end)] or msgs[:1]
        groups: list[list[str]] = []
        used = 0
        for m in reversed(window):
            grows = _render_bubbles(
                [m], PANE_INNER, username=username,
                new_from_id=new_from, plain=is_plain, compact=True,
                palette=None if is_plain else palette_for(session))
            if used + len(grows) > pane_rows_n:
                break
            groups.append(grows)
            used += len(grows)
        for grows in reversed(groups):
            pane_rows_all.extend(grows)

    # bubbles were already tail-fitted (respecting scroll-up) to the budget
    window = pane_rows_all[:pane_rows_n]

    # -- sidebar cell rows ----------------------------------------------------
    side: list[str] = []
    dms_unread = next((r.unread for r in rooms if r.kind == "dms"), 0)
    boards = [r for r in rooms if r.kind == "board"]
    overflow = max(0, len(boards) - (sidebar_slots - 1))
    shown = boards[: max(0, sidebar_slots - 1 - (1 if overflow else 0))]

    marker = ">" if sel == 0 else " "
    side.append(f"{marker} {'DMs':<15}{dms_unread:>3}")
    side.append("─" * SID_INNER if not is_plain else "-" * SID_INNER)
    for i, r in enumerate(shown):
        marker = ">" if sel == i + 1 else " "
        star = "*" if r.unread else " "
        side.append(f"{marker} {r.title:<15}{star}")
    if overflow:
        side.append(f"  ..{overflow} more")

    # -- pane cell rows -------------------------------------------------------
    pane: list[str] = []
    if thread_conv is not None:
        right = f"({len(msgs)} msgs)"
        pane.append(f"{header_title:<15}{right:>{PANE_INNER - 15}}")
        pane.append("─" * PANE_INNER if not is_plain else "-" * PANE_INNER)
        pane.extend(window)
        if not window:
            pane.append("(no messages yet - N posts first)")
    else:
        pane.append("(select a room)")

    # -- zip cells into box rows ----------------------------------------------
    def cell(text: str, width: int, selected: bool) -> str:
        # Pad by VISIBLE width -- bubble rows carry ANSI color codes.
        # The cell's total is width+2 (the flanking spaces are part of it).
        import re as _re

        body = f" {text} "
        vlen = len(_re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", body))
        if vlen > width + 2:
            plain_text = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
            body = f" {plain_text[:width]} "
            vlen = width + 2
        padded = body + " " * max(0, width + 2 - vlen)
        if is_plain:
            return padded
        if selected:
            return f"{pal.tab_fg}{pal.tab_bg}{padded}{pal.reset}"
        # Bubble rows already carry their own SGR; don't wrap those in text.
        if "\x1b[" in text:
            return padded
        return f"{pal.text}{padded}{pal.reset}"

    def side_selected(row_idx: int) -> bool:
        """Sidebar row highlight mirrors the room selection."""
        if not rooms:
            return False
        if row_idx == 0:
            return sel == 0
        board_idx = row_idx - 2  # row 1 is the separator
        return board_idx >= 0 and sel == board_idx + 1

    bar = "" if is_plain else pal.muted
    rst = "" if is_plain else pal.reset
    rows: list[str] = []
    for i in range(content_rows):
        ltxt = side[i] if i < len(side) else ""
        rtxt = pane[i] if i < len(pane) else ""
        rows.append(
            f"{bar}│{rst}"
            + cell(ltxt, SID_INNER, side_selected(i))
            + f"{bar}│{rst}"
            + cell(rtxt, PANE_INNER, False)
            + f"{bar}│{rst}"
        )

    # -- chrome -----------------------------------------------------------------
    from plugins.mainmenu import _build_top
    from plugins.mainmenu.tabs import load_tabs, visible_tabs

    bbs = getattr(conversations, "bbs", None)
    tabs_for_top = visible_tabs(load_tabs(bbs), user)
    labels = [t["label"] for t in tabs_for_top]
    active_id = getattr(session, "_pim_active_tab", "social")
    active_idx = max(0, next((i for i, t in enumerate(tabs_for_top) if t["id"] == active_id), 0))
    try:
        from plugins.mainmenu import _hint_for_session

        hint_txt = _hint_for_session(session)
    except Exception:
        hint_txt = "up/dn select"
    top = _build_top(labels, active_idx, hint_txt, is_plain, screen_width=79, session=session)
    bot = "+" + "-" * 77 + "+" if is_plain else f"{pal.muted}└{'─' * 77}┘{pal.reset}"
    hint = SOCIAL_HINT if is_plain else f"{pal.success}{SOCIAL_HINT}{pal.reset}"
    lines = [top] + rows + [bot, hint]
    return "\r\n".join(lines)
