"""Shared interactive-session driver for Modulo BBS.

The transports (telnet / SSH), the *logon* sequencer and the *mainmenu*
plugin all need to read a command from a session, send negotiation responses,
and run a plugin's interactive flow. Those helpers live here so a plugin can
drive a session identically over any transport without knowing which one is
underneath.

The core bootstrap hook (:func:`run_bootstrap`) is the single line each
transport calls after the protocol handshake: it finds the plugin named by
config key ``logon_plugin`` (default ``"logon"``) and hands the session to its
``on_session_start``. If the plugin is missing or broken it sends a minimal
notice and closes cleanly -- it never hangs.

Only :meth:`core.app.BBSApp.disconnect` closes sockets; plugins (including
the sequencer and the menu) request a disconnect through it rather than
touching writers directly.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("modulo.core.runner")

IDLE_TIMEOUT = 300  # seconds; a session idle this long is disconnected


async def read_command(bbs, session, timeout: int = IDLE_TIMEOUT) -> str | None:
    """Read and decode one chunk of input from ``session``.

    Negotiates telnet control sequences (when the session carries a
    ``negotiator``) and forwards the responses; SSH sessions carry no
    negotiator so their already-normalised bytes pass straight through.

    Returns the decoded text, or ``None`` when the connection ended or went
    idle -- the caller should tear down / return to the menu.
    """
    neg = getattr(session, "negotiator", None)
    if getattr(session, "reader", None) is None:
        return None

    # Serve any previously-buffered complete line before touching the wire.
    # A bare \r is a valid terminator (telnet Enter from many clients).
    buf = getattr(session, "_line_buffer", "")
    while True:
        cr, nl = buf.find("\r"), buf.find("\n")
        idxs = [x for x in (cr, nl) if x != -1]
        if not idxs:
            break
        cut = min(idxs)
        line, buf = buf[:cut], buf[cut + 1:]
        session._line_buffer = buf
        return line
    session._line_buffer = buf

    try:
        data = await asyncio.wait_for(
            session.reader.read(1024), timeout=timeout
        )
    except asyncio.TimeoutError:
        await bbs.send(session, "\r\n\r\n[Idle timeout. Goodbye!]\r\n")
        return None
    if not data:
        # EOF: flush a trailing partial line if any, else signal disconnect.
        leftover = getattr(session, "_line_buffer", "")
        session._line_buffer = ""
        return leftover or None

    session.touch()
    session.bytes_received += len(data)

    # Server-side echo for telnet sessions (SSH echoes at transport layer).
    # Printable characters echo; CR/LF becomes CRLF; control bytes silent.
    if neg is not None and not getattr(session, "transport_echoes", False):
        echo = bytearray()
        i = 0
        while i < len(data):
            b = data[i:i+1]
            if b in (b"\r", b"\n"):
                echo += b"\r\n"
            elif 32 <= data[i] < 127 or data[i] >= 128:
                echo += b
            i += 1
        if echo:
            await bbs.send(session, echo.decode("latin-1", errors="replace"))

    # Split the chunk into lines and hand them out one at a time. Multiple
    # keystrokes often arrive in one TCP segment (paste, fast typing, or a
    # scripted client), and callers expect line-at-a-time semantics.
    buf = getattr(session, "_line_buffer", "")

    # If a previous call stashed a partial line with no terminator yet, keep
    # reading until the line completes -- an empty return means "no input",
    # which flows treat as disconnect. Loop instead of returning partials.
    while "\r" not in buf and "\n" not in buf:
        if neg is None:
            text_chunk = data.decode("latin-1", errors="replace")
        else:
            clean, responses = neg.process_data(data)
            if responses:
                for resp in responses:
                    await bbs.send_raw(session, resp)
            session.terminal_width, session.terminal_height = neg.window_size
            session.terminal_type = neg.terminal_type
            text_chunk = clean.decode("latin-1", errors="replace") if clean else ""
        if not text_chunk:
            # Pure control traffic; read again (loop continues below via data refetch)
            pass
        buf += text_chunk
        if "\r" in buf or "\n" in buf:
            break
        try:
            data = await asyncio.wait_for(
                session.reader.read(1024), timeout=timeout
            )
        except asyncio.TimeoutError:
            await bbs.send(session, "\r\n\r\n[Idle timeout. Goodbye!]\r\n")
            return None
        if not data:
            return None
        session.touch()
        session.bytes_received += len(data)

    # Telnet clients may send bare \r as Enter; treat CR or LF as terminator.
    cr = buf.find("\r")
    nl = buf.find("\n")
    term = min((x for x in (cr, nl) if x != -1), default=-1)
    if term == -1:
        # No newline yet (partial line): stash and let caller call again.
        session._line_buffer = buf
        return ""
    line, rest = buf[:term], buf[term + 1:]
    session._line_buffer = rest
    return line


async def read_key(bbs, session, timeout: int = IDLE_TIMEOUT) -> str | None:
    """Read a SINGLE keypress -- no Enter required -- for hotkey menus.

    Deliberately does NOT go through :func:`read_command`: that function
    buffers partial lines waiting for Enter, which would swallow a lone
    menu keypress forever (the "Q inside a plugin hangs" bug). Here a
    printable byte is returned the moment it arrives; anything typed after
    it is stashed in ``_line_buffer`` for the next read. Telnet negotiation
    is handled exactly as in read_command. Hotkeys are not echoed --
    classic BBS menus don't echo single-key selections.
    """
    neg = getattr(session, "negotiator", None)
    if getattr(session, "reader", None) is None:
        return None

    while True:
        # Serve a printable character from the stash first, if any.
        buf = getattr(session, "_line_buffer", "")
        session._line_buffer = ""
        for i, ch in enumerate(buf):
            if ch.isprintable() and not ch.isspace():
                session._line_buffer = buf[i + 1:]
                return ch.upper()

        try:
            data = await asyncio.wait_for(
                session.reader.read(1024), timeout=timeout
            )
        except asyncio.TimeoutError:
            await bbs.send(session, "\r\n\r\n[Idle timeout. Goodbye!]\r\n")
            return None
        if not data:
            return None
        session.touch()
        session.bytes_received += len(data)

        if neg is None:
            text = data.decode("latin-1", errors="replace")
        else:
            clean, responses = neg.process_data(data)
            for resp in responses or []:
                await bbs.send_raw(session, resp)
            session.terminal_width, session.terminal_height = neg.window_size
            session.terminal_type = neg.terminal_type
            text = clean.decode("latin-1", errors="replace") if clean else ""

        for i, ch in enumerate(text):
            if ch.isprintable() and not ch.isspace():
                # Stash anything typed after the key (fast "MQ" lands as one
                # TCP segment); the next read serves it in order.
                session._line_buffer = text[i + 1:]
                return ch.upper()
        # Pure control traffic / stray Enters: loop and keep reading.


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