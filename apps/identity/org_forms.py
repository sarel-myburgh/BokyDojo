"""Forms for the things an organisation is made of — styles, dojos, people.

⚠ Every queryset here is scoped with ``for_actor``. A ``ModelChoiceField`` whose
queryset is unscoped is a cross-tenant hole with a dropdown attached: the posted
value is validated against whatever the queryset admits, so scoping the *display*
and not the *queryset* would let a crafted POST attach another organisation's
style to this dojo.
"""

from __future__ import annotations

import datetime

from django import forms
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.identity.models import Dojo, Person
from apps.ranks.models import Style
from apps.staffing.models import InstructorProfile

TEXT = "w-full border border-gray-300 bg-white px-3 py-2 text-sm"


class StyleForm(forms.ModelForm):
    class Meta:
        model = Style
        fields = ("name", "is_ranked")
        widgets = {"name": forms.TextInput(attrs={"class": TEXT, "autofocus": True})}
        labels = {"name": _("Style name"), "is_ranked": _("This style uses ranks")}

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization

    def clean_name(self):
        name = (self.cleaned_data["name"] or "").strip()
        clash = Style.objects.for_organization(self.organization.pk).filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(
                _("This organisation already teaches a style by that name.")
            )
        return name


class DojoForm(forms.ModelForm):
    """⚠ ``styles`` is declared here, not listed in ``Meta.fields``.

    A ``ModelForm`` builds its initial data with ``model_to_dict``, which reads an
    M2M through the target's default manager — a ``ScopedManager`` that refuses
    to evaluate without an actor. So merely constructing this form with an
    ``instance`` would raise. Declaring the field explicitly keeps Django away
    from the descriptor entirely; the current value is supplied from the through
    model instead.
    """

    styles = forms.ModelMultipleChoiceField(
        label=_("Styles taught"),
        queryset=Style.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        help_text=_(
            "Enrolling a student here gives them a track for each of these, "
            "so this decides what members are recorded as training."
        ),
    )

    class Meta:
        model = Dojo
        fields = (
            "name",
            "slug",
            "timezone",
            "currency",
            "city",
            "country",
            "contact_email",
            "contact_phone",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": TEXT, "autofocus": True}),
            "slug": forms.TextInput(attrs={"class": TEXT}),
            "timezone": forms.TextInput(attrs={"class": TEXT}),
            "currency": forms.TextInput(attrs={"class": TEXT}),
            "city": forms.TextInput(attrs={"class": TEXT}),
            "country": forms.TextInput(attrs={"class": TEXT}),
            "contact_email": forms.EmailInput(attrs={"class": TEXT}),
            "contact_phone": forms.TextInput(attrs={"class": TEXT}),
        }
        help_texts = {
            "slug": _("Short name used in links. Left blank, it is made from the name."),
        }

    def __init__(self, *args, actor=None, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.fields["slug"].required = False
        self.fields["styles"].queryset = Style.objects.for_actor(actor).order_by("name")
        if self.instance.pk:
            from apps.core.relations import scoped_m2m_ids

            self.fields["styles"].initial = scoped_m2m_ids(self.instance, "styles")

    def clean_slug(self):
        slug = (self.cleaned_data.get("slug") or "").strip()
        if not slug:
            slug = slugify(self.data.get("name", ""))[:100]
        if not slug:
            raise forms.ValidationError(_("A short name is required."))
        clash = Dojo.objects.for_organization(self.organization.pk).filter(slug=slug)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("Another dojo already uses that short name."))
        return slug

    def clean_timezone(self):
        name = (self.cleaned_data.get("timezone") or "").strip()
        # ⚠ Validated here rather than trusted. A bad zone name would otherwise be
        # written and then silently fall back to UTC on every screen that renders
        # this dojo's class times — an 18:30 class showing as 11:30.
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise forms.ValidationError(
                _("'%(name)s' is not a known timezone. Use a name like Asia/Phnom_Penh.")
                % {"name": name}
            ) from exc
        return name


class StudentForm(forms.Form):
    """Enough to put somebody on the roll. Everything else is on their page."""

    given_name = forms.CharField(
        label=_("Given name"),
        max_length=100,
        widget=forms.TextInput(attrs={"class": TEXT, "autofocus": True}),
    )
    family_name = forms.CharField(
        label=_("Family name"),
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": TEXT}),
    )
    date_of_birth = forms.DateField(
        label=_("Date of birth"),
        required=False,
        widget=forms.DateInput(attrs={"class": TEXT, "type": "date"}),
        help_text=_("Used to place them on a junior or adult ladder where a style has both."),
    )
    email = forms.EmailField(
        label=_("Email"), required=False, widget=forms.EmailInput(attrs={"class": TEXT})
    )
    phone = forms.CharField(
        label=_("Phone"),
        max_length=40,
        required=False,
        widget=forms.TextInput(attrs={"class": TEXT}),
    )
    dojo = forms.ModelChoiceField(
        label=_("Dojo"),
        queryset=Dojo.objects.none(),
        widget=forms.Select(attrs={"class": TEXT}),
        help_text=_("They get a style track for everything this dojo teaches."),
    )
    started_on = forms.DateField(
        label=_("Joined on"),
        widget=forms.DateInput(attrs={"class": TEXT, "type": "date"}),
        initial=datetime.date.today,
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["dojo"].queryset = Dojo.objects.for_actor(actor).order_by("name")

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob and dob > datetime.date.today():
            raise forms.ValidationError(_("A date of birth cannot be in the future."))
        return dob


class InstructorForm(forms.Form):
    """A person, a role, a dojo assignment and pay details, in one go.

    ⚠ Four records, and creating three of the four is useless. An instructor
    without a ``RoleAssignment`` cannot sign in; without an
    ``InstructorAssignment`` the substitution check refuses them (found dark in
    the seed once already); without an ``InstructorProfile`` they have no pay
    setup and their timesheet lines carry no rate.
    """

    given_name = forms.CharField(
        label=_("Given name"),
        max_length=100,
        widget=forms.TextInput(attrs={"class": TEXT, "autofocus": True}),
    )
    family_name = forms.CharField(
        label=_("Family name"),
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": TEXT}),
    )
    email = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(attrs={"class": TEXT}),
        help_text=_("They sign in with this. It must be unique."),
    )
    dojo = forms.ModelChoiceField(
        label=_("Dojo"),
        queryset=Dojo.objects.none(),
        widget=forms.Select(attrs={"class": TEXT}),
    )
    styles = forms.ModelMultipleChoiceField(
        label=_("Styles taught"),
        queryset=Style.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )
    is_head_instructor = forms.BooleanField(label=_("Head instructor"), required=False)
    pay_type = forms.ChoiceField(
        label=_("Pay"),
        choices=InstructorProfile.PayType.choices,
        widget=forms.Select(attrs={"class": TEXT}),
    )
    pay_rate = forms.DecimalField(
        label=_("Rate"),
        min_value=0,
        decimal_places=2,
        max_digits=9,
        initial=0,
        widget=forms.NumberInput(attrs={"class": TEXT, "step": "0.01"}),
        help_text=_("Per hour or per class, depending on the pay type above."),
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["dojo"].queryset = Dojo.objects.for_actor(actor).order_by("name")
        self.fields["styles"].queryset = Style.objects.for_actor(actor).order_by("name")

    def clean_email(self):
        from apps.identity.models import User

        email = (self.cleaned_data["email"] or "").strip().lower()
        # ⚠ Users are global, not tenant-scoped — an address already in use
        # anywhere is a collision, and the check must not be scoped or it would
        # produce a database error instead of a field message.
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("Somebody already signs in with that address."))
        return email


class PersonSearchMixin:
    """Shared helper for screens that resolve an existing person by name."""

    @staticmethod
    def people(actor):
        return Person.objects.for_actor(actor).order_by("family_name", "given_name")
