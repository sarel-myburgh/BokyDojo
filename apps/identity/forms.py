"""Forms used before an organisation exists."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.money import exponent_for
from apps.identity.guardians import guardian_candidates
from apps.identity.lifecycle import BULK_TRANSITION_LIMIT, allowed_student_transitions
from apps.identity.models import Dojo, GovernanceModel, GuardianLink, Person, StudentProfile, User
from apps.ranks.models import Rank


class FirstRunForm(forms.Form):
    organization_name = forms.CharField(max_length=200)
    organization_slug = forms.SlugField(max_length=100, required=False)
    governance_model = forms.ChoiceField(choices=GovernanceModel.choices)
    country = forms.CharField(max_length=2, initial="KH")
    timezone = forms.CharField(max_length=64, initial="Asia/Phnom_Penh")
    currency = forms.CharField(max_length=3, initial="USD")
    dojo_name = forms.CharField(max_length=200)
    dojo_city = forms.CharField(max_length=100, required=False)
    admin_given_name = forms.CharField(max_length=100)
    admin_family_name = forms.CharField(max_length=100, required=False)
    admin_email = forms.EmailField()
    admin_password = forms.CharField(widget=forms.PasswordInput)
    admin_password_confirm = forms.CharField(widget=forms.PasswordInput)
    setup_token = forms.CharField(required=False, widget=forms.PasswordInput)

    def clean_organization_slug(self):
        value = self.cleaned_data.get("organization_slug")
        if value:
            return value
        generated = slugify(self.data.get("organization_name", ""))
        if not generated:
            raise ValidationError("Enter a valid organisation slug.")
        return generated[:100]

    def clean_country(self):
        value = self.cleaned_data["country"].upper()
        if len(value) != 2 or not value.isalpha():
            raise ValidationError("Enter a two-letter country code.")
        return value

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError("Enter a valid IANA timezone.") from exc
        return value

    def clean_currency(self):
        value = self.cleaned_data["currency"].upper()
        try:
            exponent_for(value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return value

    def clean_admin_email(self):
        return self.cleaned_data["admin_email"].strip().lower()

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("admin_password")
        confirmation = cleaned.get("admin_password_confirm")
        if password and confirmation and password != confirmation:
            self.add_error("admin_password_confirm", "The two passwords do not match.")
        if password:
            provisional = User(email=cleaned.get("admin_email", ""))
            try:
                password_validation.validate_password(password, provisional)
            except ValidationError as exc:
                self.add_error("admin_password", exc)
        return cleaned


class ConsentDecisionForm(forms.Form):
    """One deliberate consent decision; signer capacity is derived server-side."""

    signer_id = forms.ChoiceField(label=_("Signing as"))
    signature_name = forms.CharField(
        label=_("Type the signer's full name"),
        max_length=200,
        help_text=_("This typed name is stored as the signature evidence."),
    )
    confirm = forms.BooleanField()

    def __init__(self, *args, signer_choices, confirm_label, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["signer_id"].choices = signer_choices
        self.fields["confirm"].label = confirm_label
        input_class = (
            "mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-gray-900"
        )
        self.fields["signer_id"].widget.attrs["class"] = input_class
        self.fields["signature_name"].widget.attrs.update(
            {"class": input_class, "autocomplete": "name"}
        )
        self.fields["confirm"].widget.attrs["class"] = (
            "h-5 w-5 rounded border-gray-300 text-gray-900"
        )


class StudentListFilterForm(forms.Form):
    """Validated, tenant-scoped filters for the student directory."""

    q = forms.CharField(label=_("Name, email, or phone"), required=False, max_length=200)
    dojo = forms.ModelChoiceField(label=_("Dojo"), required=False, queryset=Dojo.objects.none())
    rank = forms.ModelChoiceField(label=_("Rank"), required=False, queryset=Rank.objects.none())
    status = forms.ChoiceField(label=_("Status"), required=False)
    age_min = forms.IntegerField(label=_("Minimum age"), required=False, min_value=0, max_value=120)
    age_max = forms.IntegerField(label=_("Maximum age"), required=False, min_value=0, max_value=120)
    attendance_gap = forms.ChoiceField(
        label=_("No attendance for"),
        required=False,
        choices=(
            ("", _("Any time")),
            ("7", _("7 days")),
            ("14", _("14 days")),
            ("21", _("21 days")),
            ("30", _("30 days")),
            ("60", _("60 days")),
            ("90", _("90 days")),
        ),
    )
    unsigned_waiver = forms.BooleanField(label=_("Unsigned current waiver"), required=False)
    expired_licence = forms.BooleanField(label=_("Expired licence"), required=False)

    def __init__(self, *args, actor, allow_private_person_fields=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not allow_private_person_fields:
            self.fields["q"].label = _("Name")
            self.fields.pop("age_min")
            self.fields.pop("age_max")
        self.fields["dojo"].queryset = Dojo.objects.for_actor(actor).order_by("name")
        self.fields["rank"].queryset = (
            Rank.objects.for_organization(actor.organization_id)
            .select_related("ladder", "ladder__style")
            .order_by("ladder__style__name", "ladder__name", "order")
        )
        self.fields["status"].choices = [("", _("Any status")), *StudentProfile.Status.choices]
        control = "mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900"
        for name in ("q", "dojo", "rank", "status", "age_min", "age_max", "attendance_gap"):
            if name not in self.fields:
                continue
            self.fields[name].widget.attrs["class"] = control
        for name in ("unsigned_waiver", "expired_licence"):
            self.fields[name].widget.attrs["class"] = (
                "h-5 w-5 rounded border-gray-300 text-gray-900"
            )

    def clean(self):
        cleaned = super().clean()
        minimum = cleaned.get("age_min")
        maximum = cleaned.get("age_max")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValidationError(_("Minimum age cannot exceed maximum age."))
        return cleaned

    def canonical_filters(self) -> dict[str, str]:
        """Return only active, validated values in a JSON-safe stable schema."""
        if not self.is_valid():
            raise ValueError("Cannot canonicalise an invalid student filter form.")
        result = {}
        for key in ("q", "status", "attendance_gap"):
            value = self.cleaned_data.get(key)
            if value:
                result[key] = str(value).strip()
        for key in ("dojo", "rank"):
            value = self.cleaned_data.get(key)
            if value is not None:
                result[key] = str(value.pk)
        for key in ("age_min", "age_max"):
            value = self.cleaned_data.get(key)
            if value is not None:
                result[key] = str(value)
        for key in ("unsigned_waiver", "expired_licence"):
            if self.cleaned_data.get(key):
                result[key] = "on"
        return result


class StudentSegmentCreateForm(forms.Form):
    name = forms.CharField(label=_("Segment name"), max_length=80)
    filter_query = forms.CharField(max_length=2048, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update(
            {
                "class": "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900",
                "autocomplete": "off",
            }
        )

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class StudentStatusTransitionForm(forms.Form):
    to_status = forms.ChoiceField(label=_("New status"))
    hold_reason = forms.CharField(
        label=_("Administrative hold reason"),
        required=False,
        max_length=200,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Required for a hold. Keep medical details in the protected medical record."),
    )

    def __init__(self, *args, current_status, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_status"].choices = [
            (status, StudentProfile.Status(status).label)
            for status in allowed_student_transitions(current_status)
        ]
        control = "mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900"
        self.fields["to_status"].widget.attrs["class"] = control
        self.fields["hold_reason"].widget.attrs["class"] = control

    def clean_hold_reason(self):
        return self.cleaned_data["hold_reason"].strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("to_status") == StudentProfile.Status.ON_HOLD and not cleaned.get(
            "hold_reason"
        ):
            self.add_error("hold_reason", _("Enter an administrative reason for the hold."))
        return cleaned


class StudentBulkStatusForm(forms.Form):
    class Action:
        HOLD = "hold"
        RESUME = "resume"

    student_ids = forms.ModelMultipleChoiceField(
        queryset=StudentProfile.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    action = forms.ChoiceField(
        choices=((Action.HOLD, _("Place on hold")), (Action.RESUME, _("Resume training")))
    )
    hold_reason = forms.CharField(
        label=_("Administrative hold reason"),
        required=False,
        max_length=200,
        help_text=_(
            "Required when placing students on hold. Keep medical details in protected records."
        ),
    )

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student_ids"].queryset = (
            StudentProfile.objects.for_actor(actor)
            .select_related("person", "person__organization", "home_dojo")
            .order_by("person__family_name", "person__given_name")
        )
        control = "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900"
        for name in ("action", "hold_reason"):
            self.fields[name].widget.attrs["class"] = control

    def clean_hold_reason(self):
        return self.cleaned_data["hold_reason"].strip()

    def clean(self):
        cleaned = super().clean()
        profiles = cleaned.get("student_ids")
        if profiles is not None and len(profiles) > BULK_TRANSITION_LIMIT:
            self.add_error(
                "student_ids",
                _("Select at most %(limit)s students at once.") % {"limit": BULK_TRANSITION_LIMIT},
            )
        if cleaned.get("action") == self.Action.HOLD and not cleaned.get("hold_reason"):
            self.add_error("hold_reason", _("Enter an administrative reason for the hold."))
        return cleaned

    @property
    def target_status(self) -> str:
        if self.cleaned_data["action"] == self.Action.HOLD:
            return StudentProfile.Status.ON_HOLD
        return StudentProfile.Status.ACTIVE


class StudentPhotoUploadForm(forms.Form):
    photo = forms.FileField(
        label=_("Student photograph"),
        help_text=_("JPEG, PNG, GIF, or WebP. Metadata, including GPS, is removed."),
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png,image/gif,image/webp"}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["photo"].widget.attrs["class"] = (
            "mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2"
        )


class GuardianForm(forms.Form):
    existing_guardian = forms.ModelChoiceField(
        label=_("Link an existing guardian"),
        required=False,
        queryset=Person.objects.none(),
        help_text=_("Use this when the same guardian is already attached to a sibling."),
    )
    given_name = forms.CharField(label=_("Given name"), required=False, max_length=100)
    family_name = forms.CharField(label=_("Family name"), required=False, max_length=100)
    email = forms.EmailField(required=False)
    phone = forms.CharField(required=False, max_length=40)
    relationship = forms.ChoiceField(choices=GuardianLink.Relationship.choices)
    is_primary_contact = forms.BooleanField(label=_("Primary contact"), required=False)
    is_emergency_contact = forms.BooleanField(label=_("Emergency contact"), required=False)
    is_financially_responsible = forms.BooleanField(
        label=_("Financially responsible"), required=False
    )
    has_custody = forms.BooleanField(label=_("Has custody"), required=False)
    notes = forms.CharField(
        label=_("Safeguarding notes"),
        required=False,
        max_length=255,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Encrypted. Record only information needed to manage this relationship."),
    )

    def __init__(self, *args, actor, link=None, **kwargs):
        self.link = link
        super().__init__(*args, **kwargs)
        self.fields["existing_guardian"].queryset = guardian_candidates(actor).order_by(
            "family_name", "given_name"
        )
        if link is not None:
            self.fields.pop("existing_guardian")
            for field in ("given_name", "family_name", "email", "phone"):
                self.fields[field].initial = getattr(link.guardian, field)
            for field in (
                "relationship",
                "is_primary_contact",
                "is_emergency_contact",
                "is_financially_responsible",
                "has_custody",
                "notes",
            ):
                self.fields[field].initial = getattr(link, field)
        control = "mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900"
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = control
            else:
                field.widget.attrs["class"] = "h-5 w-5 rounded border-gray-300 text-gray-900"

    def clean(self):
        cleaned = super().clean()
        existing = cleaned.get("existing_guardian")
        if self.link is None and existing is None:
            if not (cleaned.get("given_name") or "").strip():
                self.add_error("given_name", _("Enter a given name for the new guardian."))
            if not cleaned.get("email") and not (cleaned.get("phone") or "").strip():
                self.add_error("email", _("Enter an email address, a phone number, or both."))
        if self.link is not None:
            if not (cleaned.get("given_name") or "").strip():
                self.add_error("given_name", _("Enter the guardian's given name."))
            if not cleaned.get("email") and not (cleaned.get("phone") or "").strip():
                self.add_error("email", _("Enter an email address, a phone number, or both."))
        return cleaned

    def contact_values(self):
        return {
            field: (self.cleaned_data.get(field) or "").strip()
            for field in ("given_name", "family_name", "email", "phone")
        }

    def link_values(self):
        return {
            field: self.cleaned_data.get(field, False)
            for field in (
                "relationship",
                "is_primary_contact",
                "is_emergency_contact",
                "is_financially_responsible",
                "has_custody",
                "notes",
            )
        }
