"""Actor context and the unscoped-access escape hatch — TODO 0.3.3 / SEC 2.2.

Design note (read before changing anything here):

Multi-tenant leakage is the single most likely real vulnerability in this system
(SEC 1.3). Defending it with developer discipline alone does not work: one
forgotten `.filter()` in one view leaks an entire dojo. So the default is
refusal — a tenant-scoped queryset that was never given an actor raises when it
is evaluated, rather than quietly returning every row in the table.

Evaluation-time rather than construction-time enforcement is deliberate. Django
builds querysets internally all over the place (admin, related descriptors,
serialisation); raising in `get_queryset()` breaks the framework. Raising in
`_fetch_all()` only affects code that actually tries to read rows.

Legitimate unscoped access (migrations, management commands, fixtures, the
permission tests themselves) goes through `allow_unscoped()`, which is greppable
and explicit.
"""

from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field
from uuid import UUID

_unscoped_depth = threading.local()
_current_actor: contextvars.ContextVar[Actor | None] = contextvars.ContextVar(
    "bokydojo_current_actor", default=None
)


class UnscopedAccessError(RuntimeError):
    """A tenant-scoped queryset was evaluated without an actor."""


@dataclass(frozen=True)
class Actor:
    """Everything the scoping and permission layers need about the caller.

    Built once per request from the authenticated user (see identity.actors).
    Never constructed from client-supplied data — that is the whole point.
    """

    user_id: UUID | None
    person_id: UUID | None
    organization_id: UUID | None
    #: Dojos this actor is limited to. ``None`` means "every dojo in the org".
    dojo_ids: frozenset[UUID] | None = None
    #: ``{(role, scope_type, scope_id)}`` — see identity.models.RoleAssignment.
    roles: frozenset[tuple[str, str, UUID | None]] = field(default_factory=frozenset)
    is_system: bool = False

    @property
    def is_org_wide(self) -> bool:
        return self.dojo_ids is None

    @property
    def is_anonymous(self) -> bool:
        return self.person_id is None and not self.is_system

    def has_role(self, role: str, *, dojo_id: UUID | None = None) -> bool:
        for held_role, scope_type, scope_id in self.roles:
            if held_role != role:
                continue
            if scope_type == "org":
                return True
            if dojo_id is None or scope_id == dojo_id:
                return True
        return False

    def role_names(self) -> set[str]:
        return {role for role, _, _ in self.roles}

    @classmethod
    def system(cls) -> Actor:
        """For background jobs and management commands acting without a user.

        Still explicit — a system actor is a deliberate choice at the call site,
        not an accident.
        """
        return cls(
            user_id=None,
            person_id=None,
            organization_id=None,
            is_system=True,
        )


class allow_unscoped:  # noqa: N801 - used as a context manager, reads better lowercase
    """Permit unscoped queryset evaluation within this block.

    Every use must be justified: migrations, fixtures, management commands that
    legitimately operate across tenants, and tests. Application request paths
    must never use it — the lint check in tests/test_scoping_guard.py enforces
    that.
    """

    def __init__(self, reason: str):
        if not reason:
            raise ValueError("allow_unscoped() requires a reason")
        self.reason = reason

    def __enter__(self) -> allow_unscoped:
        _unscoped_depth.value = getattr(_unscoped_depth, "value", 0) + 1
        return self

    def __exit__(self, *exc_info) -> None:
        _unscoped_depth.value = getattr(_unscoped_depth, "value", 1) - 1


def unscoped_access_permitted() -> bool:
    return getattr(_unscoped_depth, "value", 0) > 0


def set_current_actor(actor: Actor | None):
    """Stash the request actor. Convenience only — never a substitute for passing
    the actor explicitly into service functions."""
    return _current_actor.set(actor)


def get_current_actor() -> Actor | None:
    return _current_actor.get()


def reset_current_actor(token) -> None:
    _current_actor.reset(token)
