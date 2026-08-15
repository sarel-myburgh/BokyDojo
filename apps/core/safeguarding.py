"""Permission-checked, access-logged reads of safeguarding notes — TODO 1.8.4.

SEC §4: *"Safeguarding notes ('father not authorised for pickup') are encrypted,
access-logged, and restricted to a named safeguarding role — not visible to every
assistant instructor."*

Three separate obligations, and this module is where the second and third are
met. Encryption is a property of the column (see ``Note.body``); the role check
and the access log are properties of *reading*, so they belong on the one path
that reads.

⚠ This is deliberately not a queryset helper. ``NoteQuerySet.visible_to`` excludes
safeguarding notes unconditionally, so no ordinary screen can return one by
accident — a lazy queryset would also make "log every access" unimplementable,
since it may be evaluated never, once, or repeatedly. Here the read is eager and
the log is written before the caller gets anything back.
"""

from __future__ import annotations

from django.db import transaction

from apps.core import audit
from apps.core.notes import Note
from apps.core.scoping import Actor


def _governance(subject) -> str:
    from apps.identity.models import GovernanceModel

    organization = getattr(subject, "organization", None)
    if organization is None:
        person = getattr(subject, "person", None)
        organization = getattr(person, "organization", None)
    return getattr(organization, "governance_model", None) or GovernanceModel.CENTRAL


@transaction.atomic
def view_safeguarding_notes(
    *,
    subject,
    subject_type: str = Note.SubjectType.STUDENT,
    subject_id=None,
    actor: Actor,
) -> list[Note]:
    """Return the safeguarding notes about one subject, and record who looked.

    ``subject`` is the record the notes hang off — normally a ``StudentProfile``.
    It carries the dojo, which is what makes a dojo-scoped safeguarding officer's
    grant apply to this student rather than to every student in the organisation.

    Raises ``PermissionDenied`` unless the actor holds ``SAFEGUARDING_VIEW`` over
    that subject. The audit write is ``strict``, and inside the transaction, so a
    failure to record the access rolls back rather than quietly serving an
    unlogged read — an access log with holes in it is worse than none, because it
    is trusted.
    """
    from apps.identity.permissions import Action, require

    require(actor, Action.SAFEGUARDING_VIEW, subject, governance_model=_governance(subject))

    if subject_id is None:
        subject_id = getattr(subject, "person_id", None) or subject.pk

    notes = list(
        Note.objects.for_actor(actor)
        .filter(
            subject_type=subject_type,
            subject_id=subject_id,
            visibility=Note.Visibility.SAFEGUARDING,
        )
        .select_related("author")
    )

    # ⚠ Logged even when the result is empty. "Who went looking at this child's
    # safeguarding file" is the question §4 exists to answer, and a search that
    # found nothing is still someone going looking.
    #
    # actor_label is written explicitly rather than left to default. AuditLog's
    # actor_person is SET_NULL, so once that Person is redacted or deleted the
    # entry would no longer name anybody — and this is precisely the log that
    # gets read years later, about staff who have since left.
    audit.record(
        "view_safeguarding",
        actor=actor,
        subject=subject,
        note=f"{len(notes)} note(s)",
        actor_label=_actor_label(actor),
        strict=True,
    )
    return notes


def _actor_label(actor: Actor) -> str:
    """A durable human description of the accessor, independent of the FK."""
    from apps.identity.models import Person

    if actor.is_system:
        return "system"
    if actor.person_id is None:
        return ""
    person = (
        Person.objects.for_organization(actor.organization_id)
        .filter(pk=actor.person_id)
        .select_related("user")
        .first()
    )
    if person is None:
        return str(actor.person_id)
    email = getattr(getattr(person, "user", None), "email", "")
    return f"{person.full_name} <{email}>" if email else person.full_name


def may_view_safeguarding(actor: Actor, subject) -> bool:
    """Whether this actor holds the safeguarding role over this subject.

    For deciding whether to *offer* the view. ⚠ Not a substitute for calling
    ``view_safeguarding_notes``: hiding a control is not a control, and this
    function writes no access log.
    """
    from apps.identity.permissions import Action, can

    return can(actor, Action.SAFEGUARDING_VIEW, subject, governance_model=_governance(subject))
