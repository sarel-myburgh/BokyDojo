"""Scheduling models — TODO 1.4.1, 1.4.3, 1.4.6, 1.4.7, plan §4.5 and §12.2."""

from __future__ import annotations

import datetime

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.managers import ScopedManager
from apps.core.models import TenantScopedModel
from apps.core.timezones import dojo_zone


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


class Holiday(TenantScopedModel):
    """A date someone might care about. Creates no closure.

    The actual decision to close a dojo is stored in ``HolidayObservance``.
    """

    tenant_org_path = "organization_id"

    class Source(models.TextChoices):
        MANUAL = "manual", _("manual")
        IMPORTED = "imported", _("imported")
        BUILTIN = "builtin", _("builtin")

    organization = models.ForeignKey(
        "identity.Organization",
        on_delete=models.CASCADE,
        related_name="holidays",
    )
    name = models.CharField(_("name"), max_length=200)
    date = models.DateField(_("date"))
    country = models.CharField(_("country"), max_length=2, blank=True)
    source = models.CharField(
        _("source"),
        max_length=16,
        choices=Source.choices,
        default=Source.MANUAL,
    )
    external_id = models.CharField(_("external id"), max_length=100, blank=True)
    is_recurring_annually = models.BooleanField(
        _("recurring annually"),
        default=False,
        help_text=_("Fixed-date holidays such as 1 January. Lunar holidays are False."),
    )

    objects = ScopedManager()

    class Meta:
        verbose_name = _("holiday")
        verbose_name_plural = _("holidays")
        ordering = ("organization", "date", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "date", "name"],
                name="unique_holiday_per_org_date_name",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.date:%Y-%m-%d})"


class HolidayObservance(TenantScopedModel):
    """A dojo's decision about one holiday: closed, open, or reduced schedule."""

    tenant_org_path = "holiday__organization_id"
    tenant_dojo_path = "dojo_id"
    same_organization_fields = ("holiday", "dojo", "closure")

    class Observance(models.TextChoices):
        CLOSED = "closed", _("closed")
        OPEN = "open", _("open")
        REDUCED_SCHEDULE = "reduced_schedule", _("reduced schedule")

    holiday = models.ForeignKey(
        Holiday,
        on_delete=models.CASCADE,
        related_name="observances",
    )
    dojo = models.ForeignKey(
        "identity.Dojo",
        on_delete=models.CASCADE,
        related_name="holiday_observances",
    )
    observance = models.CharField(
        _("observance"),
        max_length=16,
        choices=Observance.choices,
        default=Observance.OPEN,
    )
    closure = models.ForeignKey(
        ClosurePeriod,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="holiday_observance",
    )
    note = models.CharField(_("note"), max_length=200, blank=True)

    objects = ScopedManager()

    class Meta:
        verbose_name = _("holiday observance")
        verbose_name_plural = _("holiday observances")
        constraints = [
            models.UniqueConstraint(
                fields=["holiday", "dojo"],
                name="unique_holiday_observance_per_dojo",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.holiday} @ {self.dojo}: {self.observance}"

    def apply(self) -> None:
        """Create or remove the linked closure to match the chosen observance.

        Idempotent: repeated calls with the same observance do not duplicate
        closures.
        """
        if self.observance == self.Observance.CLOSED:
            if self.closure_id is None:
                closure = ClosurePeriod.objects.for_organization(
                    self.holiday.organization_id
                ).create(
                    organization_id=self.holiday.organization_id,
                    dojo=self.dojo,
                    starts_on=self.holiday.date,
                    ends_on=self.holiday.date,
                    reason=f"Closed for {self.holiday.name}",
                )
                self.closure = closure
                self.save(update_fields=["closure"])
        elif self.closure_id is not None:
            self.closure.delete()
            self.closure = None
            self.save(update_fields=["closure"])


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

    #: ⚠ The wall-clock slot this occurrence was originally materialised into,
    #: set only when somebody moved this single session — TODO 1.4.5.
    #:
    #: Without it the move silently becomes a duplicate: materialisation keys on
    #: (template, starts_at) and never deletes, so the next run sees the vacated
    #: slot standing empty and helpfully recreates the class at its old time.
    #: The generator therefore treats a slot as occupied if any session *starts*
    #: there or was *moved from* there.
    moved_from = models.DateTimeField(_("moved from"), null=True, blank=True)

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
        # The dojo's wall-clock time, not UTC. This string is read by humans in
        # the admin and in page titles, and "11:30" for a 18:30 class is worse
        # than useless — it looks like a different class.
        return f"{name} @ {self.dojo} {self.local_starts_at:%Y-%m-%d %H:%M}"

    @property
    def local_zone(self):
        """The dojo's timezone, as a tzinfo — safe to hand to ``{% timezone %}``.

        Templates must not pass the raw name: the tag calls ``ZoneInfo()`` on it
        and a bad value in the database would 500 the page rather than degrade.
        """
        return dojo_zone(self.dojo)

    @property
    def local_starts_at(self):
        return self.starts_at.astimezone(self.local_zone)

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
