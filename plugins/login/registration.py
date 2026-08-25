"""
New-user registration flow for the Modulo BBS login plugin.

Collects a username, password (with confirmation), display name and email,
then creates the account through the core ``UserManager.create()``. On
success it emits ``auth:register`` and auto-logs the new user in (setting
``session.user`` / ``session.authenticated``). Finally it offers an optional,
immediate TOTP enrolment.
"""

from __future__ import annotations

from shared.telnet_protocol import ANSI

from core.user import UserExistsError
from .login import ScreenLoader, Terminal


class RegistrationFlow:
    """Interactive registration flow driven against a BBS session."""

    def __init__(self, bbs, totp_manager, screens: ScreenLoader | None = None):
        self.bbs = bbs
        self.totp = totp_manager
        self.screens = screens or ScreenLoader(bbs)

    async def run(self, session) -> bool:
        """Run the registration flow. Returns True after a successful signup
        (the new user is authenticated), False on quit / disconnect."""
        tty = Terminal(self.bbs, session)
        while getattr(session, "is_active", True):
            await tty.send(self.screens.render("register.txt"))

            raw = (await tty.read_line("Username: ")).strip()
            if not raw:
                return False                       # EOF / disconnect
            if raw.upper() == "Q":
                return False                       # back out to login/menu
            username = raw.lower()

            password = await tty.read_line("Password: ")
            if not password:
                await tty.send(
                    f"{ANSI.BRIGHT_RED}Password cannot be empty.{ANSI.RESET}\r\n"
                )
                await tty.pause()
                continue
            confirm = await tty.read_line("Confirm password: ")
            if password != confirm:
                await tty.send(
                    f"{ANSI.BRIGHT_RED}Passwords do not match. Try again."
                    f"{ANSI.RESET}\r\n"
                )
                await tty.pause()
                continue

            # That's the whole form. Display name, location, email etc. are
            # set later via user preferences -- registration stays friction-
            # free. (A sysop who wants an interrogation can write their own
            # login plugin.)

            try:
                user = await self.bbs.users.create(
                    username=username,
                    password=password,
                )
            except UserExistsError:
                await tty.send(
                    f"{ANSI.BRIGHT_RED}That username is already taken."
                    f"{ANSI.RESET}\r\n"
                )
                await tty.pause()
                continue
            except ValueError as exc:
                await tty.send(
                    f"{ANSI.BRIGHT_RED}Invalid input: {exc}.{ANSI.RESET}\r\n"
                )
                await tty.pause()
                continue

            self.bbs.events.emit("auth:register", {"session": session, "user": user})

            # Auto-login the freshly created account.
            session.user = user
            session.username = user.username
            session.authenticated = True

            await tty.send(
                f"{ANSI.BRIGHT_GREEN}Account created. Welcome, "
                f"{user.shown_name()}!{ANSI.RESET}\r\n"
            )

            # Optional immediate TOTP enrolment.
            answer = (await tty.read_line("Set up two-factor auth now? [y/N]: ")).strip().upper()
            if answer in ("Y", "YES"):
                from .totp import TOTPFlow
                await TOTPFlow(self.bbs, self.totp, self.screens).setup(session, user)

            return True
        return False