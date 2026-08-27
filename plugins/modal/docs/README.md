# modal

Core-plugin that draws pickers and notices. Other plugins call
`await core.modal.choose(bbs, session, labels)` (ESC → `None`) and
`await core.modal.notice(bbs, session, body)` (any key dismisses).

Swap the folder with one line in `config.yaml`: `modal: awesomemodal`.
If this plugin is missing, core logs a warning and falls back to a numbered
list so the board still runs.

The notepad editor is not this plugin — that lives in Social.
