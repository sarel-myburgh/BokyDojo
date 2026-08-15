"""Who is teaching — TODO 1.4.8, plan §4.5.

Two layers, and keeping them apart is the whole point:

``TemplateInstructor`` is who *normally* teaches a recurring class. It seeds new
sessions and nothing else; changing it never reaches backwards.

``SessionInstructor`` is who taught *one* class. Pay (`1.9.3`) and any
safeguarding question about who was in the room read this, never the template.

⚠ A substitution is recorded, not overwritten. The stand-in is flagged and points
at the person they covered for, because "Sensei Dara covered for Sensei Mei on
the 14th" is a different and more useful fact than "Sensei Dara taught on the
14th" — and it is the one a parent, or an investigation, actually asks for.

⚠ A substitute may come from **another dojo in the same organisation**. That is
what a substitute usually is: the cover for tonight's class at Central is the
instructor who normally teaches at Sen Sok. Requiring an assignment at *this*
dojo would refuse the ordinary case, so the check is organisation-wide.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.scoping import Actor

from .models import ClassSession, SessionInstructor


def _require_schedule_edit(actor: Actor, dojo) -> None:
    from apps.identity.models import GovernanceModel
    from apps.identity.permissions import Action, require

    governance = dojo.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.DOJO_EDIT, dojo, governance_model=governance)


def _teaches_somewhere(person, organization_id) -> bool:
    """Whether this person is an instructor anywhere in the organisation."""
    from apps.identity.models import InstructorAssignment

    return (
        InstructorAssignment.objects.for_organization(organization_id)
        .filter(person=person, ended_on__isnull=True)
        .exists()
    )


def _locked_session(session: ClassSession, actor: Actor) -> ClassSession:
    return (
        ClassSession.objects.for_actor(actor)
        .select_for_update()
        .select_related("dojo", "dojo__organization")
        .get(pk=session.pk)
    )


@transaction.atomic
def assign_instructor(
    *,
    session: ClassSession,
    person,
    actor: Actor,
    is_substitute: bool = False,
    replaces=None,
) -> SessionInstructor:
    """Put somebody on one class.

    ⚠ Unlike moving a class, this is allowed on a session that has already
    happened. "Who actually taught last Tuesday" is a correction of fact, and pay
    depends on it being right; refusing would leave the record permanently wrong.
    Every change is audited, which is what makes that safe.
    """
    locked = _locked_session(session, actor)
    _require_schedule_edit(actor, locked.dojo)

    organization_id = locked.dojo.organization_id
    if person.organization_id != organization_id:
        raise ValidationError({"person": _("That person belongs to another organisation.")})
    if not _teaches_somewhere(person, organization_id):
        raise ValidationError({"person": _("That person is not an instructor here.")})

    if replaces is not None:
        if replaces.pk == person.pk:
            raise ValidationError({"replaces": _("Somebody cannot cover for themselves.")})
        covering = SessionInstructor.objects.for_actor(actor).filter(
            session=locked, person=replaces
        )
        if not covering.exists():
            raise ValidationError(
                {"replaces": _("That person is not scheduled to teach this class.")}
            )

    existing = (
        SessionInstructor.objects.for_actor(actor).filter(session=locked, person=person).first()
    )
    if existing is not None:
        raise ValidationError({"person": _("They are already on this class.")})

    assignment = SessionInstructor.objects.create(
        session=locked,
        person=person,
        is_substitute=is_substitute,
        replaces=replaces,
    )
    audit.record(
        "create",
        actor=actor,
        subject=locked,
        note=(
            f"substitute {person.pk} covering {replaces.pk}"
            if is_substitute and replaces is not None
            else f"instructor {person.pk} assigned"
        ),
        strict=True,
    )
    return assignment


@transaction.atomic
def assign_substitute(*, session: ClassSession, replacing, substitute, actor: Actor):
    """Swap one instructor for a stand-in on a single class — TODO 1.4.8.

    Returns ``(removed, added)``. The original's row is removed from *this*
    session only; the fact that they were covered survives on the substitute's
    row, and the template is untouched, so next week reverts to normal without
    anybody having to remember to put it back.
    """
    locked = _locked_session(session, actor)
    _require_schedule_edit(actor, locked.dojo)

    original = (
        SessionInstructor.objects.for_actor(actor).filter(session=locked, person=replacing).first()
    )
    if original is None:
        raise ValidationError({"replacing": _("That person is not teaching this class.")})

    added = assign_instructor(
        session=locked,
        person=substitute,
        actor=actor,
        is_substitute=True,
        replaces=replacing,
    )
    # Removed only after the substitute is safely in place: if the assignment
    # above raises, the transaction rolls back and the class still has a teacher.
    original.delete()
    return original, added


@transaction.atomic
def remove_instructor(*, session: ClassSession, person, actor: Actor) -> None:
    locked = _locked_session(session, actor)
    _require_schedule_edit(actor, locked.dojo)

    assignment = (
        SessionInstructor.objects.for_actor(actor).filter(session=locked, person=person).first()
    )
    if assignment is None:
        raise ValidationError({"person": _("They are not on this class.")})
    assignment.delete()
    audit.record(
        "delete",
        actor=actor,
        subject=locked,
        note=f"instructor {person.pk} removed",
        strict=True,
    )


def instructors_for(session: ClassSession, actor: Actor):
    """Everyone teaching one class, substitutes included."""
    return (
        SessionInstructor.objects.for_actor(actor)
        .filter(session=session)
        .select_related("person", "replaces")
    )
