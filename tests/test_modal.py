"""Tests for the modal plugin choose/notice and the core fallback."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from core import modal as core_modal
from core.app import BBSApp
from plugins.modal import ModalPlugin
from plugins.modal.overlay import compact_overlay_geom, paint_overlay
from server.session import Session


class FakeWriter:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf.extend(data)

    def is_closing(self):
        return False

    async def drain(self):
        pass


def _session():
    s = Session(session_id="t", node_id=1, address=("h", 1))
    s.writer = FakeWriter()  # type: ignore[attr-defined]
    s.terminal_type = "ANSI-BBS"
    s.terminal_width = 80
    s.terminal_height = 24
    return s


def _app():
    app = BBSApp()
    p = ModalPlugin()
    p.on_load(app)
    app.plugins = [p]
    return app


def test_choose_enter_returns_index():
    app = _app()
    s = _session()
    keys = iter(["DOWN", "ENTER"])

    async def _next(*_a, **_k):
        return next(keys)

    async def _run():
        with patch("core.runner.read_key", side_effect=_next):
            return await core_modal.choose(app, s, ["Post", "Editor", "Discard"])

    assert asyncio.run(_run()) == 1


def test_choose_esc_returns_none():
    app = _app()
    s = _session()

    async def _esc(*_a, **_k):
        return "ESC"

    async def _run():
        with patch("core.runner.read_key", side_effect=_esc):
            return await core_modal.choose(app, s, ["A", "B"])

    assert asyncio.run(_run()) is None


def test_fallback_choose_when_modal_missing():
    app = BBSApp()
    app.plugins = []
    s = _session()

    async def _enter(*_a, **_k):
        return "ENTER"

    async def _run():
        with patch("core.runner.read_key", side_effect=_enter):
            return await core_modal.choose(app, s, ["One", "Two"], default=0)

    assert asyncio.run(_run()) == 0
    assert b"1. One" in bytes(s.writer.buf)


def test_compact_overlay_is_small():
    from core.theme import load_palette

    s = _session()
    geom = compact_overlay_geom(s, n_rows=3, min_inner=28)
    top, L, wid, interior, inner_w = geom
    assert interior == 3
    assert wid < 50
    assert top >= 14
    assert L > 1
    pal = load_palette("classic")
    painted = paint_overlay(
        s, ["Post", "Editor", "Discard"], " arrows  Enter  ESC ", pal, geom=geom
    )
    assert painted.count("\x1b[") >= 5
    assert "\r\n" not in painted
