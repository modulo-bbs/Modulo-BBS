# Modulo BBS — Plugin System Build Plan

> **Status: COMPLETE.** All six original tasks shipped and tested (see
> `docs/architecture.md` for what exists today). This file is retained as the
> historical record of the foundation build. Current roadmap lives in
> `docs/one-api.md` (One-API principle: ops registry → /api/v1 dispatch →
> web dashboard + terminal sysop menu as first consumers).

## Task 1: Event Bus (core/events.py)
**Goal:** Publish/subscribe system for inter-module communication.
**Spec:** See `docs/plugin-spec.md` — Event Bus section.
**Deliverables:**
- `core/events.py` with `EventBus` class
- Methods: `emit(event, data)`, `on(event, handler)`, `once(event, handler)`, `off(event, handler)`
- Async handlers (run in event loop)
- Core lifecycle events: session:connect, session:disconnect, user:login, user:logout, menu:open, menu:select, command:pre, command:post
**Test:** Unit test that emits events and verifies handlers fire.

## Task 2: User Model (core/user.py)
**Goal:** User data structure + CRUD operations.
**Spec:** See `docs/plugin-spec.md` — User Model section.
**Deliverables:**
- `core/user.py` with `User` dataclass and `UserManager` class
- Fields: username, display_name, password_hash, email, created, last_login, groups, stats, preferences
- Methods: get, create, update, delete, list
- Storage: `users/` directory at project root (JSON files)
- `user.in_group(group)` and `user.can_access(requires)` methods
- Password hashing with bcrypt
**Test:** Unit test that creates user, retrieves, updates, checks groups.

## Task 3: Plugin Base Class (plugins/base.py)
**Goal:** Define the plugin interface.
**Spec:** See `docs/plugin-spec.md` — Plugin System section.
**Deliverables:**
- `plugins/base.py` with `Plugin` base class
- Required attributes: name, version, description, menu_label, menu_key, menu_order
- Lifecycle methods: on_load, on_unload, on_session_start, on_session_end, handle_command
- Type hints for all methods
**Test:** Import test, verify abstract methods exist.

## Task 4: Plugin Loader (core/loader.py)
**Goal:** Scan plugins/ directory, import, and register plugins.
**Deliverables:**
- `core/loader.py` with `PluginLoader` class
- Scans `plugins/*/` for `__init__.py` with Plugin subclass
- Calls `plugin.on_load(bbs)` for each
- Returns list of loaded plugins
- Handles errors gracefully (log and skip broken plugins)
**Test:** Create a mock plugin, verify loader finds and loads it.

## Task 5: Auth Plugin (plugins/auth/)
**Goal:** Extract login/registration from core into a plugin.
**Spec:** See `docs/plugin-spec.md` — Auth System section.
**Depends on:** Tasks 1, 2, 3
**Deliverables:**
- `plugins/auth/__init__.py` with AuthPlugin class
- Login screen (username + password prompt)
- Registration flow (username, password, display name)
- Password verification against stored hash
- Emits: user:login, user:logout, auth:register, auth:login_failed
- Uses User model for storage
**Test:** Integration test that creates user, logs in, verifies session.user is set.

## Task 6: Session→User Binding
**Goal:** Link sessions to users after authentication.
**Depends on:** Tasks 2, 5
**Deliverables:**
- Update `core/session.py` to include `user: User | None` field
- Auth plugin sets `session.user` on successful login
- Core checks `session.user is not None` for authenticated access
**Test:** Verify session.user is set after login, None before.

## Build Order
1. Event Bus (standalone)
2. User Model (standalone)
3. Plugin Base Class (standalone)
4. Plugin Loader (depends on base class)
5. Auth Plugin (depends on all above)
6. Session Binding (depends on user model + auth)
