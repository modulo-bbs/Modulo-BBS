"""Tests for the operations registry (core/ops.py)."""
from __future__ import annotations

import asyncio

import pytest

from core.events import EventBus
from core.ops import (
    PLANE_MGMT,
    PLANE_PUBLIC,
    OpsRegistry,
    PermissionDeniedError,
    UnknownOperation,
    ValidationError,
)
from core.user import User


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_user(groups=None, name="tester"):
    return User(username=name, display_name=name, password_hash="x", groups=groups or ["user"])


class FakeBBS:
    def __init__(self):
        self.events = EventBus()
        self.notes = []


@pytest.fixture
def reg():
    return OpsRegistry()


@pytest.fixture
def bbs():
    return FakeBBS()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_basic_registration(self, reg):
        op = reg.register("ping", handler=lambda b, u, p: "pong")
        assert op.name == "ping"
        assert reg.get("ping") is op

    def test_duplicate_rejected(self, reg):
        reg.register("ping", handler=lambda b, u, p: 1)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("ping", handler=lambda b, u, p: 2)

    def test_bad_name_rejected(self, reg):
        with pytest.raises(ValueError):
            reg.register("", handler=lambda b, u, p: 1)
        with pytest.raises(ValueError):
            reg.register("no..dots", handler=lambda b, u, p: 1)

    def test_uncallable_handler_rejected(self, reg):
        with pytest.raises(ValueError, match="callable"):
            reg.register("bad", handler="not-callable")

    def test_unknown_param_type_rejected(self, reg):
        with pytest.raises(ValueError, match="param"):
            reg.register("bad", params={"x": dict}, handler=lambda b, u, p: 1)

    def test_names_sorted(self, reg):
        reg.register("b.op", handler=lambda b, u, p: 1)
        reg.register("a.op", handler=lambda b, u, p: 1)
        assert reg.names() == ["a.op", "b.op"]


# ---------------------------------------------------------------------------
# Plane rules
# ---------------------------------------------------------------------------

class TestPlanes:
    def test_sysop_defaults_to_mgmt_only(self, reg):
        op = reg.register("users.delete", requires=["sysop"], handler=lambda b, u, p: 1)
        assert op.planes == frozenset({PLANE_MGMT})
        assert PLANE_PUBLIC not in op.planes

    def test_public_default_for_non_sysop(self, reg):
        op = reg.register("boards.post", requires=["member"], handler=lambda b, u, p: 1)
        assert PLANE_PUBLIC in op.planes
        assert PLANE_MGMT in op.planes

    def test_explicit_planes_respected(self, reg):
        op = reg.register(
            "boards.list",
            requires=[],
            planes=(PLANE_MGMT,),
            handler=lambda b, u, p: 1,
        )
        assert op.planes == frozenset({PLANE_MGMT})

    def test_sysop_on_public_refused(self, reg):
        """The hard invariant from docs/one-api.md."""
        with pytest.raises(ValueError, match="public plane"):
            reg.register(
                "users.nuke",
                requires=["sysop"],
                planes=(PLANE_MGMT, PLANE_PUBLIC),
                handler=lambda b, u, p: 1,
            )

    def test_unknown_plane_refused(self, reg):
        with pytest.raises(ValueError, match="unknown planes"):
            reg.register("x.y", requires=[], planes=("galaxy",), handler=lambda b, u, p: 1)


# ---------------------------------------------------------------------------
# Param validation
# ---------------------------------------------------------------------------

class TestValidation:
    def make_op(self, reg):
        return reg.register(
            "test.thing",
            params={"board": str, "id": int},
            optional={"flag": (bool, False), "limit": (int, 10)},
            handler=lambda b, u, p: p,
        )

    def test_valid_params_pass(self, reg):
        self.make_op(reg)
        clean = asyncio.run(reg.call(FakeBBS(), None, "test.thing", {"board": "general", "id": 3}))
        assert clean == {"board": "general", "id": 3, "flag": False, "limit": 10}

    def test_missing_required_rejected(self, reg):
        self.make_op(reg)
        with pytest.raises(ValidationError, match="missing required"):
            asyncio.run(reg.call(FakeBBS(), None, "test.thing", {"board": "general"}))

    def test_wrong_type_rejected(self, reg):
        self.make_op(reg)
        with pytest.raises(ValidationError, match="must be"):
            asyncio.run(reg.call(FakeBBS(), None, "test.thing", {"board": "general", "id": "three"}))

    def test_bool_not_accepted_as_int(self, reg):
        self.make_op(reg)
        with pytest.raises(ValidationError):
            asyncio.run(reg.call(FakeBBS(), None, "test.thing", {"board": "g", "id": True}))

    def test_null_rejected(self, reg):
        self.make_op(reg)
        with pytest.raises(ValidationError, match="null"):
            asyncio.run(reg.call(FakeBBS(), None, "test.thing", {"board": None, "id": 1}))

    def test_optional_defaults_applied(self, reg):
        self.make_op(reg)
        clean = asyncio.run(reg.call(FakeBBS(), None, "test.thing", {"board": "g", "id": 1}))
        assert clean["flag"] is False and clean["limit"] == 10

    def test_unknown_param_rejected(self, reg):
        self.make_op(reg)
        with pytest.raises(ValidationError, match="unknown"):
            asyncio.run(
                reg.call(FakeBBS(), None, "test.thing", {"board": "g", "id": 1, "evil": "x"})
            )

    def test_float_accepts_int(self, reg):
        reg.register("f.x", params={"n": float}, handler=lambda b, u, p: p["n"])
        got = asyncio.run(reg.call(FakeBBS(), None, "f.x", {"n": 5}))
        assert isinstance(got, float) and got == 5.0


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

class TestAuthorization:
    def test_missing_user_denied_on_gated_op(self, reg, bbs):
        reg.register("admin.only", requires=["sysop"], handler=lambda b, u, p: "ok")
        with pytest.raises(PermissionDeniedError):
            asyncio.run(reg.call(bbs, None, "admin.only", {}))

    def test_wrong_group_denied(self, reg, bbs):
        reg.register("mod.only", requires=["moderator"], handler=lambda b, u, p: "ok")
        with pytest.raises(PermissionDeniedError):
            asyncio.run(reg.call(bbs, make_user(["user"]), "mod.only", {}))

    def test_right_group_allowed(self, reg, bbs):
        reg.register("mod.only", requires=["moderator"], handler=lambda b, u, p: "ok")
        got = asyncio.run(reg.call(bbs, make_user(["user", "moderator"]), "mod.only", {}))
        assert got == "ok"

    def test_sysop_passes_any_gate(self, reg, bbs):
        reg.register("vet.only", requires=["veterans"], handler=lambda b, u, p: "ok")
        got = asyncio.run(reg.call(bbs, make_user(["sysop"]), "vet.only", {}))
        assert got == "ok"

    def test_open_op_allows_anonymous(self, reg, bbs):
        reg.register("public.thing", requires=[], handler=lambda b, u, p: 42)
        got = asyncio.run(reg.call(bbs, None, "public.thing", {}))
        assert got == 42


# ---------------------------------------------------------------------------
# Handler contract + audit events
# ---------------------------------------------------------------------------

class TestHandlersAndAudit:
    def test_sync_and_async_handlers_both_work(self, reg, bbs):
        async def async_h(bbs, user, params):
            return "async"

        def sync_h(bbs, user, params):
            return "sync"

        reg.register("h.async", handler=async_h)
        reg.register("h.sync", handler=sync_h)
        assert asyncio.run(reg.call(bbs, None, "h.async", {})) == "async"
        assert asyncio.run(reg.call(bbs, None, "h.sync", {})) == "sync"

    def test_unknown_operation_raises(self, reg, bbs):
        with pytest.raises(UnknownOperation):
            asyncio.run(reg.call(bbs, None, "nope.nothing", {}))

    def test_mutating_ops_emit_audit_event(self, reg, bbs):
        async def scenario():
            seen = []
            bbs.events.on("ops:call", lambda data: seen.append(data))
            reg.register("users.create", params={"username": str}, handler=lambda b, u, p: 1)
            await reg.call(bbs, make_user(["sysop"], name="dave"), "users.create", {"username": "bob"})
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            return seen

        seen = asyncio.run(scenario())
        assert len(seen) == 1
        assert seen[0]["op"] == "users.create"
        assert seen[0]["user"] == "dave"

    def test_read_ops_stay_quiet(self, reg, bbs):
        async def scenario():
            seen = []
            bbs.events.on("ops:call", lambda data: seen.append(data))
            reg.register("users.list", handler=lambda b, u, p: [])
            await reg.call(bbs, make_user(["sysop"]), "users.list", {})
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            return seen

        seen = asyncio.run(scenario())
        assert seen == []


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_schema_lists_operations(self, reg):
        reg.register("users.list", description="List users", handler=lambda b, u, p: 1)
        reg.register("boards.post", params={"board": str}, handler=lambda b, u, p: 1)
        schema = reg.schema()
        names = [o["name"] for o in schema["operations"]]
        assert "users.list" in names and "boards.post" in names

    def test_schema_filters_by_plane(self, reg):
        reg.register("users.kick", requires=["sysop"], handler=lambda b, u, p: 1)
        reg.register("boards.post", requires=[], handler=lambda b, u, p: 1)

        pub = reg.schema(plane=PLANE_PUBLIC)
        pub_names = [o["name"] for o in pub["operations"]]
        assert "boards.post" in pub_names
        assert "users.kick" not in pub_names  # invisible, not just denied

        mgmt = reg.schema(plane=PLANE_MGMT)
        mgmt_names = [o["name"] for o in mgmt["operations"]]
        assert "users.kick" in mgmt_names
        assert "boards.post" in mgmt_names

    def test_schema_entry_shape(self, reg):
        reg.register(
            "doors.edit",
            params={"door_id": str},
            optional={"enabled": (bool, True)},
            requires=["sysop"],
            handler=lambda b, u, p: 1,
        )
        entry = reg.schema(plane=PLANE_MGMT)["operations"][0]
        assert entry["params"] == {"door_id": "str"}
        assert entry["optional"]["enabled"]["default"] is True
        assert entry["requires"] == ["sysop"]
