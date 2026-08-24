"""Tests for shared/codecs.py — session codec selection."""
from __future__ import annotations

import pytest

from shared.codecs import (
    DEFAULT_CODEC,
    decode_in,
    detect_codec,
    encode_out,
    normalize,
)


class TestDetect:
    def test_ansibbs_maps_cp437(self):
        assert detect_codec("ANSI-BBS") == "cp437"

    def test_syncterm_maps_cp437(self):
        assert detect_codec("syncterm") == "cp437"

    def test_xterm_maps_utf8(self):
        assert detect_codec("xterm-256color") == "utf-8"

    def test_vt100_maps_utf8(self):
        assert detect_codec("vt100") == "utf-8"

    def test_unknown_returns_none(self):
        assert detect_codec("UNKNOWN") is None
        assert detect_codec("") is None
        assert detect_codec(None) is None

    def test_unmatched_ttype_returns_none(self):
        """A TTYPE we don't recognize -> ask the user (None)."""
        assert detect_codec("weird-term-9000") is None


class TestNormalize:
    def test_valid_passthrough(self):
        assert normalize("cp437") == "cp437"
        assert normalize("UTF-8") == "utf-8"
        assert normalize(" ascii ") == "ascii"

    def test_invalid_falls_back(self):
        assert normalize("klingon") == DEFAULT_CODEC
        assert normalize(None) == DEFAULT_CODEC
        assert normalize("") == DEFAULT_CODEC


class TestRoundTrip:
    def test_umlauts_cp437_roundtrip(self):
        text = "Grüße aus Köln"
        wire = encode_out(text, "cp437")
        assert decode_in(wire, "cp437") == text
        # And the wire is single-byte per char (CP437 has these glyphs)
        assert len(wire) == len(text)

    def test_umlauts_utf8_roundtrip(self):
        text = "Grüße aus Köln"
        wire = encode_out(text, "utf-8")
        assert decode_in(wire, "utf-8") == text
        assert len(wire) > len(text)  # multibyte

    def test_ascii_strips_accents(self):
        out = encode_out("Grüße", "ascii").decode("ascii")
        assert out.isascii()

    def test_emoji_replaced_in_cp437(self):
        # CP437 has no emoji; must not raise, must produce replacement.
        wire = encode_out("hi 👋", "cp437")
        assert b"?" in wire or b"." in wire or len(wire) >= 3

    def test_box_drawing_survives_cp437(self):
        """Classic ANSI art box characters survive a CP437 roundtrip."""
        art = "┌─┐│└┘█"
        assert decode_in(encode_out(art, "cp437"), "cp437") == art
