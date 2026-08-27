# Modulo BBS

A bulletin board. Telnet and SSH. Files on disk are how you change it.

Python 3.11+. From this directory:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python run_server.py --host 127.0.0.1 --port 6400 --ssh-port 6422
```

Connect:

- Telnet: `telnet 127.0.0.1 6400`
- SSH: `ssh -p 6422 127.0.0.1` (no password on the SSH layer)
- SyncTERM: Telnet `127.0.0.1` port `6400`

Register from the login screen (`R`). At the home `>` prompt, `/` then `ver` shows the build; `/` then `theme` picks colours.

Want it to look different? Drop a file in `themes/` or `screens/` (or `plugins/<name>/screens/`). No Python. More: **`docs/sysop-guide.md`**.
