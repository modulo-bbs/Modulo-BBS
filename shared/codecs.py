"""Session codec selection — the terminal character-set edge.

Modulo stores everything internally as Python Unicode strings. This module
is the ONLY place that converts to/from wire bytes for interactive
sessions, so the board can speak the right character set per client:

* ``cp437``  — IBM PC glyph table. Native for Syncterm / ANSI-BBS mode,
               classic ANSI art, box drawing.
* ``utf-8``  — modern terminals (xterm, VT-family emulators, browsers).
* ``ascii``  — last resort: strip accents rather than emit mojibake.

Selection order (see plugins.login for step 3):
1. saved user preference  (user.preferences["encoding"])
2. detection from the telnet TERMINAL-TYPE report
3. asked at login, answer saved to preferences

HTTP/JSON surfaces are always UTF-8 and never pass through here.
"""

from __future__ import annotations

import asyncio
import re

# Terminal-type fragments mapped to a codec. First match wins; matching is
# case-insensitive substring against the reported TTYPE string.
#
# NOTE (2026-08-23): TTYPE names carry no encoding information — "xterm" is
# a capability claim, not a charset. This table is a *heuristic fallback*
# for clients that fail the active UTF-8 probe (shared.codecs.probe_utf8),
# ordered by what actually shows up on port 23: ANSI-BBS-family retro
# clients are CP437-native; Unix-emulator names are UTF-8 in practice.
_TTYPE_MAP: tuple[tuple[str, str], ...] = (
    # Syncterm & BBS-style clients are CP437-native in their ANSI-BBS mode.
    ("ansi-bbs", "cp437"),
    ("syncterm", "cp437"),
    ("banshi", "cp437"),
    # Retro/limited terminals commonly map to CP437-era expectations too.
    ("ansi", "cp437"),
    ("avatar", "cp437"),
    ("c64", "cp437"),
    # Modern emulator families report these and are UTF-8 by default.
    # (A true 1978 VT100 isn't, but anything reporting vt* today is an
    # emulator with a UTF-8 mode.)
    ("xterm", "utf-8"),
    ("vt1", "utf-8"),
    ("vt2", "utf-8"),
    ("vt3", "utf-8"),
    ("vt4", "utf-8"),
    ("vt5", "utf-8"),
    ("vte", "utf-8"),
    ("kitty", "utf-8"),
    ("alacritty", "utf-8"),
    ("rxvt", "utf-8"),
    ("screen", "utf-8"),
    ("tmux", "utf-8"),
    ("putty", "utf-8"),
    ("linux", "utf-8"),
)

VALID_CODECS = ("cp437", "utf-8", "ascii")
DEFAULT_CODEC = "cp437"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def detect_codec(terminal_type: str | None) -> str | None:
    """Map a reported TERMINAL-TYPE string to a codec.

    Returns None when nothing matches (caller should ask the user).
    """
    if not terminal_type:
        return None
    t = terminal_type.strip().lower()
    if t in ("unknown", ""):
        return None
    for fragment, codec in _TTYPE_MAP:
        if fragment in t:
            return codec
    return None


def normalize(codec: str | None) -> str:
    """Validate a user-supplied codec name; falls back to DEFAULT_CODEC."""
    if not codec:
        return DEFAULT_CODEC
    c = str(codec).strip().lower()
    return c if c in VALID_CODECS else DEFAULT_CODEC


def encode_out(text: str, encoding: str) -> bytes:
    """Encode outgoing display text for a session."""
    enc = normalize(encoding)
    try:
        return text.encode(enc, errors="replace")
    except LookupError:  # pragma: no cover - normalize() prevents this
        return text.encode(DEFAULT_CODEC, errors="replace")


def decode_in(data: bytes, encoding: str) -> str:
    """Decode incoming keystrokes/text from a session."""
    enc = normalize(encoding)
    try:
        return data.decode(enc, errors="replace")
    except LookupError:  # pragma: no cover
        return data.decode(DEFAULT_CODEC, errors="replace")


# ---------------------------------------------------------------------------
# Active UTF-8 probe (DSR cursor-position trick)
#
# TTYPE names carry NO encoding information (RFC 2066 never defined one; the
# MUD MTTS bit 4 = UTF-8 exists but only MUD clients answer it). The only
# reliable detector is behavioural: emit a known two-byte UTF-8 character,
# then ask for the cursor position via ESC[6n. A UTF-8 client renders ONE
# glyph and answers "col+1"; a byte-oriented client sees two stray bytes and
# answers "col+2". One round trip settles the question — and it works over
# SSH, where no TTYPE negotiation exists at all.
# ---------------------------------------------------------------------------

PROBE_PREFIX = "\x1b[1;1H\x1b[K"    # home cursor to col 1; clear line
PROBE_SEQUENCE = "\x1b[s\x1b[6n"   # save cursor; device status report
PROBE_CHAR = "é"                    # U+00E9: exactly 2 bytes in UTF-8
PROBE_BOX = "│"                     # U+2502: Ambiguous width (1 or 2 cells)
PROBE_CLEAR = "\x1b[u\x1b[K"        # restore cursor; erase to EOL


def _append_pushback(session, extra: bytes) -> None:
    prev = getattr(session, "_codec_pushback", b"") or b""
    setattr(session, "_codec_pushback", bytes(prev) + extra)


async def _dsr_column_after(bbs, session, char: str, timeout: float) -> int | None:
    """Write *char* at column 1 and return the DSR column, or None."""
    reader = getattr(session, "reader", None)
    writer = getattr(session, "writer", None)
    if reader is None or writer is None or writer.is_closing():
        return None

    try:
        payload = (
            PROBE_PREFIX.encode("ascii")
            + char.encode("utf-8")
            + b"\x1b[6n"
        )
        writer.write(payload)
        await writer.drain()
    except Exception:  # noqa: BLE001
        return None

    reply = bytearray()
    leftover = bytearray()
    deadline = asyncio.get_event_loop().time() + timeout

    def _parse(buf: bytes):
        m = re.search(rb"\x1b\[(\d+);(\d+)R", buf)
        if m:
            return m.start(), m.end(), int(m.group(1)), int(m.group(2))
        return None

    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            chunk = await asyncio.wait_for(reader.read(32), timeout=max(0.05, remaining))
        except Exception:  # noqa: BLE001
            break
        if not chunk:
            break
        reply += chunk
        found = _parse(bytes(reply))
        if found is not None:
            start, end, _, col = found
            leftover += reply[:start]
            leftover += reply[end:]
            if leftover:
                _append_pushback(session, bytes(leftover))
            try:
                await bbs.send_raw(session, PROBE_CLEAR.encode("ascii"))
            except Exception:  # noqa: BLE001
                pass
            return col
        tail = bytes(reply)
        cut = max(tail.rfind(b"\x1b"), 0)
        if len(tail) - cut > 16 or b"\x1b" not in tail:
            leftover += tail
            reply.clear()

    if leftover or reply:
        _append_pushback(session, bytes(leftover) + bytes(reply))
    try:
        await bbs.send_raw(session, PROBE_CLEAR.encode("ascii"))
    except Exception:  # noqa: BLE001
        pass
    return None


async def probe_utf8(bbs, session, timeout: float = 1.5) -> str | None:
    """Actively probe whether the client decodes UTF-8.

    Emits ``é`` (2 UTF-8 bytes) at column 1, then DSR. A UTF-8 client
    renders one glyph (col 2); a byte-oriented client advances two (col 3).
    Returns ``"utf-8"``, ``"cp437"``, or None. Never raises.
    """
    col = await _dsr_column_after(bbs, session, PROBE_CHAR, timeout)
    if col is None:
        return None
    return "utf-8" if col <= 2 else "cp437"


async def probe_ambiguous_width(bbs, session, timeout: float = 1.5) -> bool | None:
    """Whether box-drawing ``│`` occupies two cells, or None if DSR is silent.

    Started at column 1: col 2 = one cell, col 3 = two cells. Callers should
    treat None as two cells for UTF-8 so a mute telnet client cannot wrap.
    """
    col = await _dsr_column_after(bbs, session, PROBE_BOX, timeout)
    if col is None:
        return None
    return col >= 3


def take_pushback(session) -> bytes:
    """Pop and clear the codec probe's pushed-back input, if any."""
    buf = getattr(session, "_codec_pushback", b"")
    if buf:
        setattr(session, "_codec_pushback", b"")
    return buf
