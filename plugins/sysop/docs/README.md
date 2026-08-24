# sysop

The sysop terminal menu: a thin, generic client of the ops registry. New
ops registered in `core/opdefs.py` appear here automatically — no bespoke
code per feature.

## Access

- `menu_requires = ["sysop"]` — non-sysops never see `[S]` and can't enter
- `on_session_start` bounces non-sysops anyway (defense in depth)
- Every op still passes the registry's own permission gate

## Screens / output

Menu renders inline from `bbs.screens`-registered op groups. Results render
as formatted text; `users.list` gets a dedicated paginated table
(`N`=next, `P`=prev, `Q`=done). All results pause with `[Press any key]`.

## Operations surfaced

Everything from `core/opdefs.py`: system health/broadcast/shutdown, session
list/kick, full user CRUD, board/door/bulletin catalogs, chat send/names.
Run-time inventory: `GET /api/v1/_schema` (management plane).
