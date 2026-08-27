# Modulo BBS — The One-API Principle

> Status: **ADOPTED DESIGN** (2026-08-23). Not yet implemented — this document is
> the contract future code must satisfy. Build order at the bottom.

## The decision

The concept of *modular* is **all-encompassing**. There is one canonical
operations API, and every interface — bundled or third-party — is just a client
of it:

- the web sysop dashboard (static HTML + vanilla JS, served by this server)
- the terminal `[S]ysop` menu in Syncterm
- a PyQt control app someone writes next year
- an external website or bridge (e.g. Matrix ↔ message board)

None of these is "the real" interface. **The API is the product; clients are
interchangeable.**

## Architecture

```
                ┌─────────────────────────────┐
   browser  ───►│  /api/v1/...  (HTTP/JSON)   │◄─── any third-party client
   dashboard    │                             │      (PyQt, website, curl)
                ├─────────────────────────────┤
   Syncterm ───►│  ops.call(name, params)     │◄─── in-process callers
   sysop menu   │  (core/ops.py registry)     │      (plugins, core)
                ├─────────────────────────────┤
                │  UserManager · Sessions ·   │
                │  EventBus · plugin storage  │
                └─────────────────────────────┘
```

**core/ops.py** holds a registry of named operations:

```python
ops.register(
    "users.delete",
    params={"username": str},
    requires=["sysop"],
    handler=...,            # sync or async, per the await-if-coroutine rule
)
```

Every operation declares its params and permission gate exactly once. HTTP
handlers become generic dispatch: `POST /api/v1/<op.name>` with a JSON body.
The terminal menu renders from the same registry and calls the same `ops.call()`.

## Rules that make it hold

1. **Parity is enforced by test**, not convention — a parity check walks the
   registry and fails if an operation is not reachable from a surface that
   claims it.
2. **Self-describing**: `GET /api/v1/_schema` lists operations with their params
   and required groups, so third parties can build clients without reading
   source. Each plane serves only its own view of the schema.
3. **Versioned from day one** (`/api/v1/`) — external code must not break when
   we add endpoints.
4. **Not sysop-only**: messageboard/files actions go through the same
   registry, so anyone can write a complete alternative frontend for *users*
   too, not just admins.

## Two planes, one registry

Management capability must not be hackable from a gateway. The solution is the
control-plane/data-plane split applied to *exposure*, not a second API:

- **Management plane** — all sysop operations. Binds `127.0.0.1` only, never
  proxied, unreachable from any reverse proxy by construction. The browser
  dashboard talks to it locally/LAN.
- **Public plane** — user operations (boards, files, own profile).
  Designed to sit behind a TLS-terminating reverse proxy for third-party
  frontends and bridges.

Mechanics:

- Operations declare `planes`; any op whose `requires` includes `sysop` is
  management-plane-only by construction.
- **Hard invariant (test-enforced)**: a sysop-gated op may never appear on the
  public plane. The public `_schema` omits management ops entirely — the
  outside world cannot even discover that they exist.
- Both listeners share the same generic dispatcher. Planes are an exposure
  concept, never duplicated logic.
- Terminal/in-process callers are unbound by planes: the Syncterm sysop menu
  simply *is* the management interface; regular menus consume public ops.

## Authentication & authorization

- **Everyone authenticates as a user account** (bcrypt-verified, same `users/`
  store as telnet/SSH login). HTTP login issues a signed, expiring token
  (stdlib HMAC); clients send `Authorization: Bearer <token>`.
- **Machine clients get bot accounts** — ordinary users with groups chosen for
  their role (e.g. `matrix-bridge` in group `bridge`, a monitor in a read-only
  group). Audit attribution comes free: actions are attributed to an account,
  not "unknown key #3".
- **No scoped API keys.** There is deliberately NO parallel permission system
  for machines. Groups + per-operation `requires` are the only authorization
  mechanism on any surface.
- TOTP applies to HTTP login exactly as it does to terminal login.

## Boundary honesty

Both planes share one process. This split defends against reverse-proxy
misconfiguration and shrinks remote attack surface; it is **not** process
isolation. Upgrade path if ever needed: move the management plane to a separate
helper process over a local Unix socket. Not planned today.

## Constraints carried forward

- Web stack stays light/bespoke: static HTML + vanilla JS served by the existing
  stdlib server. No Flask, no React, no PHP, no build steps.
- Modern-first philosophy: never argue "Synchronet solved this in the 90s" as a
  reason to skip capability. The retro surface is the *presentation*, not the
  ceiling.

## Build order

1. `core/ops.py` — registry, param validation, group gating, plane tagging,
   `ops.call()`. Tests including the plane-isolation invariant.
2. Generic HTTP dispatch under `/api/v1/` + `_schema` + login/token endpoint;
   existing `/api/*` endpoints migrate onto the registry behind the scenes.
3. First consumers built together so parity is real from commit one:
   the terminal `[S]ysop` menu AND a minimal static-file dashboard shell.
4. Populate operations: users, sessions/kick, boards, doors catalog,
   bulletins, audit events (`sysop:*` events fire on every mutation).
5. Later: SSE/push for live views if polling proves insufficient.

## Cookbook — curl for a third-party dev

`GET /api/v1/_schema` is the contract. Everything below is `POST /api/v1/<op>` with `Content-Type: application/json` and, when logged in, `Authorization: Bearer <token>`. Tokens are 8h HMAC-signed strings from `auth.login`.

```bash
# 1) Discover what you can call (public plane shows only user ops if unauthenticated)
curl -s http://127.0.0.1:8080/api/v1/_schema | jq '.operations[] | .name'

# 2) Log in (same users/ + bcrypt as telnet/SSH)
curl -s -X POST http://127.0.0.1:8080/api/v1/auth.login \
  -H "Content-Type: application/json" \
  -d '{"username":"api_test","password":"bootstrap-pass-1"}'
# → {"token":"...","expires":"..."}  — save as $TOKEN

# 3) Create a board (sysop-only; fails with 403 for normal users)
curl -s -X POST http://127.0.0.1:8080/api/v1/conversations.create \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"kind":"board","title":"Trading Post","requires":""}'
# → {"id":"trading-post", "kind":"board", ...}

# 4) Create a DM — NOTE: participants is a comma-separated *string*, not a JSON array
#    The creator is auto-included, so as api_test you only name the other side.
curl -s -X POST http://127.0.0.1:8080/api/v1/conversations.create \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"kind":"dm","title":"Hey Dave","participants":"dave"}'
# → {"id":"hey-dave","kind":"dm","participants":["api_test","dave"],...}
# Gotcha: {"participants":["dave"]} → 400 "param participants must be str"
# For a group DM: "participants":"dave, ana, bob"

# 5) Post a message
curl -s -X POST http://127.0.0.1:8080/api/v1/messages.post \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"conversation_id":"hey-dave","body":"Hello @dave — test from curl"}'
# → {"id":1,"conversation_id":"hey-dave","author":"api_test","body":"...","created":"..."}

# 6) List / read
curl -s -X POST http://127.0.0.1:8080/api/v1/conversations.list \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"kind":"dm"}' | jq .
curl -s -X POST http://127.0.0.1:8080/api/v1/messages.list \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"conversation_id":"hey-dave","page":1,"per_page":25}' | jq .

# 7) Find across conversations
curl -s -X POST http://127.0.0.1:8080/api/v1/messages.find \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"hello","limit":20}' | jq .
```

**Common pitfalls:**
- `participants` is `str` (`"dave"` or `"dave, ana"`), not `["dave"]` — the server splits on `,` and trims. See `core/opdefs.py:_conversations_create`.
- `requires` for boards is also a comma-separated `str` of groups (`"sysop"` or `""` for public), not an array.
- Board titles are capped at **15 chars** (`SOCIAL_THREAD_TITLE_MAX`) — they are Social sidebar rows; violations return 400. DM/channel/group titles are uncapped.
- `kind` must be one of `board|channel|dm|group`; the PIM tab row is Dashboard | Social | Files | Bulletins per `plugins/mainmenu/tabs.py:DEFAULT_TABS` (Social = boards + pinned DMs composite).
- Unauthenticated `_schema` on the public plane intentionally omits sysop-gated ops — log in to see the full management plane (loopback-only).

