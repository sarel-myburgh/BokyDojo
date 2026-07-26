"""Ranking models — TODO 1.2.1-1.2.3.

Style, RankLadder, and Rank define the belt/grade progression system.
Every model is org-scoped via TenantScopedModel (plan S7.2).

Design is authoritative in project_plan.md S 4.4 — implement, do not redesign.
"""

from __future__ import annotations

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

