"""Event forms — the staff one, and the one a stranger fills in."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.identity.models import Dojo

from .models import Event, EventFormField, EventRsvp

TEXT = "w-full border border-gray-300 bg-white px-3 py-2 text-sm"


class EventForm(forms.ModelForm):
    price = forms.DecimalField(
        label=_("Price"),
        required=False,
        min_value=0,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": TEXT, "step": "0.01", "min": "0"}),
        help_text=_("Leave empty or 0 if it is free. Shown on the form; no money is taken here."),
    )

    class Meta:
        model = Event
        fields = (
            "name",
            "kind",
            "dojo",
            "summary",
            "details",
            "starts_at",
            "ends_at",
            "location_name",
            "address",
            "latitude",
            "longitude",
            "price_currency",
            "payment_note",
            "capacity",
            "rsvp_closes_at",
            "visibility",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": TEXT, "autofocus": True}),
            "kind": forms.Select(attrs={"class": TEXT}),
            "dojo": forms.Select(attrs={"class": TEXT}),
            "summary": forms.TextInput(attrs={"class": TEXT}),
            "details": forms.Textarea(attrs={"class": TEXT, "rows": 5}),
            "starts_at": forms.DateTimeInput(attrs={"class": TEXT, "type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"class": TEXT, "type": "datetime-local"}),
            "location_name": forms.TextInput(attrs={"class": TEXT}),
            "address": forms.Textarea(attrs={"class": TEXT, "rows": 2}),
            "latitude": forms.NumberInput(attrs={"class": TEXT, "step": "any"}),
            "longitude": forms.NumberInput(attrs={"class": TEXT, "step": "any"}),
            "price_currency": forms.TextInput(attrs={"class": TEXT, "maxlength": 3}),
            "payment_note": forms.TextInput(attrs={"class": TEXT}),
            "capacity": forms.NumberInput(attrs={"class": TEXT, "min": 0}),
            "rsvp_closes_at": forms.DateTimeInput(attrs={"class": TEXT, "type": "datetime-local"}),
            "visibility": forms.Select(attrs={"class": TEXT}),
        }
        labels = {
            "name": _("Event name"),
            "kind": _("What kind"),
            "dojo": _("Dojo"),
            "summary": _("One-line summary"),
            "details": _("Details"),
            "starts_at": _("Starts"),
            "ends_at": _("Ends"),
            "location_name": _("Place"),
            "address": _("Address"),
            "latitude": _("Map pin — latitude"),
            "longitude": _("Map pin — longitude"),
            "price_currency": _("Currency"),
            "payment_note": _("How to pay"),
            "capacity": _("Places available"),
            "rsvp_closes_at": _("Replies close"),
            "visibility": _("Who can see the form"),
        }
        help_texts = {
            "dojo": _("Leave blank for a whole-organisation event."),
            "summary": _("One line, shown at the top of the invitation."),
            "latitude": _(
                "Optional. In Google Maps, right-click the spot and click the numbers to copy them."
            ),
            "payment_note": _("For example: pay at the door, or bank transfer details."),
            "capacity": _("0 means no limit."),
            "visibility": _(
                '"Anyone with the link" gives a secret address and asks search engines to '
                'ignore it. "Public" can be found by anyone.'
            ),
        }

    def __init__(self, *args, actor=None, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.organization = organization
        self.fields["dojo"].queryset = Dojo.objects.for_actor(actor).order_by("name")
        self.fields["dojo"].required = False
        for optional in ("ends_at", "rsvp_closes_at", "latitude", "longitude"):
            self.fields[optional].required = False
        if self.instance.pk and self.instance.price_minor_units:
            self.fields["price"].initial = self.instance.price_minor_units / 100

    def clean(self):
        cleaned = super().clean()
        # ⚠ Both or neither, checked here as well as on the model so the message
        # lands on the field rather than as a page-level error.
        lat, lon = cleaned.get("latitude"), cleaned.get("longitude")
        if (lat is None) != (lon is None):
            self.add_error("longitude", _("Enter both latitude and longitude, or neither."))
        return cleaned

    def save(self, commit=True):
        event = super().save(commit=False)
        price = self.cleaned_data.get("price") or 0
        event.price_minor_units = int(round(price * 100))
        if self.organization is not None:
            event.organization = self.organization
        if commit:
            event.save()
        return event


class RsvpForm(forms.ModelForm):
    """⚠ Filled in by the public. Only these five fields exist on it.

    Nothing here refers to a student, a member, or anything already in the
    database — a reply is a new row typed by a stranger, and it is not matched
    against anybody. Matching would mean the form could be used to probe whether
    a given name or email is on our books.
    """

    class Meta:
        model = EventRsvp
        fields = ("name", "email", "phone", "party_size", "note")
        widgets = {
            "name": forms.TextInput(attrs={"class": TEXT, "autocomplete": "name"}),
            "email": forms.EmailInput(attrs={"class": TEXT, "autocomplete": "email"}),
            "phone": forms.TextInput(attrs={"class": TEXT, "autocomplete": "tel"}),
            "party_size": forms.NumberInput(attrs={"class": TEXT, "min": 1, "max": 50}),
            "note": forms.Textarea(attrs={"class": TEXT, "rows": 3}),
        }
        labels = {
            "name": _("Your name"),
            "email": _("Email"),
            "phone": _("Phone"),
            "party_size": _("How many of you are coming"),
            "note": _("Anything we should know"),
        }
        help_texts = {
            "email": _("Either an email or a phone number is enough."),
        }

    def __init__(self, *args, questions=None, **kwargs):
        """``questions`` are this event's own extra fields, appended in order.

        ⚠ Built per instance rather than declared on the class: two events open
        at once have different questions, and a class-level field would leak one
        event's form onto the other's page.
        """
        super().__init__(*args, **kwargs)
        self.questions = list(questions or [])
        for question in self.questions:
            self.fields[question.field_name] = _field_for(question)

    def clean_party_size(self):
        size = self.cleaned_data.get("party_size") or 1
        if size < 1 or size > 50:
            raise forms.ValidationError(_("Between 1 and 50 people."))
        return size

    def answers(self) -> dict:
        """What was typed into the custom questions, keyed by field id.

        ⚠ Stored against the question's id and label together. The id is what
        matches an answer to its question; the label is copied in so a rename —
        or a deleted question — does not turn last month's replies into
        anonymous values nobody can interpret.
        """
        collected = {}
        for question in self.questions:
            value = self.cleaned_data.get(question.field_name)
            if value in (None, "", []):
                continue
            collected[str(question.pk)] = {"label": question.label, "value": value}
        return collected

    def custom_fields(self):
        """The bound custom fields, for rendering them apart from the fixed ones."""
        return [self[question.field_name] for question in self.questions]


def _field_for(question: EventFormField) -> forms.Field:
    """One Django field for one admin-defined question."""
    common = {
        "label": question.label,
        "required": question.is_required,
        "help_text": question.help_text,
    }
    kind = question.kind
    if kind == EventFormField.Kind.PARAGRAPH:
        return forms.CharField(
            max_length=1000, widget=forms.Textarea(attrs={"class": TEXT, "rows": 3}), **common
        )
    if kind == EventFormField.Kind.NUMBER:
        return forms.IntegerField(widget=forms.NumberInput(attrs={"class": TEXT}), **common)
    if kind == EventFormField.Kind.CHOICE:
        return forms.ChoiceField(
            choices=[("", "—")] + [(o, o) for o in question.option_list],
            widget=forms.Select(attrs={"class": TEXT}),
            **common,
        )
    if kind == EventFormField.Kind.CHECKBOX:
        # ⚠ required on a tick box means "must be ticked" — that is what it means
        # in HTML too, and it is how a waiver acknowledgement has to behave.
        return forms.BooleanField(widget=forms.CheckboxInput(), **common)
    # ⚠ Capped. This is a public text box and the only thing between it and a
    # database full of somebody's novel is this number.
    return forms.CharField(max_length=200, widget=forms.TextInput(attrs={"class": TEXT}), **common)


class EventFormFieldForm(forms.ModelForm):
    """Adding a question to an event's form."""

    class Meta:
        model = EventFormField
        fields = ("label", "kind", "help_text", "options", "is_required")
        widgets = {
            "label": forms.TextInput(attrs={"class": TEXT, "autofocus": True}),
            "kind": forms.Select(attrs={"class": TEXT}),
            "help_text": forms.TextInput(attrs={"class": TEXT}),
            "options": forms.Textarea(attrs={"class": TEXT, "rows": 4}),
        }
        labels = {
            "label": _("Question"),
            "kind": _("Answer type"),
            "help_text": _("Hint below the question"),
            "options": _("Options"),
            "is_required": _("They must answer this"),
        }
        help_texts = {
            "label": _('What you want to ask — "Current grade", "Any allergies?"'),
            "options": _('Only for "Pick one". One option per line.'),
        }

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        options = [ln.strip() for ln in (cleaned.get("options") or "").splitlines() if ln.strip()]
        if kind == EventFormField.Kind.CHOICE and len(options) < 2:
            self.add_error("options", _("Give at least two options, one per line."))
        return cleaned
