# mainmenu

The main menu: the hub users return to after logon. Renders every loaded
plugin that declares a `menu_key`, plus built-ins `[I] System Info` and
`[Q] Disconnect`.

In PIM mode (default since 2026-08-24) this becomes the tabbed home:
top tabs (branches of one surface), middle pane (filtered conversations),
bottom `>` prompt. Classic list is still available via `home_mode=menu`.

## Screens

| Name | Default source | Overridable |
|---|---|---|
| `main` | generated from loaded plugins (classic) | yes — `screens/main.ans` / `.asc` / `.txt` |
| `pim` | generated tabbed chrome (tabs + pane) | yes — `screens/pim.ans` / `.asc` / `.txt` (file beats generator) |

## Preferences

| Key | Values | Default | Effect |
|---|---|---|---|
| `home_mode` | `pim` / `menu` | `pim` | `pim` = tabbed home; `menu` = classic list |
| `screen_mode` | `generated` / (unset) | unset | `generated` = skip skins, show generated defaults (see `docs/screens.md` `/screen` toggle) |

Toggle at runtime: `/screen` flips `screen_mode`; classic vs PIM is stored in
`home_mode` (set via `users.update` or a future `/home` command — currently
manual via prefs; the menu respects it immediately).

## Tabs (boards-unification B5/B7)

Default tabs (**Dashboard | Social | Files | Bulletins**), declared in
`plugins/mainmenu/tabs.py` as `DEFAULT_TABS`. Each tab:

```python
{"id": "social", "label": "Social", "kind": "board", "key": "2", "requires": []}
```

- `kind` filters `conversations.list` (`board|channel|dm|group|all`) — Social is
  the composite tab (board rooms + DMs pinned on top)
- `requires` is a group gate via `user.can_access()` — hidden unless caller qualifies
- `key` is the digit that activates the tab — capped at 5 tabs for 80-col fit

Sysop override: `plugins/mainmenu/data/tabs.json` (JSON list of tab objects) replaces
the default set entirely when present. Plugin-contributed tabs: set `pim_tab = {...}`
on any `Plugin` class; collected at `on_load` and appended (no duplicates).

Visible-tab check: `visible_tabs(load_tabs(bbs), user)` respects the gate;
anonymous sees only `requires=[]` tabs.

Keys inside the PIM (numbers reserved for tabs — selection uses up/dn+Enter):
- `1`/`2`/`3`/`4` (and `LEFT`/`RIGHT` / `H`/`L`) → switch tab, reset highlight to 0
- `UP`/`K` , `DOWN`/`J` → move highlight inside pane
- `ENTER` → Social: enter the highlighted room's chat (B8); other tabs: open
  the full-screen reader
- `/` → slash commands (`/screen`, `/help`) — same as classic

## Social chat (B8, Dave's Telegram-style surface)

ENTER on a Social room opens a full-screen chat: bubble history (own messages
right-aligned cyan, others green, `*NEW*` badge on arrivals since you entered),
tail-anchored entry, 1s polling so other nodes appear live. Keys:

| Key | Action |
|---|---|
| printable / `SPACE` | type into the draft (echo painted by the box) |
| `ENTER` | post the draft |
| `Ctrl-Enter` (LF) | insert a newline — the input box word-wraps and grows upward over bubbles (capped; tall drafts collapse to `[N lines - Ctrl-E to view]` + last 2 rows) |
| `Ctrl-E` | overlay notepad editor (see below) |
| `UP`/`DOWN` | scroll history (tail-anchored) |
| `PgUp`/`PgDn`/`SPACE`* | page history (*Space aliases PgDn only when the draft is empty) |
| `ESC` | back to the Social pane (marks the room read) |

SyncTERM key facts (hex-captured 2026-08-25): plain Enter sends CR (CRLF on the
wire — the trailing LF is swallowed), Shift-Enter is byte-identical to Enter,
**Ctrl-Enter sends LF**, Ctrl-E sends 0x05.

### Overlay notepad editor (`Ctrl-E`)

A bright-green bordered box overlays the live chat; the draft renders inside it
from the top with soft wrap. Notepad-style caret editing: arrows move the caret
anywhere in the box, `Enter` opens a line, `Backspace` joins, typing inserts at
the caret. Capacity is hard-capped to the box (an edit that would not fit is
refused — it never scrolls). `ESC` or `Ctrl-Enter` **sends** (ESC = save, per
Dave); `Ctrl-E` carries the draft back to the chat input box.

Reader (non-Social tabs): paged, threaded (`parent_id` indented), `F`ind,
`R`eply (classic `/S` save `/A` abort line editor), `D`elete (own or mod),
`N`/`P` paging, `Q` back to tabs. `R`/`D` are retired on Social — messaging
lives in the chat.

## Keys

Handled by this plugin (not in a `keys` file — they're structural):
- `Q` / `EXIT`… — disconnect
- `I` — system info block
- `X` — graceful shutdown (**sysop only**; the option only renders for sysops)
- any plugin's `menu_key` (`M`, `F`, `D`, `B`, `C`, `S`, …) — only in classic `menu` mode; PIM replaces them with tabs
- PIM navigation keys above — only in `pim` mode

## Configuration

Per-plugin menu entries come from each plugin's metadata (classic mode only):

| Attribute | Meaning |
|---|---|
| `menu_label` | Display text, e.g. `[M] Message Boards` |
| `menu_key` | Hotkey letter; empty = not on the menu |
| `menu_order` | Sort position (lower first) |
| `menu_requires` | Group gate — entry hidden unless caller can access |

## Tokens available in `main.*` / `pim.*`

All standard tokens (`{username}`, `{time}`, `{node}`, `{active}` …) work plus
`{unread}` / `{mentions}` backed by `bbs.conversations` (when available).
See `docs/screens.md` for the full vocabulary.

A screen is display only — showing `[X] Shutdown` doesn't make it available;
the dispatcher still rejects unauthorized keys.

## Data

- `plugins/mainmenu/data/tabs.json` — optional sysop tab override (not versioned)
- No other data files; conversations live under `plugins/conversations/data/`.
