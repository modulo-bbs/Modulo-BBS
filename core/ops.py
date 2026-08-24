"""The operations registry — the One-API principle (see docs/one-api.md).

Every capability the board exposes — sysop management *and* ordinary user
actions like posting a message — is declared here exactly once, with:

    name       dotted identifier, e.g. "users.list", "boards.post"
    params     {field: type} for required params; optional params live in
               ``optional`` ({field: (type, default)})
    requires   group gate; empty list = public. Evaluated with
               ``user.can_access(requires)`` so "sysop" passes everything.
    planes     where HTTP may expose it: ("mgmt",), ("public",), or both.
               Ops gated on ["sysop"] default to mgmt-only and can never be
               exposed publicly (enforced at registration + by tests).
    handler    sync or async callable ``(bbs, user, params) -> result``
               per the await-if-coroutine rule.

Surfaces are thin generic clients of this registry:

* HTTP:  POST /api/v1/<op.name> with a JSON body -> generic dispatch
         GET  /api/v1/_schema  -> self-description (per plane)
* Terminal: the sysop menu renders from the registry and calls ops.call()
* In-process: plugins/core call ops.call() directly

Nothing in this module performs I/O formatting or transport concerns.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("modulo.core.ops")

# Exposure planes. MGMT is loopback-only by deployment; PUBLIC sits behind a
# TLS proxy. A sysop-gated op is structurally absent from PUBLIC.
PLANE_MGMT = "mgmt"
PLANE_PUBLIC = "public"

_PARAM_TYPES = (str, int, float, bool)


class OpsError(Exception):
    """Base class for operation errors (mapped to HTTP 4xx)."""


class UnknownOperation(OpsError, KeyError):
    """No operation registered under this name."""


class ValidationError(OpsError, ValueError):
    """Params failed validation."""


class PermissionDeniedError(OpsError, PermissionError):
    """Caller lacks the groups this operation requires."""


@dataclass(frozen=True)
class Operation:
    """One declared capability."""

    name: str
    description: str
    params: dict[str, type]
    optional: dict[str, tuple[type, Any]]
    requires: list[str]
    planes: frozenset[str]
    handler: Callable[..., Any]

    def validate(self, supplied: dict[str, Any]) -> dict[str, Any]:
        """Return a clean params dict or raise ValidationError."""
        if not isinstance(supplied, dict):
            raise ValidationError("params must be a JSON object")
        clean: dict[str, Any] = {}
        for name, typ in self.params.items():
            if name not in supplied:
                raise ValidationError(f"missing required param: {name}")
            clean[name] = self._coerce(name, supplied[name], typ)
        for name, (typ, default) in self.optional.items():
            if name in supplied:
                clean[name] = self._coerce(name, supplied[name], typ)
            else:
                clean[name] = default
        # Reject unknown fields rather than silently ignoring them.
        unknown = set(supplied) - set(self.params) - set(self.optional)
        if unknown:
            raise ValidationError(f"unknown params: {', '.join(sorted(unknown))}")
        return clean

    def _coerce(self, name: str, value: Any, typ: type) -> Any:
        if value is None:
            raise ValidationError(f"param {name} must not be null")
        if isinstance(value, bool) and typ is not bool:
            # bool is an int subclass; don't let True pass as 1
            raise ValidationError(f"param {name} must be {typ.__name__}")
        try:
            if typ is str and isinstance(value, str):
                return value
            if typ is int and isinstance(value, int) and not isinstance(value, bool):
                return value
            if typ is float and isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            if typ is bool and isinstance(value, bool):
                return value
        except TypeError:  # pragma: no cover - defensive
            pass
        raise ValidationError(f"param {name} must be {typ.__name__}")

    def to_dict(self) -> dict[str, Any]:
        """Self-description entry for /_schema."""
        return {
            "name": self.name,
            "description": self.description,
            "params": {k: v.__name__ for k, v in self.params.items()},
            "optional": {k: {"type": t.__name__, "default": d} for k, (t, d) in self.optional.items()},
            "requires": list(self.requires),
            "planes": sorted(self.planes),
        }


def _default_planes(requires: list[str]) -> frozenset[str]:
    """Sysop-gated ops are management-plane-only by construction."""
    return frozenset({PLANE_MGMT}) if "sysop" in requires else frozenset(
        {PLANE_MGMT, PLANE_PUBLIC}
    )


class OpsRegistry:
    """Registry of named operations with validation and group gating."""

    def __init__(self) -> None:
        self._ops: dict[str, Operation] = {}

    # -- registration --------------------------------------------------------

    def register(
        self,
        name: str,
        *,
        handler: Callable[..., Any],
        description: str = "",
        params: dict[str, type] | None = None,
        optional: dict[str, tuple[type, Any]] | None = None,
        requires: list[str] | None = None,
        planes: tuple[str, ...] | None = None,
    ) -> Operation:
        """Register an operation. Raises ValueError on bad declarations.

        The plane rule is structural: any op requiring "sysop" is refused if
        the caller tries to place it on the public plane.
        """
        if not name or not all(part for part in name.split(".")):
            raise ValueError(f"operation name must be dotted non-empty: {name!r}")
        if name in self._ops:
            raise ValueError(f"operation already registered: {name}")
        reqs = [str(g).lower() for g in (requires or [])]

        if planes is None:
            chosen = _default_planes(reqs)
        else:
            chosen = frozenset(planes)
            unknown_planes = chosen - {PLANE_MGMT, PLANE_PUBLIC}
            if unknown_planes:
                raise ValueError(f"unknown planes: {sorted(unknown_planes)}")
            if "sysop" in reqs and PLANE_PUBLIC in chosen:
                # The hard invariant from docs/one-api.md.
                raise ValueError(
                    f"sysop-gated operation {name!r} cannot be exposed on the "
                    f"public plane"
                )

        for pname, ptype in {**(params or {}), **{k: v[0] for k, v in (optional or {}).items()}}.items():
            if ptype not in _PARAM_TYPES:
                raise ValueError(
                    f"param {pname} of {name} must be one of "
                    f"{[t.__name__ for t in _PARAM_TYPES]}, got {ptype}"
                )
        if not callable(handler):
            raise ValueError(f"handler for {name} is not callable")

        op = Operation(
            name=name,
            description=description or name,
            params=dict(params or {}),
            optional=dict(optional or {}),
            requires=reqs,
            planes=chosen,
            handler=handler,
        )
        self._ops[name] = op
        return op

    def unregister(self, name: str) -> None:
        self._ops.pop(name, None)

    # -- lookup ---------------------------------------------------------------

    def get(self, name: str) -> Operation | None:
        return self._ops.get(name)

    def get_or_raise(self, name: str) -> Operation:
        op = self._ops.get(name)
        if op is None:
            raise UnknownOperation(f"no such operation: {name}")
        return op

    def names(self) -> list[str]:
        return sorted(self._ops)

    def schema(self, plane: str | None = None) -> dict[str, Any]:
        """Self-description; filtered to one plane when given."""
        ops = [
            op.to_dict()
            for op in self._ops.values()
            if plane is None or plane in op.planes
        ]
        ops.sort(key=lambda o: o["name"])
        return {
            "version": 1,
            "plane": plane,
            "operations": ops,
        }

    # -- invocation -------------------------------------------------------------

    async def call(self, bbs, user: Any, name: str, supplied: dict[str, Any]) -> Any:
        """Validate + authorize + execute one operation.

        ``user`` is the authenticated core User (or None for pre-auth calls;
        only ops with empty ``requires`` accept None).
        """
        op = self.get_or_raise(name)
        clean = op.validate(supplied)

        if op.requires:
            if user is None or not user.can_access(op.requires):
                raise PermissionDeniedError(
                    f"{name} requires groups {op.requires}"
                )

        result = op.handler(bbs, user, clean)
        if inspect.isawaitable(result):
            result = await result

        # Audit trail: every mutating action announces itself on the bus.
        # Read-only listing ops stay quiet to avoid bus spam.
        if not name.split(".")[-1].startswith(("list", "get", "read")):
            bbs.events.emit(
                "ops:call",
                {
                    "op": name,
                    "user": getattr(user, "username", None),
                    "params": clean,
                },
            )
        return result


# Module-level singleton used across surfaces.
registry = OpsRegistry()


def register(*args, **kwargs) -> Operation:
    """Convenience wrapper: register on the global registry."""
    return registry.register(*args, **kwargs)
