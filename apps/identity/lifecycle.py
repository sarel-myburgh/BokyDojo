"""Controlled, audited student lifecycle transitions — TODO 1.1.12."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.scoping import Actor

from .models import GovernanceModel, StudentProfile
from .permissions import Action, require

ALLOWED_TRANSITIONS = {
    StudentProfile.Status.PROSPECT: (
        StudentProfile.Status.TRIAL,
        StudentProfile.Status.ACTIVE,
        StudentProfile.Status.LAPSED,
    ),
    StudentProfile.Status.TRIAL: (
        StudentProfile.Status.ACTIVE,
        StudentProfile.Status.LAPSED,
    ),
    StudentProfile.Status.ACTIVE: (
        StudentProfile.Status.ON_HOLD,
        StudentProfile.Status.LAPSED,
        StudentProfile.Status.ALUMNI,
    ),
    StudentProfile.Status.ON_HOLD: (
        StudentProfile.Status.ACTIVE,
        StudentProfile.Status.LAPSED,
    ),
    StudentProfile.Status.LAPSED: (
        StudentProfile.Status.ACTIVE,
        StudentProfile.Status.ALUMNI,
    ),
    StudentProfile.Status.ALUMNI: (StudentProfile.Status.ACTIVE,),
}


def allowed_student_transitions(current_status: str) -> tuple[str, ...]:
    """Return deliberate next states; an empty tuple fails closed."""
    return ALLOWED_TRANSITIONS.get(current_status, ())


@transaction.atomic
def transition_student_status(
    *,
    profile: StudentProfile,
    to_status: str,
    actor: Actor,
    hold_reason: str = "",
    required_action: str = Action.PERSON_EDIT,
) -> StudentProfile:
    """Apply one valid status transition under a row lock and strict audit.

    ⚠ ``required_action`` exists so archiving can be granted to instructors
    without granting them PERSON_EDIT, which carries the right to edit medical
    records. It is a parameter rather than a second function so that the row
    lock, the transition table and the strict audit stay on one path — a
    parallel implementation would drift away from all three.
    """
    governance = profile.person.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, required_action, profile, governance_model=governance)

    if to_status not in StudentProfile.Status.values:
        raise ValidationError({"to_status": _("Unknown student status.")})

    reason = (hold_reason or "").strip()
    if len(reason) > 200:
        raise ValidationError({"hold_reason": _("Hold reason must be at most 200 characters.")})
    if to_status == StudentProfile.Status.ON_HOLD and not reason:
        raise ValidationError({"hold_reason": _("Enter an administrative reason for the hold.")})

    locked = (
        StudentProfile.objects.for_actor(actor)
        .select_for_update()
        .select_related("person", "person__organization", "home_dojo")
        .get(pk=profile.pk)
    )
    from_status = locked.status
    if to_status not in allowed_student_transitions(from_status):
        raise ValidationError(
            {
                "to_status": _("%(from_status)s cannot transition directly to %(to_status)s.")
                % {
                    "from_status": locked.get_status_display(),
                    "to_status": StudentProfile.Status(to_status).label,
                }
            }
        )

    before = audit.snapshot(locked, ["status"])
    locked.status = to_status
    locked.hold_reason = reason if to_status == StudentProfile.Status.ON_HOLD else ""
    locked.save(update_fields=["status", "hold_reason", "updated_at"])
    audit.record(
        "update",
        subject=locked,
        before=before,
        after=audit.snapshot(locked, ["status"]),
        actor=actor,
        note=f"student lifecycle: {from_status} -> {to_status}",
        strict=True,
    )
    return locked


BULK_TRANSITION_LIMIT = 50


@transaction.atomic
def bulk_transition_student_status(
    *,
    profiles,
    to_status: str,
    actor: Actor,
    hold_reason: str = "",
) -> list[StudentProfile]:
    """Hold or resume a bounded set atomically; never leave a partial batch."""
    selected = sorted(list(profiles), key=lambda profile: str(profile.pk))
    if not selected:
        raise ValidationError({"profiles": _("Select at least one student.")})
    if len(selected) > BULK_TRANSITION_LIMIT:
        raise ValidationError(
            {
                "profiles": _("Select at most %(limit)s students at once.")
                % {"limit": BULK_TRANSITION_LIMIT}
            }
        )

    if to_status == StudentProfile.Status.ON_HOLD:
        expected_source = StudentProfile.Status.ACTIVE
    elif to_status == StudentProfile.Status.ACTIVE:
        expected_source = StudentProfile.Status.ON_HOLD
    else:
        raise ValidationError({"to_status": _("Bulk actions may only hold or resume students.")})

    wrong_state = [
        profile.person.full_name for profile in selected if profile.status != expected_source
    ]
    if wrong_state:
        raise ValidationError(
            {
                "profiles": _(
                    "Every selected student must currently be %(status)s. No statuses were changed."
                )
                % {"status": StudentProfile.Status(expected_source).label}
            }
        )

    updated = []
    for profile in selected:
        updated.append(
            transition_student_status(
                profile=profile,
                to_status=to_status,
                hold_reason=hold_reason,
                actor=actor,
            )
        )
    return updated


# -- archiving and removal ----------------------------------------------------

#: Where a student goes when archived. ⚠ ALUMNI rather than a new "archived"
#: status: the transition table already knows how to get here and back, and
#: reports already understand it. A parallel concept would need both again.
ARCHIVE_STATUS = StudentProfile.Status.ALUMNI


def archive_student(*, profile: StudentProfile, actor: Actor) -> StudentProfile:
    """Take a student off the active roll, keeping everything they did.

    ⚠ Not a deletion. Attendance, grades and notes stay exactly where they are —
    "was this child in class that evening" is a safeguarding question that gets
    asked months later, and an archive that discarded the answer would be worse
    than useless.
    """
    return transition_student_status(
        profile=profile,
        to_status=ARCHIVE_STATUS,
        actor=actor,
        required_action=Action.STUDENT_ARCHIVE,
    )


def unarchive_student(*, profile: StudentProfile, actor: Actor) -> StudentProfile:
    """Put an archived student back on the active roll."""
    return transition_student_status(
        profile=profile,
        to_status=StudentProfile.Status.ACTIVE,
        actor=actor,
        required_action=Action.STUDENT_ARCHIVE,
    )


@transaction.atomic
def delete_person(*, person, actor: Actor) -> None:
    """Remove somebody from the organisation.

    ⚠ A soft delete, and that is the honest meaning of "delete" here. The row
    stays so that attendance, audit entries and financial history keep pointing
    at a real person; the scoped managers filter it out, so they disappear from
    every list and every search. Permanent erasure is a different, larger thing
    — a data-protection request rather than an administrative tidy-up — and
    nothing here pretends to do it.

    ⚠ Their sign-in stops working immediately. ``actor_for_user`` already
    refuses to build a scope for a deleted person, and the login is deactivated
    besides: two independent stops, because "removed" that leaves a live session
    is not removed.
    """
    from apps.core import audit as audit_module

    from .models import Role, RoleAssignment, User

    organization = person.organization
    governance = organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.PERSON_DELETE, person, governance_model=governance)

    # ⚠ Not yourself. Deleting your own account revokes your own access mid
    # request, and if you were the last administrator it locks the organisation
    # out entirely — with no one left who can undo it.
    if actor.person_id and actor.person_id == person.pk:
        raise ValidationError(_("You cannot remove your own account."))

    # ⚠ Nor the last organisation administrator, for the same reason the role
    # screen refuses to revoke the last one: recovering needs database access.
    holds_org_admin = (
        RoleAssignment.objects.for_actor(actor)
        .filter(person=person, role=Role.ORG_ADMIN, revoked_at__isnull=True)
        .exists()
    )
    if holds_org_admin:
        # ⚠ person__deleted_at__isnull=True is load-bearing. Removing somebody
        # does not revoke their role assignments — deliberately, so a restore
        # brings their access back — which means a deleted administrator still
        # has a live ORG_ADMIN row. Without this filter they keep counting
        # towards the total, and an organisation could be emptied of
        # administrators one at a time while the guard never once fired.
        remaining = (
            RoleAssignment.objects.for_actor(actor)
            .filter(
                role=Role.ORG_ADMIN,
                revoked_at__isnull=True,
                person__deleted_at__isnull=True,
            )
            .exclude(person=person)
            .count()
        )
        if remaining == 0:
            raise ValidationError(
                _("This is the last organisation administrator. Appoint another first.")
            )

    person.soft_delete(actor)

    login = User.objects.filter(person=person).first()
    if login is not None and login.is_active:
        login.is_active = False
        login.save(update_fields=["is_active"])

    audit_module.record(
        "delete",
        subject=person,
        actor=actor,
        organization_id=organization.pk,
        note="person removed (soft delete); sign-in disabled",
        strict=True,
    )


@transaction.atomic
def restore_person(*, person, actor: Actor) -> None:
    """Undo a removal.

    ⚠ The reason delete is soft. Somebody removes the wrong Kim, and without
    this the only remedy is a database restore.
    """
    from apps.core import audit as audit_module

    from .models import User

    governance = person.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.PERSON_DELETE, person, governance_model=governance)

    person.restore()

    login = User.objects.filter(person=person).first()
    if login is not None and not login.is_active:
        login.is_active = True
        login.save(update_fields=["is_active"])

    audit_module.record(
        "update",
        subject=person,
        actor=actor,
        organization_id=person.organization_id,
        note="person restored; sign-in re-enabled",
        strict=True,
    )
