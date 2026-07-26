"""Scoped querysets and managers — TODO 0.3.3 / SEC 2.2.

Usage in application code is always:

    Student.objects.for_actor(actor).filter(...)

Never:

    Student.objects.filter(...)          # raises UnscopedAccessError on evaluation

See apps/core/scoping.py for why enforcement happens at evaluation time.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from .scoping import Actor, UnscopedAccessError, unscoped_access_permitted


class ScopedQuerySet(models.QuerySet):
    """A queryset that refuses to be read until it has been scoped to an actor."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scope_applied = False
        self._scope_actor: Actor | None = None

    def _clone(self, *args, **kwargs):
        clone = super()._clone(*args, **kwargs)
        clone._scope_applied = self._scope_applied
        clone._scope_actor = self._scope_actor
        return clone

    # -- scoping entry points -------------------------------------------------

    def for_actor(self, actor: Actor) -> ScopedQuerySet:
        """Restrict to rows this actor may see, and mark the queryset readable."""
        if actor is None:
            raise UnscopedAccessError(
                f"{self.model.__name__}.objects.for_actor() called with actor=None"
            )

        if actor.is_system:
            clone = self._clone()
            clone._scope_applied = True
            clone._scope_actor = actor
            return clone

        if actor.organization_id is None:
            # An authenticated user with no organisation sees nothing.
            clone = self.none()
            clone._scope_applied = True
            clone._scope_actor = actor
            return clone

        clone = self.filter(self.model.tenant_scope_q(actor))
        clone._scope_applied = True
        clone._scope_actor = actor
        return clone

    def for_organization(self, organization_id) -> ScopedQuerySet:
        """Scope to one organisation without an actor.

        For subject-driven reads that legitimately have no logged-in user — the
        check-in kiosk resolving a student's PIN policy, a background job acting
        on one tenant. This is a *scoping* entry point, not an escape hatch: the
        tenant filter is still applied and cannot be forgotten.

        ``organization_id`` must always be server-derived (from a device token,
        a job argument, a subject record). Passing a client-supplied value here
        is a tenant bypass.
        """
        if organization_id is None:
            raise UnscopedAccessError(
                f"{self.model.__name__}.for_organization() called with None"
            )
        clone = self.filter(**{self.model.tenant_org_path: organization_id})
        clone._scope_applied = True
        clone._scope_actor = None
        return clone

    def unscoped(self, reason: str) -> ScopedQuerySet:
        """Explicit, greppable escape hatch. Requires a written justification."""
        if not reason:
            raise ValueError("unscoped() requires a reason")
        clone = self._clone()
        clone._scope_applied = True
        clone._scope_actor = None
        return clone

    # -- enforcement ----------------------------------------------------------

    def _guard(self) -> None:
        if self._scope_applied or unscoped_access_permitted():
            return
        raise UnscopedAccessError(
            f"{self.model.__name__} queryset evaluated without tenant scoping.\n"
            f"Use {self.model.__name__}.objects.for_actor(actor), or wrap "
            f"deliberate cross-tenant access in allow_unscoped('reason')."
        )

    def _fetch_all(self):
        self._guard()
        return super()._fetch_all()

    def count(self):
        self._guard()
        return super().count()

    def exists(self):
        self._guard()
        return super().exists()

    def aggregate(self, *args, **kwargs):
        self._guard()
        return super().aggregate(*args, **kwargs)

    def update(self, **kwargs):
        self._guard()
        self._reject_guarded_field_writes(kwargs.keys())
        return super().update(**kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        """Enforce the same-organisation invariant that ``save()`` enforces.

        ``bulk_create`` never calls ``save()``, so without this one queryset
        call could plant a row spanning two tenants. Found in adversarial
        review — see tests/test_review_findings.py.
        """
        for obj in objs:
            check = getattr(obj, "check_same_organization", None)
            if check is not None:
                check()
        return super().bulk_create(objs, *args, **kwargs)

    def _reject_guarded_field_writes(self, field_names) -> None:
        """``QuerySet.update()`` issues raw SQL and bypasses model validation.

        Rather than try to validate a set-based write, refuse to repoint the
        fields whose cross-organisation consistency we guarantee. Callers that
        genuinely need to move one of these must load the instance and save it,
        where the check runs.
        """
        guarded = set(getattr(self.model, "same_organization_fields", ()))
        if not guarded:
            return

        attempted = {name.split("__")[0].removesuffix("_id") for name in field_names}
        collisions = sorted(attempted & guarded)
        if collisions:
            from django.core.exceptions import ValidationError

            raise ValidationError(
                f"{self.model.__name__}.update() may not change "
                f"{', '.join(collisions)} — these fields are guarded against "
                f"spanning organisations, and update() bypasses that check. "
                f"Load the instance and save() it instead."
            )

    def delete(self):
        self._guard()
        return super().delete()


class ScopedManager(models.Manager.from_queryset(ScopedQuerySet)):
    """Default manager for tenant-scoped models."""

    use_in_migrations = False


class SoftDeleteQuerySet(ScopedQuerySet):
    """Scoped queryset that hides soft-deleted rows by default — TODO 0.3.2.

    Ordering matters: this composes *on top of* scoping, so a soft-delete
    queryset is still subject to the tenant guard.
    """

    def alive(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=True)

    def dead(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=False)

    def for_actor(self, actor: Actor) -> SoftDeleteQuerySet:
        return super().for_actor(actor).filter(deleted_at__isnull=True)

    def for_actor_including_deleted(self, actor: Actor) -> SoftDeleteQuerySet:
        return super().for_actor(actor)

    def delete(self):
        raise NotImplementedError(
            "Hard delete is disabled on soft-delete models. Call .soft_delete(actor) "
            "on the instance, or use .unscoped(reason).hard_delete() deliberately."
        )

    def hard_delete(self):
        return ScopedQuerySet.delete(self)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    use_in_migrations = False


def org_scope_q(path: str, actor: Actor) -> Q:
    """Build the organisation filter for a given ORM path."""
    return Q(**{f"{path}": actor.organization_id})


def dojo_scope_q(path: str, actor: Actor) -> Q:
    """Build the dojo filter, if this actor is limited to specific dojos."""
    if actor.dojo_ids is None:
        return Q()
    return Q(**{f"{path}__in": list(actor.dojo_ids)})
