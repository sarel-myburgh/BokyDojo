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
) -> StudentProfile:
    """Apply one valid status transition under a row lock and strict audit."""
    governance = profile.person.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.PERSON_EDIT, profile, governance_model=governance)

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
