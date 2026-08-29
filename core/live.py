"""Push live Social updates to idle sessions.

A 1s poll-and-clear made SyncTERM flash. Instead, conversation mutations
set an ``asyncio.Event`` on every session that is sitting on Social; the
input wait returns :data:`WAKE` and that session paints once.
"""
from __future__ import annotations

import asyncio

WAKE = "__WAKE__"


def arm(bbs, session) -> asyncio.Event:
    """This session is idle on Social and should hear live posts."""
    waiters = getattr(bbs, "_live_waiters", None)
    if waiters is None:
        bbs._live_waiters = {}
        waiters = bbs._live_waiters
    waiters[id(session)] = session
    ev = getattr(session, "_live_wake", None)
    if ev is None:
        ev = asyncio.Event()
        session._live_wake = ev
    return ev


def disarm(bbs, session) -> None:
    waiters = getattr(bbs, "_live_waiters", None)
    if waiters:
        waiters.pop(id(session), None)
    ev = getattr(session, "_live_wake", None)
    if ev is not None:
        ev.clear()


def wake(bbs) -> None:
    """Wake every Social waiter. No-op when nobody is watching."""
    waiters = getattr(bbs, "_live_waiters", None) or {}
    for s in list(waiters.values()):
        ev = getattr(s, "_live_wake", None)
        if ev is not None:
            ev.set()
