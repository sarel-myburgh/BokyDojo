"""Permission-checked manual promotion screens."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.core.reports import csv_report_response
from apps.identity.models import GovernanceModel, StudentProfile
from apps.identity.permissions import ROLE_ACTIONS, Action, PermissionDenied, require

from .forms import BulkPromotionForm, ManualPromotionForm
from .models import StudentStyleTrack
from .promotions import bulk_promote_students, promote_student


def _holds_anywhere(actor, action):
    return any(action in ROLE_ACTIONS.get(role, set()) for role in actor.role_names())


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def manual_promotion_view(request, person_id, track_id):
    profile = get_object_or_404(
        StudentProfile.objects.for_actor(request.actor).select_related(
            "person", "person__organization", "home_dojo"
        ),
        person_id=person_id,
    )
    governance = profile.person.organization.governance_model or GovernanceModel.CENTRAL
    require(request.actor, Action.RANK_AWARD, profile, governance_model=governance)
    track = get_object_or_404(
        StudentStyleTrack.objects.for_organization(profile.organization_id).select_related(
            "student", "style", "ladder", "current_rank"
        ),
        pk=track_id,
        student=profile.person,
    )
    form = ManualPromotionForm(request.POST or None, track=track, actor=request.actor)
    if request.method == "POST" and form.is_valid():
        try:
            promote_student(
                profile=profile,
                track=track,
                rank=form.cleaned_data["rank"],
                awarded_on=form.cleaned_data["awarded_on"],
                certificate_number=form.cleaned_data["certificate_number"],
                notes=form.cleaned_data["notes"],
                actor=request.actor,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, _("Promotion recorded."))
            return HttpResponseRedirect(
                reverse("student-detail", args=[profile.person_id]) + "?tab=rank"
            )
    return render(
        request,
        "ranks/promote.html",
        {"profile": profile, "track": track, "form": form},
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def bulk_promotion_view(request):
    actor = request.actor
    if not _holds_anywhere(actor, Action.RANK_AWARD):
        raise PermissionDenied(Action.RANK_AWARD, actor)

    form = BulkPromotionForm(request.POST or None, actor=actor)
    if request.method == "POST" and form.is_valid():
        try:
            awards = bulk_promote_students(
                profiles=form.cleaned_data["student_ids"],
                rank=form.cleaned_data["rank"],
                awarded_on=form.cleaned_data["awarded_on"],
                notes=form.cleaned_data["notes"],
                actor=actor,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(
                request,
                _("%(count)s promotions recorded.") % {"count": len(awards)},
            )
            return HttpResponseRedirect(reverse("student-list"))
    return render(request, "ranks/bulk_promote.html", {"form": form})


@login_required
@never_cache
@require_GET
def active_students_by_rank_view(request):
    actor = request.actor
    if not _holds_anywhere(actor, Action.REPORT_VIEW):
        raise PermissionDenied(Action.REPORT_VIEW, actor)

    tracks = (
        StudentStyleTrack.objects.for_organization(actor.organization_id)
        .filter(status=StudentStyleTrack.Status.ACTIVE)
        .select_related("style", "ladder", "current_rank")
        .order_by("style__name", "ladder__name", "current_rank__order")
    )
    profiles = list(
        StudentProfile.objects.for_actor(actor)
        .filter(status=StudentProfile.Status.ACTIVE)
        .select_related("person", "home_dojo")
        .prefetch_related(
            Prefetch("person__style_tracks", queryset=tracks, to_attr="report_tracks")
        )
        .order_by("person__family_name", "person__given_name")
    )

    grouped = {}
    csv_rows = []
    for profile in profiles:
        visible_tracks = profile.person.report_tracks
        if not visible_tracks:
            visible_tracks = [None]
        for track in visible_tracks:
            style_name = track.style.name if track else ""
            ladder_name = track.ladder.name if track else ""
            rank_name = (
                track.current_rank.name
                if track and track.current_rank_id
                else _("Ungraded")
                if track
                else _("No active rank track")
            )
            rank_order = track.current_rank.order if track and track.current_rank_id else -1
            key = (style_name, ladder_name, rank_order, str(rank_name))
            group = grouped.setdefault(
                key,
                {
                    "style": style_name or _("No style"),
                    "ladder": ladder_name or _("No active track"),
                    "rank": rank_name,
                    "students": [],
                },
            )
            group["students"].append(profile)
            csv_rows.append(
                [
                    profile.home_dojo.name if profile.home_dojo else "",
                    profile.person.full_name,
                    style_name,
                    ladder_name,
                    rank_name,
                ]
            )

    groups = sorted(
        grouped.values(),
        key=lambda group: (str(group["style"]), str(group["ladder"]), str(group["rank"])),
    )
    if request.GET.get("format") == "csv":
        return csv_report_response(
            filename="active-students-by-rank.csv",
            header=["Dojo", "Student", "Style", "Ladder", "Rank"],
            rows=csv_rows,
            actor=actor,
        )
    return render(
        request,
        "ranks/report.html",
        {"groups": groups, "student_count": len(profiles)},
    )
