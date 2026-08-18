"""Defining the belts — TODO 1.2.x, plan §4.4.

A ladder is the ordered list of grades in a style: 10th kyu up to 1st dan, or ten
mon grades for children, or whatever this club actually awards. Until now they
could only be created in code (`create_shotokan_ladders`) or the Django admin, so
adding a style through the settings screen produced an art nobody could be graded
in.

⚠ **A ladder may belong to a dojo.** Clubs under one federation genuinely run
different syllabuses for the same art — eight kyu grades at one, ten at another,
different colours — and neither is wrong. A ladder with no dojo is the
organisation's default; a dojo that names its own belts uses those instead, and
enrolling there puts a student on them without anybody choosing.

⚠ **Belts that have been awarded cannot be deleted.** ``RankAward.rank`` is
PROTECT and awards are append-only evidence. The screen checks first and says so,
rather than letting the database raise an IntegrityError at somebody.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.core import audit
from apps.identity.models import Dojo, GovernanceModel, Organization
from apps.identity.permissions import Action, require
from apps.ranks.models import Rank, RankAward, RankLadder, StudentStyleTrack, Style

TEXT = "w-full border border-gray-300 bg-white px-3 py-2 text-sm"
SMALL = "w-full border border-gray-300 bg-white px-2 py-1 text-sm"

#: Renumbering happens in two passes through this offset, because
#: ``unique(ladder, order)`` would otherwise collide the moment two rows swap.
_RENUMBER_OFFSET = 100_000


def _organization(actor) -> Organization:
    return get_object_or_404(Organization, pk=actor.organization_id)


def _require_org_edit(actor) -> None:
    require(
        actor,
        Action.ORG_EDIT,
        _organization(actor),
        governance_model=GovernanceModel.CENTRAL,
    )


class LadderForm(forms.ModelForm):
    class Meta:
        model = RankLadder
        fields = ("name", "applies_to", "dojo")
        widgets = {
            "name": forms.TextInput(attrs={"class": TEXT, "autofocus": True}),
            "applies_to": forms.Select(attrs={"class": TEXT}),
            "dojo": forms.Select(attrs={"class": TEXT}),
        }
        labels = {"name": _("Name for this set of belts")}

    def __init__(self, *args, actor=None, style=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.style = style
        self.fields["dojo"].queryset = Dojo.objects.for_actor(actor).order_by("name")
        self.fields["dojo"].required = False
        self.fields["dojo"].empty_label = _("All dojos (organisation default)")

    def clean(self):
        cleaned = super().clean()
        dojo = cleaned.get("dojo")
        applies_to = cleaned.get("applies_to")
        if not applies_to:
            return cleaned
        clash = RankLadder.objects.for_organization(self.style.organization_id).filter(
            style=self.style, applies_to=applies_to, dojo=dojo
        )
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(
                _("There is already a %(kind)s set of belts for %(where)s.")
                % {
                    "kind": applies_to,
                    "where": dojo.name if dojo else _("all dojos"),
                }
            )

        # ⚠ "Everyone" and the age-split ladders are mutually exclusive for a
        # given style and dojo. Allowing both leaves no defensible answer for a
        # nine-year-old — two ladders claim them — and the wrong one is invisible
        # until a grading. Refused here with a message rather than resolved by a
        # precedence rule nobody would remember.
        siblings = RankLadder.objects.for_organization(self.style.organization_id).filter(
            style=self.style, dojo=dojo
        )
        if self.instance.pk:
            siblings = siblings.exclude(pk=self.instance.pk)
        existing = set(siblings.values_list("applies_to", flat=True))
        where = dojo.name if dojo else _("all dojos")
        if applies_to == RankLadder.AppliesTo.ALL and existing:
            raise forms.ValidationError(
                _(
                    "%(where)s already has age-specific belts for this style. A set "
                    "for everyone cannot sit alongside them — remove those first, or "
                    "add this one as adult or junior."
                )
                % {"where": where}
            )
        if applies_to != RankLadder.AppliesTo.ALL and RankLadder.AppliesTo.ALL in existing:
            raise forms.ValidationError(
                _(
                    "%(where)s already has one set of belts for everyone in this "
                    "style. Remove it before adding age-specific sets."
                )
                % {"where": where}
            )
        return cleaned


class RankForm(forms.ModelForm):
    class Meta:
        model = Rank
        fields = (
            "name",
            "belt_colour",
            "stripe_count",
            "min_months_at_previous",
            "min_classes_since_previous",
            "min_age",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": TEXT, "autofocus": True}),
            "belt_colour": forms.TextInput(attrs={"class": TEXT}),
            "stripe_count": forms.NumberInput(attrs={"class": TEXT, "min": 0}),
            "min_months_at_previous": forms.NumberInput(attrs={"class": TEXT, "min": 0}),
            "min_classes_since_previous": forms.NumberInput(attrs={"class": TEXT, "min": 0}),
            "min_age": forms.NumberInput(attrs={"class": TEXT, "min": 0}),
        }
        help_texts = {
            "belt_colour": _("Free text — 'white', 'brown with black stripe'."),
            "min_classes_since_previous": _(
                "Read by the grading eligibility engine later; safe to leave at zero."
            ),
        }


# -- style detail: the ladders it has -----------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def style_detail_view(request, style_id) -> HttpResponse:
    actor = request.actor
    _require_org_edit(actor)
    style = get_object_or_404(Style.objects.for_actor(actor), pk=style_id)

    form = LadderForm(request.POST or None, actor=actor, style=style)
    if request.method == "POST" and form.is_valid():
        ladder = form.save(commit=False)
        ladder.style = style
        ladder.save()
        audit.record_change("create", ladder, actor=actor)
        messages.success(request, _("Added %(name)s.") % {"name": ladder.name})
        return redirect("ladder-detail", ladder_id=ladder.pk)

    ladders = list(
        RankLadder.objects.for_actor(actor)
        .filter(style=style)
        .select_related("dojo")
        .order_by("dojo__name", "applies_to", "name")
    )
    counts = {
        ladder.pk: Rank.objects.for_actor(actor).filter(ladder=ladder).count() for ladder in ladders
    }

    return render(
        request,
        "ranks/style_detail.html",
        {
            "style": style,
            "form": form,
            "ladders": [{"ladder": ladder, "rank_count": counts[ladder.pk]} for ladder in ladders],
        },
    )


# -- ladder detail: the belts in it -------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def ladder_detail_view(request, ladder_id) -> HttpResponse:
    actor = request.actor
    _require_org_edit(actor)
    ladder = get_object_or_404(
        RankLadder.objects.for_actor(actor).select_related("style", "dojo"), pk=ladder_id
    )

    form = RankForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            rank = form.save(commit=False)
            rank.ladder = ladder
            # Appended to the end. Reordering is a separate, explicit action —
            # inserting in the middle silently renumbers everything below it.
            highest = (
                Rank.objects.for_actor(actor)
                .filter(ladder=ladder)
                .order_by("-order")
                .values_list("order", flat=True)
                .first()
            )
            rank.order = (highest or 0) + 1
            rank.save()
            audit.record_change("create", rank, actor=actor)
        messages.success(request, _("Added %(name)s.") % {"name": rank.name})
        return redirect("ladder-detail", ladder_id=ladder.pk)

    ranks = list(Rank.objects.for_actor(actor).filter(ladder=ladder).order_by("order"))
    awarded = set(
        RankAward.objects.for_organization(ladder.style.organization_id)
        .filter(rank__ladder=ladder)
        .values_list("rank_id", flat=True)
    )
    in_use = set(
        StudentStyleTrack.objects.for_organization(ladder.style.organization_id)
        .filter(current_rank__ladder=ladder)
        .values_list("current_rank_id", flat=True)
    )

    return render(
        request,
        "ranks/ladder_detail.html",
        {
            "ladder": ladder,
            "style": ladder.style,
            "form": form,
            "ranks": [
                {
                    "rank": rank,
                    "position": index + 1,
                    "is_awarded": rank.pk in awarded or rank.pk in in_use,
                }
                for index, rank in enumerate(ranks)
            ],
        },
    )


@login_required
@require_POST
def rank_reorder_view(request, ladder_id) -> HttpResponse:
    """Renumber the belts from the positions posted.

    ⚠ Two passes. ``unique(ladder, order)`` collides the instant two rows swap,
    so every row is first moved out of the way by a large offset and then given
    its final number. One pass works right up until somebody actually reorders
    something, which is the only time this code runs.
    """
    actor = request.actor
    _require_org_edit(actor)
    ladder = get_object_or_404(RankLadder.objects.for_actor(actor), pk=ladder_id)

    ranks = list(Rank.objects.for_actor(actor).filter(ladder=ladder))
    wanted: list[tuple[int, Rank]] = []
    for rank in ranks:
        raw = request.POST.get(f"order:{rank.pk}")
        try:
            wanted.append((int(raw), rank))
        except (TypeError, ValueError):
            messages.error(request, _("Positions must be whole numbers."))
            return redirect("ladder-detail", ladder_id=ladder.pk)

    with transaction.atomic():
        for offset, (_position, rank) in enumerate(wanted, start=1):
            Rank.objects.for_actor(actor).filter(pk=rank.pk).update(order=_RENUMBER_OFFSET + offset)
        for index, (_position, rank) in enumerate(sorted(wanted, key=lambda row: row[0]), start=1):
            Rank.objects.for_actor(actor).filter(pk=rank.pk).update(order=index)

    messages.success(request, _("Order saved."))
    return redirect("ladder-detail", ladder_id=ladder.pk)


@login_required
@require_POST
def rank_delete_view(request, ladder_id, rank_id) -> HttpResponse:
    """Remove a belt, unless somebody holds it.

    ⚠ Checked rather than attempted. ``RankAward.rank`` is PROTECT because awards
    are append-only evidence, so deleting an awarded belt raises an IntegrityError
    — a 500 page for what is really "you cannot un-award a grade".
    """
    actor = request.actor
    _require_org_edit(actor)
    ladder = get_object_or_404(
        RankLadder.objects.for_actor(actor).select_related("style"), pk=ladder_id
    )
    rank = get_object_or_404(Rank.objects.for_actor(actor).filter(ladder=ladder), pk=rank_id)

    organization_id = ladder.style.organization_id
    awarded = RankAward.objects.for_organization(organization_id).filter(rank=rank).exists()
    held = (
        StudentStyleTrack.objects.for_organization(organization_id)
        .filter(current_rank=rank)
        .exists()
    )
    if awarded or held:
        messages.error(
            request,
            _(
                "%(name)s has been awarded, so it cannot be removed. A grade "
                "somebody holds is a record, not a setting."
            )
            % {"name": rank.name},
        )
        return redirect("ladder-detail", ladder_id=ladder.pk)

    name = rank.name
    audit.record_change("delete", rank, actor=actor)
    rank.delete()
    messages.success(request, _("Removed %(name)s.") % {"name": name})
    return redirect("ladder-detail", ladder_id=ladder.pk)
