"""
Optional time-based one-time password (TOTP) support for the Modulo BBS
login plugin.

Per the plugin spec, plugin-specific auth data (TOTP secrets) lives in the
plugin's own ``data/`` directory, not in core. Secrets are persisted as a
small JSON mapping ``username -> base32 secret`` at ``data/totp_secrets.json``.

This module uses the ``pyotp`` library for all one-time-password math.

* :class:`TOTPManager`   -- secret storage + code verification.
* :class:`TOTPFlow`      -- interactive setup / verification against a session.

Because this is a terminal front end (telnet/SSH), there is no real QR
rendering: the setup screen shows the base32 secret string and the
``otpauth://`` URI for manual entry into an authenticator app, then asks the
user to type the 6-digit code to confirm the enrolment.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pyotp

from core.theme import palette_for

logger = logging.getLogger("modulo.plugins.totp")

from .login import DATA_DIR, ScreenLoader, Terminal  # noqa: E402


class TOTPManager:
    """Persist and verify TOTP secrets per username.

    Secrets are kept in a JSON file owned by this plugin's ``data/``
    directory. The manager is synchronous and intentionally simple -- the
    JSON file is tiny and updated rarely (only at enrolment).
    """

    def __init__(self, secrets_path: str | Path | None = None):
        self.secrets_path = (
            Path(secrets_path) if secrets_path else DATA_DIR / "totp_secrets.json"
        )
        self._secrets: dict[str, str] = {}
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        """Load secrets from disk; tolerate a missing/corrupt file."""
        try:
            loaded = json.loads(self.secrets_path.read_text("utf-8"))
            self._secrets = {
                str(k): str(v) for k, v in loaded.items() if v
            }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            self._secrets = {}

    def _save(self) -> None:
        """Atomically persist secrets to disk (temp file + rename)."""
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.secrets_path.with_suffix(self.secrets_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._secrets, indent=2), encoding="utf-8")
        tmp.replace(self.secrets_path)

    # -- query / mutate -----------------------------------------------------

    def has_secret(self, username: str) -> bool:
        return username in self._secrets

    def get_secret(self, username: str) -> str | None:
        return self._secrets.get(username)

    def set_secret(self, username: str, secret: str) -> None:
        self._secrets[username] = secret
        self._save()

    def remove_secret(self, username: str) -> bool:
        if username in self._secrets:
            del self._secrets[username]
            self._save()
            return True
        return False

    # -- verification -------------------------------------------------------

    def verify(self, username: str, code: str) -> bool:
        """Return True if ``code`` is a valid current TOTP for ``username``.

        Returns False whenever the user has no enrolled secret or the code
        does not validate (covers drift via pyotp's default validation window).
        """
        secret = self._secrets.get(username)
        if not secret or not code:
            return False
        try:
            return pyotp.TOTP(secret).verify(code.strip())
        except Exception:  # noqa: BLE001 -- a bad secret must never crash login
            logger.warning("TOTP validation failed for %s", username)
            return False

    @property
    def usernames(self) -> list[str]:
        return list(self._secrets)


class TOTPFlow:
    """Interactive TOTP setup and verification for a BBS session."""

    ISSUER = "Modulo BBS"

    def __init__(self, bbs, manager: TOTPManager | None = None,
                 screens: ScreenLoader | None = None):
        self.bbs = bbs
        self.manager = manager or TOTPManager()
        self.screens = screens or ScreenLoader(bbs)

    @staticmethod
    def generate_secret() -> str:
        """Return a fresh random base32 TOTP secret."""
        return pyotp.random_base32()

    @staticmethod
    def current_code(secret: str) -> str:
        """Convenience: compute the 6-digit code for ``secret`` right now.

        Primarily useful to tests and sysop tooling.
        """
        return pyotp.TOTP(secret).now()

    def _otp(self, secret: str) -> "pyotp.TOTP":
        return pyotp.TOTP(secret)

    # -- setup --------------------------------------------------------------

    async def setup(self, session, user=None, secret: str | None = None) -> bool:
        """Enrol ``user`` for TOTP and confirm via a 6-digit code.

        ``secret`` may be supplied (e.g. by a test/admin) so the confirming
        code is known ahead of time; otherwise a new secret is generated and
        shown on the setup screen. Stores the secret only after confirmation.
        Returns True on successful enrolment.
        """
        user = user or getattr(session, "user", None)
        if user is None:
            return False
        tty = Terminal(self.bbs, session)
        secret = secret or self.generate_secret()

        uri = self._otp(secret).provisioning_uri(
            name=user.username, issuer_name=self.ISSUER
        )
        await tty.send(self.screens.render(
            "totp_setup.txt", session, SECRET=secret, URI=uri
        ))
        code = (await tty.read_line("Six-digit code: ")).strip()
        p = palette_for(session)
        if self._otp(secret).verify(code):
            self.manager.set_secret(user.username, secret)
            await tty.send(
                f"{p.success}Two-factor authentication enabled."
                f"{p.reset}\r\n"
            )
            return True
        await tty.send(
            f"{p.error}Code did not match. TOTP not enabled."
            f"{p.reset}\r\n"
        )
        return False

    # -- verification -------------------------------------------------------

    async def verify(self, session, user=None) -> bool:
        """Prompt for a TOTP code and validate it for the session's user."""
        user = user or getattr(session, "user", None)
        if user is None:
            return False
        tty = Terminal(self.bbs, session)
        await tty.send(self.screens.render("totp_verify.txt", session))
        code = (await tty.read_line("Six-digit code: ")).strip()
        return self.manager.verify(user.username, code)