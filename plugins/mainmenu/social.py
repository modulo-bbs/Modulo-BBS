"""Social sidebar model (boards-unification §B2).

The two-pane Social surface replaces the Boards tab and absorbs DMs. This
module owns the *model*: which rooms exist for a viewer, in what order,
and what is unread. Pure data — rendering lives in the chrome, input in
the PIM dispatcher.

Sidebar shape (per plan §Design):

    > DMs             N   <- pinned aggregate (all kind=dm convs)
      + new thread       ← UI action row, added by the renderer
    ──────────────────
      1 General Dis…  *  ← kind=board rooms by recent activity
      2 Trading Post
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from shared.textwrap import wrap
from core.conversations import SOCIAL_THREAD_TITLE_MAX as TITLE_MAX

DMS_ROOM_ID = "dms"  # sentinel id of the pinned DMs aggregate row

# Two-pane geometry: │(1) + sidebar(22) + │(1) + pane(54) + │(1) = 79 cols.
SID_CELL = 22
PANE_CELL = 54
SID_INNER = SID_CELL - 2   # text width after the padding spaces
PANE_INNER = PANE_CELL - 2

SOCIAL_HINT = "  Enter open · Up/Dn rooms · Space/PgUp/PgDn scroll · R reply · N new · D del · ESC back"[:79]


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
    from shared.telnet_protocol import ANSI

    user = getattr(session, "user", None)
    h = int(getattr(session, "terminal_height", 24) or 24)
    is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
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

    # Row budget: tab bar + top + bottom + hint consume 4 rows of the frame.
    content_rows = max(8, h - 4)
    sidebar_slots = content_rows - 3  # DMs row + new-thread + separator
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
    mlines: list[str] = []
    for m in msgs:
        created = (m.get("created", "") or "")[5:16].replace("T", " ")
        mlines.append(f"#{m.get('id', '?')} [{m.get('author', '?')}] {created}")
        mlines.extend(wrap(m.get("body", "") or "", PANE_INNER))
        mlines.append("")
    while mlines and not mlines[-1]:
        mlines.pop()
    offset = max(0, len(mlines) - pane_rows_n - up)
    window = mlines[offset : offset + pane_rows_n]

    # -- sidebar cell rows ----------------------------------------------------
    side: list[str] = []
    dms_unread = next((r.unread for r in rooms if r.kind == "dms"), 0)
    boards = [r for r in rooms if r.kind == "board"]
    overflow = max(0, len(boards) - (sidebar_slots - 1))
    shown = boards[: max(0, sidebar_slots - 1 - (1 if overflow else 0))]

    marker = ">" if sel == 0 else " "
    side.append(f"{marker} {'DMs':<15}{dms_unread:>3}")
    side.append("  + new thread")
    side.append("─" * SID_INNER if not is_plain else "-" * SID_INNER)
    for i, r in enumerate(shown):
        marker = ">" if sel == i + 1 else " "
        star = "*" if r.unread else " "
        side.append(f"{marker} {r.title:<15}{star}")
    if overflow:
        side.append(f"  …{overflow} more")

    # -- pane cell rows -------------------------------------------------------
    pane: list[str] = []
    if thread_conv is not None:
        right = f"({len(msgs)} msgs)"
        pane.append(f"{header_title:<15}{right:>{PANE_INNER - 15}}")
        pane.append("─" * PANE_INNER if not is_plain else "-" * PANE_INNER)
        pane.extend(window)
        if not window:
            pane.append("(no messages yet — N posts first)")
    else:
        pane.append("(select a room)")

    # -- zip cells into box rows ----------------------------------------------
    def cell(text: str, width: int, selected: bool) -> str:
        padded = f" {text[:width]:<{width}} "
        if selected and not is_plain:
            return f"{ANSI.REVERSE}{padded}{ANSI.RESET}"
        return padded

    def side_selected(row_idx: int) -> bool:
        """Sidebar row highlight mirrors the room selection."""
        if not rooms:
            return False
        if row_idx == 0:
            return sel == 0
        board_idx = row_idx - 3  # rows 1,2 are the action + separator
        return board_idx >= 0 and sel == board_idx + 1

    rows: list[str] = []
    for i in range(content_rows):
        ltxt = side[i] if i < len(side) else ""
        rtxt = pane[i] if i < len(pane) else ""
        rows.append(
            "│"
            + cell(ltxt, SID_INNER, side_selected(i))
            + "│"
            + cell(rtxt, PANE_INNER, False)
            + "│"
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
    top = _build_top(labels, active_idx, hint_txt, is_plain, screen_width=79)
    bot = "+" + "-" * 77 + "+" if is_plain else f"{ANSI.DIM}└{'─' * 77}┘{ANSI.RESET}"
    lines = [top] + rows + [bot, SOCIAL_HINT]
    return "\r\n".join(lines)
