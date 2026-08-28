"""Tests for the active UTF-8 probe (shared.codecs.probe_utf8)."""
from __future__ import annotations

import asyncio

from shared.codecs import PROBE_BOX, PROBE_CHAR, PROBE_CLEAR, probe_ambiguous_width, probe_utf8


class FakeWriter:
    def __init__(self):
        self.buffer = bytearray()
        self._closed = False

    def write(self, data):
        self.buffer.extend(data)

    async def drain(self):
        pass

    def is_closing(self):
        return self._closed


class FakeReader:
    """Feeds scripted chunks to reader.read()."""

    def __init__(self, chunks, delay=0.0):
        self.chunks = list(chunks)
        self.delay = delay

    async def read(self, n):
        if not self.chunks:
            await asyncio.sleep(3600)  # simulate silence until timeout
        return self.chunks.pop(0)


class FakeBBS:
    async def send_raw(self, session, data):
        session.writer.buffer.extend(data)


def make_session(reply_chunks):
    from server.session import Session

    s = Session(session_id="t", node_id=1, address=("127.0.0.1", 1))
    s.writer = FakeWriter()
    s.reader = FakeReader(reply_chunks)
    return s


def run(coro):
    return asyncio.run(coro)


# A UTF-8 client renders é as one glyph: cursor advances 1 column.
UTF8_REPLY = b"\x1b[1;2R"
# A byte-oriented (CP437) client sees 2 stray bytes: cursor advances 2.
CP437_REPLY = b"\x1b[1;3R"


def test_utf8_client_detected():
    async def scenario():
        s = make_session([UTF8_REPLY])
        result = await probe_utf8(FakeBBS(), s, timeout=2)
        # Probe went out raw as UTF-8 bytes
        sent = bytes(s.writer.buffer)
        assert PROBE_CHAR.encode("utf-8") in sent
        # Cleanup sequence was emitted after answer
        assert PROBE_CLEAR.encode("ascii") in sent
        return result

    assert run(scenario()) == "utf-8"


def test_cp437_client_detected():
    async def scenario():
        s = make_session([CP437_REPLY])
        return await probe_utf8(FakeBBS(), s, timeout=2)

    assert run(scenario()) == "cp437"


def test_silent_client_returns_none():
    async def scenario():
        s = make_session([])   # never answers DSR
        return await probe_utf8(FakeBBS(), s, timeout=0.3)

    assert run(scenario()) is None


def test_reply_split_across_chunks():
    """DSR reply arriving in pieces must still parse."""
    async def scenario():
        s = make_session([b"\x1b[1;", b"2R"])
        return await probe_utf8(FakeBBS(), s, timeout=2)

    assert run(scenario()) == "utf-8"


def test_box_drawing_one_cell():
    async def scenario():
        s = make_session([b"\x1b[1;2R"])
        result = await probe_ambiguous_width(FakeBBS(), s, timeout=2)
        sent = bytes(s.writer.buffer)
        assert PROBE_BOX.encode("utf-8") in sent
        return result

    assert run(scenario()) is False


def test_box_drawing_two_cells():
    async def scenario():
        s = make_session([b"\x1b[1;3R"])
        return await probe_ambiguous_width(FakeBBS(), s, timeout=2)

    assert run(scenario()) is True


def test_box_drawing_silent_dsr_returns_none():
    async def scenario():
        s = make_session([])
        return await probe_ambiguous_width(FakeBBS(), s, timeout=0.3)

    assert run(scenario()) is None
