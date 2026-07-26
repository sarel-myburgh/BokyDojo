"""Note model — TODO 1.8.1 and 1.8.3, plan §4.7.

Polymorphic note attachable to a student, a session, an enrolment or an invoice.
Pinned notes surface on the student header via the custom queryset helper.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

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
    body = models.TextField(_("body"))
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
        preview = self.body[:60].replace("\n", " ")
        if len(self.body) > 60:
            preview += "…"
        subject = f"{self.subject_type}:{self.subject_id}"
        return f"Note on {subject}: {preview}"
