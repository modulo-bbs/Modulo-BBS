"""Social sidebar model (boards-unification §B2).

The two-pane Social surface replaces the Boards tab and absorbs DMs. This
module owns the *model*: which rooms exist for a viewer, in what order,
and what is unread. Pure data — rendering lives in the chrome, input in
the PIM dispatcher.

Sidebar shape (per plan §Design):

    ► DMs            N   ← pinned aggregate (all kind=dm convs)
      + new thread       ← UI action row, added by the renderer
    ──────────────────
      1 General Dis…  *  ← kind=board rooms by recent activity
      2 Trading Post
"""
from __future__ import annotations

from dataclasses import dataclass

TITLE_MAX = 15  # Dave decision 2026-08-25: thread titles cap at 15 chars

DMS_ROOM_ID = "dms"  # sentinel id of the pinned DMs aggregate row


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
