"""Built-in operation registrations for Modulo BBS.

Registers the standard operations on the global ops registry at import time.
Groups: system, sessions, users, boards, doors, bulletins, chat, auth.

Plane conventions (docs/one-api.md):
* sysop-gated ops  -> management plane only (structural default)
* user-facing ops  -> both planes by default

Handler contract: ``(bbs, user, params) -> result``, sync or async.
For ops with optional string params, an empty-string value means
"not provided / leave unchanged" (documented per-op below).
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
import time

from core.ops import ValidationError, PermissionDeniedError, OpsError, registry
from core.user import SYSOP_GROUP


def _public_user(u) -> dict | None:
    """User fields safe to return over the API (never the password hash)."""
    if u is None:
        return None
    return {
        "username": u.username,
        "display_name": u.display_name,
        "email": u.email,
        "location": u.location,
        "created": u.created.isoformat(),
        "last_login": u.last_login.isoformat() if u.last_login else None,
        "groups": list(u.groups),
        "stats": dict(u.stats),
    }


def _split_groups(raw: str) -> list[str]:
    return [g.strip().lower() for g in raw.split(",") if g.strip()]


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

def _health(bbs, user, params):
    mgr = bbs.session_manager
    return {
        "status": "running",
        "name": "Modulo BBS",
        "version": "0.1-alpha",
        "nodes": {"active": mgr.active_count, "max": mgr.max_nodes},
        "plugins": [p.name for p in bbs.plugins],
    }


registry.register(
    "system.health",
    description="Server status, node counts, loaded plugins.",
    handler=_health,
)


async def _shutdown(bbs, user, params):
    message = params.get("message") or "BBS shutting down. Goodbye!"
    loop = asyncio.get_running_loop()

    async def _later():
        await asyncio.sleep(0.2)  # let the HTTP response flush first
        if bbs.server is not None:
            await bbs.server.stop(message)

    loop.create_task(_later())
    return {"status": "shutting_down", "message": message}


registry.register(
    "system.shutdown",
    description="Graceful shutdown; broadcasts goodbye to connected users.",
    optional={"message": (str, "")},
    requires=[SYSOP_GROUP],
    handler=_shutdown,
)


async def _broadcast(bbs, user, params):
    sent = 0
    for session in list(bbs.session_manager.active_sessions):
        try:
            await bbs.send(session, f"\r\n[Broadcast] {params['message']}\r\n")
            sent += 1
        except Exception:  # noqa: BLE001 - one dead socket must not stop the rest
            pass
    return {"sent": sent}


registry.register(
    "system.broadcast",
    description="Send a line to every connected user.",
    params={"message": str},
    requires=[SYSOP_GROUP],
    handler=_broadcast,
)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _sessions_list(bbs, user, params):
    return {
        "sessions": bbs.session_manager.get_all_sessions(),
        "count": bbs.session_manager.active_count,
    }


registry.register("sessions.list", description="Active sessions.", handler=_sessions_list)


async def _sessions_kick(bbs, user, params):
    session = bbs.session_manager.get_session(params["session_id"])
    if session is None:
        raise ValidationError(f"no such session: {params['session_id']}")
    await bbs.send(session, "\r\n\r\n[Disconnected by SysOp]\r\n")
    await bbs.disconnect(session)
    return {"kicked": params["session_id"]}


registry.register(
    "sessions.kick",
    description="Disconnect one session.",
    params={"session_id": str},
    requires=[SYSOP_GROUP],
    handler=_sessions_kick,
)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def _users_list(bbs, user, params):
    """Paged, sorted account list.

    page starts at 1; per_page caps at 200 so one call can never dump
    thousands of accounts at any surface.
    """
    users = await bbs.users.list()
    per_page = min(params["per_page"], 200)
    start = (params["page"] - 1) * per_page
    slice_ = users[start:start + per_page]
    return {
        "total": len(users),
        "page": params["page"],
        "per_page": per_page,
        "pages": max(1, -(-len(users) // per_page)),
        "users": [_public_user(u) for u in slice_],
    }


registry.register(
    "users.list",
    description="Accounts, paged & sorted (sanitized — no password hashes).",
    optional={"page": (int, 1), "per_page": (int, 50)},
    requires=[SYSOP_GROUP],
    handler=_users_list,
)


async def _users_get(bbs, user, params):
    u = await bbs.users.get(params["username"])
    if u is None:
        raise ValidationError(f"no such user: {params['username']}")
    return _public_user(u)


registry.register(
    "users.get",
    description="One account, sanitized.",
    params={"username": str},
    requires=[SYSOP_GROUP],
    handler=_users_get,
)


async def _users_create(bbs, user, params):
    groups = _split_groups(params["groups"]) or ["user"]
    u = await bbs.users.create(
        params["username"],
        params["password"],
        display_name=params["display_name"] or None,
        email=params["email"] or None,
        groups=groups,
    )
    return _public_user(u)


registry.register(
    "users.create",
    description="Create an account. groups: comma-separated (default 'user').",
    params={"username": str, "password": str},
    optional={
        "display_name": (str, ""),
        "email": (str, ""),
        "groups": (str, "user"),
    },
    requires=[SYSOP_GROUP],
    handler=_users_create,
)


async def _users_update(bbs, user, params):
    """Empty-string optional = leave unchanged."""
    username = params["username"]
    fields: dict = {}
    for key in ("display_name", "email", "location"):
        if params.get(key):
            fields[key] = params[key]
    if params.get("password"):
        fields["password"] = params["password"]
    if params.get("groups"):
        fields["groups"] = _split_groups(params["groups"])
    if not fields:
        raise ValidationError("nothing to update (all optional fields empty)")
    u = await bbs.users.update(username, **fields)
    return _public_user(u)


registry.register(
    "users.update",
    description=(
        "Edit account. Empty optional = unchanged. "
        "groups: comma-separated replaces the set."
    ),
    params={"username": str},
    optional={
        "display_name": (str, ""),
        "email": (str, ""),
        "location": (str, ""),
        "password": (str, ""),
        "groups": (str, ""),
    },
    requires=[SYSOP_GROUP],
    handler=_users_update,
)


async def _users_delete(bbs, user, params):
    deleted = await bbs.users.delete(params["username"])
    if not deleted:
        raise ValidationError(f"no such user: {params['username']}")
    return {"deleted": params["username"]}


registry.register(
    "users.delete",
    description="Delete an account. Authored content keeps its author name.",
    params={"username": str},
    requires=[SYSOP_GROUP],
    handler=_users_delete,
)


# ---------------------------------------------------------------------------
# Message board
# ---------------------------------------------------------------------------

def _mb_plugin(bbs):
    plugin = bbs.get_plugin("messageboard")
    if plugin is None:
        raise OpsError("messageboard plugin not loaded")
    return plugin


def _boards_list(bbs, user, params):
    plugin = _mb_plugin(bbs)
    visible = plugin.boards if user is None else plugin.visible_boards(user)
    return {"boards": [{"id": b["id"], "name": b["name"]} for b in visible]}


def _boards_messages(bbs, user, params):
    plugin = _mb_plugin(bbs)
    board = next((b for b in plugin.boards if b["id"] == params["board"]), None)
    if board is None:
        raise ValidationError(f"no such board: {params['board']}")
    reqs = board.get("requires", [])
    if reqs and (user is None or not user.can_access(reqs)):
        raise PermissionDeniedError(f"board {params['board']} requires {reqs}")
    msgs = plugin.store.list_messages(params["board"])
    return {"board": params["board"], "messages": msgs}


def _boards_post(bbs, user, params):
    if user is None:
        raise PermissionDeniedError("login required to post")
    plugin = _mb_plugin(bbs)
    board = next((b for b in plugin.boards if b["id"] == params["board"]), None)
    if board is None:
        raise ValidationError(f"no such board: {params['board']}")
    reqs = board.get("requires", [])
    if reqs and not user.can_access(reqs):
        raise PermissionDeniedError(f"board {params['board']} requires {reqs}")
    msg = plugin.store.add_message(
        params["board"], user.username, params["subject"], params["body"]
    )
    bbs.events.emit("messageboard:post", {"board": params["board"], "msg": msg})
    return msg


def _boards_delete_message(bbs, user, params):
    if user is None:
        raise PermissionDeniedError("login required")
    plugin = _mb_plugin(bbs)
    msg = plugin.store.get_message(params["board"], params["id"])
    if msg is None:
        raise ValidationError(f"no such message #{params['id']} on {params['board']}")
    is_own = msg.get("author") == user.username
    if not (is_own or user.can_access(["moderator"])):
        raise PermissionDeniedError("not your message and you are not a moderator")
    plugin.store.delete_message(params["board"], params["id"])
    bbs.events.emit(
        "messageboard:delete",
        {"board": params["board"], "msg_id": params["id"], "by": user.username},
    )
    return {"deleted": params["id"]}


registry.register("boards.list", description="Boards visible to caller.", handler=_boards_list)
registry.register(
    "boards.messages",
    description="Messages in one board (group gates enforced).",
    params={"board": str},
    handler=_boards_messages,
)
registry.register(
    "boards.post",
    description="Post a message to a board as the authenticated user.",
    params={"board": str, "subject": str, "body": str},
    handler=_boards_post,
)
registry.register(
    "boards.delete_message",
    description="Delete a message (own, or any as moderator).",
    params={"board": str, "id": int},
    handler=_boards_delete_message,
)


# ---------------------------------------------------------------------------
# Doors + bulletins catalogs (read views; editing stays file-based for now)
# ---------------------------------------------------------------------------

def _doors_list(bbs, user, params):
    plugin = bbs.get_plugin("doors")
    if plugin is None:
        return {"doors": []}
    # A door missing from the keys file is hidden/disabled by convention.
    bound_ids = set(plugin.keys.values())
    out = []
    for d in plugin.catalog:
        if d["id"] not in bound_ids:
            continue
        reqs = d.get("requires", [])
        if reqs and (user is None or not user.can_access(reqs)):
            continue
        out.append({"id": d["id"], "name": d.get("name", d["id"]), "requires": reqs})
    return {"doors": out}


registry.register("doors.list", description="Doors visible to caller.", handler=_doors_list)


def _bulletins_list(bbs, user, params):
    plugin = bbs.get_plugin("bulletins")
    items = []
    if plugin is not None:
        for b in plugin.scan():
            reqs = b.get("requires", [])
            if reqs and (user is None or not user.can_access(reqs)):
                continue
            items.append({"id": b["id"], "title": b["title"], "requires": reqs})
    return {"bulletins": items}


registry.register(
    "bulletins.list", description="Bulletins visible to caller.", handler=_bulletins_list
)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def _chat_plugin(bbs):
    from plugins.chat import HUB  # process-wide singleton

    plugin = bbs.get_plugin("chat")
    if plugin is None:
        raise OpsError("chat plugin not loaded")
    return HUB


def _chat_send(bbs, user, params):
    if user is None:
        raise PermissionDeniedError("login required")
    text = params["text"]
    if len(text) > 400:
        raise ValidationError("text too long (max 400 chars)")
    hub = _chat_plugin(bbs)
    # Reuse the session fan-out: format like a normal chat line and push to
    # every listening queue. sender session is unused by the hub.
    bbs.events.emit("chat:message", {"user": user, "text": text})
    asyncio.ensure_future(hub.broadcast(None, f"{user.shown_name()}> {text}"))
    return {"sent": True}


def _chat_names(bbs, user, params):
    try:
        hub = _chat_plugin(bbs)
    except OpsError:
        return {"names": []}
    return {"names": hub.names()}


registry.register(
    "chat.send",
    description="Send a line to the global chat channel.",
    params={"text": str},
    handler=_chat_send,
)
registry.register("chat.names", description="Current chat participants.", handler=_chat_names)


# ---------------------------------------------------------------------------
# Auth: exchange credentials for a token (stdlib HMAC, in-memory store)
# ---------------------------------------------------------------------------

TOKEN_TTL_S = 8 * 3600
_tokens: dict[str, dict] = {}  # token digest -> {username, expires}


def _digest(raw: str) -> str:
    return hmac.new(raw.encode(), b"modulo-token", "sha256").hexdigest()


def issue_token(username: str) -> str:
    raw = secrets.token_urlsafe(32)
    _tokens[_digest(raw)] = {"username": username, "expires": time.time() + TOKEN_TTL_S}
    # prune expired tokens opportunistically
    now = time.time()
    for k in [k for k, v in _tokens.items() if v["expires"] < now]:
        del _tokens[k]
    return raw


def resolve_token(raw: str) -> str | None:
    entry = _tokens.get(_digest(raw))
    if entry is None or entry["expires"] < time.time():
        return None
    return entry["username"]


def revoke_token(raw: str) -> None:
    _tokens.pop(_digest(raw), None)


async def _auth_login(bbs, user, params):
    username = params["username"].strip().lower()
    u = await bbs.users.get(username)
    if u is None or not u.verify_password(params["password"]):
        raise PermissionDeniedError("invalid credentials")
    return {
        "token": issue_token(username),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_S,
        "user": _public_user(u),
    }


registry.register(
    "auth.login",
    description="Exchange username+password for a bearer token.",
    params={"username": str, "password": str},
    handler=_auth_login,
)


def _auth_logout(bbs, user, params):
    token = params.get("token") or ""
    revoke_token(token)
    return {"logged_out": True}


registry.register(
    "auth.logout",
    description="Invalidate a bearer token.",
    optional={"token": (str, "")},
    handler=_auth_logout,
)


# ---------------------------------------------------------------------------
# Conversations — unified engine (boards + channels + DMs + groups)
# See core/conversations.py + docs/build-plan.md Phase 1.
# All ops are user-facing (both planes); permission is per-conversation
# (group gates for boards, participant lists for DMs/groups), not per-op.
# ---------------------------------------------------------------------------

async def _conversations_list(bbs, user, params):
    kind = params.get("kind") or None
    if kind == "":
        kind = None
    convs = await bbs.conversations.list_conversations(kind=kind, visible_to=user)
    # paging — same discipline as users.list (cap 200)
    per_page = min(params["per_page"], 200)
    page = params["page"]
    start = (page - 1) * per_page
    slice_ = convs[start : start + per_page]
    return {
        "total": len(convs),
        "page": page,
        "per_page": per_page,
        "pages": max(1, -(-len(convs) // per_page)),
        "conversations": slice_,
    }


async def _conversations_get(bbs, user, params):
    conv = await bbs.conversations.get_conversation(params["conversation_id"])
    if conv is None:
        raise ValidationError(f"no such conversation: {params['conversation_id']}")
    # visibility check (reuse list filter logic by checking single)
    visible = await bbs.conversations.list_conversations(visible_to=user)
    if not any(c["id"] == conv["id"] for c in visible):
        raise PermissionDeniedError("not visible to you")
    return conv


async def _conversations_create(bbs, user, params):
    if user is None:
        raise PermissionDeniedError("login required to create conversations")
    kind = params["kind"]
    title = params["title"]
    requires = _split_groups(params.get("requires") or "")
    participants_raw = params.get("participants") or ""
    participants = [p.strip() for p in participants_raw.split(",") if p.strip()]
    # Boards are sysop-gated creation (moderated spaces); DMs/channels anyone
    if kind == "board" and not user.can_access([SYSOP_GROUP]):
        raise PermissionDeniedError("only sysop may create boards")
    if kind in ("dm", "group") and not participants:
        raise ValidationError("dm/group requires participants (comma-separated usernames)")
    # For DMs/groups, auto-include creator if missing
    if kind in ("dm", "group") and user.username not in participants:
        participants = [user.username] + participants
    conv = await bbs.conversations.create_conversation(
        kind=kind,
        title=title,
        created_by=user.username,
        requires=requires if kind == "board" else [],
        participants=participants if kind in ("dm", "group") else [],
    )
    bbs.events.emit("conversations:create", {"conversation": conv, "by": user.username})
    return conv


async def _messages_list(bbs, user, params):
    conv = await bbs.conversations.get_conversation(params["conversation_id"])
    if conv is None:
        raise ValidationError(f"no such conversation: {params['conversation_id']}")
    visible = await bbs.conversations.list_conversations(visible_to=user)
    if not any(c["id"] == conv["id"] for c in visible):
        raise PermissionDeniedError("not visible to you")
    msgs = await bbs.conversations.list_messages(params["conversation_id"])
    # paging for large threads (same cap discipline)
    per_page = min(params["per_page"], 200)
    page = params["page"]
    start = (page - 1) * per_page
    slice_ = msgs[start : start + per_page]
    return {
        "conversation_id": params["conversation_id"],
        "total": len(msgs),
        "page": page,
        "per_page": per_page,
        "pages": max(1, -(-len(msgs) // per_page)),
        "messages": slice_,
    }


async def _messages_post(bbs, user, params):
    if user is None:
        raise PermissionDeniedError("login required to post")
    conv = await bbs.conversations.get_conversation(params["conversation_id"])
    if conv is None:
        raise ValidationError(f"no such conversation: {params['conversation_id']}")
    visible = await bbs.conversations.list_conversations(visible_to=user)
    if not any(c["id"] == conv["id"] for c in visible):
        raise PermissionDeniedError("not visible to you")
    parent_id = params.get("parent_id")
    if parent_id == 0:
        parent_id = None
    msg = await bbs.conversations.post_message(
        params["conversation_id"], author=user.username, body=params["body"], parent_id=parent_id
    )
    bbs.events.emit("conversations:post", {"conversation_id": params["conversation_id"], "msg": msg})
    return msg


async def _messages_delete(bbs, user, params):
    if user is None:
        raise PermissionDeniedError("login required")
    conv = await bbs.conversations.get_conversation(params["conversation_id"])
    if conv is None:
        raise ValidationError(f"no such conversation: {params['conversation_id']}")
    visible = await bbs.conversations.list_conversations(visible_to=user)
    if not any(c["id"] == conv["id"] for c in visible):
        raise PermissionDeniedError("not visible to you")
    ok = await bbs.conversations.delete_message(params["conversation_id"], params["id"], by_user=user)
    if not ok:
        raise ValidationError(f"no such message #{params['id']}")
    bbs.events.emit("conversations:delete", {"conversation_id": params["conversation_id"], "id": params["id"], "by": user.username})
    return {"deleted": params["id"]}


async def _messages_find(bbs, user, params):
    q = params["query"]
    kind = params.get("kind") or None
    if kind == "":
        kind = None
    # find respects visibility implicitly via list_conversations filter inside Conversations.find_messages?
    # Instead filter post-search to visible conversations.
    hits = await bbs.conversations.find_messages(q, kind=kind, limit=params["limit"])
    visible_ids = {c["id"] for c in await bbs.conversations.list_conversations(visible_to=user)}
    hits = [h for h in hits if h.get("conversation_id") in visible_ids]
    return {"query": q, "hits": hits, "count": len(hits)}


registry.register(
    "conversations.list",
    description="Conversations visible to caller (boards/channels/DMs), paged. kind filter optional.",
    optional={"kind": (str, ""), "page": (int, 1), "per_page": (int, 50)},
    handler=_conversations_list,
)
registry.register(
    "conversations.get",
    description="One conversation by id (visibility-checked).",
    params={"conversation_id": str},
    handler=_conversations_get,
)
registry.register(
    "conversations.create",
    description="Create a conversation. kind=board|channel|dm|group. Boards are sysop-only; DMs need participants.",
    params={"kind": str, "title": str},
    optional={"requires": (str, ""), "participants": (str, "")},
    handler=_conversations_create,
)
registry.register(
    "messages.list",
    description="Messages in one conversation, paged.",
    params={"conversation_id": str},
    optional={"page": (int, 1), "per_page": (int, 50)},
    handler=_messages_list,
)
registry.register(
    "messages.post",
    description="Post a message (threaded via parent_id).",
    params={"conversation_id": str, "body": str},
    optional={"parent_id": (int, 0)},
    handler=_messages_post,
)
registry.register(
    "messages.delete",
    description="Delete a message (own, or any as moderator/sysop).",
    params={"conversation_id": str, "id": int},
    handler=_messages_delete,
)
registry.register(
    "messages.find",
    description="Substring search across bodies+author, visibility-filtered.",
    params={"query": str},
    optional={"kind": (str, ""), "limit": (int, 25)},
    handler=_messages_find,
)
