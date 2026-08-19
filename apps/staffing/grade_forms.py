"""Recording the rank a member of staff holds — plan §3."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.ranks.models import Rank, Style

from .models import StaffGrade

TEXT = "w-full border border-gray-300 bg-white px-3 py-2 text-sm"


class StaffGradeForm(forms.ModelForm):
    """Pick a grade off a ladder, or type one when it is not on any.

    ⚠ Both fields are offered because dan grades routinely are not on the
    ladders an organisation has set up — those are built for students and often
    stop at black belt. Refusing to record "5th Dan" until somebody extends a
    student ladder would mean the field went unused, which is worse than a
    typed string.
    """

    class Meta:
        model = StaffGrade
        fields = ("style", "rank", "label", "awarded_on")
        widgets = {
            "style": forms.Select(attrs={"class": TEXT}),
            "rank": forms.Select(attrs={"class": TEXT}),
            "label": forms.TextInput(
                attrs={"class": TEXT, "placeholder": _("for example, 5th Dan")}
            ),
            "awarded_on": forms.DateInput(attrs={"class": TEXT, "type": "date"}),
        }
        labels = {
            "style": _("Style"),
            "rank": _("Grade"),
            "label": _("…or type the grade"),
            "awarded_on": _("Awarded on"),
        }
        help_texts = {
            "rank": _("Pick from the belts set up for this style."),
            "label": _("Use this if the grade is not on any of your ladders."),
            "awarded_on": _("Optional."),
        }

    def __init__(self, *args, actor=None, person=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.person = person
        self.fields["style"].queryset = Style.objects.for_actor(actor).order_by("name")
        self.fields["rank"].queryset = (
            Rank.objects.for_actor(actor)
            .select_related("ladder", "ladder__style")
            .order_by("ladder__style__name", "ladder__name", "order")
        )
        self.fields["rank"].required = False
        self.fields["label"].required = False
        self.fields["awarded_on"].required = False

    def clean(self):
        cleaned = super().clean()
        style = cleaned.get("style")
        if style is None:
            return cleaned

        # ⚠ The duplicate check lives here rather than in Model.full_clean.
        # full_clean runs validate_unique, which evaluates a queryset with no
        # tenant scope and therefore raises UnscopedAccessError — recording any
        # grade at all would have failed. Done here it stays scoped and the
        # message is one somebody can act on.
        if (
            StaffGrade.objects.for_actor(self.actor)
            .filter(person=self.person, style=style)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError(
                _("They already have a grade recorded in this style. Remove it first.")
            )

        # ⚠ Refused when the same person already has a live student track in this
        # style. That track is the promotable record; a second number beside it
        # is two answers to one question and nothing to say which is current.
        from apps.ranks.models import StudentStyleTrack

        clashing = (
            StudentStyleTrack.objects.for_actor(self.actor)
            .filter(
                student=self.person,
                style=style,
                status=StudentStyleTrack.Status.ACTIVE,
            )
            .exists()
        )
        if clashing:
            raise forms.ValidationError(
                _(
                    "They are already graded in this style as a student. "
                    "Record the promotion there instead so there is one answer, not two."
                )
            )
        return cleaned
