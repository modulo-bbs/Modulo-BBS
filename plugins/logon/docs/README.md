# logon

Runs the sysop-configurable logon sequence, one step at a time, from
`config.yaml`:

```yaml
logon_sequence:
  - screen:splash.txt
  - plugin:login
  - screen:welcome.txt
  - plugin:mainmenu
```

Step types: `screen:<file>` (display a screen) and `plugin:<name>` (run that
plugin's session flow). Unknown steps are logged and skipped; the sequence
never crashes the board.

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
