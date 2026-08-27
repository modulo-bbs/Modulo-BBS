# Modulo BBS — SysOp Guide

You run the board. Out of the box it should already *be* a board — not a
kit you assemble. Change how it looks by dropping files (themes, screens),
or point an agent at this tree and tell it to make those same changes.
Either way the contract is the files on disk, not a special control panel.

**To start the server:** the tree-root **`README.md`**. This guide is the
rest — screens, themes, users, plugins.

## Quick Start

### Running the Server

```bash
venv/bin/python run_server.py --port 6400 --ssh-port 6422
```

Once it is up, `/ver` at the home `>` prompt (and the splash `Version`
line) show a dotted number plus a git short hash, e.g. `0.2.0 (8eae965)`.
If that does not match the tree you just pulled, you are on an old process.

Flags:
- `--host HOST` — bind address (default: `127.0.0.1`, use `0.0.0.0` for LAN)
- `--port PORT` — telnet port (default: 6400)
- `--ssh-port PORT` — SSH port (default: 6422)
- `--nodes N` — max simultaneous connections (default: 8)
- `--plain` — strip ANSI codes (for debugging)

### Connecting

**Telnet:**
```
telnet localhost 6400
```

**SSH (no auth):**
```
ssh -p 6422 localhost
```

**Syncterm:**
- Telnet: Address=`localhost`, Port=`6400`, ConnectionType=Telnet
- SSH: Address=`127.0.0.1`, Port=`6422`, ConnectionType=SSH (no auth)

### Stopping the Server

Any of these performs a **graceful** shutdown (connected users get a goodbye
notice before their connections close):

- `Ctrl+C` in the server's terminal (SIGINT)
- `kill <pid>` (SIGTERM — the default; never needed: `-9`)
- If the HTTP API is enabled (`api.enabled: true` in config.yaml):
  `curl -X POST http://127.0.0.1:8080/api/shutdown`

A sysop logged into the board can also use the `[X] Shutdown` main-menu option
(Y/N confirmation required).

Finding the PID:
```bash
ss -tlnp | grep -E ':(6400|6422)'
```

## User Management

### Creating Users

Users are stored in `users/` at the project root. Each user is a JSON file:
```json
{
    "username": "dave",
    "password_hash": "bcrypt_hash_here",
    "display_name": "Dave",
    "created": "2026-08-21T00:00:00Z",
    "groups": ["sysop"]
}
```

### User Groups

Access control is group-based. A user's `groups` list decides what they can
use; plugins may attach their own group requirements to any area, menu item,
or action (all exposed in `config.yaml`).

One group name is special:

| Group | Description |
|-------|-------------|
| `sysop` | Reserved. Members have access to everything, everywhere. |
| `user` | Conventional default for new accounts (not reserved — just a name) |
| anything else | Free-form labels you invent (`moderator`, `veterans`, `traders`, ...) |

Assign groups by editing the user's JSON or via
`bbs.users.update(username, groups=[...])`. New users get `["user"]`.

To restrict part of a plugin (a sub-board, a door game), set its
`requires: [groupname]` in `config.yaml`; leave it empty or omit it to keep
it open to everyone.

### Password Policy

Passwords are bcrypt-hashed. Minimum 6 characters. The server never stores plaintext.

## Plugin Management

### Customizing screens

Every plugin's visible output is a **named screen** you can override with a
file — no code, no restart. Drop a file into the plugin's `screens/` folder:

```
plugins/mainmenu/screens/main.asc     ← overrides the main menu
screens/splash.ans                    ← CP437 art version of the logon splash
```

Resolution per name: `.ans` (CP437+ANSI) → `.asc` (plain ASCII) →
`.txt` (shipped default). Delete your file to restore the default.

Tokens (`{username}`, `{time}`, `{node}`, `{active}`, `{ACCENT}`,
`{BRIGHT_CYAN}` …) work in any screen. Semantic `{ACCENT}` / `{SUCCESS}` /
`{WARNING}` / `{ERROR}` / `{MUTED}` / `{TEXT}` follow the caller's
`/theme` (saved as `preferences.theme`). Literal colour names stay that
colour. `.ans` files with painted SGR bytes are **not** recolored — they
are art, not templates. Full vocabulary: **`docs/screens.md`**. Theme
files (`themes/*.theme`) and the `/theme` picker: **`docs/themes.md`**.
Each plugin's own `plugins/<name>/docs/README.md` lists its screen names,
keys, config files, and data locations. An in-board screens editor is a
later accessory — reskins are files you drop, not something the BBS edits.

### Colour themes

Callers pick a named file after login. At the home `>` prompt press `/`,
type `theme`, Enter. Arrows move; the overlay **previews** the highlight;
Enter saves; ESC cancels. `/` then `theme amber` sets without the picker.

`/` then `ver` prints the board version (and a git short hash) so you can
tell this process matches the tree you think you are running. Number lives
in `core/version.py`.

Palettes live in **`themes/*.theme`** — one `key=fg` or `key=fg,bg` per
line, DOS colour numbers (0–15). Missing keys use classic defaults. Drop
`themes/sunset.theme` and it shows up in `/theme`; edit the file and the
next paint picks it up. No Python, no restart. How-to and colour chart:
**`docs/themes.md`**. Pre-login stays classic. `.ans` art does not follow
the palette.

### Loading

Plugins are discovered automatically: the loader scans `plugins/*/` and loads
every `__init__.py` that exports a `Plugin` subclass. A plugin that fails to
import or raises during load is logged and skipped — one broken plugin never
prevents startup. There is no `plugins.enabled` list; to remove a plugin from
the board, remove its directory (or move it out of `plugins/`). Restart the
server after changes.

### HTTP Control API (shipped)

Enabled in `config.yaml`:

```yaml
api:
  enabled: true        # off by default
  host: "127.0.0.1"    # keep loopback unless you need LAN access
  port: 8080
  # keys:              # optional X-API-Key allowlist; empty = open (dev mode)
  #   - name: "admin"
  #     key: "replace-with-a-real-secret"
```

Endpoints (stdlib-only implementation, no frameworks):

```
GET  /api/health      → status, node counts, loaded plugins
GET  /api/sessions    → active sessions
POST /api/shutdown    → graceful shutdown, optional {"message": "..."}
POST /api/broadcast   → send a line to every connected user
```

This is the interim control surface. The full sysop management API — users,
plugin configuration, audit — is specified in `docs/one-api.md` (the One-API
principle) and not yet implemented.

### Plugin Directory

Each plugin is self-contained in `plugins/<name>/`:
```
plugins/
├── base.py           # Plugin interface (not a plugin itself)
├── login/
│   ├── __init__.py   # plugin class + flow wiring
│   ├── login.py      # LoginFlow
│   ├── registration.py
│   ├── totp.py       # TOTPManager/TOTPFlow (optional 2FA)
│   ├── screens/      # display templates (CRLF, ASCII-safe)
│   └── data/         # runtime data (totp_secrets.json)
├── messageboard/
│   ├── __init__.py     # definition-holder only: boards.json + boot sync ([M] retired)
│   ├── boards.py       # BoardStore (board definitions)
│   ├── screens/
│   └── data/           # boards.json (definitions; messages live in conversations/)
├── conversations/      # unified message store (Social/DMs/boards since 2026-08-25)
│   └── data/           # index.json, reads.json, <conversation_id>/ messages
└── ...
```

Social rooms are `kind=board` conversations. Sysops manage messages through the
One-API ops (`conversations.*`, with `boards.*` shims) — see `docs/one-api.md`;
in-app, Social rooms are the Telegram-style chat (see
`plugins/mainmenu/docs/README.md` for the key map: overlay notepad is
Ctrl-E, Ctrl-S save / ESC cancel).

## Monitoring

### Server Logs

Logs go to stdout (and can be redirected):
```bash
python3 run_server.py 2>&1 | tee bbs.log
```

Log levels:
- `INFO` — connections, disconnections, menu selections
- `DEBUG` — detailed protocol negotiation, byte counts
- `WARNING` — node exhaustion, failed auth
- `ERROR` — crashes, unhandled exceptions

### Active Sessions

Check connected users:
```bash
# Via SSH
ssh -p 6422 localhost <<< "3"

# Or check logs for "shell_loop: node N"
```

### Node Usage

The server tracks node usage:
```
Active nodes: 3/8
```

When all nodes are full, new connections get "All nodes busy" and are disconnected.

## Backups

### What to Back Up

- `users/` — all user accounts (one JSON file per user)
- `plugins/*/data/` — plugin runtime data (messages, uploads, chat logs)
- `keys/` — SSH host keys (regenerate if lost, but clients will need to re-accept)
- `config.yaml` — server configuration

### Backup Command

```bash
tar czf modulo-backup-$(date +%Y%m%d).tar.gz \
    users/ plugins/*/data/ keys/ config.yaml
```

## Troubleshooting

### "Connection refused" on port 6400/6422

Server isn't running. Start it:
```bash
python3 run_server.py --host 0.0.0.0 --port 6400 --ssh-port 6422
```

### SSH handshake fails (Syncterm error -20)

Algorithm mismatch. Ensure `config.yaml` has cryptlib-compatible algorithms. See `docs/architecture.md` for the correct `create_server()` call.

### Banner garbled in Syncterm

Use `--plain` flag or ensure terminal is set to CP437. The banner uses only safe ASCII characters (`#`, letters, spaces).

### Node exhaustion

Increase `--nodes` or disconnect idle users. Check logs for which nodes are occupied.

### Plugin not loading

Check:
1. Plugin directory exists under `plugins/` with an `__init__.py`
2. That file exports a `Plugin` subclass
3. No import errors in the startup log (broken plugins are skipped with a
   logged reason, not fatal)

## Security Notes

### SSH Transport

- Host key: RSA 2048-bit (PEM format)
- Algorithms: SHA-1 KEX, CBC ciphers (cryptlib compatible)
- Authentication: none (no-auth mode)

This is adequate for a local/enthusiast BBS. For public-facing deployments, consider:
- Adding password authentication
- Upgrading to AES-CTR + HMAC-SHA256
- Implementing fail2ban or rate limiting

### Telnet Transport

**No encryption.** Passwords sent in plaintext. Use only on trusted networks. SSH is preferred.

### File Permissions

```bash
chmod 700 users/ plugins/*/data/   # User + plugin data
chmod 600 keys/*         # SSH keys
chmod 644 config.yaml    # Config (readable, not writable by others)
```
