"""Event forms — the staff one, and the one a stranger fills in."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.identity.models import Dojo

from .models import Event, EventRsvp

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

    def clean_party_size(self):
        size = self.cleaned_data.get("party_size") or 1
        if size < 1 or size > 50:
            raise forms.ValidationError(_("Between 1 and 50 people."))
        return size
