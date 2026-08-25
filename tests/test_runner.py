"""B0 — lone-ESC detection in ``core/runner.read_key`` (boards-unification §B0).

A bare ESC keypress must surface as the key ``"ESC"`` instead of blocking
forever as an incomplete arrow sequence. Arrow bursts arriving as one chunk
are unaffected; arrow sequences split across chunks still resolve.
"""
from __future__ import annotations

import asyncio

import pytest

from core import runner
from server.session import Session


class RecordingBBS:
    """Minimal bbs stub: records outbound text so we can assert no idle
    notice fires during the escape window."""

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, session, text):
        self.sent.append(text)

    async def send_raw(self, session, data):
        pass


def _session(reader: asyncio.StreamReader) -> Session:
    s = Session(session_id="t", node_id=1, address=("h", 1))
    s.reader = reader  # type: ignore[attr-defined]
    return s


def _reader() -> asyncio.StreamReader:
    return asyncio.StreamReader()


@pytest.fixture(autouse=True)
def fast_window(monkeypatch):
    # Shrink the escape window so tests stay fast but margins are safe.
    monkeypatch.setattr(runner, "ESC_KEY_WINDOW", 0.05)


def test_lone_esc_returns_esc():
    async def _a():
        r = _reader()
        r.feed_data("\x1b".encode("cp437"))
        r.feed_eof()
        bbs, s = RecordingBBS(), _session(r)
        assert await runner.read_key(bbs, s) == "ESC"
        # The escape window must NOT trip the idle-timeout machinery.
        assert not any("Idle timeout" in t for t in bbs.sent)

    asyncio.run(_a())


def test_double_esc_yields_two_esc_keys():
    async def _a():
        r = _reader()

        async def _feed_later():
            await asyncio.sleep(0.01)
            r.feed_data("\x1b".encode("cp437"))
            await asyncio.sleep(0.01)
            r.feed_eof()

        task = asyncio.ensure_future(_feed_later())
        r.feed_data("\x1b".encode("cp437"))
        s = _session(r)
        assert await runner.read_key(RecordingBBS(), s) == "ESC"
        assert await runner.read_key(RecordingBBS(), s) == "ESC"
        # EOF contract: once input is drained and closed, reads end.
        assert await runner.read_key(RecordingBBS(), s) is None
        await task

    asyncio.run(_a())


def test_arrow_burst_single_chunk_unaffected():
    async def _a():
        r = _reader()
        r.feed_data("\x1b[A".encode("cp437"))
        r.feed_eof()
        s = _session(r)
        # Resolves immediately -- well under one escape window, no ESC leak.
        assert await asyncio.wait_for(
            runner.read_key(RecordingBBS(), s), timeout=runner.ESC_KEY_WINDOW / 2
        ) == "UP"

    asyncio.run(_a())


def test_split_arrow_chunks_still_resolve():
    async def _a():
        r = _reader()

        async def _feed_later():
            await asyncio.sleep(0.01)  # well inside the escape window
            r.feed_data("A".encode("cp437"))

        task = asyncio.ensure_future(_feed_later())
        r.feed_data("\x1b[".encode("cp437"))
        s = _session(r)
        assert await runner.read_key(RecordingBBS(), s) == "UP"
        await task

    asyncio.run(_a())


def test_esc_then_letter_keeps_stash_intact():
    async def _a():
        r = _reader()

        async def _feed_later():
            await asyncio.sleep(0.12)  # beyond the escape window
            r.feed_data("q".encode("cp437"))

        task = asyncio.ensure_future(_feed_later())
        r.feed_data("\x1b".encode("cp437"))
        s = _session(r)
        assert await runner.read_key(RecordingBBS(), s) == "ESC"
        assert await runner.read_key(RecordingBBS(), s) == "Q"
        await task

    asyncio.run(_a())
