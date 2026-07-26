"""Scheduling models — TODO 1.4.1, 1.4.3, 1.4.6, 1.4.7, plan §4.5 and §12.2."""

from __future__ import annotations

import datetime

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.managers import ScopedManager
from apps.core.models import TenantScopedModel


class ClassTemplate(TenantScopedModel):
    """A recurring class definition — plan §4.5.

    Sessions are materialised from the rrule on a rolling horizon elsewhere.
    """

    tenant_org_path = "dojo__organization_id"
    tenant_dojo_path = "dojo_id"
    #: A template must not reference another organisation's style or ranks.
    same_organization_fields = ("dojo", "style", "rank_min", "rank_max")

    dojo = models.ForeignKey(
        "identity.Dojo",
        on_delete=models.PROTECT,
        related_name="class_templates",
    )
    name = models.CharField(_("name"), max_length=200)
    style = models.ForeignKey(
        "ranks.Style",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="class_templates",
    )
    rrule = models.CharField(
        _("recurrence rule"),
        max_length=255,
        help_text=_("RFC 5545 recurrence rule, e.g. FREQ=WEEKLY;BYDAY=MO,WE,FR"),
    )
    start_time = models.TimeField(_("start time"))
    duration_minutes = models.PositiveSmallIntegerField(_("duration (minutes)"))
    room = models.CharField(_("room"), max_length=100, blank=True)
    capacity = models.PositiveSmallIntegerField(
        _("capacity"),
        null=True,
        blank=True,
    )
    rank_min = models.ForeignKey(
        "ranks.Rank",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="class_templates_min",
    )
    rank_max = models.ForeignKey(
        "ranks.Rank",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="class_templates_max",
    )
    age_min = models.PositiveSmallIntegerField(
        _("minimum age"),
        null=True,
        blank=True,
    )
    age_max = models.PositiveSmallIntegerField(
        _("maximum age"),
        null=True,
        blank=True,
    )
    active_from = models.DateField(_("active from"))
    active_to = models.DateField(
        _("active until"),
        null=True,
        blank=True,
    )
    counts_toward = models.JSONField(
        _("counts toward"),
        default=list,
        blank=True,
        help_text=_('Tags such as ["kata", "kihon"] used for grading eligibility.'),
    )

    objects = ScopedManager()

    class Meta:
        verbose_name = _("class template")
        verbose_name_plural = _("class templates")
        ordering = ("dojo", "name")

    def __str__(self) -> str:
        return f"{self.name} @ {self.dojo}"

    @staticmethod
    def _organization_of(obj):
        """Resolve a Rank's organisation through its ladder and style."""
        if obj is not None and obj.__class__.__name__ == "Rank":
            return obj.ladder.style.organization_id
        return TenantScopedModel._organization_of(obj)


class ClosurePeriod(TenantScopedModel):
    """Dates on which no sessions should exist — plan §12.2.

    Org-wide when ``dojo`` is null; dojo-specific otherwise.
    """

    tenant_org_path = "organization_id"
    same_organization_fields = ("organization", "dojo")

    organization = models.ForeignKey(
        "identity.Organization",
        on_delete=models.CASCADE,
        related_name="closure_periods",
    )
    dojo = models.ForeignKey(
        "identity.Dojo",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="closure_periods",
    )
    starts_on = models.DateField(_("starts on"))
    ends_on = models.DateField(_("ends on"))
    reason = models.CharField(_("reason"), max_length=200)
    suppress_billing = models.BooleanField(
        _("suppress billing"),
        default=False,
        help_text=_("When set, billing is paused for this closure period."),
    )

    objects = ScopedManager()

    class Meta:
        verbose_name = _("closure period")
        verbose_name_plural = _("closure periods")
        ordering = ("organization", "starts_on")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__gte=models.F("starts_on")),
                name="closure_ends_on_gte_starts_on",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reason} ({self.starts_on} – {self.ends_on})"

    def covers(self, date: datetime.date) -> bool:
        return self.starts_on <= date <= self.ends_on


class ClassSession(TenantScopedModel):
    """A single class occurrence — plan §4.5.

    ``template`` is null for ad-hoc one-off sessions. Cancelled sessions are
    never deleted; their status changes so parents can see what happened.
    """

    tenant_org_path = "dojo__organization_id"
    tenant_dojo_path = "dojo_id"
    same_organization_fields = ("dojo", "template")

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("scheduled")
        CANCELLED = "cancelled", _("cancelled")
        COMPLETED = "completed", _("completed")

    template = models.ForeignKey(
        ClassTemplate,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    dojo = models.ForeignKey(
        "identity.Dojo",
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    starts_at = models.DateTimeField(_("starts at"))
    ends_at = models.DateTimeField(_("ends at"))
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )
    cancellation_reason = models.CharField(
        _("cancellation reason"),
        max_length=200,
        blank=True,
    )
    room = models.CharField(_("room"), max_length=100, blank=True)

    objects = ScopedManager()

    class Meta:
        verbose_name = _("class session")
        verbose_name_plural = _("class sessions")
        ordering = ("-starts_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="session_ends_at_gt_starts_at",
            ),
        ]

    def __str__(self) -> str:
        name = self.template.name if self.template else str(_("One-off session"))
        return f"{name} @ {self.dojo} {self.starts_at:%Y-%m-%d %H:%M}"

    @staticmethod
    def _organization_of(obj):
        """Resolve a ClassTemplate's organisation through its dojo."""
        if obj is not None and obj.__class__.__name__ == "ClassTemplate":
            return obj.dojo.organization_id
        return TenantScopedModel._organization_of(obj)

    def cancel(self, reason: str = "") -> None:
        """Mark this session as cancelled. Never deletes the row."""
        self.status = self.Status.CANCELLED
        self.cancellation_reason = reason
        self.save(update_fields=["status", "cancellation_reason", "updated_at"])

    @property
    def duration_minutes(self) -> int | None:
        if self.ends_at and self.starts_at:
            return int((self.ends_at - self.starts_at).total_seconds() / 60)
        return None
