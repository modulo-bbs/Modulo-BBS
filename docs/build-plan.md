# Modulo BBS — Build Plan (Rail)

> **This is the rail.** Every build commit references a section here. When the build diverges, the commit message notes which section it diverged from so post-hoc audit is mechanical. No code is written without a plan entry.

## Status

| Phase | Description | Status |
|---|---|---|
| 0 | Foundation: event bus, user model, plugin base, loader, auth, session binding | **COMPLETE** (Tasks 1–6 below, shipped, 246 tests at `15bd54c`) |
| 1 | One-API + screens + tabbed PIM home | **COMPLETE** (Steps 1–11 below; PIM home shipped through `32ca439`, 2026-08-25) |
| 2 | Boards → Social unification (`.hermes/plans/2026-08-25_150714-boards-unification.md`) | **COMPLETE** — A1–A4 + B0–B6 landed 2026-08-25 (`4afe66c`…`9dda401`); B7 Syncterm pass pending Dave. Tab row: Dashboard \| Social \| Files \| Bulletins; DMs pinned inside Social (OQ2). **B8 divergence (same evening, per Dave):** Telegram-style chat replaces the classic reader — type-at-prompt posting, tail-anchored history, 1s polling with NEW badges, compact bubble previews (`d63f8bf`→). **B8 part 4:** shift-enter (LF) multi-line compose — draft box word-wraps and grows upward over bubbles, Enter posts with line breaks intact. |

Historical Tasks 1–6 are retained below as the record of the foundation build. Current roadmap is Phase 1.

---

## Phase 0 — Foundation (COMPLETE, retained for audit)

### Task 1: Event Bus (core/events.py)
**Deliverables:** `EventBus` with `emit/on/once/off`, async handlers, lifecycle events `session:connect`, `session:disconnect`, `user:login`, `user:logout`, `menu:open`, `menu:select`, `command:pre`, `command:post`.

### Task 2: User Model (core/user.py)
**Deliverables:** `User` dataclass + `UserManager` (get/create/update/delete/list), `users/` JSON store, `in_group()` / `can_access()` , bcrypt.

### Task 3: Plugin Base Class (plugins/base.py)
**Deliverables:** `Plugin` base with `name, version, description, menu_label, menu_key, menu_order, menu_requires` + lifecycle `on_load/on_unload/on_session_start/on_session_end/handle_command`.

### Task 4: Plugin Loader (core/loader.py)
**Deliverables:** `PluginLoader` scans `plugins/*/__init__.py`, calls `on_load(bbs)`, graceful skip of broken plugins.

### Task 5: Auth Plugin (plugins/login/)
**Deliverables:** Login/registration/TOTP, `ScreenLoader` via `core/screens.py`, `Terminal` I/O, emits `user:login`, `auth:login_failed`.

### Task 6: Session→User Binding
**Deliverables:** `Session.user: User|None`, auth plugin sets on login, core checks `session.authenticated`.

---

## Phase 1 — Dashboard Hybrid Menu (Planned)

### 1. Goal

Replace the single-page `[M][F][B][C][D][I][Q]` menu with a **Dashboard-first hybrid menu** — a tabbed, branch-style PIM home where the first tab is a quick-links Dashboard and every other tab is a filtered view of the same `conversations` engine. The user's sketches are the north stars (caps = active in plain-ASCII fallback; ANSI adds colors + box-drawing):

```
DASHBOARD  |  Boards  |  DMs      |  Files    |  Bulletins
-------------------------------------------------------------------------------
DMs: (10 new) from Anna, Bob, Zork, lobo, gwen, ...
Bulletins: (3 new) What my plans are... | New Login Procedure | BBS Li...
Files: (8 new) BBS DOORS | TELNET | OTHER CATEGORIES UNTIL WE NEED ...
Boards: (2 new) General | Trading
-------------------------------------------------------------------------------
+-----------------------/        up/dn select      \---------------------+
|  @nox (3m ago): lol                                                        |
|  @danny (5d ago): How ya doin?                                 |
|  @dave (3mo ago): souds good, I'd like to see it.     |
+--------------------------------------------------------------------------+
```

Top tabs = branches of the same interface. **Dashboard = digest quick-links** — one row per area that actually has new activity since your last login, elided with `...` when we run out of columns (so you know definitively "that row is DMs/Bulletins/etc."). Every row is a filtered shortcut: `Up/Dn` moves the highlight, `Enter` jumps straight to the meat (DMs tab with those 10 highlighted, bulletins at first unread, files at new files). Boards/DMs/Files still exist as their own tabs — Dashboard just aggregates them.

Modern-first principle: Modulo differentiates from Synchronet/WWIV/RBBS by treating **every interaction as a persistent, searchable conversation with a tempo control** — not "messages vs chat" as separate silos.

### 2. Current Context & Assumptions

- BBS is healthy on `15bd54c`: `bido`? telnet 6400 / SSH 6422 / API 8080, 246 tests, `core/screens.py` service (`bbs.screens.render/send`, `.ans→.asc→.txt` resolution, CRLF canonical, tokens `{bbsname}/{time}/{node}/{active}` etc., file beats generator, `/screen` toggle persisted in `preferences.screen_mode`, `/help` via `core/slash.py`), `core/app.py` 79-col pad + `row(h)`-pinned `>` prompt, `core/ops.py` One-API registry (20 ops, plane isolation), `plugins/messageboard` + `chat` + `files` + `bulletins` + `doors` + `api` + `sysop`.
- Live override `plugins/mainmenu/screens/main.asc` = 3 lines (`This is a dummy menu…` + blank + `/screen to change modes.`) — committed as valid override.
- Input is `read_key` (single-key hotkeys, no Enter) at `mainmenu` loop; `read_command` is the line-mode path. TAB/PGUP/PGDN/Ctrl-C/ESC are banned per KISS rule; keep `LEFT/RIGHT` or `1/2/3` for tabs.
- Board is moderate-low speculation budget (hindsight budget mid), so reuse existing `bbs.storage.dir()` + `core/ops.py` rather than inventing new backends.
- **Constraint:** plan lives here at `docs/build-plan.md`; `.hermes/plans/` is not the canonical location. Previous draft at `.hermes/plans/2026-08-24_190500-tabs-pim.md` was misplaced and will be removed.

### 3. Architecture

**One `conversations` engine, Dashboard quick-links + filtered lenses.**

- **Store:** `core/conversations.py` owns `conversations/{id}/messages.jsonl` + `conversations/index.json`. Types: `board` (slow, group-gated, threaded), `channel` (fast, presence-typed), `dm` (private `participants=[2]`), `group` (private `participants=[n]`). Same `Message {id, conversation_id, parent_id?, author, body, created}` shape.
- **API:** New ops in `core/opdefs.py`: `conversations.list`, `conversations.get`, `conversations.create`, `messages.list`, `messages.post`, `messages.delete`, `messages.find` (self-describing via `GET /api/v1/_schema` on both planes, same as `one-api.md`). Add `dashboard.summary` (or reuse `conversations.list` filtered by `since=last_login`) that returns per-area digests: `{area, new_count, names_or_titles[]}`.
- **Chrome vs pane:** `plugins/mainmenu` becomes the Dashboard/PIM chrome owner. It renders three bands every redraw: **A) top tab bar** (`DASHBOARD | Boards | DMs | Files | Bulletins` — caps = active in plain), **B) pane content**, **C) bottom prompt**. In **Dashboard tab**, pane is the digest quick-links list — one row per area with new activity since last login, formatted `DMs: (10 new) from Anna, Bob, Zork, ...` / `Bulletins: (3 new) Title | Title | ...` / `Files: (8 new) ...` — each elided with `...` to fit 79 visible cols (so you know definitively "that row is DMs"). In **other tabs**, pane is the filtered conversation list (`@user (time): preview`) as before. Content plugins stop owning their own screen layout — they render *into* the pane. The chrome does one `\x1b[2J\x1b[H` clear, pane never overprints stale rows alone.
- **Graceful degradation:** if `session.terminal_type` is not ANSI-capable, tabs render as `DASHBOARD` in caps with `|` separators; otherwise CP437 `─┌┐│└┘` + `ANSI.BRIGHT_WHITE`/`ANSI.BG_BLUE` + `{DIM}` per `shared/telnet_protocol.py`. Digest elision uses `...` (CP437-safe, not `…`).
- **Prefs:** `preferences.screen_mode == "generated"` still bypasses any file override; add `preferences.home_mode == "menu"|"pim"` for classic list vs PIM. File `plugins/mainmenu/screens/dashboard.asc` (and `pim.asc`) can reskin the chrome without code, same as `core/screens.py` contract.

### 4. Step-by-Step Plan (bite-sized, TDD)

#### Step 1: Move rail to canonical location (this commit)
- **File:** `docs/build-plan.md` (this file) ← the only mutation this turn per plan-mode rules
- **Validation:** `git status -sb` shows `M docs/build-plan.md`, no other `M`; `ls .hermes/plans/` still holds misplaced draft until next commit removes it.

#### Step 2: Engine — `core/conversations.py` schema + storage
- **Create:** `core/conversations.py` (Conversations service, `bbs.storage.dir("conversations")`, `asyncio.to_thread` I/O like `messageboard/boards.py`)
- **Test:** `tests/test_conversations.py::test_create_board_persists` — failing first, then minimal pass
- **Verify:** `pytest tests/test_conversations.py -v` → 1 passed

#### Step 3: Engine — CRUD + threading + find
- **Modify:** `core/conversations.py:40-180` (post/reply with `parent_id`, soft-delete tombstone, `find(query)` across `board_id`/`author`/`body`)
- **Test:** `tests/test_conversations.py` (thread ordering, own-delete vs `moderator` delete via `user.can_access(["moderator"])`, `find` pagination to 3k-scale)
- **Verify:** `pytest tests/test_conversations.py -q` green

#### Step 4: One-API ops for conversations
- **Modify:** `core/opdefs.py` (register 7 ops with `params` + `requires` + `planes`), `tests/test_ops.py` (plane-isolation invariant + parity)
- **Verify:** `curl /api/v1/_schema | jq .conversations` lists ops on management plane only if gated; public plane omits them — test-enforced.

#### Step 5: Migration shims (messageboard + chat → conversations)
- **Modify:** `plugins/messageboard/__init__.py`, `plugins/chat/__init__.py` (thin wrappers over `bbs.conversations`); **Create:** `core/conversations.py::migrate_legacy()` idempotent
- **Data:** `plugins/messageboard/data/<board_id>/*.json` → `kind=board` conversations; `chat` history → one `kind=channel` conversation titled `Lobby`
- **Test:** `tests/test_conversations.py::test_legacy_messageboard_migrates`

#### Step 6: Chrome — tab registry
- **Create:** `plugins/mainmenu/tabs.py` (`TABS = [{id,label,kind,key,requires}]` — now `DASHBOARD` first as the digest quick-links, then `Boards`, `DMs`, `Files`, `Bulletins` — sysop override `plugins/mainmenu/data/tabs.json`, plugin-contributed `pim_tab = {...}` collected at `on_load`)
- **Test:** `tests/test_pim_tabs.py` (ordering — DASHBOARD is `key=1`, `menu_requires` gating hides tab for non-members, `requires=[]` visible to all, Dashboard visible to all)

#### Step 7: Chrome — three-band render (tabs / pane / prompt)
- **Modify:** `plugins/mainmenu/__init__.py: _show_menu()` + `core/screens.py` helper `_render_chrome()` + new tokens `{unread}`/`{mentions}` backed by `bbs.conversations`
- **Layout:** `render_tabs(active_id, session)` → 79-col padded line (`DASHBOARD | Boards | DMs | Files | Bulletins`); `─` separator; `render_pane(active_tab)` — in **Dashboard** it draws the digest quick-links list (`DMs: (10 new) from Anna, Bob, ...` / `Bulletins: (3 new) Title | Title` / `Files: (8 new) ...` — each row elided with `...` to 79, `Up/Dn` moves highlight, `Enter` jumps to the filtered tab at its first new); in **other tabs** it draws the `up/dn select` list box with ANSI fallback; bottom `>`: pinned at `\x1b[{h};1H\x1b[2K{BRIGHT_GREEN}> {RESET}` (already built, reuse)
- **Test:** `tests/test_screens.py` snapshot: ANSI vs plain fallback chrome + Dashboard digest elision to 79

#### Step 8: Input routing for PIM
- **Modify:** `plugins/mainmenu/__init__.py` (`PIMLoop`: `read_key` top-level, `LEFT/RIGHT` or `1/2/3` switch tab, `UP/DN` moves pane selection, `ENTER` — in **Dashboard** jumps to the digest's target tab at its first new (`DMs: (10 new) from ...` → DMs tab, highlight = first unread), in **other tabs** opens full-screen reader or `N)ew DM` picker, `/` → `core/slash.py`, `Q` → `bbs.disconnect`)
- **Test:** `tests/test_pim_input.py` (key → active tab, wrap at ends, Dashboard digest `Enter` jumps to correct tab, pane selection clamps, `/screen` still toggles inside PIM)

#### Step 9: Full-screen reader/editor inside pane
- **Modify:** `plugins/messageboard/__init__.py` (`BoardReader`: paged list, `F`ind via `messages.find`, threaded view with quoting, one-key reply, `D`elete with `can_access` check); **Create:** `core/screens.py` pane-border helper
- **Editor:** classic BBS line editor `/S` save / `/A` abort inside pane, not full-ctrl — per prior decision
- **Test:** Syncterm manual 80x24 lap + `tests/test_messageboard.py` ported

#### Step 10: Prefs, file-override, and docs
- **Modify:** `core/user.py` (new `preferences.home_mode`), `docs/screens.md` (PIM chrome tokens), `plugins/mainmenu/docs/README.md`, `docs/sysop-guide.md`, `docs/architecture.md` (menu diagram → PIM)
- **Rule:** file beats generator: `plugins/mainmenu/screens/pim.asc` overrides chrome; deleting it falls back. Document reskin path.

#### Step 11: Web console parity + E2E
- **Modify:** `plugins/api/admin/app.js` + `index.html` (conversations pane mirrors terminal tabs, same `_schema`)
- **Validation:** `pytest tests/ -q` → ~260+ ; manual Syncterm `localhost:6400` + `ssh -p 6422 localhost` (ANSI box-drawing, DM privacy only participants see, group-gated boards invisible, `/screen` toggle works inside PIM); `curl http://127.0.0.1:8080/api/v1/_schema` shows new ops

### 5. Files Likely to Change

- **New:** `core/conversations.py`, `plugins/mainmenu/tabs.py`, `tests/test_conversations.py`, `tests/test_pim_tabs.py`, `tests/test_pim_input.py`
- **Modify:** `core/app.py` (wire `self.conversations`), `core/opdefs.py` (ops), `core/screens.py` (chrome helper + `{unread}/{mentions}`), `plugins/mainmenu/__init__.py` (become PIM), `plugins/messageboard/__init__.py` + `plugins/chat/__init__.py` (become pane consumers), `docs/screens.md`, `docs/sysop-guide.md`, `docs/architecture.md`, `docs/one-api.md`, `plugins/mainmenu/docs/README.md`
- **Config:** `config.yaml` (optional `pim_tabs` order), `plugins/mainmenu/data/tabs.json` (sysop override)
- **Legacy data:** `plugins/messageboard/data/` + `plugins/chat/data/` (read by migrator, then retired)

### 6. Tests / Validation

- **Unit:** CRUD/threading/permissions/find, tab ordering/gating, chrome snapshots (ANSI vs plain)
- **Suite:** full `pytest tests/ -q --tb=line -p no:cacheprovider` must stay green each commit (246 at `15bd54c` baseline, expected to grow)
- **Manual:** Syncterm `localhost:6400` + SSH `6422` for box-drawing, arrow-key nav, quote-reply, group gates, DM privacy, `/screen` inside PIM; API `_schema` on both planes
- **Perf:** `_pad_line` to 79-col stays; `messages.find` is per-conversation jsonl + in-memory index at boot (same pattern as `boards.py`) — no 3k-row dump

### 7. Risks, Tradeoffs, Open Questions

- **Unified store vs just fixing boards:** unified costs a shim but avoids 3× plumbing for boards/chat/DMs. Risk: `find` across many DMs could be slow → per-conversation jsonl + boot index.
- **ANSI mis-measure:** CP437 `┌`/`─` are single-width but could be miscounted by `_visible_len` if `core/app.py:_ANSI_RE` incomplete → verify with `shared/telnet_protocol.py:ANSI`.
- **Empty PIM on first boot:** seed a `General` board conversation (existing `messageboard` default) so pane is never blank.
- **Key clash:** `1/2/3` for tabs vs numeric board selection — reserve numbers for tabs in PIM; board selection inside pane uses `up/dn`+`enter` only in PIM mode.
- **Open (Dave decides, not this plan):** initial tab set/order (Boards/DMs/Mentions are placeholders), whether Files/Bulletins/Doors get tabs or stay as `[F][B][D]` hotkeys, max tabs before 80-col wrap (cap at ~5 with truncation).

### 8. Commit Discipline

- One commit per step above; each commit message cites its section (`Build plan § Step N`); if a step diverges, the commit notes `diverged from § Step N: reason`.
- Board health check each commit: `pytest tests/ -q` + `curl /api/v1/system.health | jq .status == "running"` before push.

---
*Teams: Coraline — don't wait for another bug to document synchronously. This rail is the history of design intent. After it lands, implementation proceeds via `subagent-driven-development` — one fresh subagent per step, two-stage review (spec compliance → code quality).*
