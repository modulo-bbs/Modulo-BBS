# Modulo BBS — Plugin System Specification

*First draft — 2026-08-21*

## Overview

Modulo BBS uses a plugin-based architecture. Every feature (message boards, file areas, bulletins, etc.) is a plugin that registers with the core. The core provides session management, user model, event bus, and transport. Plugins provide everything else.

## Design Principles

1. **Core is minimal** — session management, user model, event bus, transport
2. **Plugins are replaceable** — swap the menu system, auth flow, or message board without touching core
3. **Events are the nervous system** — core fires lifecycle events, plugins listen and react
4. **Storage is convention-based** — `plugins/<name>/data/` for each plugin, do whatever inside
5. **Everything references the User model** — plugins don't re-invent user data

## Architecture Layers

```
┌─────────────────────────────────────────────────┐
│                   Transport                      │
│              (telnet / SSH / API)                │
├─────────────────────────────────────────────────┤
│                    Core                          │
│  Session Manager │ User Model │ Event Bus       │
├─────────────────────────────────────────────────┤
│                  Plugins                         │
│  Auth │ Menu │ MessageBoard │ Files │ Chat │ .. │
├─────────────────────────────────────────────────┤
│                  Storage                         │
│  users/ (core) │ plugins/<name>/data/ (plugins) │
└─────────────────────────────────────────────────┘
```

## Core Components

### Session Manager

Manages connected users. Each connection gets a `Session` object.

```python
class Session:
    session_id: str
    node_id: int
    state: SessionState          # CONNECTED → LOGIN → MAIN_MENU → IN_PLUGIN → DISCONNECTED
    transport: str               # "telnet" | "ssh" | "api"
    user: User | None            # Set after authentication
    authenticated: bool
    terminal_type: str
    terminal_width: int
    terminal_height: int
    bytes_sent: int
    bytes_received: int
    connected_at: datetime
    last_active: datetime
```

### User Model

Core owns the User model. Every plugin references it.

```python
class User:
    username: str                # Unique, immutable
    display_name: str            # How they appear to others
    password_hash: str           # bcrypt
    email: str
    created: datetime
    last_login: datetime
    groups: list[str]            # ["user"], ["sysop"], ["moderator", ...]
    stats: dict                  # Per-plugin stats (posts, files, etc.)
    preferences: dict            # theme, encoding, home_mode, screen_mode, …
```

Saved preference keys (plain dict; unknown keys are ignored):

| Key | Values | Default | Effect |
|---|---|---|---|
| `theme` | stem of a `themes/*.theme` file (`classic`, `amber`, `matrix`, … or one you dropped) | `classic` | Colour palette after login (`core.theme.palette_for`). Files are DOS `fg,bg` lines; missing keys default to classic. Aliases come from `alias=` in the file (`phosphor` → `matrix`). `/theme` opens a picker; `/theme amber` sets it. How-to: `docs/themes.md`. |
| `encoding` | `cp437` / `utf-8` / `ascii` | (detect) | Wire codec |
| `home_mode` | `pim` / `menu` | `pim` | Tabbed PIM vs classic list |
| `screen_mode` | `generated` / unset | unset | `/screen` machine view |

Plugins read `session.user.preferences` (and `palette_for(session)` for
colour). The event bus does **not** deliver prefs. Adding a named
palette: `docs/themes.md`.

**Core provides:**
- `bbs.users.get(username)` → User
- `bbs.users.create(username, password, display_name)` → User
- `bbs.users.update(username, **fields)` → User
- `bbs.users.delete(username)` → bool
- `bbs.users.list()` → list[User]
- `user.in_group("moderator")` → bool (pure membership test)
- `user.can_access(["moderator"])` → bool (the one access rule)

**Core owns:** the `users/` directory at the project root (one JSON file per
account). Core-owned data lives *outside* `plugins/` so a plugin can be
removed without ever touching accounts.

### Auth System (Dual-Layer)

| Layer | Owner | Responsibility |
|-------|-------|----------------|
| User model + storage | Core | Data structure, CRUD, session binding |
| Auth flows | Plugin | Login screen, registration, password handling |

**Why split:** The User model is infrastructure — everything depends on it. Auth flows (how you authenticate) are replaceable. Someone can swap passwords for OAuth without breaking the User model.

**Auth plugin interface:**
```python
class AuthPlugin(Plugin):
    def handle_login(self, session) -> bool:
        """Show login screen, collect credentials, validate.
        Set session.user on success. Return False if user disconnects."""
        pass
    
    def handle_registration(self, session) -> bool:
        """Show registration screen, collect info, create user.
        Return False if user disconnects."""
        pass
    
    def handle_password_change(self, session, user) -> bool:
        """Optional: password change flow."""
        pass
```

**Core's only auth check:**
```python
if not session.authenticated:
    # Run auth plugin
    auth_plugin.handle_login(session)
```

### Event Bus

The nervous system. Core fires events, plugins listen. This is how the core maintains observability when plugins replace core components.

```python
# Emit an event
bbs.events.emit("user:login", {"user": user, "session": session})

# Listen for events
bbs.events.on("user:login", handle_login)

# Listen once
bbs.events.once("session:disconnect", cleanup)

# Remove listener
bbs.events.off("user:login", handle_login)
```

#### Core Lifecycle Events (always fired, can't be suppressed)

| Event | When | Data |
|-------|------|------|
| `session:connect` | User connects | `{session}` |
| `session:disconnect` | User disconnects | `{session}` |
| `user:login` | Authenticated | `{session, user}` |
| `user:logout` | Logged out | `{session, user}` |
| `menu:open` | Menu displayed | `{session, menu_name}` |
| `menu:select` | User selected option | `{session, option, menu_name}` |
| `command:pre` | Before command executes | `{session, command, plugin}` |
| `command:post` | After command executes | `{session, command, plugin, result}` |

#### Plugin Events (fired by plugins)

| Event | Plugin | Data |
|-------|--------|------|
| `messageboard:post` | messageboard | `{session, post}` |
| `messageboard:reply` | messageboard | `{session, reply, parent}` |
| `files:upload` | files | `{session, filename, size}` |
| `files:download` | files | `{session, filename}` |
| `auth:register` | auth | `{session, user}` |
| `auth:login_failed` | auth | `{session, username, reason}` |

#### Event Flow Example

```
User presses "M" in main menu
  ↓
Core emits: menu:select {session, option: "M", menu: "main"}
  ↓
  ├→ Messageboard plugin handles it (shows message board)
  ├→ Stats plugin increments menu_selections counter
  └→ Audit log plugin records it
  ↓
Core emits: menu:open {session, menu: "messageboard"}
```

**Key insight:** Even if a plugin replaces the menu system, the core still fires `menu:select`. Instrumentation works regardless of which plugin handles the UI.

## Plugin System

### Plugin Base Class

```python
class Plugin:
    """Base class for all Modulo BBS plugins."""
    
    name: str              # Unique identifier ("messageboard")
    version: str           # Semver ("1.0.0")
    description: str       # Human-readable description
    menu_label: str        # Display text ("[M] Message Board")
    menu_key: str          # Hotkey ("M")
    menu_order: int        # Sort order in main menu (lower = higher)
    home_label: str        # Tab text when listed in mainmenu's home file
    
    def on_load(self, bbs):
        """Called once at startup. Register event handlers."""
        pass
    
    def on_unload(self):
        """Called when plugin is being removed."""
        pass
    
    def on_session_start(self, session):
        """Called when a user connects."""
        pass
    
    def on_session_end(self, session):
        """Called when a user disconnects."""
        pass
    
    def handle_command(self, session, command) -> bool:
        """Handle a command while this plugin is active.
        Return True to stay in plugin, False to return to menu."""
        pass

    def render_home_pane(self, session) -> str:
        """Middle pane when this plugin is listed in mainmenu's home file."""
        return ""

    def handle_home_key(self, session, key) -> bool:
        """Key while this plugin's home tab is active. True = consumed."""
        return False

    def home_digest(self, session):
        """Optional dashboard row(s): (text, jump_plugin_id) or a list of those."""
        return None
```

The shipped mainmenu is chrome: tab bar + `>` prompt. It reads `plugins/mainmenu/data/home` (one plugin name per line) and delegates the pane. A plugin appears on the strip only if it is loaded **and** listed. `home_label` is the tab text.

### Plugin Lifecycle

1. Server starts → scans `plugins/` directory
2. Each plugin's `__init__.py` exports a `Plugin` subclass
3. Core calls `plugin.on_load(bbs)` — plugin registers commands, events
4. Core adds plugin's menu item to main menu
5. User selects plugin → core emits `menu:select`, calls `plugin.on_session_start(session)`
6. Plugin handles commands via `handle_command(session, cmd)`
7. User exits plugin → core calls `plugin.on_session_end(session)`
8. Server shutdown → core calls `plugin.on_unload()` for each plugin

### Plugin Directory Structure

Each plugin is a self-contained directory at `plugins/<name>/`. Code, data, screens — everything lives in one place. Like WordPress.

### Standard Plugin Directory Layout

```
plugins/
├── base.py                    # Plugin interface
├── login/                     # Login plugin (example)
│   ├── __init__.py            # Plugin class
│   ├── login.py               # Login logic
│   ├── totp.py                # TOTP support
│   ├── screens/               # ← Display templates (sysops edit here)
│   │   ├── login.txt
│   │   ├── register.txt
│   │   └── totp.txt
│   └── data/                  # Runtime data
│       └── totp_secrets.json
├── messageboard/
│   ├── __init__.py
│   ├── screens/
│   │   ├── board_list.txt
│   │   ├── thread_view.txt
│   │   └── post_editor.txt
│   └── data/
│       ├── boards.json
│       └── posts/
└── files/
    ├── __init__.py
    ├── screens/
    │   ├── file_list.txt
    │   └── upload.txt
    └── data/
        └── uploads/
```

**Convention:**
- `screens/` — display templates (sysops edit these to customize look)
- `data/` — runtime data (JSON, SQLite, files)
- `*.py` — plugin code

**Rule:** Everything for a plugin lives in `plugins/<name>/`. Don't touch `plugins/<other_name>/`.

**Exception:** Core owns `users/` at the root level since everything references users.

### Plugin Storage

Each plugin owns the `data/` subdirectory inside its own package. The plugin
decides what goes inside — JSON, SQLite, flat files, whatever.

```python
# Get plugin's data directory (created on first access)
plugin_dir = bbs.storage.dir("messageboard") → Path("plugins/messageboard/data/")

# Compose standard pathlib/json calls against it
boards = plugin_dir / "boards.json"
```

There is no key-value convenience layer — plugins compose `pathlib` and
`json` directly against their directory. Names passed to `storage.dir()` must
match `[a-z0-9_-]+`; anything else raises `core.storage.StorageError`.

## Permission System (Groups)

One mechanism, no levels, no ACL matrices. A user belongs to **groups**
(plain lowercase labels); plugins gate access through group requirements.
This is a BBS, not a classified system — the whole design fits on one page.

### The one built-in rule: the sysop group

`sysop` is the only reserved group name. Members of the `sysop` group have
access to everything, on every plugin and every action. It always exists as
a static; you cannot redefine or shadow it.

Every other group is a free-form label the sysop invents (`user`,
`moderator`, `veterans`, `traders`, ...) and assigns to users via
`bbs.users.update(username, groups=[...])`. New users default to
`groups=["user"]`.

### How plugins gate access

**Rule 1 — every plugin gates itself.** Each plugin checks at entry that the
user may use it at all:

```python
async def on_session_start(self, session):
    if not session.user.can_access(self.required_groups):
        await self.bbs.send(session, "Access denied.\r\n")
        return False          # back to menu
```

`required_groups` comes from config (below). Empty list or missing key =
open to everyone.

**Rule 2 — plugins may attach group requirements anywhere inside.** Any
action, sub-board, menu item, or door can carry its own requirement:

```python
# messageboard sub-boards with different audiences
BOARDS = [
    {"name": "general",  "requires": []},            # public
    {"name": "trading",  "requires": ["traders"]},   # members-only
    {"name": "ops",      "requires": ["moderator"]},
]
for board in BOARDS:
    if session.user.can_access(board["requires"]):
        ...render this board's line...
```

Same shape for a door menu: each game is an option with its own `requires`
list, all exposed to the sysop through config. One helper — `can_access` —
covers plugin-level, area-level, and action-level gates alike.

### Communicating options to core/config

Plugins expose their gateable resources by declaring them in their section of
the central config (see Configuration). The convention:

```yaml
plugins:
  messageboard:
    required_groups: []            # plugin-level gate (Rule 1)
    boards:
      general:   { requires: [] }
      trading:   { requires: [traders] }
      ops:       { requires: [moderator] }
  doors:
    required_groups: []
    games:
      tradewars: { requires: [veterans] }
      klondike:  { requires: [] }
```

The plugin reads its own section via `bbs.config`, applies `can_access`
against whatever it finds, and treats unknown/missing keys as open access.
The future HTTP API exposes the same sections so remote tooling can edit
them. Core stays ignorant of plugin-specific shapes — it only supplies the
config dict, the user's groups, and `can_access`.

### The access rule

Implemented once in core as `User.can_access(requires)`:

| Condition | Result |
|-----------|--------|
| `requires` empty/None | everyone enters (public/open) |
| user is in the `sysop` group | always granted |
| user's groups intersect `requires` | granted (**any-of**, not all-of) |
| otherwise | denied |

Design notes:

* Case-insensitive everywhere; stored lowercase. A required group nobody
  belongs to fails closed (an empty room, not an open door).
* Groups are labels, not ranks — there are no levels, no inheritance, no
  action-keyword magic. If a plugin wants "moderator-only" behavior, the
  sysop creates a `moderator` group and puts people in it.
* Legacy user files written before groups existed load cleanly and default
  to `["user"]`.

## Keybindings

Every plugin may ship a `keys` file in its plugin directory
(`plugins/<name>/keys`) binding command names to keys. Format: plain text,
one binding per line, comma-separated — deliberately not YAML, because the
audience is a sysop in SyncTERM at 1am.

```
# plugins/messageboard/keys
L, LIST        # list messages
P, POST
R, REPLY
Q, QUIT
```

**Semantics** (implemented once in core via `bbs.keys_for(name, defaults)`):

| Rule | Behavior |
|------|----------|
| File absent | plugin's documented defaults apply unchanged |
| Name omitted from file | that command is **disabled** (omit = kill switch) |
| Unknown name in file | logged warning, line ignored (typos fail safe) |
| Case | normalized to uppercase everywhere |
| Comments/blanks | `#` lines and blank lines ignored; CRLF tolerated |
| Unparseable line | logged warning, skipped |

**Well-known defaults:** conventional actions use standard keys unless the
sysop rebinds them — `Q` = quit/back, `?` = help. A plugin author who ships
a `quit: X` default is wrong and will be judged harshly.

**Plugins never hardcode keys.** They call `bbs.keys_for(plugin_name,
defaults)` and read the result; disabled commands simply don't appear, and
the plugin's input loop treats those keys as unknown input.

## HTTP API

**Adopted design: see `docs/one-api.md` (the One-API Principle).** Summary of
what is decided; that document is authoritative:

- One canonical operations registry (`core/ops.py`, planned) — every capability
  (sysop management *and* ordinary user actions like posting) is declared once
  with params, permission groups, and exposure plane.
- Two exposure planes off one dispatcher:
  - **Management plane** — sysop ops, loopback-only listener, never proxied.
  - **Public plane** — user ops, meant to sit behind a TLS reverse proxy.
  - A sysop-gated operation can never appear on the public plane or its schema
    (test-enforced invariant).
- Versioned paths (`/api/v1/...`) and a self-describing `GET /api/v1/_schema`
  per plane.
- Authentication is by user account (bcrypt), including bot accounts for
  machine clients with role-appropriate groups. There are no scoped API keys;
  group gates are the only authorization mechanism.

Interim shipped endpoints (until the registry lands): `GET /api/health`,
`GET /api/sessions`, `POST /api/shutdown`, `POST /api/broadcast` — see the
SysOp Guide. These will migrate onto `/api/v1/` when the registry exists.

## Configuration

`config.yaml` — server settings, plugin options. The actual shipped schema:

```yaml
server:
  host: "127.0.0.1"    # bind address (CLI --host overrides)
  telnet_port: 6400
  ssh_port: 6422       # SSH off unless --ssh / --ssh-port given
  max_nodes: 8

# Core roles — which plugin directory fills each job. Omit a line to use
# the role name as the folder. Callers use bbs.plugin_for("modal"), not a
# hard-coded directory.
login: login
logon: logon
mainmenu: mainmenu
modal: modal

api:                    # HTTP control API (see SysOp Guide)
  enabled: false
  host: "127.0.0.1"
  port: 8080

# The logon sequence lives in plugins/logon/data/sequence (owned by logon).
# The home tab strip lives in plugins/mainmenu/data/home (owned by mainmenu).
# There is no global plugins.enabled list — the loader auto-discovers
# plugins/ subdirectories.
```

## Logon Sequence (Sysop-Configurable)

The order of what a caller sees — splash screens, login, bulletins, menu — is
data, not code. And the sequencer itself is **just another plugin**: it reads
`plugins/logon/data/sequence` and executes steps. Everything in the sequence is
pluggable; there is no `built-in` step type.

```
# plugins/logon/data/sequence
screen:splash.txt
plugin:login
screen:welcome.txt
plugin:bulletins
plugin:mainmenu
```

`plugin:<role>` resolves through the core role map (`bbs.plugin_for`), so
`plugin:mainmenu` follows `mainmenu:` in config.yaml.

### Core-plugins

**Core-plugins** are the plugins Modulo ships with that the board needs to
operate: `login`, `logon` (sequencer), `mainmenu` (chrome + prompt), and
`modal` (pickers). They are ordinary plugins — same base class, same
directory layout, same rules, replaceable like any other plugin — but a board
without them has no authentication, no menu, and no overlay picker, so they
ship enabled by default. Swap a folder with one line in config.yaml
(`modal: awesomemodal`). Social, files, bulletins, and dashboard are
**optional** home tabs listed in `plugins/mainmenu/data/home`, not core roles.

The distinction is about packaging and support, not privilege:

| | core-plugins | third-party plugins |
|---|---|---|
| Shipped with Modulo | yes | no |
| Enabled by default | yes | sysop's choice |
| Tested with each release | yes | author's responsibility |
| Replaceable | yes (same mechanism) | yes |

A sysop who swaps out or misconfigures a core-plugin owns the result — same as
deleting `/bin/sh` on Linux. The system won't stop you; it also won't pretend
the outcome is supported. If a required core-plugin fails to load at startup,
core logs it loudly and refuses to serve sessions rather than half-working.

### Step types

| Step | What it does |
|------|--------------|
| `plugin:<name>` | Run the named plugin's session flow |
| `screen:<file>` | Display `screens/<file>`, no input |

Sysops reorder, remove, or duplicate steps freely. Two splash screens before
login? Add two `screen:` lines. Don't want bulletins? Delete the line.

### Why the sequencer is a plugin

Consistency: the runner is pure orchestration — read a list, call each thing,
emit events. It needs no special privileges, so it gets none. The payoff is
that the *entire* logon experience is swappable: a sysop can point
the `logon` role at any orchestrator — a wizard-style onboarding, straight-to-
chat, kiosk mode — without touching core.

### What stays in core (and why)

1. **The bootstrap hook.** Something must run first. After transport
   handshake, core invokes `bbs.plugin_for("logon")` — one identical
   line per transport. This avoids infinite regress (who runs the runner?)
2. **Graceful failure.** Missing or broken logon plugin → core displays a
   minimal "system unavailable" notice and closes cleanly. Never hangs.
3. **Non-suppressible primitives.** `bbs.disconnect(session)`,
   `session:connect`, `session:disconnect` are core-owned. Plugins (including
   the sequencer) cannot close sockets or hide connection/disconnection from
   instrumentation. Step-level `logon:step` events are emitted by the
   sequencer — worst case a broken sequencer loses step granularity, never the
   whole audit trail.

### Main menu is a plugin

There is no core menu system. A `mainmenu` plugin iterates `bbs.plugins`,
sorts by `menu_order`, and renders each plugin's `menu_label` / `menu_key`
(every plugin already self-describes via the base class). Swap in a different
menu plugin and nothing else changes.

### Hard boundary: disconnect

Plugins interpret keystrokes (`Q` → return False), but closing the socket is a
core primitive: `bbs.disconnect(session)`. Plugins never touch the writer
directly, so a buggy plugin can always be cleaned up reliably by core.

## Implementation Order

1. Plugin base class + loader ✓
2. Event bus ✓
3. User model + storage ✓
4. Login plugin ✓
5. Logon sequencer plugin + core bootstrap hook (`logon` role)
6. Mainmenu plugin (extract from server.py)
7. Message board plugin
8. File transfer plugin
9. Chat plugin
10. HTTP API
