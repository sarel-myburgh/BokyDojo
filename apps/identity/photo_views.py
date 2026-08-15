"""Secure student photograph upload and rendering — TODO 1.1.14."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.core.documents import open_document

from .forms import StudentPhotoUploadForm
from .models import GovernanceModel, StudentProfile
from .permissions import Action, require
from .photos import (
    active_photo_policy,
    current_photo_consent,
    current_student_photo,
    upload_student_photo,
)


def _profile_for_actor(actor, person_id) -> StudentProfile:
    return get_object_or_404(
        StudentProfile.objects.for_actor(actor).select_related(
            "person", "person__organization", "home_dojo"
        ),
        person_id=person_id,
    )


def _governance(profile: StudentProfile) -> str:
    return profile.person.organization.governance_model or GovernanceModel.CENTRAL


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def student_photo_upload_view(request, person_id):
    profile = _profile_for_actor(request.actor, person_id)
    require(
        request.actor,
        Action.PERSON_EDIT,
        profile,
        governance_model=_governance(profile),
    )
    policy = active_photo_policy(profile)
    consent = (
        current_photo_consent(profile=profile, actor=request.actor) if policy is not None else None
    )
    form = StudentPhotoUploadForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        try:
            upload_student_photo(
                profile=profile,
                uploaded_file=form.cleaned_data["photo"],
                actor=request.actor,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, _("Student photograph updated."))
            return redirect("student-detail", person_id=person_id)

    return render(
        request,
        "students/photo_upload.html",
        {
            "profile": profile,
            "form": form,
            "policy": policy,
            "consent": consent,
        },
    )


@login_required
@never_cache
@require_GET
def student_photo_view(request, person_id):
    profile = _profile_for_actor(request.actor, person_id)
    photo = current_student_photo(profile=profile, actor=request.actor)
    if photo is None:
        raise Http404

    payload = open_document(
        request.actor,
        photo,
        governance_model=_governance(profile),
    )
    response = HttpResponse(payload, content_type=photo.content_type)
    response["Content-Disposition"] = "inline"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
