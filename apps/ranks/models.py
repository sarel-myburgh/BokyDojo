"""Ranking models — TODO 1.2.1-1.2.3.

Style, RankLadder, and Rank define the belt/grade progression system.
Every model is org-scoped via TenantScopedModel (plan S7.2).

Design is authoritative in project_plan.md S 4.4 — implement, do not redesign.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class Style(TenantScopedModel):
    """A martial art taught by the organisation (Shotokan Karate, BJJ, Judo...).

    Plan S4.4: Style — org_id, name
    """

    tenant_org_path = "organization_id"

    organization = models.ForeignKey(
        "identity.Organization",
        on_delete=models.PROTECT,
        related_name="styles",
    )
    name = models.CharField(_("name"), max_length=100)

    class Meta:
        verbose_name = _("style")
        verbose_name_plural = _("styles")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_style_name_per_org",
            )
        ]

    def __str__(self) -> str:
        return self.name



class RankLadder(TenantScopedModel):
    """A progression ladder belonging to a Style.

    Plan S4.4: RankLadder — style_id, name, applies_to (adult|junior)
    Juniors get their own ladder with mon grades/stripes.
    """

    class AppliesTo(models.TextChoices):
        ADULT = "adult", _("Adult")
        JUNIOR = "junior", _("Junior")

    tenant_org_path = "style__organization_id"

    style = models.ForeignKey(
        Style,
        on_delete=models.CASCADE,
        related_name="ladders",
    )
    name = models.CharField(_("name"), max_length=100)
    applies_to = models.CharField(
        _("applies to"),
        max_length=8,
        choices=AppliesTo.choices,
    )

    class Meta:
        verbose_name = _("rank ladder")
        verbose_name_plural = _("rank ladders")
        ordering = ("style", "applies_to", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["style", "name"],
                name="unique_ladder_name_per_style",
            ),
            models.UniqueConstraint(
                fields=["style", "applies_to"],
                name="unique_ladder_applies_to_per_style",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_applies_to_display()})"



class Rank(TenantScopedModel):
    """A single rank within a ladder — belt colour, stripes, and promotion rules.

    Plan S4.4: Rank — ladder_id, order, name, belt_colour, stripe_count,
    min_months_at_previous, min_classes_since_previous, min_age
    """

    tenant_org_path = "ladder__style__organization_id"

    ladder = models.ForeignKey(
        RankLadder,
        on_delete=models.CASCADE,
        related_name="ranks",
    )
    order = models.PositiveIntegerField(
        _("order"),
        help_text=_("Defines progression within a ladder. Unique per ladder."),
    )
    name = models.CharField(_("name"), max_length=50)
    belt_colour = models.CharField(_("belt colour"), max_length=30, blank=True)
    stripe_count = models.PositiveSmallIntegerField(
        _("stripe count"),
        default=0,
    )
    min_months_at_previous = models.PositiveIntegerField(
        _("min months at previous rank"),
        default=0,
    )
    min_classes_since_previous = models.PositiveIntegerField(
        _("min classes since previous rank"),
        default=0,
    )
    min_age = models.PositiveSmallIntegerField(
        _("min age"),
        default=0,
    )

    class Meta:
        verbose_name = _("rank")
        verbose_name_plural = _("ranks")
        ordering = ("ladder", "order")
        constraints = [
            models.UniqueConstraint(
                fields=["ladder", "order"],
                name="unique_rank_order_per_ladder",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.ladder})"


class StudentStyleTrack(TenantScopedModel):
    """A student's progression within one style — TODO 1.2.4, plan §4.2.

    ⚠ **Rank is per style, not per student.** A student can be 3rd kyu in
    karate and a blue belt in BJJ at the same organisation, progressing
    independently on separate ladders. Storing ``current_rank`` on the student
    record breaks the moment an organisation teaches two arts, which is most of
    them, and unpicking it later means rewriting every rank query.

    It also carries the junior-to-adult transition. A child on the mon ladder
    who turns sixteen does not have their rank rewritten: their junior track is
    closed with status ``transferred`` and an adult track opens. Both are kept,
    because "what colour belt did she hold at twelve" is a real question a
    parent will ask.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        #: Closed because the student moved to another ladder in the same style
        #: (junior → adult). The rank history stays readable.
        TRANSFERRED = "transferred", _("Moved to another ladder")
        #: Closed because the student stopped training this style.
        ENDED = "ended", _("Ended")

    tenant_org_path = "student__organization_id"
    same_organization_fields = ("student", "style", "ladder", "current_rank")

    student = models.ForeignKey(
        "identity.Person",
        on_delete=models.CASCADE,
        related_name="style_tracks",
    )
    style = models.ForeignKey(Style, on_delete=models.PROTECT, related_name="tracks")
    ladder = models.ForeignKey(
        RankLadder, on_delete=models.PROTECT, related_name="tracks"
    )
    #: Denormalised from the latest RankAward for query speed (task 1.2.5
    #: recomputes it on write). Null means "enrolled but not yet graded" —
    #: a white belt who has not had their first grading.
    current_rank = models.ForeignKey(
        Rank,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    started_on = models.DateField(_("started on"))
    ended_on = models.DateField(_("ended on"), null=True, blank=True)
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        verbose_name = _("student style track")
        verbose_name_plural = _("student style tracks")
        ordering = ("student", "style")
        constraints = [
            # One live track per style. A student may hold several tracks in the
            # same style over time (junior then adult) but only one at a time.
            models.UniqueConstraint(
                fields=["student", "style"],
                condition=models.Q(status="active"),
                name="unique_active_track_per_student_style",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ended_on__isnull=True) | models.Q(ended_on__gte=models.F("started_on"))
                ),
                name="track_ended_on_after_started_on",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="active", ended_on__isnull=True)
                    | ~models.Q(status="active")
                ),
                name="active_track_has_no_end_date",
            ),
        ]
        indexes = [models.Index(fields=["student", "status"])]

    def __str__(self) -> str:
        rank = self.current_rank.name if self.current_rank_id else _("ungraded")
        return f"{self.student} — {self.style}: {rank}"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    def clean(self):
        super().clean()
        self.check_rank_belongs_to_ladder()

    def save(self, *args, **kwargs):
        self.check_rank_belongs_to_ladder()
        return super().save(*args, **kwargs)

    def check_rank_belongs_to_ladder(self) -> None:
        """A track's current rank must come from the track's own ladder.

        Without this a junior track could hold an adult dan grade, and every
        eligibility calculation downstream would be computed against the wrong
        progression.
        """
        if self.current_rank_id is None or self.ladder_id is None:
            return
        if self.current_rank.ladder_id != self.ladder_id:
            raise ValidationError(
                {
                    "current_rank": _(
                        "This rank belongs to a different ladder than the track."
                    )
                }
            )

    def close(self, *, status: str, on_date) -> None:
        """End this track. Never deleted — the rank history is a record."""
        if status == self.Status.ACTIVE:
            raise ValueError("close() requires a terminal status")
        self.status = status
        self.ended_on = on_date
        self.save(update_fields=["status", "ended_on", "updated_at"])

    def transfer_to_ladder(self, new_ladder: RankLadder, *, on_date) -> StudentStyleTrack:
        """Close this track and open a fresh one on another ladder.

        The junior-to-adult crossing. The old track keeps its final rank, so the
        student's history at twelve remains answerable.
        """
        if new_ladder.style_id != self.style_id:
            raise ValidationError(
                {"ladder": _("A transfer must stay within the same style.")}
            )

        self.close(status=self.Status.TRANSFERRED, on_date=on_date)
        return StudentStyleTrack.objects.create(
            student=self.student,
            style=self.style,
            ladder=new_ladder,
            started_on=on_date,
        )

