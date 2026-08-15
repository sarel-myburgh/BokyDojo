"""Guardian management screens for the student family hub — TODO 1.1.4."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .forms import GuardianForm
from .guardians import add_guardian, remove_guardian, update_guardian
from .models import GovernanceModel, GuardianLink, StudentProfile
from .permissions import Action, require


def _profile(actor, person_id):
    return get_object_or_404(
        StudentProfile.objects.for_actor(actor).select_related(
            "person", "person__organization", "home_dojo"
        ),
        person_id=person_id,
    )


def _require_edit(actor, profile):
    governance = profile.person.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.PERSON_EDIT, profile, governance_model=governance)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def guardian_add_view(request, person_id):
    profile = _profile(request.actor, person_id)
    _require_edit(request.actor, profile)
    form = GuardianForm(request.POST or None, actor=request.actor)
    if request.method == "POST" and form.is_valid():
        try:
            add_guardian(
                profile=profile,
                actor=request.actor,
                existing_guardian=form.cleaned_data.get("existing_guardian"),
                contact_values=form.contact_values(),
                link_values=form.link_values(),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, _("Guardian added."))
            return redirect("student-detail", person_id=person_id)
    return render(
        request,
        "students/guardian_form.html",
        {"profile": profile, "form": form, "editing": False},
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def guardian_edit_view(request, person_id, link_id):
    profile = _profile(request.actor, person_id)
    _require_edit(request.actor, profile)
    link = get_object_or_404(
        GuardianLink.objects.for_actor(request.actor).select_related("guardian", "student"),
        pk=link_id,
        student=profile.person,
    )
    form = GuardianForm(request.POST or None, actor=request.actor, link=link)
    if request.method == "POST" and form.is_valid():
        try:
            update_guardian(
                link=link,
                profile=profile,
                actor=request.actor,
                contact_values=form.contact_values(),
                link_values=form.link_values(),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, _("Guardian updated."))
            return redirect("student-detail", person_id=person_id)
    return render(
        request,
        "students/guardian_form.html",
        {"profile": profile, "form": form, "editing": True, "link": link},
    )


@login_required
@require_POST
def guardian_remove_view(request, person_id, link_id):
    profile = _profile(request.actor, person_id)
    _require_edit(request.actor, profile)
    link = get_object_or_404(
        GuardianLink.objects.for_actor(request.actor),
        pk=link_id,
        student=profile.person,
    )
    remove_guardian(link=link, profile=profile, actor=request.actor)
    messages.success(request, _("Guardian unlinked. The person's other records were retained."))
    return redirect("student-detail", person_id=person_id)
