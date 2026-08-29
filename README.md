# Modulo BBS

We're trying to make a BBS.

You still call it with a terminal — SyncTERM, telnet, SSH — and it should
feel like one of those boards that just worked when you dialed. We are not
trying to recreate every door and every sysop knob from 1994. We are
trying to keep that feeling, and then steal a few things from how people
talk on the internet now.

The big one is Social. Classic boards were a list of messages you read
later. Most of us live in threads now: a room, a back-and-forth, DMs in
the same place, and you see the other person while you are still there.
That is the shape we are aiming at. We are borrowing it, not inventing it.

The other bet is that almost everything you see is a plugin. Login, the
menu, Social, files, bulletins — each one is a folder. The core is supposed
to stay small: sessions, users, the event bus, getting bytes to the
terminal. If you want a feature, you write a plugin (or you ask an agent
to, with this tree and `docs/plugin-dev.md` in front of it) and drop the
directory in. No enable list. Restart, and it is there. We want that to be
easy enough that a human can do it on a weekend, and an agent can do it
without inventing a second way to configure the board.

It is early, and we know it. First connect should still feel like a working
board, not a project you assemble. If it doesn't, that is on us.

Python 3.11+. From this directory:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python run_server.py --host 127.0.0.1 --port 6400 --ssh-port 6422
```

SyncTERM: Telnet `127.0.0.1` port `6400`. Or `telnet 127.0.0.1 6400`, or
`ssh -p 6422 127.0.0.1` (no SSH password; the board asks you to log in).

`R` on the login screen makes an account. At the `>` prompt, `/` then
`ver` is the build; `/` then `theme` picks colours.

Running the board, screens, themes: **`docs/sysop-guide.md`**.
Writing a plugin: **`docs/plugin-dev.md`**.
