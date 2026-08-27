# social

Boards and DMs as a two-pane Social surface. Listed in
`plugins/mainmenu/data/home` as `social`. Delete that line to drop the tab.

ENTER on a room focuses that thread — same two-pane screen, left column
stays. The middle divider points at the column that currently has the
keys (`<` rooms / `>` thread; UTF-8 `←` `→`) with ESC stacked in the
gutter. Bubbles are **me vs everyone else**:
your messages sit **right / accent**, everyone else **left / success**.
Sidebar `*` is unread until you open the thread. `*NEW*` is other people's
mail that arrived since you last left — never on history you already opened,
and never on a thread you have not opened yet (the star already said look).
Tail-anchored. The highlight stays on the room you are looking at even
when another thread gets new mail and jumps to the top of the list.
The list preview and the focused thread both poll once a second so other
people's posts show up without switching tabs. Compose: one-line prompt; Enter with text opens
Post / Editor / Discard (the modal plugin). Empty Enter / wrap / LF opens
the overlay notepad; leaving it with a draft reopens that modal. ESC on
the picker keeps the draft; ESC on the prompt returns to browsing rooms.
`N` new thread (title ≤15).

SyncTERM: Enter is CR (trailing LF swallowed); Ctrl-Enter is LF.
