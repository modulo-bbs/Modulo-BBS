# mainmenu

The hub after logon: **tab bar + `>` prompt**. Panes belong to the plugins
listed in this plugin's home file. Classic list (`home_mode=menu`) still
renders every loaded plugin that declares a `menu_key`, plus `[I]` / `[Q]`.

## Home file

`plugins/mainmenu/data/home` — one plugin name per line, `#` comments, order
is tab order (keys 1–5, cap 5). Missing file → `dashboard`, `social`,
`files`, `bulletins`. A name whose plugin is not loaded is skipped.

```
dashboard
social
files
bulletins
```

Delete a line to drop that tab. Add a third-party plugin the same way (it
must set `home_label` and implement `render_home_pane` / `handle_home_key`).

## Screens

| Name | Default source | Overridable |
|---|---|---|
| `main` | generated from loaded plugins (classic) | yes — `screens/main.ans` / `.asc` / `.txt` |
| `pim` | generated tabbed chrome (tabs + pane) | yes — `screens/pim.ans` / `.asc` / `.txt` (file beats generator) |

## Preferences

| Key | Values | Default | Effect |
|---|---|---|---|
| `home_mode` | `pim` / `menu` | `pim` | `pim` = tabbed home; `menu` = classic list |
| `screen_mode` | `generated` / (unset) | unset | `generated` = skip skins, show generated defaults |
| `theme` | stem of a `themes/*.theme` file | `classic` | named colour palette (`docs/themes.md`) |

At the `>` prompt press `/`, type a command, Enter. Bare `theme` opens the
modal picker. Other commands paint in a notice overlay; any key dismisses.

## Keys (PIM)

- `1`/`2`/`3`/`4`/`5` and `LEFT`/`RIGHT` / `H`/`L` — switch tab
- Remaining keys go to the active home plugin (Social thread, Files list, …)
- Idle 1s redraw (new Social mail appears without leaving the tab)
- `/` — slash command
- `Q` — disconnect; `I` — system info; `X` — shutdown (sysop, classic or fall-through)

## Configuration (classic mode)

Per-plugin menu entries come from each plugin's metadata:

| Attribute | Meaning |
|---|---|
| `menu_label` | Display text, e.g. `[F] Files` |
| `menu_key` | Hotkey letter; empty = not on the classic menu |
| `menu_order` | Sort position (lower first) |
| `menu_requires` | Group gate |

## Data

- `plugins/mainmenu/data/home` — home strip (shipped default; edit to taste)
