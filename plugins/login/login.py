"""
Login screen logic for the Modulo BBS login plugin.

This module owns the interactive *login* experience: showing the login
screen, prompting for a username and (hidden) password, verifying the
credentials against the core ``User`` model via ``User.verify_password()``,
enforcing optional two-factor authentication, and finally binding the user
to the active session.

The core owns the User model and storage; this plugin owns the auth *flow*.
On success the plugin sets ``session.user`` and ``session.authenticated`` and
emits ``user:login``. Failures emit ``auth:login_failed``.

The ``Terminal`` class is a thin async I/O wrapper around a BBS session so
every flow in this plugin can send text (via ``bbs.send`` or ``session.writer``)
and read a CRLF-terminated line from ``session.reader``. The ``ScreenLoader``
loads ``screens/*.txt`` templates and substitutes ANSI escape codes (from
``shared.telnet_protocol.ANSI``) and runtime placeholders like ``{SECRET}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from shared.telnet_protocol import ANSI

logger = logging.getLogger("modulo.plugins.login")

# --- Package-relative paths -------------------------------------------------

_PLUGIN_DIR = Path(__file__).resolve().parent   # plugins/login/
SCREENS_DIR = _PLUGIN_DIR / "screens"           # display templates
DATA_DIR = _PLUGIN_DIR / "data"                 # runtime data

# Session state reuse (guarded so this module imports standalone too).
try:  # pragma: no cover - guard for environments without server.session
    from server.session import SessionState
except Exception:  # noqa: BLE001
    SessionState = None


# --- ANSI placeholder tokens -------------------------------------------------

# Map a {TOKEN} written in a screen template to an ANSI code from
# shared.telnet_protocol.ANSI. Keeps screen files readable while still using
# the canonical ANSI constants.
_ANSI_NAMES = [
    "RESET", "BOLD", "DIM", "UNDERLINE", "BLINK", "REVERSE",
    "BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE",
    "BRIGHT_BLACK", "BRIGHT_BLUE", "BRIGHT_CYAN", "BRIGHT_GREEN",
    "BRIGHT_MAGENTA", "BRIGHT_RED", "BRIGHT_WHITE", "BRIGHT_YELLOW",
    "BG_BLACK", "BG_BLUE", "BG_CYAN", "BG_GREEN", "BG_MAGENTA",
    "BG_RED", "BG_WHITE", "BG_YELLOW",
    "CLEAR_SCREEN", "CLEAR_LINE",
]
_ANSI_TOKENS: dict[str, str] = {
    name: getattr(ANSI, name) for name in _ANSI_NAMES
}
_ANSI_TOKENS["CLEAR"] = ANSI.CLEAR_SCREEN          # screen-clear shortcut
_ANSI_TOKENS["HOME"] = "\x1b[H"                     # cursor home (1;1)


class ScreenLoader:
    """Load ``screens/<name>.txt`` templates and substitute ANSI constants
    and runtime placeholder values.

    Screens are sysop-editable text files. ``{ANSI_NAME}`` tokens are replaced
    with the matching escape code; any extra keyword placeholder (e.g.
    ``{SECRET}``) passed to :meth:`render` is replaced with its runtime value.
    """

    def __init__(self, bbs=None, screens_dir: Path | None = None):
        self.bbs = bbs
        self.screens_dir = Path(screens_dir) if screens_dir else SCREENS_DIR
        self._cache: dict[str, str] = {}

    def load(self, name: str) -> str:
        """Return the ANSI-substituted template for ``name`` (cached).

        Reads bytes and decodes manually so ``\\r\\n`` line endings survive:
        ``Path.read_text()`` applies universal-newline translation which
        collapses CRLF to bare LF, causing staircase wrapping in SyncTERM
        (bare LF moves down a row without returning to column 0).
        """
        if name not in self._cache:
            text = (self.screens_dir / name).read_bytes().decode("utf-8")
            for token, code in _ANSI_TOKENS.items():
                text = text.replace("{" + token + "}", code)
            self._cache[name] = text
        return self._cache[name]

    def render(self, name: str, **kwargs: object) -> str:
        """Load the template and substitute runtime ``{KEY}`` placeholders."""
        text = self.load(name)
        for key, value in kwargs.items():
            text = text.replace("{" + key + "}", str(value))
        return text


# --- Session I/O ---------------------------------------------------------------

class Terminal:
    """Thin async I/O wrapper for interacting with a BBS session.

    Sending prefers ``bbs.send(session, text)`` (matching the documented core
    interface) and falls back to writing directly to ``session.writer``.
    Reading pulls one CRLF-terminated line from ``session.reader``.
    """

    def __init__(self, bbs, session):
        self.bbs = bbs
        self.session = session

    async def send(self, text: str) -> None:
        """Send ``text`` to the session. No-op if there is no transport."""
        if not text:
            return
        if self.bbs is not None and hasattr(self.bbs, "send"):
            result = self.bbs.send(self.session, text)
            if asyncio.iscoroutine(result):
                await result
            return
        writer = getattr(self.session, "writer", None)
        if writer is None:
            return
        writer.write(text.encode("latin-1", errors="replace"))
        await writer.drain()

    async def read_line(self, prompt: str = "") -> str:
        """Send an optional prompt, then read one line (CRLF-terminated).

        Uses the core negotiator-aware reader so telnet control bytes are
        handled and server-side echo fires; SSH sessions (which echo at
        the transport layer) pass through with echo suppressed there.
        """
        if prompt:
            await self.send(prompt)
        from core import runner

        text = await runner.read_command(self.bbs, self.session)
        if text is None:
            return ""
        return text.strip("\r\n")


# --- Login flow ---------------------------------------------------------------

class LoginFlow:
    """Interactive login flow: screen -> username -> password -> verify."""

    def __init__(self, bbs, totp_manager, screens: ScreenLoader | None = None):
        self.bbs = bbs
        self.totp = totp_manager          # TOTPManager (optional TOTP check)
        self.screens = screens or ScreenLoader(bbs)

    async def run(self, session) -> bool:
        """Run the login flow. Returns True once authenticated.

        Returns False when the user quits ("Q") or the connection ends, which
        signals the caller to return to the menu / stop.
        """
        tty = Terminal(self.bbs, session)
        while getattr(session, "is_active", True):
            await tty.send(self.screens.render("login.txt"))
            answer = (await tty.read_line("Login: ")).strip()
            if not answer:
                return False                       # EOF / disconnect
            choice = answer.upper()
            if choice == "Q":
                return False                       # return to main menu
            if choice == "R":
                from .registration import RegistrationFlow
                await RegistrationFlow(self.bbs, self.totp, self.screens).run(session)
                if getattr(session, "authenticated", False):
                    return True                    # newly registered + auto-login
                continue
            if choice in ("L", "LOGIN"):           # optional explicit "login"
                answer = choice
                continue
            ok = await self._authenticate(session, tty, answer.lower())
            if ok:
                return True
            # failed -- loop back to the login screen
        return False

    async def _authenticate(self, session, tty: Terminal, username: str) -> bool:
        """Prompt for the password and validate credentials + optional TOTP."""
        password = await tty.read_line(f"{ANSI.CYAN}Password: {ANSI.RESET}")

        user = await self.bbs.users.get(username)
        if user is None or not user.verify_password(password):
            self.bbs.events.emit("auth:login_failed", {
                "session": session, "username": username,
                "reason": "Invalid username or password",
            })
            await tty.send(
                f"{ANSI.BRIGHT_RED}Invalid username or password.{ANSI.RESET}\r\n"
            )
            return False

        # Optional two-factor authentication (enforced only if enrolled).
        if self.totp.has_secret(user.username):
            from .totp import TOTPFlow
            valid = await TOTPFlow(self.bbs, self.totp, self.screens).verify(session, user)
            if not valid:
                self.bbs.events.emit("auth:login_failed", {
                    "session": session, "username": username,
                    "reason": "Invalid TOTP code",
                })
                await tty.send(
                    f"{ANSI.BRIGHT_RED}Two-factor authentication failed.{ANSI.RESET}\r\n"
                )
                return False

        # Success: bind the user to the session and record the login.
        session.user = user
        session.username = user.username
        session.authenticated = True
        if SessionState is not None:
            session.state = SessionState.MAIN_MENU

        try:
            await self.bbs.users.update(
                user.username, last_login=datetime.now().astimezone()
            )
        except Exception:  # noqa: BLE001 -- a stats failure must not block login
            logger.warning("Could not update last_login for %s", user.username)

        self.bbs.events.emit("user:login", {"session": session, "user": user})
        await tty.send(
            f"{ANSI.BRIGHT_GREEN}Welcome back, "
            f"{user.shown_name()}!{ANSI.RESET}\r\n"
        )
        return True