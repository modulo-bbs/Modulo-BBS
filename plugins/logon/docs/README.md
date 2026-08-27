# logon

Runs the logon sequence, one step at a time, from **`plugins/logon/data/sequence`**
(owned by this plugin, not `config.yaml`):

```
screen:splash.txt
plugin:login
screen:welcome.txt
plugin:bulletins
plugin:mainmenu
```

`plugin:<role>` follows the core role map in `config.yaml` (`mainmenu: mainmenu`).
Unknown steps are logged and skipped; the sequence never crashes the board.
Missing file → factory default (same four steps plus bulletins).

## Screens

Screens resolve from the **project-root `screens/` directory** (board-global,
not plugin-local). Extension priority `.ans` > `.asc` > `.txt`; reference
steps by any extension and the best existing variant wins.

| Name | Purpose |
|---|---|
| `splash` | MODULO banner / first thing callers see |
| `welcome` | Post-login greeting |

Add your own steps: drop `mymessage.asc` into `screens/` and add
`screen:mymessage.asc` to the sequence wherever you want it.

## Tokens

All standard tokens work in these screens — `{bbsname}`, `{version}`,
`{node}`, `{termwidth}`, `{termheight}`, `{active}`, `{maxnodes}`,
`{python}`-era extras are also accepted in legacy form (`NODE`, `TTERM`,
`TW`, `TH`, `VERSION`, `ACTIVE`, `MAXNODES`, `NAME`). New screens should use
the lowercase vocabulary from `docs/screens.md`.

## Events

Emits `logon:step` with `{session, step, result}` for every step — useful
for debugging a broken sequence from the server log.
