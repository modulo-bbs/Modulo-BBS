# Colour themes

Named palettes, stored on the caller's account. Generated chrome and
`{ACCENT}` token screens follow them after login.

A theme is a **text file** in `themes/`. You edit it yourself, or tell
an agent to — no Python, no restart. Missing keys fall back to classic
defaults. Extra keys are picked up as screen tokens.

## For callers

At the home `>` prompt (hotkey, not a shell):

1. Press `/` — that key starts a line read.
2. Type the rest (`theme`, `help`, `screen`, …) and Enter.
   You do **not** type a second slash. `/` then `help` is `/help`.
3. Bare `theme` opens an **up/down picker** in a bordered overlay.
   The PIM does not scroll. The list is every `*.theme` file in
   `themes/`.
4. Arrows (or `J`/`K`) move the highlight. The **box itself** paints in
   the highlighted palette so you can preview before saving.
5. **Enter** writes `preferences.theme` (the file's name, e.g. `amber`).
   **ESC** or `Q` cancels.
6. The saved name is marked `*` in the list.

Shortcut: `/` then `theme amber` sets that file without the picker.
Nicknames from `alias=` lines work too (`phosphor` → `matrix`).

`help`, `screen`, and unknown commands also land in that overlay. Any
key dismisses; then the tabs redraw.

Inside Social chat, `/` is a character in the draft. Slash commands are
the home `>` prompt only.

Pre-login (splash, login, register, TOTP) always paints **classic**.

## Writing a theme

Drop `themes/mylook.theme` (the name before `.theme` is what `/theme`
shows). Edit with any text editor. **Refresh the picker** — files are
re-read on use; no server restart.

```
# Comments start with #. Keys can appear in any order.
highlight=4,7
text=7,0
prompt=7,0
```

That is a complete theme. Everything you omit is filled from classic
(cyan titles, green ok, red errors, white-on-blue selection).

### Colour numbers (DOS / IBM)

`key=fg` or `key=fg,bg`. Foreground is 0–15; background is 0–7.

|  | dim | bright |
|---|---|---|
| black / gray | 0 | 8 |
| blue | 1 | 9 |
| green | 2 | 10 |
| cyan | 3 | 11 |
| red | 4 | 12 |
| magenta | 5 | 13 |
| brown / yellow | 6 | 14 |
| light gray / white | 7 | 15 |

Examples: `11` is bright cyan, `15,1` is white on blue, `0,6` is black
on brown (amber selection).

### Keys the board already uses

| Key | What it paints | Screen token |
|---|---|---|
| `accent` | headers, titles, your chat bubbles | `{ACCENT}` |
| `success` | ok, footer hints, other people's bubbles | `{SUCCESS}` |
| `warning` | `*NEW*`, caution | `{WARNING}` |
| `error` | failures | `{ERROR}` |
| `muted` | inactive tab labels, dim text | `{MUTED}` |
| `frame` | box drawing: tab bars, pane borders, rules | `{FRAME}` |
| `inactive` | unfocused region chrome (idle pane) | `{INACTIVE}` |
| `active` | focused region chrome (the pane that has the keys) | `{ACTIVE}` |
| `text` | list body, unselected rows | `{TEXT}` |
| `prompt` | the `>` prompt | `{PROMPT}` |
| `highlight` | **active tab and list selection** (`fg,bg`) | `{HIGHLIGHT}` `{TAB_FG}` `{TAB_BG}` |

Aliases: `title=` is `accent`, `hint=` is `success`.

List selection uses `highlight`, not reverse video (reverse is a silver
bar on SyncTERM).

### Extra keys

Any other `name=fg[,bg]` is kept. It becomes `{NAME}` in `.txt` screens
and lives on the palette for plugins that ask for it.

```
banner=14
```

in `themes/ice.theme` makes `{BANNER}` work in a screen file while that
theme is active.

### Meta lines

```
order=25          # picker sort (lower first). Shipped: 10, 20, 30…
alias=phosphor    # extra name for /theme; can repeat
alias=hacker
```

The canonical saved name is still the filename (`matrix.theme` →
`matrix`).

### Mixed vs CRT

- **Mixed** (classic, amber, green, magenta): several hues. Keep
  `error=12` (light red) so failures still read as failures.
- **CRT** (matrix, honey): every key is a shade of one phosphor.
  Green family: 2, 10, and `highlight=0,2`. Amber family: 6, 14, and
  `highlight=0,6`. Do not use 8 (gray) on a CRT — it breaks the mono.

### Shipped files

| File | Feel |
|---|---|
| `classic.theme` | cyan / green, white-on-blue tabs |
| `amber.theme` | yellow, black-on-brown tabs |
| `green.theme` | green mixed, black-on-green tabs |
| `magenta.theme` | magenta / cyan |
| `matrix.theme` | green phosphor (`alias=phosphor`, `hacker`) |
| `honey.theme` | amber phosphor (`alias=ambercrt`) |

Edit these, or copy one to `themes/sunset.theme` and change numbers.
Delete a file to remove it from `/theme` (keep `classic.theme` — it is
also the fallback).

## What does not retheme

- **`.ans` art** — baked colour bytes. Drop a different `.ans` if you
  want different art.
- **Literal colour tokens** — `{BRIGHT_CYAN}` is always cyan. Use
  `{ACCENT}` (or your extra `{BANNER}`) to follow the file.
- **SyncTERM's own status bar** — the client paints that.
- **Pre-login** — no account yet, so classic.

## Screens

```
{ACCENT}Welcome{RESET}  {MUTED}node {node}{RESET}
{PROMPT}> {RESET}
```

`ScreenService` injects the active file's tokens, then literal
`{BRIGHT_CYAN}`, then ad-hoc extras.

## Plugins

```python
from core.theme import palette_for

p = palette_for(session)
await self.bbs.send(session, f"{p.accent}Title{p.reset}\r\n")
await self.bbs.send(session, f"{p.prompt}  >{p.reset}")
# extra keys from the file:
banner = p.extras.get("banner", p.accent)
```

Selected rows: `p.tab_fg` + `p.tab_bg` (from `highlight=`). Box drawing
(`│` `─` `└┘`) uses `p.frame`. Split regions (Social panes, later
widgets) use `p.active` for the focused side and `p.inactive` for the
idle one. Always pair a colour with `p.reset`. Do not branch on the
theme *name* inside a plugin — ask for roles.

The loader lives in `core/theme.py`. Tests: `tests/test_theme.py`.
The directory is `themes/` at the board root (overridable in tests via
`set_themes_dir`).
