"""Forms for rank workflows."""

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.identity.models import StudentProfile

from .models import Rank
from .promotions import (
    BULK_PROMOTION_LIMIT,
    bulk_promotion_rank_choices,
    promotion_rank_choices,
)


class ManualPromotionForm(forms.Form):
    rank = forms.ModelChoiceField(label=_("New rank"), queryset=Rank.objects.none())
    awarded_on = forms.DateField(
        label=_("Promotion date"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    certificate_number = forms.CharField(
        label=_("Certificate number"), required=False, max_length=64
    )
    notes = forms.CharField(
        label=_("Promotion notes"),
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, track, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rank"].queryset = promotion_rank_choices(track, actor=actor)
        self.fields["awarded_on"].widget.attrs["max"] = timezone.localdate().isoformat()
        control = "mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900"
        for field in self.fields.values():
            field.widget.attrs["class"] = control

    def clean_awarded_on(self):
        value = self.cleaned_data["awarded_on"]
        if value > timezone.localdate():
            raise forms.ValidationError(_("A promotion date cannot be in the future."))
        return value


class BulkPromotionForm(forms.Form):
    student_ids = forms.ModelMultipleChoiceField(
        label=_("Students"),
        queryset=StudentProfile.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    rank = forms.ModelChoiceField(label=_("Target rank"), queryset=Rank.objects.none())
    awarded_on = forms.DateField(
        label=_("Promotion date"),
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    notes = forms.CharField(
        label=_("Shared promotion note"),
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Applied to every selected award. Do not include medical information."),
    )

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student_ids"].queryset = (
            StudentProfile.objects.for_actor(actor)
            .select_related("person", "person__organization", "home_dojo")
            .order_by("person__family_name", "person__given_name")
        )
        self.fields["rank"].queryset = bulk_promotion_rank_choices(actor)
        self.fields["awarded_on"].widget.attrs["max"] = timezone.localdate().isoformat()
        control = "mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900"
        for name in ("rank", "awarded_on", "notes"):
            self.fields[name].widget.attrs["class"] = control

    def clean_awarded_on(self):
        value = self.cleaned_data["awarded_on"]
        if value > timezone.localdate():
            raise forms.ValidationError(_("A promotion date cannot be in the future."))
        return value

    def clean_notes(self):
        return self.cleaned_data["notes"].strip()

    def clean_student_ids(self):
        profiles = self.cleaned_data["student_ids"]
        if len(profiles) > BULK_PROMOTION_LIMIT:
            raise forms.ValidationError(
                _("Select at most %(limit)s students at once.") % {"limit": BULK_PROMOTION_LIMIT}
            )
        return profiles
