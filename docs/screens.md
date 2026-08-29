# Screens — The Display Standard

One interpreter, every plugin. Every piece of visible output a plugin shows
(a menu, a header, a bulletin, a login prompt) is a **named screen**, and
every named screen can be overridden by the sysop without touching code.

## Where screens live

```
plugins/<plugin>/screens/
    <name>.ans    ← CP437 + ANSI colours (byte-faithful DOS art)
    <name>.asc    ← plain ASCII (no colour codes)
    <name>.txt    ← shipped default (UTF-8)

screens/          ← logon's board-global screens (splash, welcome) live here
```

**Resolution per name: `.ans` → `.asc` → `.txt`, first hit wins.**
The extension says only how the bytes decode. Everything goes through the
same pipeline afterwards: read raw bytes (CRLF preserved — never
`read_text()`), decode with the extension's codec, substitute tokens, send.

To reskin any screen: drop your file into that plugin's `screens/` folder
with the right name and refresh (or have an agent drop it). Delete it to
fall back to the default. No restarts, no code. An in-board screens editor
is a later **accessory**, not part of the screen service — files on disk
are the contract.

## Tokens

Tokens are written directly in screen files; the service swaps them at render
time. Same vocabulary works in every screen of every plugin.

### ANSI colour constants

`{RESET}` `{BOLD}` `{DIM}` `{UNDERLINE}` `{REVERSE}`
`{BLACK}…{WHITE}` `{BRIGHT_BLACK}…{BRIGHT_WHITE}`
`{BG_BLACK}…{BG_YELLOW}` `{CLEAR}` (clear screen) `{HOME}`

These names are **literal** SGR codes. `{BRIGHT_CYAN}` is always cyan, even
when the caller has picked another theme.

### Semantic theme roles

Named palettes live on `preferences.theme` and resolve through
`core.theme.palette_for(session)`. Each name is a file in `themes/`
(`amber.theme`, or one you drop). How to write one, DOS colour numbers,
and the `/theme` picker: **`docs/themes.md`**. Shipped files:

- mixed: `classic`, `amber`, `green`, `magenta` (errors stay red)
- CRT mono: `matrix` (green phosphor; aliases `phosphor`, `hacker`),
  `honey` (amber phosphor; alias `ambercrt`) — every role is a shade of
  one colour, including errors

Anonymous (pre-login) sessions get **classic**. Shipped `.txt` screens use
these roles so they recolor after login:

| Token | Role |
|---|---|
| `{ACCENT}` | headers, titles, me-bubbles |
| `{SUCCESS}` | ok / hints / other-bubbles |
| `{WARNING}` | NEW / caution |
| `{ERROR}` | failures |
| `{MUTED}` | inactive tab labels, dim text |
| `{FRAME}` | box drawing: tab bars, pane borders, rules |
| `{TEXT}` | emphasized body |
| `{PROMPT}` | the `>` prompt |
| `{TAB_FG}` `{TAB_BG}` `{HIGHLIGHT}` | active PIM tab / list selection |

`.ans` art with baked colour bytes is **not** rethemed — drop a new `.ans` if
you want different art. Generated UI and token `.txt` / `.asc` screens follow
the palette.

### Runtime values

| Token | Meaning |
|---|---|
| `{bbsname}` | Board name ("Modulo BBS") |
| `{version}` | Board version (`core/version.py`) |
| `{time}` | Current time, `HH:MM` |
| `{date}` | Current date, `MM/DD/YY` |
| `{datetime}` | Both together |
| `{username}` / `{displayname}` | Caller's account / shown name (`-` if none) |
| `{node}` | Caller's node number |
| `{active}` / `{maxnodes}` | Users online now / capacity |
| `{termwidth}` / `{termheight}` | Terminal size |
| `{sessiontime}` | Minutes:seconds since connect |

Plugins may register **namespaced tokens** (e.g. `{boards.count}`,
`{doors.online}`) usable from any screen — ask the plugin author what's
available, or grep `register_provider` in the plugin source.

### Ad-hoc values

Flows can pass extra tokens for a single render (e.g. the TOTP secret during
enrolment). Those are documented by the flow that uses them.

## Files vs generators

Screens whose content is computed (the main menu lists loaded plugins) ship
as **generators**: Python fallbacks registered per `(plugin, screen)` pair.
A sysop file always beats the generator.

A screen is **presentation only** — it never grants or gates anything. Show
whatever commands you like; the dispatcher decides what each caller may
actually do, and unauthorized keys are rejected no matter what the screen
displayed.

## /screen — machine view, a saved preference

The tabbed home `>` is a **hotkey** prompt (one key at a time), not a Unix
shell. Press `/` to start a command, type the rest, then Enter:

- `/` + `theme` + Enter — up/down picker (Enter applies, ESC cancels;
  the box previews the highlighted palette)
- `/` + `theme amber` + Enter — save that palette without the picker
- `/` + `screen` + Enter — toggle machine view
- `/` + `ver` + Enter — board version (and git short hash)
- `/` + `help` + Enter — list commands (`help` with no extra slash is
  the same: `/` already started the line)

The result appears in a bordered overlay (same geometry as the Social
notepad) until a keypress; then the tabs redraw. Inside Social chat, `/`
is just a character in the draft — slash commands are the home `>` prompt
only. Caller UX and how to ship a new palette: **`docs/themes.md`**.

Because a reskin can hide the real commands, every line-mode input loop
supports the **`/screen`** command (recommended plugin convention):

- `/screen` — **toggle** machine view. When ON, *all* screens render as
  generated defaults, skipping skins entirely. Saved in your preferences;
  persists across logins.
- `/screen <plugin> [name]` — one-shot peek at a generated screen without
  toggling.
- `/help` — lists registered slash commands; plugins add their own via
  `core.slash.register`.
- `/ver` (`/version`) — print the board version from `core/version.py`
  (`0.2.0`) plus a git short hash when `.git` is present, so you can
  tell which process is running.
- `/theme` — at the home `>` prompt, an up/down overlay picker (Enter
  applies, ESC cancels; the box previews the highlight). `/theme amber`
  (etc.) still sets it directly. Saved as `preferences.theme`. Full
  how-to: **`docs/themes.md`**. `.ans` art is not recolored.

This is a **general preference, open to every user** — some folks simply
like plain output. Generated menus are rendered from the caller's own
permissions, so each person sees exactly what they can use and nothing
else; there is nothing gated about the toggle itself.

Single-key menus accept a leading `/` too: pressing `/` switches that keypress
into a line read for the command.

## For developers

```python
# render + transmit:
await bbs.screens.send(session, "messageboard", "boardlist")

# render only:
text = bbs.screens.render(session, "mainmenu", "main", extra="value")

# register a generated default (on_load):
bbs.screens.register_generator("mainmenu", "main", self._generate_main)

# contribute tokens:
bbs.screens.register_provider(lambda ctx: {"boards.count": 7})
```

Missing screen + no generator renders a visible
`[missing screen: plugin/name]` placeholder — broken reskins are obvious,
never silent.

## Inventory

Each plugin documents its own screens and tokens in `plugins/<name>/docs/README.md`.

| Plugin | Screens |
|---|---|
| mainmenu | `main` (classic, generated) + `pim` (tabbed PIM chrome, generated — file `pim.ans/.asc/.txt` beats it) |
| logon | `splash`, `welcome` (+ anything referenced in `plugins/logon/data/sequence`) |
| login | `login`, `register`, `totp_setup`, `totp_verify` |
