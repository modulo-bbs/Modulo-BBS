# Modulo BBS — Plugin Development Guide

## Overview

Modulo uses a plugin architecture. Every feature (message boards, file areas, bulletins, etc.) is a plugin that registers with the core. This guide explains how to write your own plugins.

## Quick Start

### 1. Create the Plugin Directory

```bash
mkdir -p plugins/myplugin
```

### 2. Write the Plugin Class

```python
# plugins/myplugin/__init__.py
from plugins.base import Plugin

class MyPlugin(Plugin):
    name = "myplugin"
    version = "1.0.0"
    menu_label = "[X] My Plugin"
    menu_key = "X"
    
    def on_load(self, bbs):
        self.bbs = bbs
        # Event handlers may be sync or async
        bbs.events.on("user:login", self._on_user_login)
    
    def _on_user_login(self, data):
        session = data["session"]
        # ... react to logins
    
    async def handle_command(self, session, command):
        """Handle input while this plugin is active."""
        if command.strip().upper() == "QUIT":
            return False  # Return to main menu
        await self.bbs.send(session, f"You said: {command}\r\n")
        return True  # Stay in plugin
```

Any lifecycle hook (`on_load`, `handle_command`, ...) may be `def` or
`async def` — the core awaits coroutines automatically. Use `async def`
whenever you need to `await` something like `bbs.send()`.

### 3. Drop It In

The loader discovers every package under `plugins/` that exports a `Plugin`
subclass — there is no enable list to edit. Create the directory, and the
plugin appears in the main menu after a restart. Remove the directory (or
the subclass) to disable it.

### 4. Restart the Server

The plugin appears in the main menu automatically.

## Plugin Interface

### Required Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Unique identifier (lowercase, no spaces) |
| `version` | `str` | Semver version string |
| `menu_label` | `str` | Text shown in main menu |
| `menu_key` | `str` | Single character hotkey |

### Lifecycle Methods

| Method | When Called | Purpose |
|--------|------------|---------|
| `on_load(bbs)` | Server startup | Register events, load config |
| `on_unload()` | Server shutdown | Cleanup resources |
| `on_session_start(session)` | User connects | Per-session init |
| `on_session_end(session)` | User disconnects | Cleanup session data |
| `handle_command(session, cmd)` | User types input | Process commands |

### The `bbs` Object

Available after `on_load(bbs)`:

```python
# Send data to a user (async -- await it)
await bbs.send(session, "Hello!\r\n")
await bbs.send_raw(session, b"\x1b[32mGreen text\x1b[0m\r\n")

# Disconnect a user (core closes sockets -- never touch session.writer)
await bbs.disconnect(session)

# Persistent storage: your plugin's data directory (created on demand)
plugin_dir = bbs.storage.dir("myplugin")   # Path("plugins/myplugin/data/")

# Event bus (handlers may be sync or async, one `data` dict argument)
bbs.events.emit("my:event", {"data": 123})
bbs.events.on("other:event", self._handler)

# User management (all async -- await them)
user = await bbs.users.get("dave")          # None if missing
users = await bbs.users.list()
user = await bbs.users.create("dave", "password", display_name="Dave")

# Active sessions
sessions = bbs.session_manager.active_sessions()
count = len(sessions)

# Logon sequence is owned by the logon plugin, not config.yaml
# plugins/logon/data/sequence — one screen: or plugin: line per step
```

## Command Handling

### Returning from a Plugin

`handle_command()` returns a boolean:
- `True` — stay in the plugin, keep handling commands
- `False` — return to main menu

### Command Parsing

```python
async def handle_command(self, session, command):
    parts = command.strip().split()
    if not parts:
        return True
    
    cmd = parts[0].upper()
    args = parts[1:]
    
    if cmd == "HELP":
        self.show_help(session)
    elif cmd == "READ":
        self.read_post(session, args)
    elif cmd == "POST":
        self.create_post(session, args)
    elif cmd == "QUIT":
        return False
    else:
        await self.bbs.send(session, f"Unknown command: {cmd}\r\n")
    
    return True
```

## Storage API

### Your Data Directory

Every plugin owns `plugins/<name>/data/`, handed to you as a `Path`
(created on first access):

```python
plugin_dir = bbs.storage.dir("myplugin")   # Path("plugins/myplugin/data/")
```

There is no key-value layer — compose standard `pathlib` and `json`.

### Simple Values

```python
import json

# Write
(plugin_dir / "counter.json").write_text(json.dumps({"count": 42}))

# Read
data = json.loads((plugin_dir / "counter.json").read_text())
count = data.get("count", 0)
```

### Structured Records

```python
post = {
    "id": 1,
    "author": "dave",
    "subject": "Hello",
    "body": "First post!",
    "timestamp": "2026-08-21T12:00:00Z"
}

(plugin_dir / "post_1.json").write_text(json.dumps(post))
```

### Large or Binary Data

Just use files inside your directory — uploads, SQLite databases, whatever
the plugin needs. Keep everything under your own `data/` so SysOp backups
(`tar czf backup.tar.gz plugins/*/data/`) and clean plugin removal keep
working.

## Event System

### Emitting Events

```python
# Simple event
bbs.events.emit("myplugin:new_post", {"post_id": 42})

# Event with session context
bbs.events.emit("myplugin:user_action", {
    "session": session,
    "action": "read",
    "target": "post_42"
})
```

### Listening for Events

```python
def on_load(self, bbs):
    # Listen for events from other plugins
    bbs.events.on("user:login", self.handle_login)
    bbs.events.on("messageboard:post", self.handle_post)
    
    # Listen for your own events
    bbs.events.on("myplugin:new_post", self.notify_mods)

def handle_login(self, event):
    username = event["user"].username
    # Broadcast = send to each active session (async)
    for s in self.bbs.session_manager.active_sessions():
        await self.bbs.send(s, f"{username} has logged in.\r\n")
```

### Event Naming Convention

```
<namespace>:<action>

Examples:
user:login
user:logout
messageboard:post
files:upload
system:shutdown
```

## UI Patterns

Colour after login comes from `palette_for(session)`, not hardcoded
`ANSI.BRIGHT_CYAN`. Palettes are `themes/*.theme` files (DOS numbers).
Roles, extra keys, and how to write a file: **`docs/themes.md`**. Screen
tokens (`{ACCENT}` …): **`docs/screens.md`**.

### Sending Formatted Text

```python
from core.theme import palette_for
from shared.telnet_protocol import ANSI

p = palette_for(session)
await self.bbs.send(session, f"{p.success}Success!{p.reset}\r\n")
await self.bbs.send(session, f"{p.error}! failed{p.reset}\r\n")

# Bold (attribute, not a theme role)
await self.bbs.send(session, f"{ANSI.BOLD}Important:{ANSI.RESET} read this\r\n")

# Clear screen
await self.bbs.send(session, f"\033[2J\033[1;1H")
```

Selected rows use `p.tab_fg` + `p.tab_bg`, not `ANSI.REVERSE` (REVERSE
is a silver bar on SyncTERM and fights CRT palettes). Always pair a
colour with `p.reset`.

### Menu Display

```python
def show_menu(self, session):
    menu = (
        "\r\n"
        "=== My Plugin ===\r\n"
        "\r\n"
        "  [R] Read Posts\r\n"
        "  [P] New Post\r\n"
        "  [S] Search\r\n"
        "  [Q] Back to Main Menu\r\n"
        "\r\n"
        "  Select: "
    )
    self.bbs.send(session, menu)
```

### Pagination

```python
def show_list(self, session, items, page=0, per_page=10):
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]
    
    for item in page_items:
        self.bbs.send(session, f"  {item['id']}. {item['subject']}\r\n")
    
    if end < len(items):
        self.bbs.send(session, "\r\n  [N] Next page\r\n")
    if page > 0:
        self.bbs.send(session, "  [P] Previous page\r\n")
```

## Best Practices

### 1. Don't Block the Event Loop

One blocked hook stalls every connected node. Use `async def` hooks, await
the core's async APIs, and push CPU-heavy work to a worker thread:

```python
# Good
async def handle_command(self, session, command):
    data = await self.bbs.users.get(command.strip())
    await self.bbs.send(session, f"Hello, {data.shown_name()}!\r\n")
    return True

# CPU-heavy work (bcrypt, big parsing) -- off the event loop
async def rehash(self, password):
    return await asyncio.to_thread(self._expensive_hash, password)

# Bad — blocks every node while it runs
def load_data(self):
    data = open("huge_file.txt").read()   # synchronous disk I/O
```

### 2. Clean Up on Unload

```python
def on_unload(self):
    # Close database connections
    # Cancel background tasks
    # Remove event listeners
    pass
```

### 3. Handle Edge Cases

```python
def handle_command(self, session, command):
    if not command.strip():
        self.show_menu(session)
        return True
    
    # Validate input
    if len(command) > 1000:
        self.bbs.send(session, "Command too long.\r\n")
        return True
    
    # Handle unknown commands gracefully
    try:
        self.process_command(session, command)
    except Exception as e:
        logger.error(f"Command error: {e}")
        self.bbs.send(session, "An error occurred.\r\n")
    
    return True
```

### 4. Log Important Events

```python
import logging

logger = logging.getLogger("myplugin")

def on_load(self, bbs):
    logger.info("MyPlugin loaded")
    
def handle_command(self, session, command):
    logger.debug(f"Session {session.session_id}: {command}")
```

### 5. Test with the AsyncSSH Client

Quick test without Syncterm:
```bash
python3 -c "
import asyncio, asyncssh
class S(asyncssh.SSHClientSession):
    def data_received(self, data, d):
        print(data.decode('latin-1', errors='replace'), end='')
async def run():
    async with asyncssh.connect('127.0.0.1', 6422, known_hosts=None) as c:
        ch, s = await c.create_session(S, term_type='xterm')
        await asyncio.sleep(1)
        ch.write('X\r\n')  # Select your plugin
        await asyncio.sleep(1)
        ch.write('QUIT\r\n')
        await asyncio.sleep(1)
asyncio.run(run())
"
```

## Example: Minimal Plugin

```python
# plugins/hello/__init__.py
from plugins.base import Plugin

class HelloPlugin(Plugin):
    name = "hello"
    version = "1.0.0"
    menu_label = "[H] Hello World"
    menu_key = "H"
    
    def on_load(self, bbs):
        self.bbs = bbs
        self.greetings = 0
    
    def handle_command(self, session, command):
        cmd = command.strip().upper()
        
        if cmd == "QUIT":
            return False
        
        if cmd == "HELLO":
            self.greetings += 1
            self.bbs.send(session, 
                f"Hello, {session.username or 'stranger'}! "
                f"(greeting #{self.greetings})\r\n")
        elif cmd == "COUNT":
            self.bbs.send(session, 
                f"Total greetings: {self.greetings}\r\n")
        else:
            self.bbs.send(session, 
                "Commands: HELLO, COUNT, QUIT\r\n")
        
        return True
```
