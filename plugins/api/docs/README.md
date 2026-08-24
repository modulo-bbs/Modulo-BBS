# api

HTTP control plane: the One-API (`/api/v1/<op>`), the self-describing
`_schema`, the legacy endpoints, and the static web console.

See `docs/one-api.md` for the architecture (ops registry, planes, auth).

## Endpoints

- `POST /api/v1/<op.name>` — generic dispatch; body = params JSON
- `GET  /api/v1/_schema` — this plane's operations, params, required groups
- Legacy: `/api/health`, `/api/sessions`, `/api/shutdown`, `/api/broadcast`
- `GET /admin/*` — static web console (login + nodes/users/API explorer)

## Auth

- `POST /api/v1/auth.login` `{username, password}` → bearer token (8h TTL)
- Send as `Authorization: Bearer <token>`
- Authorization = caller's groups vs. each op's `requires` — no scoped API
  keys by design

## Planes

- **Management** listener: loopback-only, all ops (default `127.0.0.1:8080`)
- **Public** listener (optional `public_port`): sysop-gated ops are
  structurally absent from routing *and* schema

## Screens

None — HTTP speaks JSON. The console is vanilla HTML/CSS/JS under
`admin/` (no build step).
