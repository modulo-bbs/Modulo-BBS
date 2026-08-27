# files

File-area catalog with group gates. **Catalog only — no byte transfer yet**
(no X/Y/Zmodem, no SCP). Listing works; moving files is future work.

Listed in `plugins/mainmenu/data/home` as `files`. Delete that line to drop
the Files tab; `[F]` still works in classic menu mode.

## Screens

Renders area lists inline. Reserved screen names for the overridable
future: `arealist` (areas), `filelist` (one area's contents).

## Data

- `data/areas.json` — file-area definitions: id, name, path, required
  groups. Empty groups = public.

## Access

Area visibility goes through `user.can_access()` against each area's
groups, same model as messageboard and doors.
