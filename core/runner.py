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
bridge); telnet also echoes the *clean* payload in :func:`_read_chunk`
(printable as-is, backspace in place). Secret fields set ``session.echo_mask``
to ``"*"`` and briefly ``WILL ECHO`` so SyncTERM stops local-echoing the
password. :func:`read_command` applies backspace/DEL to the line buffer —
painting ``\\b \\b`` without dropping the byte was why a mistyped-then-
corrected password still failed.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger("modulo.core.runner")

IDLE_TIMEOUT = 300  # seconds; a session idle this long is disconnected

# B0 (boards-unification): how long read_key waits for the rest of an ESC
# sequence before declaring the ESC a lone keypress ("back" on Social).
ESC_KEY_WINDOW = 0.25  # seconds

_ARROW_MAP = {
    "\x1b[A": "UP", "\x1b[B": "DOWN", "\x1b[C": "RIGHT", "\x1b[D": "LEFT",
    "\x1bOA": "UP", "\x1bOB": "DOWN", "\x1bOC": "RIGHT", "\x1bOD": "LEFT",
}

# CSI ~ sequences (B4: Social thread-pane scrolling)
_CSI_MAP = {
    "\x1b[5~": "PGUP",
    "\x1b[6~": "PGDN",
}

def _try_arrow(buf: str):
    if buf.startswith(("\x1b[", "\x1bO")):
        if buf.startswith("\x1b[") and len(buf) >= 3 and buf[2].isdigit():
            m = _CSI_MAP.get(buf[:4])
            if m:
                return m, buf[4:]
            if len(buf) < 4:
                return None  # waiting for the '~'
            return "__SKIP__", buf[4:]  # unknown CSI~ — skip the whole form
        if len(buf) < 3:
            return None  # incomplete
        seq = buf[:3]
        rest = buf[3:]
        key = _ARROW_MAP.get(seq)
        if key:
            return key, rest
        return "__SKIP__", rest
    return False


def _edit_line_buffer(buf: str) -> str:
    """Apply backspace/DEL and drop arrow/CSI junk; keep an incomplete ESC.

    Visual echo already paints ``\\b \\b``; without this the line still
    contained the erased characters (mistype, backspace, retype → auth fail).
    """
    out: list[str] = []
    i = 0
    n = len(buf)
    while i < n:
        ch = buf[i]
        if ch in ("\x08", "\x7f"):
            if out:
                out.pop()
            i += 1
            continue
        if ch == "\x1b":
            r = _try_arrow(buf[i:])
            if r is None:
                return "".join(out) + buf[i:]
            if r is False:
                i += 1
                continue
            _key, rest = r
            i = n - len(rest)
            continue
        if ch in ("\r", "\n") or ch.isprintable():
            out.append(ch)
            i += 1
            continue
        i += 1
    return "".join(out)


async def _offer_server_echo(bbs, session, on: bool) -> None:
    """WILL/WONT ECHO for the secret-field window (telnet only)."""
    neg = getattr(session, "negotiator", None)
    if neg is None or bbs is None or not hasattr(bbs, "send_raw"):
        return
    from shared.telnet_protocol import IAC, OPT_ECHO, WILL, WONT

    if on:
        neg.local_options[OPT_ECHO] = True
        await bbs.send_raw(session, bytes([IAC, WILL, OPT_ECHO]))
    else:
        neg.local_options[OPT_ECHO] = False
        await bbs.send_raw(session, bytes([IAC, WONT, OPT_ECHO]))


@asynccontextmanager
async def secret_echo(bbs, session, mask: str | None):
    """Mask keystroke echo (``*``) for one password prompt."""
    if not mask:
        yield
        return
    prev = getattr(session, "echo_mask", None)
    session.echo_mask = mask
    session._echoed_cols = 0
    await _offer_server_echo(bbs, session, True)
    try:
        yield
    finally:
        session.echo_mask = prev
        session._echoed_cols = 0
        await _offer_server_echo(bbs, session, False)


def _idle(bbs, session) -> None:
    """Send the idle-timeout notice (fire-and-forget)."""
    asyncio.ensure_future(bbs.send(session, "\r\n\r\n[Idle timeout. Goodbye!]\r\n"))


async def _read_chunk(bbs, session, timeout, *, idle_on_timeout=True):
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
            if idle_on_timeout:
                _idle(bbs, session)
            return None
        if not data:
            return None

    session.touch()
    session.bytes_received += len(data)

    from shared.codecs import decode_in

    neg = getattr(session, "negotiator", None)
    if neg is None:
        clean = data
        text = decode_in(data, getattr(session, "codec", "cp437"))
    else:
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
    # source of the CP437 glyph garbage. Printable chars echo as-is (or as
    # session.echo_mask for passwords); CR/LF becomes CRLF; Backspace erases
    # in place only when a column was actually echoed; ESC sequences silent.
    # Chat mode (suppress_echo) paints its own feedback instead.
    if not getattr(session, "transport_echoes", False) and not getattr(
        session, "suppress_echo", False
    ):
        echo = bytearray()
        echoed = int(getattr(session, "_echoed_cols", 0) or 0)
        mask = getattr(session, "echo_mask", None)
        mask_b = (
            mask.encode("ascii", errors="replace")[:1] if mask else None
        )
        i = 0
        while i < len(clean):
            b = clean[i:i + 1]
            if b == b"\x08" or b == b"\x7f":          # Backspace / DEL
                if echoed > 0:
                    echo += b"\b \b"
                    echoed -= 1
            elif b == b"\r" or b == b"\n":            # Enter -> one CRLF
                echo += b"\r\n"
                echoed = 0
                if b == b"\r" and i + 1 < len(clean) and clean[i + 1:i + 2] == b"\n":
                    i += 1                            # swallow LF of CRLF
            elif b == b"\x1b":                        # ESC: skip ANSI seq
                i += 2
            elif b >= b" ":                            # printable
                echo += mask_b if mask_b else b
                echoed += 1
            i += 1
        session._echoed_cols = echoed
        if echo:
            # Keystroke echo is cursor painting, not a display row — bbs.send
            # would _pad_line a bare CRLF into 79 spaces and shove Password:
            # down a blank gutter (SyncTERM CRLF doubles it).
            if hasattr(bbs, "send_raw"):
                await bbs.send_raw(session, bytes(echo))
            else:
                await bbs.send(
                    session,
                    decode_in(bytes(echo), getattr(session, "codec", "cp437")),
                )

    return text


async def read_command(
    bbs,
    session,
    timeout: int = IDLE_TIMEOUT,
    *,
    echo: str | None = None,
) -> str | None:
    """Read one LINE of input (CR/LF-terminated).

    Handles telnet negotiation, buffers until the terminator arrives, and
    returns the line *without* its terminator. Backspace/DEL edit the line
    (they are not left in the returned string). Returns ``None`` on EOF or
    idle timeout. Never returns a partial line as if it were complete.

    ``echo="*"`` masks keystroke echo for password fields.
    """
    async with secret_echo(bbs, session, echo):
        buf = getattr(session, "_line_buffer", "")

        while True:
            buf = _edit_line_buffer(buf)
            for sep in ("\r\n", "\r", "\n"):
                if sep in buf:
                    line, rest = buf.split(sep, 1)
                    if sep == "\r" and rest.startswith("\n"):
                        rest = rest[1:]
                    session._line_buffer = rest
                    session._echoed_cols = 0
                    return line

            chunk = await _read_chunk(bbs, session, timeout)
            if chunk is None:
                # EOF / idle: hand back a trailing partial line if one exists.
                session._line_buffer = ""
                return buf or None
            buf += chunk


async def read_key(
    bbs,
    session,
    timeout: int = IDLE_TIMEOUT,
    *,
    preserve_case: bool = False,
    idle_on_timeout: bool = True,
) -> str | None:
    """Read a SINGLE keypress (no Enter) for hotkey menus.

    Returns the first printable, non-whitespace character (uppercased), or
    ``None`` on EOF/timeout. Arrow keys are normalized to ``UP``/``DOWN``/
    ``LEFT``/``RIGHT``; Enter (CR) is ``ENTER``; LF (SyncTERM Ctrl-Enter)
    is ``"LF"`` (chat multi-line newline); Ctrl-E is ``"CTRL_E"`` (chat
    overlay editor); Ctrl-S is ``"CTRL_S"`` (overlay save). A bare ESC with no
    follow-up byte within :data:`ESC_KEY_WINDOW` returns ``"ESC"`` (B0:
    Social's back key); a fast-following byte still parses as its sequence.
    BACKSPACE (DEL/BS) is ``"BACKSPACE"``. Chat mode reads with
    ``preserve_case=True`` and ``idle_on_timeout=False`` (short polling
    without tripping the idle-notice).
    Bytes typed after the key are stashed in ``_line_buffer`` for the next
    read. Does not echo (see module docstring).
    """
    # Serve a printable char from any stash first.
    buf = getattr(session, "_line_buffer", "")
    pending_esc = False
    while buf:
        # Check for stashed arrow/enter sequences first (both ESC[ and ESCO)
        r = _try_arrow(buf)
        if r is None:
            pending_esc = True  # incomplete ESC — wait, but only briefly
            break
        if r is not False:
            key, rest = r
            session._line_buffer = rest
            if key == "__SKIP__":
                continue
            return key
        if buf[0] == "\x1b":
            # Unrecognized ESC-led bytes (not [ or O): give the terminal a
            # window to complete the sequence; silence means lone ESC (B0).
            pending_esc = True
            break
        if buf[0] == "\r":
            session._line_buffer = buf[1:]
            return "ENTER"
        if buf[0] == "\n":
            session._line_buffer = buf[1:]
            return "LF"  # Ctrl-Enter (SyncTERM): newline inside a draft
        if buf[0] == " ":
            session._line_buffer = buf[1:]
            return "SPACE"  # B4: PgDn alias on Social
        if buf[0] == "\x05":
            session._line_buffer = buf[1:]
            return "CTRL_E"  # overlay compose editor (Social chat)
        if buf[0] == "\x13":
            session._line_buffer = buf[1:]
            return "CTRL_S"  # overlay save/send
        ch, buf = buf[0], buf[1:]
        session._line_buffer = buf
        if ch in ("\x7f", "\x08"):
            return "BACKSPACE"
        if ch.isprintable() and not ch.isspace():
            return ch if preserve_case else ch.upper()

    while True:
        if pending_esc:
            # B0: an ESC arrived alone. Give the rest of the sequence a
            # short window to show up; on silence (or EOF) hand back
            # "ESC". The outer wait_for fires long before _read_chunk's
            # idle timeout, so the idle machinery is never triggered.
            try:
                chunk = await asyncio.wait_for(
                    _read_chunk(bbs, session, timeout,
                                idle_on_timeout=idle_on_timeout),
                    timeout=ESC_KEY_WINDOW,
                )
            except asyncio.TimeoutError:
                chunk = None
            if chunk is None:
                session._line_buffer = buf[1:]
                return "ESC"
        else:
            chunk = await _read_chunk(bbs, session, timeout,
                                      idle_on_timeout=idle_on_timeout)
            if chunk is None:
                return None
        # Prepend to stash and re-decode via the same logic above so
        # arrow sequences that arrived split across chunks still work.
        buf = getattr(session, "_line_buffer", "") + chunk
        session._line_buffer = buf
        # Try to consume one key from the stashed buf (handles ESC[ and ESCO)
        buf = session._line_buffer
        r = _try_arrow(buf)
        if r is None:
            pending_esc = True  # incomplete ESC — read more, briefly
            continue
        pending_esc = False
        if r is not False:
            key, rest = r
            session._line_buffer = rest
            if key == "__SKIP__":
                continue
            return key
        for i, ch in enumerate(buf):
            if ch == "\r":
                session._line_buffer = buf[i + 1 :]
                return "ENTER"
            if ch == "\n":
                session._line_buffer = buf[i + 1 :]
                return "LF"  # Ctrl-Enter (SyncTERM): newline inside a draft
            if ch == " ":
                session._line_buffer = buf[i + 1 :]
                return "SPACE"  # B4: PgDn alias on Social
            if ch == "\x05":
                session._line_buffer = buf[i + 1 :]
                return "CTRL_E"  # overlay compose editor (Social chat)
            if ch == "\x13":
                session._line_buffer = buf[i + 1 :]
                return "CTRL_S"  # overlay save/send
            if ch == "\x1b":
                # start of an ESC sequence — wait, but only briefly
                # (B0: silence resolves to a lone "ESC" keypress)
                pending_esc = True
                break
            if ch in ("\x7f", "\x08"):
                session._line_buffer = buf[i + 1 :]
                return "BACKSPACE"
            if ch.isprintable() and not ch.isspace():
                session._line_buffer = buf[i + 1 :]
                return ch if preserve_case else ch.upper()
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
    """Core bootstrap hook: invoke the plugin filling the ``logon`` role.

    Every transport calls this once per session after the protocol handshake.
    It fires ``session:connect``, then awaits the logon plugin's
    ``on_session_start``. A missing or failing logon plugin sends a minimal
    "System unavailable." notice and closes the session via ``bbs.disconnect``
    -- never hangs.
    """
    plugin = bbs.plugin_for("logon")
    name = bbs.role_plugin_name("logon")
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
