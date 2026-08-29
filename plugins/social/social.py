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
from shared.visible import (
    fill_display,
    fit_display,
    hline,
    sanitize_cell,
    wide_ambiguous_for,
)

DMS_ROOM_ID = "dms"  # sentinel id of the pinned DMs aggregate row

# Two-pane geometry: │(1) + sidebar(22) + │(1) + pane(54) + │(1) = 79 cols
# when every glyph is 1 cell (CP437). UTF-8 Ambiguous box drawing is 2;
# pane_cell shrinks so the same 79 *display* columns still hold.
SCREEN_COLS = 79
SID_CELL = 22
PANE_CELL = 54
SID_INNER = SID_CELL - 2   # text width after the padding spaces
PANE_INNER = PANE_CELL - 2


def social_geometry(wide: bool) -> tuple[int, int, int, int]:
    """sid_cell, pane_cell, sid_inner, pane_inner — all display columns."""
    bar = 2 if wide else 1
    sid = SID_CELL
    pane = SCREEN_COLS - bar - sid - bar - bar
    return sid, pane, sid - 2, pane - 2

SOCIAL_HINT = "  Enter thread · Up/Dn · N new thread · D delete · Space/PgUp peek · ESC"[:79]
THREAD_HINT = "  Enter post · empty Enter editor · ESC back"[:79]


def focus_arrows(session, is_plain: bool) -> tuple[str, str]:
    """Left/right pointer for the Social divider, per session codec.

    ASCII and CP437 use ``<`` / ``>`` (IBM C0 arrows are 0x1A/0x1B and
    collide with ESC when mixed with colour). UTF-8 gets ``←`` / ``→``.
    """
    if is_plain:
        return "<", ">"
    codec = getattr(session, "codec", None) or "cp437"
    if codec == "utf-8":
        return "←", "→"
    return "<", ">"


def gutter_stack(
    n_rows: int, focus_left: bool, left: str, right: str, bar: str,
) -> list[str]:
    """One glyph per content row for the middle divider.

    Arrows point at the column you can move *into*, with the key that
    takes you there stacked between them: ENTER from rooms, ESC from
    the thread.
    """
    if focus_left:
        arrow, word = right, "ENTER"
    else:
        arrow, word = left, "ESC"
    block = [arrow, *word, arrow]
    glyphs = [bar] * max(0, n_rows)
    if n_rows <= 0:
        return glyphs
    if n_rows < len(block):
        block = block[:n_rows]
    start = (n_rows - len(block)) // 2
    for i, ch in enumerate(block):
        glyphs[start + i] = ch
    return glyphs


def new_badge_from_id(last_read: int) -> int:
    """First message id that earns *NEW*. 0 = never visited (star only)."""
    last = int(last_read or 0)
    return last + 1 if last else 0


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


def _apply_social_selection(session, rooms: list[Room], sel: int) -> int:
    n = len(rooms)
    if n:
        sel = max(0, min(sel, n - 1))
    else:
        sel = 0
    try:
        session._pim_selected = sel
        session._social_selected_id = rooms[sel].id if n else ""
    except Exception:
        pass
    return sel


def remember_social_selection(session, rooms: list[Room]) -> int:
    """Highlighted sidebar index, pinned to room id when activity reorders."""
    sel = int(getattr(session, "_pim_selected", 0) or 0)
    keep = getattr(session, "_social_selected_id", None) or ""
    if keep:
        for i, r in enumerate(rooms):
            if r.id == keep:
                sel = i
                break
    return _apply_social_selection(session, rooms, sel)


def set_social_selection(session, rooms: list[Room], sel: int) -> int:
    """User moved the highlight; pin to that room from now on."""
    return _apply_social_selection(session, rooms, sel)


def forget_social_selection(session) -> None:
    """Leave Social (tab switch / dashboard). Next visit starts at DMs."""
    try:
        session._pim_selected = 0
        session._social_selected_id = ""
    except Exception:
        pass


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


async def render_social(
    conversations,
    session,
    *,
    compact: bool = True,
    new_from_id: int | None = None,
    scroll_up: int | None = None,
    hint: str | None = None,
    status: str = "",
) -> str:
    """The Social tab's pane band: room sidebar | live thread pane.

    Selection is ``session._pim_selected`` (index into ``social_rooms()``),
    pinned to ``session._social_selected_id`` so a new post that reorders
    the activity list does not jump the highlight. Changing rooms
    re-renders immediately and re-anchors the message scroll to the
    newest activity. Scroll position is stored as lines scrolled *up
    from the bottom* (0 = tail) in ``session._social_scroll_up``.

    *compact* is the browse preview (author-only bubbles). Thread focus
    passes ``compact=False`` so the same two-pane chrome shows full
    bubbles; *hint* / *status* / *new_from_id* / *scroll_up* override the
    browse defaults without a second surface.
    """
    from core.theme import palette_for

    user = getattr(session, "user", None)
    h = int(getattr(session, "terminal_height", 24) or 24)
    is_plain = getattr(session, "terminal_type", "") in ("UNKNOWN", "dumb", "")
    pal = palette_for(session)
    username = getattr(user, "username", "") or ""
    wide = wide_ambiguous_for(session, is_plain)
    _, _, sid_inner, pane_inner = social_geometry(wide)
    rule_ch = "-" if is_plain else "─"

    rooms = await social_rooms(conversations, user)
    sel = remember_social_selection(session, rooms)

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
    if scroll_up is None:
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
    else:
        up = max(0, int(scroll_up))

    # -- build message lines for the pane ------------------------------------
    # B8: compact bubbles (summarized) instead of the old #id [author] list.
    # Tail-anchored; _social_scroll_up now counts MESSAGES scrolled past.
    # *NEW* = arrived since last leave (last_read); never-opened → star only.
    last_read = 0
    if username and thread_conv is not None:
        try:
            last_read = await conversations.get_last_read(
                username, thread_conv["id"])
        except Exception:
            last_read = 0
    new_from = (
        int(new_from_id) if new_from_id is not None
        else new_badge_from_id(last_read)
    )

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
                [m], pane_inner, username=username,
                new_from_id=new_from, plain=is_plain, compact=compact,
                palette=None if is_plain else palette_for(session),
                wide_ambiguous=wide)
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
    side.append(fill_display(rule_ch, sid_inner, wide_ambiguous=wide))
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
        title_w = min(15, pane_inner)
        rest = max(0, pane_inner - title_w)
        pane.append(f"{header_title:<{title_w}}{right:>{rest}}")
        pane.append(fill_display(rule_ch, pane_inner, wide_ambiguous=wide))
        pane.extend(window)
        if not window:
            pane.append("(no messages yet - N posts first)")
    else:
        pane.append("(select a room)")

    # -- zip cells into box rows ----------------------------------------------
    def cell(text: str, inner: int, selected: bool) -> str:
        # Flanking spaces are part of the cell (inner + 2 = sid_cell / pane_cell).
        # sanitize first: a bare ESC in stored data (a board titled ESC) makes
        # the terminal swallow the rest of the row, so the divider and right
        # border land ~22 columns left of the rest of the frame.
        text = sanitize_cell(text)
        fitted = fit_display(text, inner, wide_ambiguous=wide)
        padded = fit_display(f" {fitted} ", inner + 2, wide_ambiguous=wide)
        if is_plain:
            return padded
        if selected:
            return f"{pal.tab_fg}{pal.tab_bg}{padded}{pal.reset}"
        # Bubble rows already carry their own SGR; don't wrap those in text.
        if "\x1b[" in text:
            return padded
        return f"{pal.text}{padded}{pal.reset}"

    def side_selected(row_idx: int) -> bool:
        """Sidebar row highlight only while the left column has the keys."""
        if not compact or not rooms:
            return False
        if row_idx == 0:
            return sel == 0
        board_idx = row_idx - 2  # row 1 is the separator
        return board_idx >= 0 and sel == board_idx + 1

    bar = "" if is_plain else pal.frame
    rst = "" if is_plain else pal.reset
    left_a, right_a = focus_arrows(session, is_plain)
    stack = gutter_stack(content_rows, compact, left_a, right_a, "│")
    focus = "" if is_plain else pal.accent
    gutter_cols = 2 if wide else 1
    rows: list[str] = []
    for i in range(content_rows):
        ltxt = side[i] if i < len(side) else ""
        rtxt = pane[i] if i < len(pane) else ""
        gch = stack[i]
        gpad = fit_display(gch, gutter_cols, wide_ambiguous=wide)
        if gch == "│":
            gutter = f"{bar}{gpad}{rst}"
        else:
            gutter = f"{focus}{gpad}{rst}"
        rows.append(
            f"{bar}│{rst}"
            + cell(ltxt, sid_inner, side_selected(i))
            + gutter
            + cell(rtxt, pane_inner, False)
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
    bot = (
        "+" + "-" * 77 + "+"
        if is_plain
        else f"{pal.frame}{hline('└', '─', '┘', SCREEN_COLS, wide_ambiguous=wide)}{pal.reset}"
    )
    hint_txt = SOCIAL_HINT if hint is None else hint
    if status:
        hint_txt = f" {status}  {hint_txt.strip()}"
    hint_txt = fit_display(hint_txt, SCREEN_COLS, wide_ambiguous=wide)
    hint = hint_txt if is_plain else f"{pal.success}{hint_txt}{pal.reset}"
    lines = [top] + rows + [bot, hint]
    return "\r\n".join(lines)
