"""Building a dojo's weekly timetable — plan §1.4.

⚠ Nobody types an rrule here.

``ClassTemplate.rrule`` holds an RFC 5545 string like
``FREQ=WEEKLY;BYDAY=MO,WE,FR``. That is the right thing to store — the whole
materialiser is built on it — but it is not a thing to ask a dojo owner for. The
form offers the days of the week as checkboxes and composes the rule; the string
never appears on screen.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.identity.models import Dojo
from apps.ranks.models import Style

from .models import ClassTemplate

TEXT = "w-full border border-gray-300 bg-white px-3 py-2 text-sm"

#: Ordered Monday-first, which is how a timetable is read. The two-letter codes
#: are RFC 5545's own.
WEEKDAYS = (
    ("MO", _("Monday")),
    ("TU", _("Tuesday")),
    ("WE", _("Wednesday")),
    ("TH", _("Thursday")),
    ("FR", _("Friday")),
    ("SA", _("Saturday")),
    ("SU", _("Sunday")),
)
_ORDER = [code for code, _label in WEEKDAYS]


def days_from_rrule(rrule: str) -> list[str]:
    """The weekday codes in an rrule, for re-populating the form on edit.

    ⚠ Tolerant by design. A template may have been created by the importer, by a
    fixture, or by hand in the admin with a rule this form would never produce;
    returning nothing is better than raising and making the row uneditable.
    """
    for part in (rrule or "").split(";"):
        name, _, value = part.partition("=")
        if name.strip().upper() == "BYDAY":
            return [d for d in (v.strip().upper() for v in value.split(",")) if d in _ORDER]
    return []


def rrule_from_days(days) -> str:
    chosen = sorted(set(days), key=_ORDER.index)
    return "FREQ=WEEKLY;BYDAY=" + ",".join(chosen)


class ClassTemplateForm(forms.ModelForm):
    """One recurring class in a dojo's week."""

    days = forms.MultipleChoiceField(
        label=_("Days"),
        choices=WEEKDAYS,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "peer sr-only"}),
        help_text=_("Tick every day this class runs."),
    )

    class Meta:
        model = ClassTemplate
        fields = (
            "name",
            "style",
            "start_time",
            "duration_minutes",
            "room",
            "capacity",
            "age_min",
            "age_max",
            "active_from",
            "active_to",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": TEXT, "autofocus": True}),
            "style": forms.Select(attrs={"class": TEXT}),
            "start_time": forms.TimeInput(attrs={"class": TEXT, "type": "time"}),
            "duration_minutes": forms.NumberInput(attrs={"class": TEXT, "min": 5, "max": 480}),
            "room": forms.TextInput(attrs={"class": TEXT}),
            "capacity": forms.NumberInput(attrs={"class": TEXT, "min": 0}),
            "age_min": forms.NumberInput(attrs={"class": TEXT, "min": 0, "max": 120}),
            "age_max": forms.NumberInput(attrs={"class": TEXT, "min": 0, "max": 120}),
            "active_from": forms.DateInput(attrs={"class": TEXT, "type": "date"}),
            "active_to": forms.DateInput(attrs={"class": TEXT, "type": "date"}),
        }
        labels = {
            "name": _("Class name"),
            "style": _("Style"),
            "start_time": _("Starts at"),
            "duration_minutes": _("Length (minutes)"),
            "room": _("Room"),
            "capacity": _("Maximum students"),
            "age_min": _("Youngest age"),
            "age_max": _("Oldest age"),
            "active_from": _("Runs from"),
            "active_to": _("Runs until"),
        }
        help_texts = {
            "name": _('What people call it — "Kids Beginners", "Adult Sparring".'),
            "capacity": _("Leave at 0 for no limit."),
            "age_min": _("Leave at 0 if any age may attend."),
            "age_max": _("Leave at 0 for no upper limit."),
            "active_to": _("Leave empty if it runs indefinitely."),
        }

    def __init__(self, *args, actor=None, dojo: Dojo, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.dojo = dojo
        # ⚠ Only styles this dojo actually teaches. Offering the rest invites a
        # boxing class on a karate-only timetable, and the enrolment tracks that
        # hang off style would then never match anybody attending.
        self.fields["style"].queryset = Style.objects.for_actor(actor).filter(dojos=dojo)
        self.fields["style"].required = False
        self.fields["room"].required = False
        self.fields["active_to"].required = False
        if self.instance.pk:
            self.fields["days"].initial = days_from_rrule(self.instance.rrule)

    def clean(self):
        cleaned = super().clean()
        age_min = cleaned.get("age_min") or 0
        age_max = cleaned.get("age_max") or 0
        if age_max and age_min and age_max < age_min:
            self.add_error("age_max", _("The oldest age cannot be below the youngest."))

        active_from = cleaned.get("active_from")
        active_to = cleaned.get("active_to")
        if active_from and active_to and active_to < active_from:
            self.add_error("active_to", _("The end date cannot be before the start date."))
        return cleaned

    def save(self, commit=True):
        template = super().save(commit=False)
        template.dojo = self.dojo
        template.rrule = rrule_from_days(self.cleaned_data["days"])
        if commit:
            template.save()
        return template
