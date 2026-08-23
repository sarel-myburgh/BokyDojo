"""Archiving students and removing people — plan §3."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from .lifecycle import archive_student, delete_person, restore_person, unarchive_student
from .models import GovernanceModel, Person, StudentProfile
from .permissions import Action, can


def _profile(request, person_id) -> StudentProfile:
    return get_object_or_404(
        StudentProfile.objects.for_actor(request.actor).select_related(
            "person", "person__organization", "home_dojo"
        ),
        person_id=person_id,
    )


@login_required
@require_POST
def student_archive_view(request, person_id):
    profile = _profile(request, person_id)
    try:
        archive_student(profile=profile, actor=request.actor)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(
            request,
            _("%(name)s has been archived. Their record and history are kept.")
            % {"name": profile.person.full_name},
        )
    return redirect("student-detail", person_id=person_id)


@login_required
@require_POST
def student_unarchive_view(request, person_id):
    profile = _profile(request, person_id)
    try:
        unarchive_student(profile=profile, actor=request.actor)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, _("Back on the active roll."))
    return redirect("student-detail", person_id=person_id)


@login_required
@require_http_methods(["GET", "POST"])
def person_delete_view(request, person_id):
    """Remove somebody, behind a confirmation that says what will happen.

    ⚠ A GET step on purpose. This is the most destructive control in the
    product, and the confirmation is where an administrator finds out that the
    record is kept, the sign-in stops, and it can be undone — none of which is
    obvious from a button labelled "remove".
    """
    person = get_object_or_404(Person.objects.for_actor(request.actor), pk=person_id)
    governance = person.organization.governance_model or GovernanceModel.CENTRAL
    if not can(request.actor, Action.PERSON_DELETE, person, governance_model=governance):
        from .permissions import PermissionDenied

        raise PermissionDenied(action=Action.PERSON_DELETE, actor=request.actor)

    if request.method == "POST":
        try:
            delete_person(person=person, actor=request.actor)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("person-detail", person_id=person.pk)
        messages.success(
            request,
            _("%(name)s has been removed. You can undo this from Removed people.")
            % {"name": person.full_name},
        )
        return redirect("org-settings")

    from apps.identity.profiles import person_page_context

    context = person_page_context(person=person, actor=request.actor)
    context["is_student"] = (
        StudentProfile.objects.for_actor(request.actor).filter(person=person).exists()
    )
    return render(request, "identity/person_delete.html", context)


@login_required
def removed_people_view(request):
    """Who has been removed, and the way back.

    ⚠ This list is the whole reason the delete is soft rather than final. Remove
    the wrong Kim and without this the only remedy is a database restore.
    """
    from .permissions import PermissionDenied

    # ⚠ Checked on the actor's roles rather than against a row, because the list
    # may legitimately be empty and there would then be nothing to check
    # against — which would leave the page open to anybody.
    if not _holds_delete(request.actor):
        raise PermissionDenied(action=Action.PERSON_DELETE, actor=request.actor)

    people = list(
        Person.objects.for_actor_including_deleted(request.actor)
        .filter(deleted_at__isnull=False)
        .select_related("organization")
        .order_by("-deleted_at")[:200]
    )
    return render(request, "identity/removed_people.html", {"people": people})


def _holds_delete(actor) -> bool:
    from .permissions import ROLE_ACTIONS

    return any(
        Action.PERSON_DELETE in ROLE_ACTIONS.get(role, set()) for role, _s, _d in actor.roles
    )


@login_required
@require_POST
def person_restore_view(request, person_id):
    person = get_object_or_404(
        Person.objects.for_actor_including_deleted(request.actor).filter(deleted_at__isnull=False),
        pk=person_id,
    )
    try:
        restore_person(person=person, actor=request.actor)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, _("%(name)s is back.") % {"name": person.full_name})
    return redirect("removed-people")
