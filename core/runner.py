"""Shared interactive-session driver for Modulo BBS.

The transports (telnet / SSH), the *logon* sequencer and the *mainmenu*
plugin all need to read a command from a session, send negotiation responses,
and run a plugin's interactive flow. Those helpers live here so a plugin can
drive a session identically over any transport without knowing which one is
underneath.

Input model (two distinct modes):

* :func:`read_command` -- LINE mode. Waits for Enter (CR and/or LF) and
  returns the completed line. Used for usernames, passwords, message text.
* :func:`read_key` -- KEY mode. Returns the first printable byte the moment
  it arrives, no Enter. Used for hotkey menus.

Echo policy: SSH echoes keystrokes at the transport layer (``data_received``
bridge); telnet clients (SyncTERM) do LOCAL echo themselves. Neither reader
echoes, so there is no double-echo and no raw IAC bytes leak to the display.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("modulo.core.runner")

IDLE_TIMEOUT = 300  # seconds; a session idle this long is disconnected


def _idle(bbs, session) -> None:
    """Send the idle-timeout notice (fire-and-forget)."""
    asyncio.ensure_future(bbs.send(session, "\r\n\r\n[Idle timeout. Goodbye!]\r\n"))


async def _read_chunk(bbs, session, timeout):
    """Read one chunk of bytes, handling telnet negotiation.

    Returns the decoded clean text (IAC sequences stripped), or ``None`` on
    EOF/timeout. Negotiation responses are sent raw and never echoed.
    Bytes pushed back by the codec probe are served first.
    """
    reader = getattr(session, "reader", None)
    if reader is None:
        return None

    from shared.codecs import take_pushback

    pushed = take_pushback(session)
    if pushed:
        data = pushed
    else:
        neg = getattr(session, "negotiator", None)

        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=timeout)
        except asyncio.TimeoutError:
            _idle(bbs, session)
            return None
        if not data:
            return None

    session.touch()
    session.bytes_received += len(data)

    from shared.codecs import decode_in

    neg = getattr(session, "negotiator", None)
    if neg is None:
        return decode_in(data, getattr(session, "codec", "cp437"))

    clean, responses = neg.process_data(data)
    for resp in responses or []:
        await bbs.send_raw(session, resp)
    session.terminal_width, session.terminal_height = neg.window_size
    session.terminal_type = neg.terminal_type

    text = (
        decode_in(clean, getattr(session, "codec", "cp437")) if clean else ""
    )

    # Server-side echo for telnet (SSH echoes at transport layer).
    # Echo the CLEAN bytes only -- never raw IAC negotiation, which was the
    # source of the CP437 glyph garbage. Printable chars echo as-is; CR/LF
    # becomes CRLF; Backspace erases in place; ESC sequences pass silent.
    if not getattr(session, "transport_echoes", False):
        echo = bytearray()
        i = 0
        while i < len(clean):
            b = clean[i:i + 1]
            if b == b"\x08" or b == b"\x7f":          # Backspace / DEL
                echo += b"\b \b"
            elif b == b"\r" or b == b"\n":            # Enter -> CRLF
                echo += b"\r\n"
            elif b == b"\x1b":                        # ESC: skip ANSI seq
                i += 2
            elif b >= b" ":                            # printable
                echo += b
            i += 1
        if echo:
            await bbs.send(
                session,
                decode_in(bytes(echo), getattr(session, "codec", "cp437")),
            )

    return text


async def read_command(bbs, session, timeout: int = IDLE_TIMEOUT) -> str | None:
    """Read one LINE of input (CR/LF-terminated).

    Handles telnet negotiation, buffers until the terminator arrives, and
    returns the line *without* its terminator. Returns ``None`` on EOF or
    idle timeout. Never returns a partial line as if it were complete.
    """
    buf = getattr(session, "_line_buffer", "")

    while True:
        # Serve a buffered complete line first.
        for sep in ("\r\n", "\r", "\n"):
            if sep in buf:
                line, rest = buf.split(sep, 1)
                session._line_buffer = rest
                return line

        chunk = await _read_chunk(bbs, session, timeout)
        if chunk is None:
            # EOF / idle: hand back a trailing partial line if one exists.
            session._line_buffer = ""
            return buf or None
        buf += chunk


async def read_key(bbs, session, timeout: int = IDLE_TIMEOUT) -> str | None:
    """Read a SINGLE keypress (no Enter) for hotkey menus.

    Returns the first printable, non-whitespace character (uppercased), or
    ``None`` on EOF/timeout. Bytes typed after the key are stashed in
    ``_line_buffer`` for the next read. Does not echo (see module docstring).
    """
    # Serve a printable char from any stash first.
    buf = getattr(session, "_line_buffer", "")
    while buf:
        ch, buf = buf[0], buf[1:]
        session._line_buffer = buf
        if ch.isprintable() and not ch.isspace():
            return ch.upper()

    while True:
        chunk = await _read_chunk(bbs, session, timeout)
        if chunk is None:
            return None
        for i, ch in enumerate(chunk):
            if ch.isprintable() and not ch.isspace():
                session._line_buffer = chunk[i + 1:]
                return ch.upper()
        # Pure control / whitespace: loop for a real key.


async def run_plugin_flow(bbs, plugin, session) -> bool:
    """Enter a menu plugin: run its ``on_session_start`` flow and return.

    The plugin owns its whole interaction loop inside ``on_session_start``
    (reading input itself via ``read_command``/``read_key``). Returning
    False (or the session dying) pops back to the caller's menu. There is
    deliberately no outer command loop here -- that double loop was the
    cause of "Q needs two Enters".
    """
    try:
        result = plugin.on_session_start(session)
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001
        logger.exception("plugin %s on_session_start failed", plugin.name)
    return session.is_active


async def run_bootstrap(bbs, session) -> None:
    """Core bootstrap hook: invoke the configured ``logon_plugin``.

    Every transport calls this once per session after the protocol handshake.
    It fires ``session:connect``, then awaits the logon plugin's
    ``on_session_start``. A missing or failing logon plugin sends a minimal
    "System unavailable." notice and closes the session via ``bbs.disconnect``
    -- never hangs.
    """
    name = (bbs.config or {}).get("logon_plugin", "logon")
    plugin = bbs.get_plugin(name)
    bbs.events.emit("session:connect", {"session": session})

    if plugin is None:
        logger.error("logon plugin %r not loaded; refusing session", name)
        await bbs.send(session, "\r\nSystem unavailable.\r\n")
        if session.is_active:
            await bbs.disconnect(session)
        return

    try:
        result = plugin.on_session_start(session)
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001
        logger.exception("logon plugin %r failed", name)
        await bbs.send(session, "\r\nSystem unavailable.\r\n")
        if session.is_active:
            await bbs.disconnect(session)
