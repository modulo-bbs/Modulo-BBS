"""Telnet negotiator tests — TTYPE discovery (RFC 1091) and NAWS.

Regression: the server answered WILL TERMINAL-TYPE with DO but never sent
SB TERMINAL-TYPE SEND, so clients like SyncTERM never revealed their name
and every session stayed terminal_type="UNKNOWN" (plain ASCII fallback).
"""
from shared.telnet_protocol import (
    TelnetNegotiator,
    IAC, SB, SE, DO, WILL, OPT_TERMINAL_TYPE, OPT_WINDOW_SIZE,
)


def _client_will_ttype() -> bytes:
    return bytes([IAC, WILL, OPT_TERMINAL_TYPE])


def _client_is(name: str) -> bytes:
    payload = name.encode("ascii")
    return bytes([IAC, SB, OPT_TERMINAL_TYPE, 0]) + payload + bytes([IAC, SE])


def test_will_ttype_triggers_sb_send():
    neg = TelnetNegotiator()
    clean, responses = neg.process_data(_client_will_ttype())
    assert clean == b""
    # Must answer DO and follow with SB TERMINAL-TYPE SEND
    blob = b"".join(responses)
    assert bytes([IAC, DO, OPT_TERMINAL_TYPE]) in blob
    assert bytes([IAC, SB, OPT_TERMINAL_TYPE, 1, IAC, SE]) in blob


def test_ttype_is_sets_terminal_type():
    neg = TelnetNegotiator()
    neg.process_data(_client_will_ttype())
    clean, _ = neg.process_data(_client_is("ANSI-BBS"))
    assert clean == b""
    assert neg.terminal_type == "ANSI-BBS"


def test_sb_send_does_not_repeat_on_second_will():
    neg = TelnetNegotiator()
    neg.process_data(_client_will_ttype())
    # Some clients re-assert WILL; must not re-send the SEND loop
    _, responses = neg.process_data(_client_will_ttype())
    blob = b"".join(responses or [])
    assert bytes([IAC, SB, OPT_TERMINAL_TYPE, 1, IAC, SE]) not in blob


def test_full_syncterm_handshake_yields_ansi_bbs():
    """End-to-end: initial negotiation -> WILL -> SB SEND -> IS."""
    neg = TelnetNegotiator()
    # Server opens with initial_negotiation (DO TTYPE among others)
    assert bytes([IAC, DO, OPT_TERMINAL_TYPE]) in neg.initial_negotiation()
    # Client agrees
    neg.process_data(_client_will_ttype())
    # Client answers the SB SEND with IS
    neg.process_data(_client_is("ANSI-BBS"))
    assert neg.terminal_type == "ANSI-BBS"


def test_naws_still_parsed_after_ttype():
    neg = TelnetNegotiator()
    neg.process_data(_client_will_ttype())
    neg.process_data(_client_is("ANSI-BBS"))
    naws = bytes([IAC, SB, OPT_WINDOW_SIZE, 0, 80, 0, 24, IAC, SE])
    clean, _ = neg.process_data(naws)
    assert clean == b""
    assert neg.window_size == (80, 24)


def test_do_echo_not_wont_while_server_already_echoing():
    """Password-mask window WILL's ECHO; a client DO ECHO must not undo it."""
    from shared.telnet_protocol import IAC, DO, WONT, OPT_ECHO

    neg = TelnetNegotiator()
    neg.local_options[OPT_ECHO] = True
    _clean, responses = neg.process_data(bytes([IAC, DO, OPT_ECHO]))
    blob = b"".join(responses or [])
    assert bytes([IAC, WONT, OPT_ECHO]) not in blob
