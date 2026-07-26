"""Building an Actor from an authenticated user — TODO 0.5.7.

The Actor is derived server-side from the session, never from client input.
This is the property the AI integration later depends on (SEC §5.3): scope is
not a parameter anyone — or any model — can supply.
"""

from __future__ import annotations

from apps.core.scoping import Actor, allow_unscoped

from .models import Role, RoleAssignment, ScopeType

#: Roles that see the whole organisation rather than a list of dojos.
ORG_WIDE_ROLES = {Role.ORG_ADMIN}


def actor_for_user(user) -> Actor:
    """Resolve a user's organisation, dojo scope and roles in one query."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Actor(user_id=None, person_id=None, organization_id=None)

    if not getattr(user, "is_active", False):
        return Actor(user_id=getattr(user, "pk", None), person_id=None, organization_id=None)

    person = getattr(user, "person", None)

    # ⚠ Removing someone must revoke their access, not merely hide them from
    # lists. Without this a soft-deleted or suspended instructor keeps a live
    # session and full scope until it happens to expire. Found in adversarial
    # review — see tests/test_review_findings.py.
    if person is not None and (
        getattr(person, "deleted_at", None) is not None
        or not getattr(person, "is_active", True)
    ):
        return Actor(user_id=user.pk, person_id=None, organization_id=None)

    if person is None:
        # A user without a Person (e.g. a bare superuser) gets no tenant scope.
        return Actor(
            user_id=user.pk,
            person_id=None,
            organization_id=None,
        )

    with allow_unscoped("building the actor's own scope from their role assignments"):
        assignments = list(
            RoleAssignment.objects.unscoped("actor construction")
            .filter(person_id=person.pk, revoked_at__isnull=True)
            .values_list("role", "scope_type", "dojo_id")
        )

    roles = frozenset(
        (role, scope_type, dojo_id) for role, scope_type, dojo_id in assignments
    )

    org_wide = any(
        scope_type == ScopeType.ORG for _role, scope_type, _dojo in assignments
    )
    if org_wide:
        dojo_ids = None
    else:
        dojo_ids = frozenset(
            dojo_id for _role, _scope, dojo_id in assignments if dojo_id is not None
        )

    return Actor(
        user_id=user.pk,
        person_id=person.pk,
        organization_id=person.organization_id,
        dojo_ids=dojo_ids,
        roles=roles,
    )
