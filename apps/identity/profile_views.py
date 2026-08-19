"""Profile screens — a person's own details, and an administrator's view of them.

Plan §3. Two views over one form, because "edit my details" and "edit theirs"
differ only in who is allowed and where you land afterwards.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.documents import open_document

from .models import Person
from .permissions import PermissionDenied
from .profile_forms import PersonDetailsForm, ProfilePhotoForm
from .profiles import (
    current_profile_photo,
    is_self,
    may_edit_person,
    person_page_context,
    require_edit,
    upload_profile_photo,
)


def _person(request, person_id) -> Person:
    return get_object_or_404(Person.objects.for_actor(request.actor), pk=person_id)


def _detail_context(request, person: Person) -> dict:
    return person_page_context(person=person, actor=request.actor)


def _edit(request, person: Person, *, template: str, redirect_to, back_url: str):
    require_edit(request.actor, person)
    form = PersonDetailsForm(request.POST or None, instance=person)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Details updated."))
        if isinstance(redirect_to, tuple):
            return redirect(redirect_to[0], person_id=redirect_to[1])
        return redirect(redirect_to)
    return render(
        request,
        template,
        {"form": form, "back_url": back_url, **_detail_context(request, person)},
    )


def account_view(request):
    """Your own profile.

    ⚠ Dojos and grades are shown, never edited. Which dojo you belong to and
    what grade you hold are somebody else's decisions; an editable field here
    would be a self-service promotion.
    """
    person = _person(request, request.actor.person_id) if request.actor.person_id else None
    if person is None:
        # A login with no person behind it — the bootstrap superuser, typically.
        raise Http404("No profile for this account.")
    return render(request, "identity/account.html", _detail_context(request, person))


def account_edit_view(request):
    if not request.actor.person_id:
        raise Http404("No profile for this account.")
    person = _person(request, request.actor.person_id)
    return _edit(
        request,
        person,
        template="identity/account_edit.html",
        redirect_to="account",
        back_url=reverse("account"),
    )


def person_detail_view(request, person_id):
    """The one page for a person — plan §3.

    ⚠ Details, picture, dojos, grades, roles and sign-in, all here. There used
    to be a staff list and a separate roles screen alongside this, so the same
    person could be reached three ways and offered different things by each.
    """
    person = _person(request, person_id)
    if not may_edit_person(request.actor, person):
        raise PermissionDenied(action="person.edit", actor=request.actor)
    return render(request, "identity/person_detail.html", _detail_context(request, person))


def person_edit_view(request, person_id):
    person = _person(request, person_id)
    return _edit(
        request,
        person,
        template="identity/person_edit.html",
        redirect_to=("person-detail", person.pk),
        back_url=reverse("person-detail", args=[person.pk]),
    )


@require_POST
def profile_photo_upload_view(request, person_id):
    person = _person(request, person_id)
    form = ProfilePhotoForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            upload_profile_photo(
                person=person,
                uploaded_file=form.cleaned_data["photo"],
                actor=request.actor,
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, _("Profile picture updated."))
    else:
        messages.error(request, _("That file could not be used as a picture."))

    if is_self(request.actor, person):
        return redirect("account")
    return redirect("person-detail", person_id=person.pk)


def profile_photo_view(request, person_id):
    person = _person(request, person_id)
    photo = current_profile_photo(person=person, actor=request.actor)
    if photo is None:
        raise Http404("No profile picture.")

    payload = open_document(
        request.actor,
        photo,
        governance_model=person.organization.governance_model,
    )
    # ⚠ inline, unlike documents.download_headers, because this is an <img> src
    # and an attachment header would download it instead of showing it. Safe
    # here and not for documents generally: validate_upload re-encodes the
    # image, so what is stored is an image and not a polyglot, and the sandbox
    # CSP below stops it running anything even if that ever fails.
    response = HttpResponse(payload, content_type=photo.content_type)
    response["Content-Disposition"] = "inline"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
