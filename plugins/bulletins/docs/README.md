# bulletins

Sysop-written notices shown to users at logon (unseen ones) and re-readable
from the menu. Group-gated; seen-tracking per user.

Listed in `plugins/mainmenu/data/home` as `bulletins`. Delete that line to
drop the Bulletins tab; the logon sequence step is separate
(`plugins/logon/data/sequence`).

## Screens

Renders bulletin bodies directly from their text files — the bulletins
*are* the screens. Write them as `.asc` (plain ASCII, ≤60 columns, CRLF)
or `.txt`; ANSI colour tokens (`{BRIGHT_CYAN}` etc.) substitute in either.

## Data

- `data/bulletins/NNN-name.txt` — numbered notice files (sort order = number)
- `data/bulletins/NNN-name.meta.json` — optional sidecar: `{"groups": [...]}`
  gates who sees it
- `data/seen.json` — per-user seen tracking

## Behaviour

- At logon: every unseen (and group-visible) bulletin is shown once.
- From the menu: full list, re-read anything; **reading here does not mark
  seen** — seen status only changes during the logon pass.
- Long bulletins page with `-- More [N=next, Q=stop]`.

## Keys

`N`=next · `P`=previous · `Q`=stop/quit (pager and list).
