# Modulo BBS

A bulletin board you call with a terminal. SyncTERM, telnet, or SSH.

It is supposed to feel like a 90s board the first time you connect — not a
kit you assemble. Log in, pick a tab, talk. **Social** is live rooms and DMs;
other people on the board show up in the thread while you are sitting there.
Home also has a dashboard, bulletins, and a file listing.

Python 3.11+. From this directory:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python run_server.py --host 127.0.0.1 --port 6400 --ssh-port 6422
```

Connect with SyncTERM to `127.0.0.1` port `6400` (Telnet). Or:

- Telnet: `telnet 127.0.0.1 6400`
- SSH: `ssh -p 6422 127.0.0.1` — no SSH password; the board's own login is next

New account: `R` on the login screen. At the home `>` prompt, `/` then `ver`
shows the build; `/` then `theme` picks colours.

Want a different look? Drop a file in `themes/` or `screens/`. How to run it
and change it: **`docs/sysop-guide.md`**.
