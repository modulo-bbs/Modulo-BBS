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

from core.screens import ANSI_TOKENS

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

# ANSI token substitution lives in core/screens.py (ANSI_TOKENS) now; the
# table below is retained only for the custom-dir fallback path.
_ANSI_TOKENS = ANSI_TOKENS


class ScreenLoader:
    """Load login screens through the core screen service.

    Resolution (.ans > .asc > .txt), byte-faithful CRLF handling, and token
    substitution ({ANSI_NAME} constants + runtime {KEY} placeholders) are
    the screen service's job now (see core/screens.py). This wrapper keeps
    the ``render(name, **kwargs)`` call-shape the flows use.
    """

    def __init__(self, bbs=None, screens_dir: Path | None = None):
        self.bbs = bbs
        self.screens_dir = Path(screens_dir) if screens_dir else SCREENS_DIR

    def render(self, name: str, session=None, **kwargs: object) -> str:
        svc = getattr(self.bbs, "screens", None) if self.bbs else None
        if svc is not None and self.screens_dir == SCREENS_DIR:
            stem = name.rsplit(".", 1)[0]
            return svc.render(session, "login", stem, **kwargs)
        # Fallback for custom dirs (tests): direct read + ANSI substitution.
        from core.banner import substitute_tokens

        raw = (self.screens_dir / name).read_bytes()
        return substitute_tokens(
            raw.decode("utf-8", errors="replace"), session=session, **kwargs
        )

    def load(self, name: str) -> str:
        return self.render(name)


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
        from shared.codecs import encode_out

        writer.write(
            encode_out(text, getattr(self.session, "codec", "cp437"))
        )
        await writer.drain()

    async def read_line(self, prompt: str = "", *, secret: bool = False) -> str:
        """Send an optional prompt, then read one line (CRLF-terminated).

        Uses the core negotiator-aware reader so telnet control bytes are
        handled and server-side echo fires; SSH sessions (which echo at
        the transport layer) pass through with echo suppressed there.
        ``secret=True`` echoes asterisks and applies backspace to the
        hidden buffer. Telnet already ``WILL ECHO`` from connect so the
        client is not local-echoing the real password.
        """
        from core import runner

        echo = "*" if secret else None
        async with runner.secret_echo(self.bbs, self.session, echo):
            if prompt:
                await self.send(prompt)
            text = await runner.read_command(self.bbs, self.session)
        if text is None:
            return ""
        return text.strip("\r\n")

    async def pause(self, msg: str = "[Press any key]") -> None:
        """Show a message and wait for one keypress.

        Error paths need this: the flow loops back to a full-screen
        template, which would otherwise scroll the error off a 24-row
        terminal before anyone can read it (Dave hit this 2026-08-25 —
        "registration silently bounces back to Username").
        """
        from core.theme import palette_for

        pal = palette_for(self.session)
        await self.send(f"{pal.muted}{msg}{pal.reset}\r\n")
        from core import runner

        await runner.read_key(self.bbs, self.session)


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
            await tty.send(self.screens.render("login.txt", session))
            answer = (await tty.read_line("Login: ")).strip()
            if not answer:
                return False                       # EOF / disconnect
            choice = answer.upper()
            if choice == "Q":
                return False                       # hang up (logon sequencer disconnects)
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
        from core.theme import palette_for

        p = palette_for(session)
        password = await tty.read_line(
            f"{p.accent}Password: {p.reset}", secret=True
        )

        user = await self.bbs.users.get(username)
        if user is None or not user.verify_password(password):
            self.bbs.events.emit("auth:login_failed", {
                "session": session, "username": username,
                "reason": "Invalid username or password",
            })
            await tty.send(
                f"{p.error}Invalid username or password.{p.reset}\r\n"
            )
            await tty.pause()
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
                    f"{p.error}Two-factor authentication failed.{p.reset}\r\n"
                )
                await tty.pause()
                return False

        # Success: bind the user to the session and record the login.
        session.user = user
        session.username = user.username
        session.authenticated = True
        if SessionState is not None:
            session.state = SessionState.MAIN_MENU

        # --- character codec selection -----------------------------------
        # Order (per 2026-08-23 research; see shared/codecs.py notes):
        #   1. saved user preference
        #   2. active UTF-8 probe (DSR cursor trick — the only true detector,
        #      works over SSH where TTYPE doesn't exist)
        #   3. TERMINAL-TYPE name heuristics (ANSI-BBS -> cp437 etc.)
        #   4. default cp437 (never block login on a question; users change
        #      encoding via preferences/web console)
        from shared.codecs import (
            DEFAULT_CODEC, detect_codec, normalize, probe_ambiguous_width, probe_utf8,
        )

        saved = (user.preferences or {}).get("encoding")
        if saved:
            session.codec = normalize(saved)
        else:
            probed = await probe_utf8(self.bbs, session)
            if probed:
                session.codec = probed
            else:
                heuristic = detect_codec(getattr(session, "terminal_type", None))
                session.codec = heuristic or DEFAULT_CODEC

        session.wide_ambiguous = False
        if session.codec == "utf-8":
            # Only a terminal that *answers* col 3 gets the two-cell layout.
            # Assuming wide on silence clipped rows on ordinary terminals,
            # where glibc wcwidth reports one cell for box drawing.
            probed_wide = await probe_ambiguous_width(self.bbs, session)
            session.wide_ambiguous = bool(probed_wide)
            logger.info(
                "session codec=%s wide_ambiguous=%s (probe=%s) ttype=%s %sx%s",
                session.codec,
                session.wide_ambiguous,
                probed_wide,
                getattr(session, "terminal_type", ""),
                getattr(session, "terminal_width", 80),
                getattr(session, "terminal_height", 24),
            )

        try:
            await self.bbs.users.update(
                user.username, last_login=datetime.now().astimezone()
            )
        except Exception:  # noqa: BLE001 -- a stats failure must not block login
            logger.warning("Could not update last_login for %s", user.username)

        self.bbs.events.emit("user:login", {"session": session, "user": user})
        p = palette_for(session)  # themed from the bound account
        await tty.send(
            f"{p.success}Welcome back, "
            f"{user.shown_name()}!{p.reset}\r\n"
        )
        return True