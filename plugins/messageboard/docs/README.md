# messageboard

Multi-board messaging with group-gated areas, threaded replies, quoting,
and author-or-moderator deletion.

## Screens

Currently this plugin renders its board list and reader inline in code
(no screen files yet). To make a screen overridable, register it:
`bbs.screens.register_generator("messageboard", "boardlist", self._render_boards)`
— after which sysops can override `boardlist` via `screens/boardlist.ans|.asc|.txt`.

Planned screen names (reserved — don't conflict):

- `boardlist` — numbered list of accessible boards
- `readlist` — message index for one board
- `reader` — one message body

## Keys

From the `keys` file (comma-delimited, uppercase; omitted = disabled):

```
L=list,P=post,R=reply,D=delete,Q=quit
```

## Data

- `data/boards.json` — board definitions: id, name, required groups
- `data/<board_id>/` — messages as JSON files (id, author, subject, body,
  timestamp, replies)

## Access

Each board lists required groups; visibility and posting go through
`user.can_access()`. Mods may delete any message; authors may delete their
own.

## Events

- `messageboard:post` `{session, board_id, message}`
- `messageboard:reply` `{session, board_id, message}`
- `messageboard:delete` `{session, board_id, message}`
