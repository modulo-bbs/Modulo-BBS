# login

Owns the entire authentication experience: the login screen, credential
collection, optional TOTP two-factor, and registration. On success it binds
`session.user`, sets `session.authenticated`, and emits `user:login`.

## Screens

Resolved from `plugins/login/screens/` (`.ans` > `.asc` > `.txt`).

| Name | Purpose |
|---|---|
| `login` | Username/password prompt (also offers `R` = register) |
| `register` | New-account form |
| `totp_setup` | Shows your TOTP secret + otpauth URI for enrolment |
| `totp_verify` | 6-digit code prompt during login |

All standard tokens work. `totp_setup` receives `{SECRET}` and
`{OTPAUTH}` ad-hoc values from the flow.

## Keys

| Context | Key | Action |
|---|---|---|
| login screen | `R` | register instead of logging in |
| any auth screen | `Q` | back out / disconnect |

## Data

- `data/totp_secrets.json` — TOTP secrets, plugin-owned. **Never** in core.
  Backup note: include `plugins/login/data/` in board backups.

## Events

- `user:login` `{session, user}`
- `user:logout` `{session, user}`
- `auth:login_failed` `{session, username, reason}`
- `auth:register` `{session, user}`

## Character-set detection

On successful login this plugin runs the codec selection chain (saved
preference → UTF-8 probe → terminal-type heuristic → CP437 default). It
never prompts; users change encoding via preferences.
