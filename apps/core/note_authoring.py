"""Writing notes — the authoring half of TODO 1.8.x.

``visible_to`` decides who may *read* a level; this decides who may *write* one,
which is a separate question and gets a separate answer.

⚠ **The rule: you may only write at a level you could read back.** Plus
``private``, which is always your own, and ``safeguarding``, which needs the
named role from SEC §4.

The alternative — letting an instructor write a note only admins can read — was
rejected deliberately. A note you cannot read is one you cannot check, correct,
or be shown to have written, and a write-only channel into a child's file is
exactly the shape of thing this system exists to prevent. The cost is that an
instructor cannot privately escalate to the office; they write at
``instructors``, which every admin can already read. If that trade is ever
revisited, it is this docstring and ``writable_visibilities`` that change.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.notes import Note
from apps.core.scoping import Actor

MAX_BODY_LENGTH = 5_000


def _governance(subject) -> str:
    from apps.identity.models import GovernanceModel

    organization = getattr(subject, "organization", None)
    if organization is None:
        person = getattr(subject, "person", None)
        organization = getattr(person, "organization", None)
    return getattr(organization, "governance_model", None) or GovernanceModel.CENTRAL


def writable_visibilities(actor: Actor, subject, *, governance_model=None) -> list[str]:
    """The levels this actor may author on this subject, in display order.

    Empty when the actor may not write at all — check it rather than assuming a
    non-empty list, or a form renders a choice field with nothing in it.
    """
    from apps.identity.permissions import Action, can

    governance_model = governance_model or _governance(subject)
    if not can(actor, Action.NOTE_WRITE, subject, governance_model=governance_model):
        return []

    # Mirrors the grants in NoteQuerySet.visible_to, minus the guardian branch:
    # being a parent lets you read a note about your child, not write one.
    levels = [Note.Visibility.PRIVATE]
    if can(actor, Action.NOTE_VIEW_INSTRUCTOR, subject, governance_model=governance_model):
        levels += [Note.Visibility.INSTRUCTORS, Note.Visibility.PARENT_VISIBLE]
    if can(actor, Action.NOTE_VIEW_ADMIN, subject, governance_model=governance_model):
        levels.append(Note.Visibility.ADMINS)
    # ⚠ Gated on SAFEGUARDING_VIEW because that action *is* the named role marker
    # in SEC §4 — there is no separate write action, and inventing one would mean
    # a role holding "write safeguarding" without "read safeguarding".
    if can(actor, Action.SAFEGUARDING_VIEW, subject, governance_model=governance_model):
        levels.append(Note.Visibility.SAFEGUARDING)
    return levels


@transaction.atomic
def create_note(
    *,
    subject,
    body: str,
    visibility: str,
    actor: Actor,
    subject_type: str = Note.SubjectType.STUDENT,
    subject_id=None,
    pinned: bool = False,
) -> Note:
    """Record one note, after checking the actor may write at that level.

    ⚠ The level is re-checked here and not only in the form. A form's choices are
    a convenience for the browser; this is the control.
    """
    from apps.identity.permissions import Action, PermissionDenied, require

    governance_model = _governance(subject)
    require(actor, Action.NOTE_WRITE, subject, governance_model=governance_model)

    allowed = writable_visibilities(actor, subject, governance_model=governance_model)
    if visibility not in allowed:
        # Denied, not a validation error: choosing a level you do not hold is an
        # attempt to write somewhere you have no standing, not a typo.
        raise PermissionDenied(action=Action.NOTE_WRITE, actor=actor)

    body = (body or "").strip()
    if not body:
        raise ValidationError({"body": _("A note cannot be empty.")})
    if len(body) > MAX_BODY_LENGTH:
        raise ValidationError(
            {"body": _("A note must be at most %(limit)s characters.") % {"limit": MAX_BODY_LENGTH}}
        )

    if subject_id is None:
        subject_id = getattr(subject, "person_id", None) or subject.pk
    organization_id = getattr(subject, "organization_id", None)
    if organization_id is None:
        organization_id = subject.person.organization_id

    note = Note.objects.create(
        organization_id=organization_id,
        author_id=actor.person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        body=body,
        visibility=visibility,
        pinned=pinned,
    )

    # ⚠ `note=` records the level, never the text. The body is in
    # audit.SENSITIVE_FIELDS so the snapshot drops it; saying "a safeguarding
    # note was written about this child" is the audit's job, quoting it is not.
    audit.record(
        "create",
        actor=actor,
        subject=note,
        after=audit.snapshot(note),
        organization_id=organization_id,
        note=f"note visibility: {visibility}",
        strict=True,
    )
    return note
