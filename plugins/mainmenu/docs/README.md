# mainmenu

The main menu: the hub users return to after logon. Renders every loaded
plugin that declares a `menu_key`, plus built-ins `[I] System Info` and
`[Q] Disconnect`.

## Screens

| Name | Default source | Overridable |
|---|---|---|
| `main` | generated from loaded plugins | yes — `screens/main.ans` / `.asc` / `.txt` |

## Keys

Handled by this plugin (not in a `keys` file — they're structural):

- `Q` / `EXIT`… — disconnect
- `I` — system info block
- `X` — graceful shutdown (**sysop only**; the option only renders for sysops)
- any plugin's `menu_key` (`M`, `F`, `D`, `B`, `C`, `S`, …)

## Configuration

Per-plugin menu entries come from each plugin's metadata:

| Attribute | Meaning |
|---|---|
| `menu_label` | Display text, e.g. `[M] Message Boards` |
| `menu_key` | Hotkey letter; empty = not on the menu |
| `menu_order` | Sort position (lower first) |
| `menu_requires` | Group gate — entry hidden unless caller can access |

## Tokens available in `main.*`

All standard tokens (`{username}`, `{time}`, `{node}`, `{active}` …) work.
See `docs/screens.md` for the full vocabulary.

A screen is display only — showing `[X] Shutdown` doesn't make it available;
the dispatcher still rejects unauthorized keys.

## Data

None. This plugin owns no data files.
