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

from apps.identity.models import Dojo, Person, Role
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


class StyleCreateForm(StyleForm):
    """Name, whether it grades, and its first set of belts — all on one screen.

    ⚠ The belts are optional but the field is here on purpose. A style created
    without them is marked graded and has nothing to grade anybody on, which the
    settings screen then has to flag in amber; asking once at the point of
    creation avoids the half-made state entirely.
    """

    applies_to = forms.ChoiceField(
        label=_("These belts apply to"),
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": TEXT}),
    )
    dojo = forms.ModelChoiceField(
        label=_("Used at"),
        queryset=Dojo.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": TEXT}),
    )
    belts = forms.CharField(
        label=_("Belt levels"),
        required=False,
        widget=forms.Textarea(attrs={"class": TEXT, "rows": 10, "spellcheck": "false"}),
        help_text=_(
            "One per line, lowest grade first — 10th Kyu, 9th Kyu, and so on. "
            "Leave empty to add them later. Colours and minimum waits are set "
            "afterwards on the belt screen."
        ),
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.ranks.models import RankLadder

        self.fields["applies_to"].choices = RankLadder.AppliesTo.choices
        self.fields["applies_to"].initial = RankLadder.AppliesTo.ALL
        self.fields["dojo"].queryset = Dojo.objects.for_actor(actor).order_by("name")
        self.fields["dojo"].empty_label = _("All dojos (organisation default)")

    def clean_belts(self):
        raw = self.cleaned_data.get("belts") or ""
        names = [line.strip() for line in raw.splitlines() if line.strip()]
        seen: set[str] = set()
        for name in names:
            key = name.casefold()
            if key in seen:
                raise forms.ValidationError(_("'%(name)s' is listed twice.") % {"name": name})
            seen.add(key)
        return names

    def clean(self):
        cleaned = super().clean()
        # ⚠ Belts on an unranked style are a contradiction, not a harmless extra.
        # Silently dropping them would lose what somebody typed; saying so lets
        # them decide which half they meant.
        if cleaned.get("belts") and not cleaned.get("is_ranked"):
            self.add_error(
                "belts",
                _(
                    "This style is marked unranked, so it cannot have belts. Tick 'uses ranks', or clear these."
                ),
            )
        return cleaned


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


#: Roles that make somebody staff. ⚠ GUARDIAN and STUDENT are deliberately
#: absent: they are created through enrolment and the parent portal, not by an
#: admin handing out a role, and offering them here would produce a person with
#: a sign-in and no student record.
STAFF_ROLES = (
    Role.ORG_ADMIN,
    Role.DOJO_ADMIN,
    Role.INSTRUCTOR,
    Role.ASSISTANT_INSTRUCTOR,
    Role.FRONT_DESK,
    Role.SAFEGUARDING,
)

#: Roles that mean this person actually teaches, and therefore need an
#: InstructorAssignment and pay details. ⚠ DOJO_ADMIN is here because the role's
#: own label is "Dojo administrator / head instructor" — they take classes.
TEACHING_ROLES = (Role.DOJO_ADMIN, Role.INSTRUCTOR, Role.ASSISTANT_INSTRUCTOR)


class StaffForm(forms.Form):
    """One person, any number of roles — TODO 0.5.x, plan §3.

    ⚠ Roles are a *set*, not a choice. The permission layer has always read them
    that way (``Actor.roles`` is a frozenset and ``can()`` walks all of them, and
    RoleAssignment is unique on person+role+scope+dojo precisely so somebody can
    hold several) — it was only this screen that pretended a person had one job.
    An organisation administrator who also teaches Tuesday evenings is the
    ordinary case, not an exception.
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
    roles = forms.MultipleChoiceField(
        label=_("Roles"),
        choices=[(r, Role(r).label) for r in STAFF_ROLES],
        widget=forms.CheckboxSelectMultiple(),
        help_text=_("Somebody may hold more than one — an admin who also teaches, say."),
    )
    scope = forms.ChoiceField(
        label=_("These roles apply to"),
        choices=(("dojo", _("One dojo")), ("org", _("The whole organisation"))),
        initial="dojo",
        widget=forms.Select(attrs={"class": TEXT}),
    )
    dojo = forms.ModelChoiceField(
        label=_("Dojo"),
        queryset=Dojo.objects.none(),
        required=False,
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
        required=False,
        widget=forms.Select(attrs={"class": TEXT}),
    )
    pay_rate = forms.DecimalField(
        label=_("Rate"),
        min_value=0,
        decimal_places=2,
        max_digits=9,
        initial=0,
        required=False,
        widget=forms.NumberInput(attrs={"class": TEXT, "step": "0.01"}),
        help_text=_("Per hour or per class, depending on the pay type. Only for roles that teach."),
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["dojo"].queryset = Dojo.objects.for_actor(actor).order_by("name")
        self.fields["styles"].queryset = Style.objects.for_actor(actor).order_by("name")

    def clean_email(self):
        from apps.identity.models import User

        email = (self.cleaned_data["email"] or "").strip().lower()
        # ⚠ Users are global, not tenant-scoped — an address in use anywhere is a
        # collision, and scoping this check would produce a database error rather
        # than a field message.
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("Somebody already signs in with that address."))
        return email

    @property
    def teaches(self) -> bool:
        return any(role in TEACHING_ROLES for role in self.cleaned_data.get("roles", []))

    def clean(self):
        cleaned = super().clean()
        roles = cleaned.get("roles") or []
        scope = cleaned.get("scope")
        dojo = cleaned.get("dojo")

        if scope == "dojo" and dojo is None:
            self.add_error("dojo", _("Choose which dojo these roles apply to."))

        # ⚠ An organisation administrator is organisation-scoped by definition;
        # a dojo-scoped one would silently hold none of the powers the role
        # implies, because can() only grants a dojo-scoped role over that dojo's
        # own objects.
        if Role.ORG_ADMIN in roles and scope != "org":
            self.add_error(
                "scope",
                _("An organisation administrator applies to the whole organisation."),
            )

        # ⚠ Somebody who teaches needs a dojo whatever the role scope: the
        # InstructorAssignment that lets them be put on a class is per dojo, and
        # without one every substitution is refused.
        if any(role in TEACHING_ROLES for role in roles) and dojo is None:
            self.add_error("dojo", _("A role that teaches must be attached to a dojo."))

        if any(role in TEACHING_ROLES for role in roles) and not cleaned.get("pay_type"):
            self.add_error("pay_type", _("Choose how this person is paid."))

        return cleaned


class RoleGrantForm(forms.Form):
    """Add one role to somebody who already exists."""

    role = forms.ChoiceField(
        label=_("Role"),
        choices=[(r, Role(r).label) for r in STAFF_ROLES],
        widget=forms.Select(attrs={"class": TEXT}),
    )
    scope = forms.ChoiceField(
        label=_("Applies to"),
        choices=(("dojo", _("One dojo")), ("org", _("The whole organisation"))),
        initial="dojo",
        widget=forms.Select(attrs={"class": TEXT}),
    )
    dojo = forms.ModelChoiceField(
        label=_("Dojo"),
        queryset=Dojo.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": TEXT}),
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["dojo"].queryset = Dojo.objects.for_actor(actor).order_by("name")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("scope") == "dojo" and cleaned.get("dojo") is None:
            self.add_error("dojo", _("Choose which dojo this role applies to."))
        if cleaned.get("role") == Role.ORG_ADMIN and cleaned.get("scope") != "org":
            self.add_error(
                "scope", _("An organisation administrator applies to the whole organisation.")
            )
        return cleaned


class PersonSearchMixin:
    """Shared helper for screens that resolve an existing person by name."""

    @staticmethod
    def people(actor):
        return Person.objects.for_actor(actor).order_by("family_name", "given_name")
