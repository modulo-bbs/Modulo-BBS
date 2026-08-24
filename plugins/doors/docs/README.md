# doors

Sysop-configurable external-program catalog (door games). Currently a
catalog + launcher-menu with stub handlers; actual process spawning is
future work.

## Screens

Menu renders inline from the catalog. Reserved screen names for future
overridable versions: `menu` (the door list).

## Data

- `data/doors.json` — the catalog:

```json
{
  "tradewars": {"name": "TradeWars 2002", "groups": ["verified"]},
  "lord":      {"name": "Legend of the Red Dragon", "groups": []}
}
```

Empty `groups` = everyone. Missing/disabled doors are hidden automatically.

## Keys

From the `keys` file — hotkey per door id (e.g. `T=tradewars,L=lord`),
plus `Q=quit`. Unknown keys log a warning and are ignored (fail-safe).

## Events

Emits `doors:launch` `{session, door_id}` on selection — the hook point
where real door-process launching will integrate.
