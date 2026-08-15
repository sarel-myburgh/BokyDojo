"""Note model — TODO 1.8.1, 1.8.2, 1.8.3 and 1.8.4, plan §4.7.

Polymorphic note attachable to a student, a session, an enrolment or an invoice.
Pinned notes surface on the student header via the custom queryset helper.

⚠ Read notes through ``Note.objects...visible_to(actor, subject=...)``. Tenant
scoping alone will hand back every visibility level, private notes included.

⚠ ``visible_to`` never returns a ``safeguarding`` note. Those are read only
through ``apps.core.safeguarding.view_safeguarding_notes``, which checks the
named role and writes the access log SEC §4 requires.
"""

from __future__ import annotations

import operator
from functools import reduce

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.fields import EncryptedTextField
from apps.core.ids import uuid7
from apps.core.managers import ScopedManager, ScopedQuerySet
from apps.core.models import TenantScopedModel


class NoteQuerySet(ScopedQuerySet):
    """Extends ScopedQuerySet with note-specific query helpers."""

    def pinned_for(self, subject_type: str, subject_id) -> NoteQuerySet:
        """Return pinned notes for one subject, newest first."""
        return self.filter(
            subject_type=subject_type,
            subject_id=subject_id,
            pinned=True,
        ).order_by("-created_at")

    def visible_to(self, actor, *, subject=None, governance_model=None) -> NoteQuerySet:
        """Apply the visibility levels — TODO 1.8.2, plan §4.7.

        ⚠ **This is the only sanctioned way to read notes as somebody.**
        Tenant scoping (``for_actor``) answers "which organisation's notes", which
        is a different question from "which of them may this person read". A note
        marked ``private`` belongs to its author alone, and an instructor and a
        dojo admin standing in the same room are entitled to different subsets of
        the same student's file. Reach for ``Note.objects`` without this and you
        get every level, including somebody else's private note.

        ``subject`` is the record the notes hang off — usually a
        ``StudentProfile``. It is what carries the dojo, so it decides whether a
        dojo-scoped instructor's permission actually reaches these notes; a Note
        itself has an organisation but no dojo, so passing one to ``can()`` would
        deny every dojo-scoped role.

        The levels:

        - ``private`` — the author, and nobody else. Not admins, not the owner.
        - ``instructors`` — anyone holding ``NOTE_VIEW_INSTRUCTOR`` over the
          subject.
        - ``admins`` — anyone holding ``NOTE_VIEW_ADMIN`` over the subject.
          Deliberately *not* implied by the instructor permission: "escalate this
          to the office" is the whole point of the level.
        - ``parent_visible`` — as ``instructors``, plus a guardian of that
          student, established through ``GuardianLink``. A guardian of a
          different child gets nothing.

        Unknown or anonymous actors get nothing. Failing closed here matters more
        than anywhere else in the app: these are the notes about children.
        """
        from apps.identity.models import GovernanceModel, GuardianLink
        from apps.identity.permissions import Action, can

        if actor is None or getattr(actor, "is_anonymous", False):
            return self.none()
        if getattr(actor, "is_system", False):
            return self

        if governance_model is None:
            governance_model = GovernanceModel.CENTRAL

        clauses = []
        person_id = getattr(actor, "person_id", None)

        # ⚠ Guard the None. `Q(author_id=None)` is not "no author clause", it is
        # `author_id IS NULL` — which matches every system-written, authorless
        # note and hands them to any actor that happens to have no Person.
        if person_id is not None:
            clauses.append(Q(author_id=person_id))

        if can(actor, Action.NOTE_VIEW_INSTRUCTOR, subject, governance_model=governance_model):
            clauses.append(
                Q(visibility__in=[Note.Visibility.INSTRUCTORS, Note.Visibility.PARENT_VISIBLE])
            )

        if can(actor, Action.NOTE_VIEW_ADMIN, subject, governance_model=governance_model):
            clauses.append(Q(visibility=Note.Visibility.ADMINS))

        if person_id is not None:
            guarded = (
                GuardianLink.objects.for_organization(actor.organization_id)
                .filter(guardian_id=person_id)
                .values_list("student_id", flat=True)
            )
            clauses.append(
                Q(visibility=Note.Visibility.PARENT_VISIBLE)
                & Q(subject_type=Note.SubjectType.STUDENT)
                & Q(subject_id__in=guarded)
            )

        if not clauses:
            return self.none()
        # ⚠ Excluded last and unconditionally, so no clause above can grant it —
        # not the author's own, not an admin's. A safeguarding note is reached
        # only through view_safeguarding_notes(), which checks the named role and
        # writes the access log. Authorship is not a standing entitlement here:
        # whoever wrote it may since have left the safeguarding role, and SEC §4
        # says "restricted to a named role", not "to a role or whoever typed it".
        return self.filter(reduce(operator.or_, clauses)).exclude(
            visibility=Note.Visibility.SAFEGUARDING
        )


class NoteManager(ScopedManager.from_queryset(NoteQuerySet)):
    """Manager for Note with scoped + pinned_for helpers."""

    use_in_migrations = False


class Note(TenantScopedModel):
    """Polymorphic note attachable to a student, session, enrollment or invoice.

    Task 1.8.1: stores subject_type + subject_id rather than a Django
    ContentType FK, because the subject apps do not all exist yet.
    """

    class SubjectType(models.TextChoices):
        STUDENT = "student", _("Student")
        SESSION = "session", _("Session")
        ENROLLMENT = "enrollment", _("Enrollment")
        INVOICE = "invoice", _("Invoice")

    class Visibility(models.TextChoices):
        PRIVATE = "private", _("Private — author only")
        INSTRUCTORS = "instructors", _("Instructors at this dojo")
        ADMINS = "admins", _("Dojo and org administrators")
        PARENT_VISIBLE = "parent_visible", _("Student's guardians can read it")
        #: ⚠ Task 1.8.4 / SEC §4. "Father not authorised for pickup" is the
        #: canonical example: it must reach the safeguarding officer and nobody
        #: else — not every assistant instructor, and not the dojo admin. Never
        #: returned by ``visible_to``; read it through
        #: ``apps.core.safeguarding.view_safeguarding_notes``, which is the only
        #: path that checks the role and writes the access log.
        SAFEGUARDING = "safeguarding", _("Safeguarding — named role only")

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    organization = models.ForeignKey(
        "identity.Organization",
        on_delete=models.CASCADE,
        related_name="notes",
    )
    subject_type = models.CharField(
        _("subject type"),
        max_length=16,
        choices=SubjectType.choices,
        db_index=True,
    )
    subject_id = models.UUIDField(_("subject id"), db_index=True)
    author = models.ForeignKey(
        "identity.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="authored_notes",
    )
    #: ⚠ Encrypted at rest — SEC §4 requires it for safeguarding notes, and the
    #: column is shared, so *every* note body is encrypted rather than only
    #: some. That is the stricter reading and the only one this field type can
    #: express: encryption is a property of the column, not of a row.
    #: Consequence: note bodies can never be searched. If a note search is ever
    #: wanted it must exclude safeguarding notes by construction, not by filter.
    body = EncryptedTextField(_("body"))
    visibility = models.CharField(
        _("visibility"),
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.INSTRUCTORS,
    )
    pinned = models.BooleanField(_("pinned"), default=False)

    same_organization_fields = ("organization", "author")

    objects = NoteManager()

    class Meta:
        verbose_name = _("note")
        verbose_name_plural = _("notes")
        ordering = ("-pinned", "-created_at")
        indexes = [
            models.Index(
                fields=["subject_type", "subject_id", "-pinned", "-created_at"],
                name="note_subject_pinned_idx",
            ),
        ]

    def __str__(self) -> str:
        subject = f"{self.subject_type}:{self.subject_id}"
        # ⚠ A safeguarding note never previews its body. __str__ is what the
        # admin changelist, `repr` in a traceback and a stray log line all end up
        # printing, and "father not authorised for pickup" must not leak through
        # any of them to somebody without the role.
        if self.visibility == self.Visibility.SAFEGUARDING:
            return f"Safeguarding note on {subject}"
        preview = self.body[:60].replace("\n", " ")
        if len(self.body) > 60:
            preview += "…"
        return f"Note on {subject}: {preview}"
