# Modulo BBS — Architecture

## Overview

Modulo is a modular, plugin-based Bulletin Board System built in Python 3.11+ with asyncio. It supports multiple transports (telnet, SSH) and exposes an API for external tooling.

## Design Principles

1. **Unix philosophy** — each module does one thing well
2. **Plugin architecture** — features are plugins, not hardcoded
3. **API-first** — everything accessible via HTTP API for external tools
4. **Transport-agnostic** — core logic doesn't know or care about telnet vs SSH
5. **CP437 native** — raw bytes throughout, no UTF-8 mangling

## Directory Structure

```
modulo-bbs/
├── core/                    # Infrastructure (non-negotiable core)
│   ├── app.py               # BBSApp: shared application object
│   ├── runner.py            # read_command/read_key, plugin flow, bootstrap
│   ├── session.py           # Session state machine + node tracking
│   ├── user.py              # User model + CRUD (users/ storage)
│   ├── events.py            # Event bus
│   ├── keys.py              # Plugin keybinding loader (plugins/<name>/keys)
│   ├── loader.py            # Plugin discovery + loading
│   └── storage.py           # PluginStorage: bbs.storage.dir(name)
├── plugins/                 # All plugins (self-contained)
│   ├── base.py              # Plugin interface (not a plugin itself)
│   ├── login/               # Auth flows: login, registration, TOTP
│   ├── logon/               # Logon sequencer (config-driven steps)
│   ├── mainmenu/            # Main menu (renders from plugin metadata)
│   ├── messageboard/        # Multi-board message base + threads
│   ├── files/               # File area catalog (transfers: future)
│   ├── bulletins/           # Sysop notices, new-since-last-call at logon
│   ├── chat/                # Inter-node live chat
│   ├── doors/               # Door game catalog (launching: future)
│   └── api/                 # HTTP control API (health/sessions/shutdown/broadcast)
├── server/                  # Transports + session plumbing
│   ├── server.py            # Telnet server
│   ├── ssh_server.py        # SSH server (asyncssh)
│   └── session.py           # Session/SessionManager
├── shared/
│   ├── telnet_protocol.py   # RFC 854/855 negotiator, ANSI codes
│   └── blockletters.py      # '#' block-letter banner renderer
├── tests/                   # pytest suite (one file per module/plugin)
├── tools/                   # Dev + ops tools
├── docs/                    # Documentation
├── keys/                    # SSH host keys (gitignored)
├── users/                   # User data (core-owned, one JSON per account)
├── client/                  # Dev/test terminal client
├── run_server.py            # Entry point
├── config.yaml              # Server configuration
├── LICENSE                  # Apache 2.0
├── TRADEMARK.md             # Trademark policy
└── .gitignore
```

Each plugin at `plugins/<name>/` is self-contained: code, screens, data — everything in one place. Like WordPress. Standard layout: `screens/` for display templates, `data/` for runtime data, `*.py` for code.

## Core Components

### Session (`core/session.py`)

Manages connected users. Each connection gets a `Session` object that tracks:
- State (CONNECTED → LOGIN → MAIN_MENU → IN_PLUGIN → DISCONNECTED)
- Node number (1-N, where N = max_nodes)
- Terminal info (type, width, height)
- User identity (once authenticated)
- Byte counters + idle timer

Sessions are protocol-agnostic — telnet and SSH both create Session objects.

### Menu System (`plugins/mainmenu/`)

There is no core menu module — the menu is just another plugin. In classic
`home_mode=menu` the mainmenu plugin iterates `bbs.plugins`, sorts by
`menu_order`, and renders each plugin's `menu_label` / `menu_key`. In the
default `home_mode=pim` (since 2026-08-24) it becomes the tabbed PIM home:
top tabs (branches of one surface; see `plugins/mainmenu/tabs.py` +
`docs/build-plan.md` Phase 1), middle pane (filtered `core/conversations.py`
views — boards / DMs / Mentions), bottom `>` prompt. File `pim.*` beats the
generated chrome; `preferences.home_mode` toggles classic vs PIM.

Classic board shape (when `home_mode=menu`; PIM replaces the list with tabs):

```
Main Menu
├── [M] Message Boards   → plugin: messageboard   (menu_order 10)
├── [F] Files            → plugin: files
├── [B] Bulletins        → plugin: bulletins
├── [C] Chat             → plugin: chat
├── [D] Doors            → plugin: doors
├── [I] System Info      → built into mainmenu
├── [X] Shutdown         → built into mainmenu (sysop group only, Y/N confirm)
└── [Q] Disconnect       → built into mainmenu
```

### Event Bus (`core/events.py`)

Publish/subscribe system for inter-module communication. Plugins emit events and subscribe to events without knowing about each other.

```python
# Emit an event
bbs.events.emit("user:login", {"user": user, "session": session})

# Listen for events
bbs.events.on("chat:message", handle_chat_message)
```

Events are async — handlers run in the event loop.

### Transport Layer (`server/`)

Implements network protocols. Each transport:
1. Accepts connections
2. Performs protocol negotiation (telnet IAC / SSH handshake)
3. Creates a Session object via the shared SessionManager
4. Hands the session to the core bootstrap hook (`core.runner.run_bootstrap`),
   which invokes the configured logon plugin — identical flow for every
   transport

Telnet lives in `server/server.py`, SSH in `server/ssh_server.py` (asyncssh).
Adding a new transport means writing another listener that calls the same
bootstrap hook.

## Plugin System

### Plugin Base Class (`plugins/base.py`)

```python
class Plugin:
    """Base class for all Modulo plugins."""
    
    name: str              # Unique identifier ("messageboard")
    version: str           # Semver ("1.0.0")
    menu_label: str        # Display text ("[M] Message Board")
    menu_key: str          # Hotkey ("M")
    
    def on_load(self, bbs):
        """Called once at startup. Register event handlers, etc."""
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
    
    def handle_command(self, session, command):
        """Handle a command while this plugin is active."""
        pass
```

### Plugin Lifecycle

1. Server starts → scans `plugins/` directory
2. Each plugin's `__init__.py` exports a `Plugin` subclass
3. Core calls `plugin.on_load(bbs)` — plugin registers commands, events
4. Core adds plugin's menu item to main menu
5. User selects plugin → core calls `plugin.on_session_start(session)`
6. Plugin handles commands via `handle_command(session, cmd)`
7. User exits plugin → core calls `plugin.on_session_end(session)`
8. Server shutdown → core calls `plugin.on_unload()` for each plugin

### Plugin Storage

Storage is split by ownership:

```
users/                        # Core-owned: one JSON file per account
plugins/
├── login/data/               # Plugin-owned: each plugin's runtime data
├── messageboard/data/        #   (boards.json, posts/, ...)
├── files/data/               #   (uploads/, ...)
└── chat/data/                #   (chat logs, ...)
```

Plugins get their data directory via `bbs.storage.dir(plugin_name)` (a
`Path`, created on demand) and compose standard `pathlib`/`json` calls
against it. There is no pluggable backend — just files on disk.

## API Layer

### Internal Python API

Plugins interact with the core via the `bbs` object:
- `bbs.send(session, text)` — send text to a user (async)
- `bbs.send_raw(session, data)` — send raw bytes, e.g. telnet negotiation (async)
- `bbs.disconnect(session)` — close a session cleanly (async)
- `bbs.storage.dir(plugin_name)` — the plugin's data directory as a `Path`
- `bbs.events.emit/on` — event bus
- `bbs.users.get/create/update/delete/list` — user accounts (all async)
- `bbs.session_manager.active_sessions()` — list of active sessions
- `bbs.config` — server configuration from config.yaml

### External HTTP API

**Adopted design: `docs/one-api.md` (the One-API Principle).** One canonical
operations registry; two exposure planes (management = loopback-only sysop ops,
public = user ops behind a TLS proxy); versioned `/api/v1/` paths with a
self-describing `_schema`; authentication by user account including bot
accounts for machine clients — group gates are the only authorization, there
are no scoped API keys.

Interim shipped endpoints (until the registry is implemented):

```
GET  /api/health      → Server status
GET  /api/sessions    → Active sessions
POST /api/shutdown    → Graceful shutdown
POST /api/broadcast   → Message every connected user
```

Auth: optional `X-API-Key` allowlist from config (`api.keys`); empty list =
open, intended for loopback/dev use.

## Data Model

### User
```json
{
    "username": "dave",
    "password_hash": "...",
    "display_name": "Dave",
    "created": "2026-08-21T00:00:00Z",
    "last_login": "2026-08-21T12:00:00Z",
    "groups": ["sysop"],
    "stats": {
        "posts": 42,
        "files_uploaded": 5,
        "files_downloaded": 12,
        "time_online": 3600
    }
}
```

### Message
```json
{
    "id": 1,
    "board": "general",
    "author": "dave",
    "subject": "Welcome!",
    "body": "...",
    "timestamp": "2026-08-21T12:00:00Z",
    "parent_id": null,
    "tags": ["announcement"]
}
```

## Configuration

`config.yaml` — server settings, plugin options. The shipped schema (CLI flags
`--host --port --ssh-port --nodes --plain --ssh` override these values):

```yaml
server:
  host: "127.0.0.1"
  telnet_port: 6400
  ssh_port: 6422       # SSH enabled by --ssh or --ssh-port flag
  max_nodes: 8

logon_plugin: logon

logon_sequence:
  - screen:splash.txt
  - plugin:login
  - screen:welcome.txt
  - plugin:bulletins
  - plugin:mainmenu

api:                   # HTTP control API, off by default
  enabled: false
  host: "127.0.0.1"
  port: 8080
  # keys:
  #   - name: "admin"
  #     key: "replace-with-a-real-secret"
```

There is no `plugins.enabled` list — the loader auto-discovers every
`plugins/<name>/__init__.py`.
