"""
SSH transport for Modulo BBS.
Uses asyncssh to provide SSH access alongside telnet.
Supports "no auth" mode (public access without credentials).
SyncTERM/cryptlib compatible: RSA host key, SHA-1 KEX, CBC ciphers, raw bytes.

After the SSH handshake the session bridges the asyncssh channel to a shared
:class:`server.session.Session` object (providing ``reader`` / ``writer`` so
``bbs.send`` and the plugin line-reading terminals work unchanged over SSH).
Like the telnet server, it then invokes the core bootstrap hook
(:func:`core.runner.run_bootstrap`), handing the session to the configured
``logon_plugin`` (the ``logon`` sequencer) which drives the whole logon flow --
splash, login, welcome, menu -- identically over SSH.
"""

import asyncio
import logging
import sys
from pathlib import Path

try:
    import asyncssh
except ImportError:
    asyncssh = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.session import Session, SessionState

logger = logging.getLogger("bbs.ssh")


class _SSHWriter:
    """Expose a stream-writer surface (``write``/``is_closing``/``drain``)
    over an asyncssh channel so the core ``bbs.send`` / ``server._send``
    transport path works for SSH sessions too (the logon sequencer, login
    plugin and main menu send text through those paths)."""

    def __init__(self, chan):
        self._chan = chan

    def write(self, data):
        self._chan.write(data)

    def is_closing(self):
        return self._chan.is_closing()

    async def drain(self):
        # asyncssh buffers and flushes internally; nothing to await here.
        return None

    def close(self):
        if not self._chan.is_closing():
            self._chan.close()

    async def wait_closed(self):
        return None


class BBSSSHSession(asyncssh.SSHServerSession):
    """AsyncSSH session that bridges to the BBS session logic.
    Uses encoding=None (raw bytes) for CP437 compatibility."""

    def __init__(self, bbs):
        # ``bbs`` is the shared BBSApp (same object the telnet server uses).
        self.bbs = bbs
        self._chan = None
        self._session: Session | None = None
        # StreamReader fed by data_received so plugins can read CRLF-terminated
        # lines via ``session.reader.readline()``.
        self._reader: asyncio.StreamReader | None = None

    def connection_made(self, chan):
        self._chan = chan
        peer = chan.get_extra_info('peername')
        addr = (peer[0], peer[1]) if peer else ('0.0.0.0', 0)
        session_id = f"ssh-{id(self) & 0xFFFFFF:06x}"
        logger.info(f"SSH connection from {addr} (id={session_id})")
        self._session = Session(
            session_id=session_id, node_id=0, address=addr,
            terminal_type="SSH", terminal_width=80, terminal_height=24,
        )
        # Wire transport so plugins send via bbs.send / server._send and read
        # via session.reader, exactly like a telnet session.
        self._reader = asyncio.StreamReader()
        self._session.reader = self._reader
        self._session.writer = _SSHWriter(chan)

    def pty_requested(self, term_type, term_size, term_modes):
        if self._session:
            if term_size[0] > 0:
                self._session.terminal_width = term_size[0]
            if term_size[1] > 0:
                self._session.terminal_height = term_size[1]
            self._session.terminal_type = term_type or "SSH"
        return True

    def shell_requested(self):
        return True

    def session_started(self):
        logger.info(f"session_started: {self._session.session_id if self._session else '???'}")
        asyncio.ensure_future(self._safe_shell_loop())

    async def _safe_shell_loop(self):
        try:
            await self._shell_loop()
        except Exception as e:
            logger.error(f"Shell loop crashed: {e}", exc_info=True)
            await self._cleanup()

    def data_received(self, data, datatype):
        # With encoding=None, data is bytes. Normalise line endings so the
        # plugin line readers see clean \n-terminated lines from any client
        # (SyncTERM sends bare \r on Enter).
        if self._reader is None:
            return

        # Server-side echo: SSH clients (SyncTERM included) expect the remote
        # end to echo typed characters, like a real tty line discipline.
        # Printable characters echo as-is (or session.echo_mask for passwords);
        # DEL/Backspace erase in place only when a column was echoed;
        # Enter echoes as CRLF. Control sequences (ESC ...) pass unechoed.
        echo = bytearray()
        echoed = int(getattr(self._session, "_echoed_cols", 0) or 0) if self._session else 0
        mask = getattr(self._session, "echo_mask", None) if self._session else None
        mask_b = mask.encode("ascii", errors="replace")[:1] if mask else None
        i = 0
        while i < len(data):
            b = data[i:i + 1]
            if b == b"\x7f" or b == b"\x08":          # DEL / Backspace
                if echoed > 0:
                    echo += b"\b \b"
                    echoed -= 1
            elif b == b"\r":                          # Enter -> CRLF
                echo += b"\r\n"
                echoed = 0
            elif b == b"\x1b":                        # ESC: skip sequence
                i += 2                                # skip ESC + first byte
            elif b >= b" ":
                echo += mask_b if mask_b else b
                echoed += 1
            i += 1
        if self._session is not None:
            self._session._echoed_cols = echoed
        if echo:
            # data_received is a sync callback invoked by asyncssh; schedule
            # the async send on the loop rather than awaiting it here.
            asyncio.ensure_future(self._send(bytes(echo)))

        raw = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        self._reader.feed_data(raw)

    async def _shell_loop(self):
        logger.info("shell_loop: start")
        if not self._chan or not self._session:
            logger.warning("shell_loop: chan or session is None")
            return
        try:
            node_id = self.bbs.session_manager._assign_node()
            self._session.node_id = node_id
            self.bbs.session_manager.sessions[self._session.session_id] = self._session
            logger.info(f"shell_loop: node {node_id}")
        except RuntimeError as e:
            logger.warning(f"shell_loop: node assign failed: {e}")
            await self._send(b"\r\n[All nodes busy.]\r\n")
            self._chan.close()
            return

        # No telnet negotiation over SSH: input is already clean text.
        self._session.negotiator = None
        # SSH transport echoes keystrokes itself (data_received bridge), so
        # read_command must NOT echo again (double characters otherwise).
        self._session.transport_echoes = True
        self._session.state = SessionState.CONNECTED

        # Core bootstrap hook (identical to the telnet server): the configured
        # ``logon_plugin`` orchestrates the whole logon flow over SSH.
        logger.info("shell_loop: running bootstrap (logon plugin)")
        from core.runner import run_bootstrap
        await run_bootstrap(self.bbs, self._session)

        # The logon flow is responsible for disconnecting on exit (Q, idle,
        # unavailable). If it finished with the session still open, close it
        # so the connection never hangs.
        if self._session.is_active:
            await self.bbs.disconnect(self._session)

        logger.info("shell_loop: exiting")
        await self._cleanup()

    async def _send(self, data: bytes):
        if not self._chan or self._chan.is_closing():
            return
        self._chan.write(data)
        if self._session:
            self._session.bytes_sent += len(data)

    async def _cleanup(self):
        if self._session:
            logger.info(f"SSH session {self._session.session_id} closed")
            # Give every plugin a chance to clean up (e.g. the login plugin
            # emits user:logout when an authenticated session disconnects).
            for plugin in self.bbs.plugins:
                try:
                    result = plugin.on_session_end(self._session)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
            await self.bbs.session_manager.remove_session(self._session.session_id)
            self._session = None

    def eof_received(self):
        logger.info("EOF received — ignoring")
        return True  # Keep channel half-open

    def connection_lost(self, exc):
        if self._session:
            self._session.state = SessionState.DISCONNECTED
        if self._reader is not None:
            self._reader.feed_eof()


class BBSSSHServer(asyncssh.SSHServer):
    """SSH server that accepts all connections (no auth) and wires each
    session to the shared BBSApp (the logon sequencer drives auth and the
    main menu)."""

    def __init__(self, bbs):
        self.bbs = bbs

    def session_requested(self):
        return BBSSSHSession(self.bbs)

    def begin_auth(self, username):
        return False

    def password_auth_supported(self):
        return False

    def public_key_auth_supported(self):
        return False


async def start_ssh_server(bbs, host: str = "127.0.0.1", port: int = 6422):
    """Start the SSH BBS server with SyncTERM/cryptlib compatibility.

    ``bbs`` is the shared :class:`core.app.BBSApp` (same object handed to the
    telnet server); its loaded plugins -- orchestrated by the logon sequencer --
    drive authentication and the menu.
    """
    if asyncssh is None:
        logger.error("asyncssh not installed. Run: pip install asyncssh")
        return

    # Use RSA host key (cryptlib needs RSA, not ed25519)
    rsa_key_path = Path(__file__).parent.parent / "keys" / "ssh_host_rsa_key"
    if rsa_key_path.exists():
        host_key = asyncssh.read_private_key(str(rsa_key_path))
    else:
        rsa_key_path.parent.mkdir(exist_ok=True)
        host_key = asyncssh.generate_private_key('ssh-rsa', key_size=2048)
        host_key.write_private_key(str(rsa_key_path))
        logger.info(f"Generated RSA host key: {rsa_key_path}")

    def server_factory():
        return BBSSSHServer(bbs)

    server = await asyncssh.create_server(
        server_factory,
        host,
        port,
        server_host_keys=[host_key],
        kex_algs=[
            'diffie-hellman-group14-sha1',
            'diffie-hellman-group-exchange-sha1',
            'ecdh-sha2-nistp256',
            'diffie-hellman-group14-sha256',
            'diffie-hellman-group16-sha512',
        ],
        encryption_algs=[
            'aes128-cbc', 'aes256-cbc',
            'aes128-ctr', 'aes256-ctr',
            '3des-cbc',
        ],
        mac_algs=['hmac-sha1', 'hmac-sha2-256'],
        line_editor=False,
        encoding=None,  # Raw bytes for CP437 support
    )

    addrs = ", ".join(str(s.getsockname()) for s in server._sockets)
    logger.info(f"BBS SSH Server listening on {addrs}")
    print(f"  SSH on {addrs} (no auth, cryptlib compat)")

    async with server:
        await server.wait_closed()