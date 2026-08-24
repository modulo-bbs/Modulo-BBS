"""Tests for the Modulo BBS logon sequencer.

Covers the ``logon`` plugin contract (metadata, config-driven sequence),
step ordering, ``logon:step`` event emission, graceful handling of missing
plugin/screen steps, and the core bootstrap hook's behaviour when the
configured ``logon_plugin`` is missing (send a minimal notice, close
cleanly -- never hang).

Async flows are driven synchronously via ``asyncio.run``; emitted handler
tasks are given a short sleep to run, matching the pattern used elsewhere in
the suite.
"""

import asyncio

from core.app import BBSApp
from core.runner import run_bootstrap
from plugins.base import Plugin
from plugins.logon import LogonPlugin
from server.session import Session, SessionState


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeWriter:
    """Minimal stream writer capturing everything written to the wire."""

    def __init__(self):
        self.buffer = bytearray()

    def write(self, data):
        self.buffer.extend(data)

    async def drain(self):
        pass

    def is_closing(self):
        return False


class ScriptReader:
    """Session reader returning a scripted sequence of byte chunks/EOF."""

    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])

    async def read(self, n=1024):
        return self.chunks.pop(0) if self.chunks else b""

    async def readline(self):
        return self.chunks.pop(0) if self.chunks else b""


def make_session(chunks=None):
    return Session(
        session_id="test1", node_id=1,
        address=("127.0.0.1", 0),
        reader=ScriptReader(chunks or []),
        writer=FakeWriter(),
    )


def run(coro):
    return asyncio.run(coro)


def run_emitting(coro):
    """Run ``coro``, then let fire-and-forget event handlers finish."""

    async def _runner():
        result = await coro
        await asyncio.sleep(0.05)
        return result

    return asyncio.run(_runner())


def make_app(tmp_path):
    """Bare BBSApp bound to a throwaway users dir."""
    return BBSApp(users_dir=tmp_path / "users")


def make_logon(app, tmp_path, sequence):
    """Build a loaded LogonPlugin wired to config + a throwaway screens dir.

    Points the core screen service's logon dir at the throwaway location so
    screen steps read from tmp (not the project-root screens/).
    """
    screens = tmp_path / "screens"
    screens.mkdir(exist_ok=True)
    (screens / "a.txt").write_bytes(b"AAA\r\n")
    (screens / "b.txt").write_bytes(b"BBB\r\n")
    app.config["logon_sequence"] = list(sequence)
    logon = LogonPlugin()
    logon.on_load(app)
    logon.screens_dir = screens
    if getattr(app, "screens", None) is not None:
        app.screens.plugin_screens_dir = lambda plugin: screens
    return logon


# ---------------------------------------------------------------------------
# Plugin contract
# ---------------------------------------------------------------------------

def test_logon_plugin_is_a_plugin_and_has_metadata(tmp_path):
    p = LogonPlugin()
    assert isinstance(p, LogonPlugin)
    assert isinstance(p, Plugin)
    assert p.name == "logon"
    assert p.version == "1.0.0"
    assert p.description
    assert isinstance(p.menu_order, int)
    # The sequencer never appears as a hotkey menu item.
    assert p.menu_key == ""


def test_on_load_stores_bbs_reference(tmp_path):
    app = make_app(tmp_path)
    p = LogonPlugin()
    assert p.bbs is None
    p.on_load(app)
    assert p.bbs is app


def test_default_sequence_when_config_missing(tmp_path):
    app = make_app(tmp_path)
    p = LogonPlugin()
    p.on_load(app)
    # No logon_sequence configured -> ship the default splash/login/menu flow.
    assert p._sequence() == [
        "screen:splash.txt",
        "plugin:login",
        "screen:welcome.txt",
        "plugin:mainmenu",
    ]


# ---------------------------------------------------------------------------
# Sequencer behaviour
# ---------------------------------------------------------------------------

def test_sequencer_runs_steps_in_order_and_emits_events(tmp_path):
    app = make_app(tmp_path)
    calls = []

    class StepA(Plugin):
        name = "stepa"
        async def on_session_start(self, session):
            calls.append("stepa")
            return True

    class StepB(Plugin):
        name = "stepb"
        async def on_session_start(self, session):
            calls.append("stepb")
            session.authenticated = True
            return True

    logon = make_logon(app, tmp_path,
                       ["screen:a.txt", "plugin:stepa", "screen:b.txt", "plugin:stepb"])
    app.plugins = [logon, StepA(), StepB()]

    hits = []

    async def handler(data):
        hits.append(data)

    app.events.on("logon:step", handler)

    s = make_session()
    run_emitting(logon.on_session_start(s))

    # Steps executed in order: screen, plugin, screen, plugin.
    assert calls == ["stepa", "stepb"]

    steps = [h["step"] for h in hits]
    assert steps == ["screen:a.txt", "plugin:stepa", "screen:b.txt", "plugin:stepb"]

    # Every event carries the session and a per-step result.
    for h in hits:
        assert h["session"] is s
        assert "result" in h
    assert hits[0]["step"] == "screen:a.txt" and hits[0]["result"] == "displayed"
    assert hits[1]["result"] is True          # plugin stepa returned True
    assert hits[2]["result"] == "displayed"


def test_sequencer_missing_plugin_step_is_graceful(tmp_path):
    app = make_app(tmp_path)
    logon = make_logon(app, tmp_path, ["plugin:does_not_exist"])
    app.plugins = [logon]

    hits = []

    async def handler(data):
        hits.append(data)

    app.events.on("logon:step", handler)

    s = make_session()
    run_emitting(logon.on_session_start(s))

    assert len(hits) == 1
    assert hits[0]["step"] == "plugin:does_not_exist"
    assert hits[0]["result"] == "missing"


def test_sequencer_missing_screen_step_is_graceful(tmp_path):
    app = make_app(tmp_path)
    logon = make_logon(app, tmp_path, ["screen:no_such.txt"])
    app.plugins = [logon]

    hits = []

    async def handler(data):
        hits.append(data)

    app.events.on("logon:step", handler)

    s = make_session()
    run_emitting(logon.on_session_start(s))

    assert len(hits) == 1
    assert hits[0]["step"] == "screen:no_such.txt"
    # The screen service renders a visible "[missing screen: …]" placeholder
    # for missing files (fail-obvious, not fail-silent); the step still runs.
    assert hits[0]["result"] == "displayed"


def test_sequencer_preserves_crlf_in_screen_bytes(tmp_path):
    app = make_app(tmp_path)
    logon = make_logon(app, tmp_path, ["screen:a.txt"])
    app.plugins = [logon]

    s = make_session()
    run(logon.on_session_start(s))

    out = bytes(s.writer.buffer)
    assert b"\r\n" in out          # CRLF survives reading/decoding screens


def test_aborted_auth_step_ends_sequence(tmp_path):
    app = make_app(tmp_path)

    class Failing(Plugin):
        name = "failing"
        async def on_session_start(self, session):
            return False            # caller declined / not authenticated

    logon = make_logon(app, tmp_path, ["plugin:failing", "plugin:failing"])
    app.plugins = [logon, Failing()]

    s = make_session()
    run(logon.on_session_start(s))

    # The first falsy non-authenticated step stops the sequence and requests
    # a disconnect via the core primitive (state -> DISCONNECTED).
    assert s.state is SessionState.DISCONNECTED
    assert b"Goodbye! Thanks for calling." in bytes(s.writer.buffer)


# ---------------------------------------------------------------------------
# Bootstrap hook fallback
# ---------------------------------------------------------------------------

def test_bootstrap_missing_logon_plugin_notices_and_closes(tmp_path):
    app = make_app(tmp_path)
    app.plugins = []                          # nothing loaded
    app.config["logon_plugin"] = "no_such_plugin"

    s = make_session()
    run(run_bootstrap(app, s))

    assert b"System unavailable." in bytes(s.writer.buffer)
    assert s.state is SessionState.DISCONNECTED


def test_bootstrap_default_logon_plugin(tmp_path):
    # With no config, run_bootstrap defaults to the "logon" plugin.
    app = make_app(tmp_path)
    logon = make_logon(app, tmp_path, ["screen:a.txt"])
    app.plugins = [logon]

    s = make_session()
    run(run_bootstrap(app, s))

    assert b"AAA\r\n" in bytes(s.writer.buffer)
    assert s.state is SessionState.DISCONNECTED   # sequence ended -> closed


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))