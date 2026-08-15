"""Enrolment and transfer services — TODO 1.3.1 – 1.3.4, plan §4.3.

Call these rather than writing ``Enrollment`` rows directly. Three things have
to happen together and are easy to get wrong one at a time:

1. The primary-enrolment invariant (exactly one home dojo) must hold.
2. ``StudentProfile.home_dojo`` is a denormalised copy of that primary, and it
   is a *tenant scoping path* — if it drifts, the student becomes visible to the
   wrong dojo's staff. That is a tenancy bug, not a display bug.
3. A transfer must never mutate the old enrolment's dojo. Attendance, invoices
   and time entries all hang off the dojo where they happened; repointing the
   enrolment would silently rewrite history.

Permissions use ``PERSON_EDIT`` rather than an enrolment-specific action:
changing where somebody trains is administration of that person's record, and
the existing matrix already draws the line in the right place — front desk and
admins may, instructors and assistants may not.
"""

from __future__ import annotations

import datetime

from django.db import transaction

from apps.core import audit
from apps.core.scoping import Actor

from .models import Dojo, Enrollment, GovernanceModel, Person, StudentProfile, TransferRecord
from .permissions import Action, require


def _governance_of(dojo: Dojo) -> str:
    return dojo.organization.governance_model or GovernanceModel.CENTRAL


def _sync_home_dojo(student: Person, dojo: Dojo | None, actor: Actor) -> None:
    """Keep ``StudentProfile.home_dojo`` equal to the primary enrolment's dojo.

    Absent profile is not an error: a prospect can be enrolled before anyone has
    filled in their student record.
    """
    # for_organization() rather than for_actor(): the profile's own tenant path
    # runs through home_dojo, which is the field being corrected. A dojo-scoped
    # actor cannot see a profile whose home_dojo is still null or elsewhere, so
    # scoping by actor here would silently skip exactly the rows that need
    # fixing. The organisation id is server-derived from the dojo, never
    # client-supplied — see ScopedQuerySet.for_organization.
    organization_id = dojo.organization_id if dojo else student.organization_id
    profile = (
        StudentProfile.objects.for_organization(organization_id).filter(person=student).first()
    )
    if profile is None or profile.home_dojo_id == (dojo.pk if dojo else None):
        return

    before = audit.snapshot(profile, ["home_dojo_id"])
    profile.home_dojo = dojo
    profile.save(update_fields=["home_dojo", "updated_at"])
    audit.record_change(
        "update",
        profile,
        before=before,
        actor=actor,
        note="home dojo followed the primary enrolment",
    )


@transaction.atomic
def enrol_student(
    *,
    student: Person,
    dojo: Dojo,
    started_on: datetime.date,
    actor: Actor,
    is_primary: bool | None = None,
    status: str = Enrollment.Status.ACTIVE,
    notes: str = "",
) -> Enrollment:
    """Enrol a student at a dojo — TODO 1.3.1/1.3.2.

    ``is_primary`` defaults to "primary if they have no other live enrolment",
    which is what the first enrolment of a new student should be without the
    caller having to think about it.
    """
    require(actor, Action.PERSON_EDIT, dojo, governance_model=_governance_of(dojo))

    live = list(Enrollment.objects.for_actor(actor).filter(student=student, ended_on__isnull=True))
    if is_primary is None:
        is_primary = not live

    if is_primary:
        for existing in live:
            if existing.is_primary:
                existing.is_primary = False
                existing.save(update_fields=["is_primary", "updated_at"])

    enrollment = Enrollment(
        student=student,
        dojo=dojo,
        started_on=started_on,
        is_primary=is_primary,
        status=status,
        notes=notes,
        created_by_id=actor.person_id,
    )
    # Field-level validation and the cross-organisation check, but not the unique
    # and check constraints: Django validates those by querying the model's
    # default manager, which is tenant-scoped and refuses an unscoped read. The
    # database enforces them regardless — see the Enrollment.Meta constraints.
    enrollment.full_clean(validate_unique=False, validate_constraints=False)
    enrollment.save()

    audit.record_change("create", enrollment, actor=actor)
    if is_primary:
        _sync_home_dojo(student, dojo, actor)
    return enrollment


@transaction.atomic
def transfer_student(
    *,
    student: Person,
    to_dojo: Dojo,
    effective_on: datetime.date,
    actor: Actor,
    from_dojo: Dojo | None = None,
    reason: str = "",
    approved_by: Person | None = None,
) -> TransferRecord:
    """Move a student from one dojo to another — TODO 1.3.3.

    Ends the old enrolment, opens a new one, and records the transfer. History
    is not touched: attendance stays attached to the dojo it happened at, which
    is the property ``tests/test_enrollment.py`` pins.

    The actor needs ``PERSON_EDIT`` on **both** dojos. That is deliberately
    conservative — a dojo admin cannot push a student into a dojo they have no
    standing at, and cannot pull one out of a dojo they don't administer.
    """
    if from_dojo is None:
        primary = (
            Enrollment.objects.for_actor(actor)
            .filter(student=student, is_primary=True, ended_on__isnull=True)
            .first()
        )
        if primary is None:
            raise ValueError(
                f"{student} has no primary enrolment to transfer from; pass from_dojo "
                f"explicitly or enrol them first."
            )
        from_dojo = primary.dojo
    else:
        primary = (
            Enrollment.objects.for_actor(actor)
            .filter(student=student, dojo=from_dojo, ended_on__isnull=True)
            .first()
        )
        if primary is None:
            raise ValueError(f"{student} has no live enrolment at {from_dojo}.")

    if from_dojo.pk == to_dojo.pk:
        raise ValueError("A transfer needs two different dojos.")

    require(actor, Action.PERSON_EDIT, from_dojo, governance_model=_governance_of(from_dojo))
    require(actor, Action.PERSON_EDIT, to_dojo, governance_model=_governance_of(to_dojo))

    was_primary = primary.is_primary
    before = audit.snapshot(primary)
    primary.end(effective_on, reason=reason)
    audit.record_change("update", primary, before=before, actor=actor, note="transferred out")

    # Deliberately not enrol_student(): the permission checks above already cover
    # both dojos, and the primary flag must follow the enrolment being replaced
    # rather than be re-derived.
    new_enrollment = Enrollment(
        student=student,
        dojo=to_dojo,
        started_on=effective_on,
        is_primary=was_primary,
        status=Enrollment.Status.ACTIVE,
        notes=reason[:255],
        created_by_id=actor.person_id,
    )
    new_enrollment.full_clean(validate_unique=False, validate_constraints=False)
    new_enrollment.save()
    audit.record_change("create", new_enrollment, actor=actor, note="transferred in")

    record = TransferRecord(
        student=student,
        from_dojo=from_dojo,
        to_dojo=to_dojo,
        effective_on=effective_on,
        reason=reason[:255],
        approved_by=approved_by,
        created_by_id=actor.person_id,
    )
    record.full_clean(validate_unique=False, validate_constraints=False)
    record.save()
    audit.record_change("create", record, actor=actor)

    if was_primary:
        _sync_home_dojo(student, to_dojo, actor)
    return record


@transaction.atomic
def set_primary_dojo(*, student: Person, dojo: Dojo, actor: Actor) -> Enrollment:
    """Promote an existing live enrolment to primary — TODO 1.3.2.

    Not a transfer: the student keeps training at both dojos, and only the
    question of which one owns them changes.
    """
    require(actor, Action.PERSON_EDIT, dojo, governance_model=_governance_of(dojo))

    live = list(Enrollment.objects.for_actor(actor).filter(student=student, ended_on__isnull=True))
    target = next((e for e in live if e.dojo_id == dojo.pk), None)
    if target is None:
        raise ValueError(f"{student} has no live enrolment at {dojo}.")

    for existing in live:
        if existing.is_primary and existing.pk != target.pk:
            existing.is_primary = False
            existing.save(update_fields=["is_primary", "updated_at"])

    if not target.is_primary:
        target.is_primary = True
        target.save(update_fields=["is_primary", "updated_at"])
        audit.record_change("update", target, actor=actor, note="became the primary dojo")

    _sync_home_dojo(student, dojo, actor)
    return target
