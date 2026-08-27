# social

Boards and DMs as a two-pane Social surface. Listed in
`plugins/mainmenu/data/home` as `social`. Delete that line to drop the tab.

ENTER on a room opens a full-screen chat. Bubbles are **me vs everyone else**:
your messages sit **right / accent**, everyone else **left / success**.
`*NEW*` marks other people's messages that arrived after you entered.
Tail-anchored; 1s polling. Compose: one-line prompt; Enter with text opens
Post / Editor / Discard (the modal plugin). Empty Enter / wrap / LF opens
the overlay notepad; leaving it with a draft reopens that modal. ESC on
the picker keeps the draft; ESC on the prompt leaves chat. `N` new thread
(title ≤15).

SyncTERM: Enter is CR (trailing LF swallowed); Ctrl-Enter is LF.
