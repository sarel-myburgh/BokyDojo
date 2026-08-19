"""Instructor profiles and time entries — TODO 1.9.1, 1.9.2, plan §4.2 / §4.8.

InstructorProfile links a Person to their pay details and grading ceiling.
TimeEntry records hours worked at a dojo, with a snapshotted pay rate.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel
from apps.core.money import Money


class InstructorProfile(TenantScopedModel):
    """Pay details and grading ceiling for an instructor — TODO 1.9.1, plan §4.2."""

    tenant_org_path = "person__organization_id"
    same_organization_fields = ("person", "max_grading_rank")

    class PayType(models.TextChoices):
        HOURLY = "hourly", _("Hourly")
        PER_CLASS = "per_class", _("Per class")
        SALARY = "salary", _("Salary")
        VOLUNTEER = "volunteer", _("Volunteer")

    person = models.OneToOneField(
        "identity.Person",
        on_delete=models.CASCADE,
        related_name="instructor_profile",
    )
    bio = models.TextField(_("bio"), blank=True)
    #: Which arts this person teaches. ⚠ Descriptive for now — nothing refuses a
    #: grading or a class assignment on the strength of it. The control that
    #: *does* bite is ``max_grading_rank`` below. If style should ever constrain
    #: who may teach or grade what, this is the field it would read, and that is
    #: a deliberate decision rather than something to slide in.
    styles = models.ManyToManyField(
        "ranks.Style",
        blank=True,
        related_name="instructors",
        verbose_name=_("styles taught"),
    )
    pay_type = models.CharField(
        _("pay type"),
        max_length=16,
        choices=PayType.choices,
    )
    pay_rate_minor_units = models.PositiveIntegerField(
        _("pay rate (minor units)"),
        default=0,
    )
    pay_currency = models.CharField(_("currency"), max_length=3, default="USD")
    employment_started_on = models.DateField(_("employment started on"), null=True, blank=True)
    max_grading_rank = models.ForeignKey(
        "ranks.Rank",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="grading_ceiling_for",
    )

    class Meta:
        verbose_name = _("instructor profile")
        verbose_name_plural = _("instructor profiles")

    def __str__(self) -> str:
        return f"InstructorProfile: {self.person}"

    @property
    def pay_rate(self) -> Money:
        return Money(self.pay_rate_minor_units, self.pay_currency)


class TimeEntry(TenantScopedModel):
    """Hours worked at a dojo — TODO 1.9.2, plan §4.8.

    The pay rate is snapshotted, not looked up later: an approved timesheet
    from last year must not change value if the instructor's rate changes next
    year.
    """

    tenant_org_path = "dojo__organization_id"
    tenant_dojo_path = "dojo_id"
    same_organization_fields = ("dojo", "instructor", "approved_by")

    class Category(models.TextChoices):
        CLASS = "class", _("Class")
        ADMIN = "admin", _("Admin")
        PRIVATE_LESSON = "private_lesson", _("Private lesson")
        EVENT = "event", _("Event")
        TRAVEL = "travel", _("Travel")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SUBMITTED = "submitted", _("Submitted")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    instructor = models.ForeignKey(
        "identity.Person",
        on_delete=models.PROTECT,
        related_name="time_entries",
    )
    dojo = models.ForeignKey(
        "identity.Dojo",
        on_delete=models.PROTECT,
        related_name="time_entries",
    )
    session_id = models.UUIDField(_("session id"), null=True, blank=True)
    category = models.CharField(
        _("category"),
        max_length=16,
        choices=Category.choices,
    )
    started_at = models.DateTimeField(_("started at"))
    ended_at = models.DateTimeField(_("ended at"), null=True, blank=True)
    minutes = models.PositiveIntegerField(_("minutes"), default=0)
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    approved_by = models.ForeignKey(
        "identity.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_time_entries",
    )
    pay_rate_snapshot_minor_units = models.PositiveIntegerField(
        _("pay rate snapshot (minor units)"),
        null=True,
        blank=True,
    )
    pay_rate_snapshot_currency = models.CharField(
        _("pay rate snapshot currency"),
        max_length=3,
        blank=True,
    )
    notes = models.CharField(_("notes"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("time entry")
        verbose_name_plural = _("time entries")
        ordering = ("-started_at",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(ended_at__isnull=True) | models.Q(ended_at__gt=models.F("started_at"))
                ),
                name="timeentry_ended_after_started",
            ),
        ]

    def __str__(self) -> str:
        return f"TimeEntry: {self.instructor} @ {self.dojo} ({self.get_category_display()})"

    def save(self, *args, **kwargs):
        self._compute_minutes()
        update_fields = kwargs.get("update_fields")
        if (
            update_fields is not None
            and "ended_at" in update_fields
            and "minutes" not in update_fields
        ):
            update_fields = list(update_fields) + ["minutes"]
            kwargs["update_fields"] = update_fields
        super().save(*args, **kwargs)

    def _compute_minutes(self) -> None:
        """Compute minutes from timestamps when ended_at is set."""
        if self.ended_at is not None and self.started_at is not None:
            delta = self.ended_at - self.started_at
            self.minutes = max(0, int(delta.total_seconds() // 60))

    def clean(self):
        super().clean()
        if self.ended_at is not None and self.started_at is not None:
            if self.ended_at <= self.started_at:
                raise ValidationError({"ended_at": _("ended_at must be after started_at.")})


class StaffGrade(TenantScopedModel):
    """The rank a member of staff holds in a style — plan §3.

    ⚠ Deliberately not a ``StudentStyleTrack``.

    A student track hangs off ``StudentProfile`` and is the live, promotable
    record: it drives eligibility, gradings, and the rank reports. An instructor
    who is 5th dan is usually not enrolled as a student anywhere, and giving them
    a student profile so they could hold a grade would put them in class rosters,
    the check-in grid, and the active-students count — every one of which would
    be wrong.

    ⚠ Optional, and optional per style. Some styles have no belts at all, and an
    instructor may hold a grade in one art and none in another they also teach.
    Nothing here requires a grade to exist.
    """

    tenant_org_path = "person__organization_id"
    same_organization_fields = ("person", "style", "rank")

    person = models.ForeignKey(
        "identity.Person",
        on_delete=models.CASCADE,
        related_name="staff_grades",
    )
    style = models.ForeignKey(
        "ranks.Style",
        on_delete=models.PROTECT,
        related_name="staff_grades",
    )
    #: ⚠ Either ``rank`` or ``label``, never both and never neither — see the
    #: constraint below. ``rank`` is preferred and is what you get when the grade
    #: is on a ladder somebody has configured. ``label`` exists because dan
    #: grades routinely are not: a ladder set up for students often stops at
    #: black belt, and refusing to record "5th Dan" until somebody extends it
    #: would mean the field simply goes unused.
    rank = models.ForeignKey(
        "ranks.Rank",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="held_by_staff",
    )
    label = models.CharField(_("grade"), max_length=50, blank=True)
    awarded_on = models.DateField(_("awarded on"), null=True, blank=True)
    notes = models.CharField(_("notes"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("staff grade")
        verbose_name_plural = _("staff grades")
        ordering = ("style__name",)
        constraints = [
            # ⚠ One grade per person per style. Two rows would be two answers to
            # "what dan is she", and nothing would say which is current.
            models.UniqueConstraint(
                fields=["person", "style"],
                name="staffgrade_one_per_person_per_style",
            ),
            # ⚠ Exactly one of rank/label. Both set means two names for one
            # grade that can disagree; neither means a row that says nothing.
            models.CheckConstraint(
                condition=(
                    models.Q(rank__isnull=False, label="")
                    | models.Q(rank__isnull=True) & ~models.Q(label="")
                ),
                name="staffgrade_rank_or_label_not_both",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.person} — {self.style}: {self.display_name}"

    @property
    def display_name(self) -> str:
        return self.rank.name if self.rank_id else self.label

    def clean(self):
        super().clean()
        has_rank = self.rank_id is not None
        has_label = bool((self.label or "").strip())
        if has_rank and has_label:
            raise ValidationError(
                {"label": _("Choose a rank from the list or type one, not both.")}
            )
        if not has_rank and not has_label:
            raise ValidationError({"label": _("Enter the grade they hold.")})
        # ⚠ A rank belongs to a ladder which belongs to a style. Picking one from
        # a different style would file a karate belt under boxing.
        if has_rank and self.style_id and self.rank.ladder.style_id != self.style_id:
            raise ValidationError({"rank": _("That grade belongs to a different style.")})
