"""Forms for editing a person's own details — plan §3."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Person, User

TEXT = "w-full border border-gray-300 bg-white px-3 py-2 text-sm"


class PersonDetailsForm(forms.ModelForm):
    """Name, contact details, language. Not roles, not dojos, not grades.

    ⚠ ``Person.email`` and ``User.email`` are two columns, and it is the second
    one you sign in with. Editing the first alone changes the address on the
    contact card while leaving the login untouched — an administrator would
    "fix" somebody's email and they would still be signing in with the old one,
    with nothing on screen to say so. So this form writes both, and refuses an
    address already used by another login.
    """

    class Meta:
        model = Person
        fields = (
            "given_name",
            "family_name",
            "preferred_name",
            "email",
            "phone",
            "locale",
        )
        widgets = {
            "given_name": forms.TextInput(attrs={"class": TEXT, "autofocus": True}),
            "family_name": forms.TextInput(attrs={"class": TEXT}),
            "preferred_name": forms.TextInput(attrs={"class": TEXT}),
            "email": forms.EmailInput(attrs={"class": TEXT}),
            "phone": forms.TextInput(attrs={"class": TEXT, "inputmode": "tel"}),
            "locale": forms.TextInput(attrs={"class": TEXT}),
        }
        help_texts = {
            "preferred_name": _("What they are actually called, if it differs."),
            "phone": _("Include the country code if they are not local."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.linked_user = User.objects.filter(person=self.instance).first()
        if self.linked_user is not None:
            self.fields["email"].help_text = _("They sign in with this address.")
        else:
            self.fields["email"].help_text = _("Contact address. They have no login yet.")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            if self.linked_user is not None:
                # Blanking it would leave a login nobody can name.
                raise forms.ValidationError(_("Somebody who signs in needs an email address."))
            return email
        # ⚠ Across every organisation, not just this one: User.email is globally
        # unique because it is the login. A clash with somebody in another
        # organisation is still a clash, and letting it through would surface as
        # an IntegrityError on save instead of a field error here.
        clash = User.objects.filter(email__iexact=email)
        if self.linked_user is not None:
            clash = clash.exclude(pk=self.linked_user.pk)
        if clash.exists():
            raise forms.ValidationError(_("Another account already uses this address."))
        return email

    def save(self, commit=True):
        person = super().save(commit=commit)
        if commit and self.linked_user is not None:
            new_email = self.cleaned_data.get("email") or ""
            if new_email and self.linked_user.email != new_email:
                self.linked_user.email = new_email
                self.linked_user.save(update_fields=["email"])
        return person


class ProfilePhotoForm(forms.Form):
    photo = forms.ImageField(
        label=_("Profile picture"),
        widget=forms.ClearableFileInput(attrs={"accept": "image/*", "class": "text-sm"}),
        help_text=_("A head-and-shoulders photo. JPEG, PNG, GIF, or WebP."),
    )
