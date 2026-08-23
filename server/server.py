"""
Main BBS Telnet Server.
Async multi-node server with telnet protocol handling.

The server owns the transport and the protocol negotiation. Everything a
caller sees after the handshake -- splash, login, welcome, menu -- is owned
by plugins: after handshake the server invokes the core bootstrap hook
(:func:`core.runner.run_bootstrap`), which hands the session to the plugin
named by config key ``logon_plugin`` (the ``logon`` sequencer by default).
If that plugin is missing or broken, the hook sends a minimal notice and
closes cleanly -- it never hangs.
"""

import asyncio
import logging
import re
import signal
import sys
import uuid
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.telnet_protocol import TelnetNegotiator
from server.session import Session, SessionState

logger = logging.getLogger("bbs.server")


class BBSServer:
    """Async telnet BBS server.

    The server owns the transport; authentication and post-login features
    are supplied by plugins loaded onto the shared ``bbs`` application object
    (see :class:`core.app.BBSApp`) and orchestrated by the logon sequencer.
    """

    def __init__(self, bbs=None, host: str = "127.0.0.1", port: int = 6400,
                 max_nodes: int = 8, plain_text: bool = False):
        if bbs is None:
            from core.app import BBSApp
            bbs = BBSApp(max_nodes=max_nodes)
        self.bbs = bbs
        bbs.server = self
        self.session_manager = bbs.session_manager
        self.max_nodes = bbs.session_manager.max_nodes
        self.host = host
        self.port = port
        self.plain_text = plain_text
        self._server: asyncio.Server | None = None
        self._running = False

    async def start(self):
        """Start the BBS server."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
            reuse_address=True,
        )
        self._running = True

        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        logger.info(f"BBS Server listening on {addrs}")
        logger.info(f"Max nodes: {self.max_nodes}")
        mode = " (plain text)" if self.plain_text else ""
        print(f"\n{'='*60}")
        print(f"  MODULO BBS Server v0.1{mode}")
        print(f"  Listening on {addrs}")
        print(f"  Max nodes: {self.max_nodes}")
        print(f"{'='*60}\n")

        # Handle graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        async with self._server:
            await self._server.serve_forever()
        # serve_forever() is cancelled by stop() calling server.close();
        # the CancelledError is expected and not an error.

    async def stop(self, message: str = "BBS shutting down. Goodbye!"):
        """Gracefully stop the server.

        Args:
            message: Shutdown notice sent to every connected session before
                closing their connections.  Defaults to the standard goodbye.
        """
        logger.info("Shutting down BBS server...")
        self._running = False

        for session in list(self.session_manager.active_sessions):
            if session.writer:
                try:
                    await self._send(session, f"\r\n\r\n[{message}]\r\n")
                    session.writer.close()
                    await session.writer.wait_closed()
                except Exception:
                    pass

        if self._server:
            self._server.close()
            await self._server.wait_closed()

        logger.info("Server stopped.")

    async def _handle_connection(self, reader: asyncio.StreamReader,
                                  writer: asyncio.StreamWriter):
        """Handle a new incoming connection."""
        addr = writer.get_extra_info('peername')
        session_id = str(uuid.uuid4())[:8]

        logger.info(f"Connection from {addr} (id={session_id})")

        session = await self.session_manager.create_session(
            session_id, addr, reader, writer
        )

        negotiator = TelnetNegotiator()
        session.negotiator = negotiator

        try:
            if self.session_manager.active_count > self.max_nodes:
                await self._send(session, "\r\n[All nodes busy. Try again later.]\r\n")
                writer.close()
                await writer.wait_closed()
                await self.session_manager.remove_session(session_id)
                return

            session.state = SessionState.NEGOTIATING
            await self._send_raw(session, negotiator.initial_negotiation())

            session.state = SessionState.CONNECTED
            # Core bootstrap hook: invoke the configured logon_plugin (the
            # ``logon`` sequencer by default), which runs the whole logon
            # sequence. On a missing/broken logon plugin this sends a
            # "System unavailable." notice and closes cleanly -- never hangs.
            from core.runner import run_bootstrap
            await run_bootstrap(self.bbs, session)

        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            logger.info(f"Connection lost: {session_id}")
        except Exception as e:
            logger.error(f"Error in session {session_id}: {e}", exc_info=True)
        finally:
            # Give every plugin a chance to clean up (e.g. the login plugin
            # emits user:logout when an authenticated session disconnects).
            for plugin in self.bbs.plugins:
                try:
                    result = plugin.on_session_end(session)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
            await self.session_manager.remove_session(session_id)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info(f"Session {session_id} closed")

    async def _send(self, session: Session, text: str):
        """Send text to a session. Strips ANSI in plain_text mode."""
        if not session.writer or session.writer.is_closing():
            return
        if self.plain_text:
            text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
        data = text.encode('latin-1', errors='replace')
        session.writer.write(data)
        await session.writer.drain()
        session.bytes_sent += len(data)

    async def _send_raw(self, session: Session, data: bytes):
        """Send raw bytes to a session."""
        if not session.writer or session.writer.is_closing():
            return
        session.writer.write(data)
        await session.writer.drain()
        session.bytes_sent += len(data)


async def main():
    """Entry point for the BBS server."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    host = "127.0.0.1"
    port = 6400
    max_nodes = 8
    plain_text = False

    if '--port' in sys.argv:
        idx = sys.argv.index('--port')
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    if '--host' in sys.argv:
        idx = sys.argv.index('--host')
        if idx + 1 < len(sys.argv):
            host = sys.argv[idx + 1]

    if '--nodes' in sys.argv:
        idx = sys.argv.index('--nodes')
        if idx + 1 < len(sys.argv):
            max_nodes = int(sys.argv[idx + 1])

    if '--plain' in sys.argv:
        plain_text = True

    from core.app import BBSApp
    from core.loader import PluginLoader

    bbs = BBSApp(max_nodes=max_nodes)
    bbs.plugins = await PluginLoader().load(bbs)

    server = BBSServer(bbs=bbs, host=host, port=port, plain_text=plain_text)
    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass