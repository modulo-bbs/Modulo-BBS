# dashboard

Home-tab digest. Listed in `plugins/mainmenu/data/home` as `dashboard`.
Delete that line to drop the tab.

Each loaded plugin may implement `home_digest(session)` returning
`(text, jump_id)` or a list of those. This plugin stacks the rows; Enter
jumps to that plugin's tab if it is on the strip, otherwise opens its
full-screen flow.
