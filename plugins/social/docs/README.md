# social

Boards and DMs as a two-pane Social surface. Listed in
`plugins/mainmenu/data/home` as `social`. Delete that line to drop the tab.

ENTER on a room focuses that thread — same two-pane screen, left column
stays. The middle divider points at the column you can move into:
`»` ENTER from rooms, `«` ESC from the thread (plain terminals `>` / `<`).
The focused pane's box — walls and floor, with `┴` at the gutter — uses
the theme's `active=` colour; the idle pane uses `inactive=`. ENTER/ESC
on the gutter is `text=` so it stays readable on amber `active=`.
Bubbles are **me vs everyone else**:
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
`N` new thread (title ≤15). `D` deletes the highlighted board after a
confirm: sysops can remove any thread; the author can remove theirs only
while nobody else has posted. The DMs row cannot be deleted.

SyncTERM: Enter is CR (trailing LF swallowed); Ctrl-Enter is LF.
