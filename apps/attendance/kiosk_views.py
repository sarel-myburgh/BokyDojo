"""The check-in screen — TODO 1.7, decision D1.

One screen. A grid of faces for the class the instructor is already looking at;
tap yours, it goes green, the queue moves on. Nothing else is reachable until the
instructor types their password.

⚠ **Every mark goes through ``mark_attendance``**, like the roster, the offline
sync endpoint and the importer. Four paths, one service, or they drift.

⚠ **The grid shows a first name and a face and nothing else.** No surname, no
date of birth, no rank, no alerts. It is on a screen a queue of children and
whichever parents are standing behind them can all read.
"""

from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.throttle import (
    LOGIN_POLICY,
    Throttled,
    enforce,
    register_failure,
    register_success,
)
from apps.identity.models import GovernanceModel, StudentProfile
from apps.identity.permissions import Action, require
from apps.identity.photos import current_student_photo
from apps.scheduling.models import ClassSession

from . import kiosk
from .models import AttendanceRecord
from .services import complete_session, mark_attendance, session_roster

#: Throttle scope for the "let me out" password.
KIOSK_EXIT_SCOPE = "kiosk-exit"


def _governance(session: ClassSession) -> str:
    return session.dojo.organization.governance_model or GovernanceModel.CENTRAL


def _get_session(actor, session_id) -> ClassSession:
    return get_object_or_404(
        ClassSession.objects.for_actor(actor).select_related(
            "dojo", "dojo__organization", "template"
        ),
        pk=session_id,
    )


def _tiles(session, actor):
    """One tile per student: first name, and a photo where consent allows.

    ⚠ A student without current photo consent gets a name tile in the same grid
    rather than a separate search mode. `1.7.9` asks for a fallback; a tile that
    looks slightly different is a smaller, calmer answer than a second screen,
    and it keeps the queue moving in one place. Nobody has to be told why their
    face is missing in front of the line.
    """
    tiles = []
    for entry in session_roster(session=session, actor=actor):
        profile = StudentProfile.objects.for_actor(actor).filter(person=entry.student).first()
        photo = None
        if profile is not None:
            photo = current_student_photo(profile=profile, actor=actor)
        record = entry.record
        tiles.append(
            {
                "student_id": str(entry.student.pk),
                # ⚠ First name only. See the module docstring.
                "name": entry.student.preferred_name or entry.student.given_name,
                "has_photo": photo is not None,
                "checked_in": record is not None
                and record.status in AttendanceRecord.ATTENDED_STATUSES,
                "marked": record is not None,
            }
        )
    return tiles


@login_required
@require_http_methods(["GET", "POST"])
def kiosk_view(request, session_id) -> HttpResponse:
    """Open, or continue, check-in for one class."""
    actor = request.actor
    session = _get_session(actor, session_id)
    require(actor, Action.ATTENDANCE_RECORD, session, governance_model=_governance(session))

    if request.method == "POST":
        # Entering is a POST so it cannot be reached by a stray link or a
        # prefetch, and so the lock is never applied by a GET somebody bookmarked.
        kiosk.start(request, session.pk)
        return redirect("kiosk", session_id=session.pk)

    locked = kiosk.locked_session_id(request)
    if locked and locked != str(session.pk):
        # Locked to a different class — send them back to the one in progress
        # rather than quietly switching, which would abandon a queue mid-line.
        return redirect("kiosk", session_id=locked)

    return render(
        request,
        "attendance/kiosk.html",
        {
            "session": session,
            "tiles": _tiles(session, actor),
            "locked": bool(locked),
        },
    )


@login_required
@require_POST
def kiosk_mark_view(request, session_id) -> JsonResponse:
    """Mark one student present. Called by a tap on the grid."""
    actor = request.actor
    session = _get_session(actor, session_id)

    raw = request.POST.get("student_id", "")
    try:
        student_id = uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        return JsonResponse({"error": "invalid_student"}, status=400)

    entry = next(
        (e for e in session_roster(session=session, actor=actor) if e.student.pk == student_id),
        None,
    )
    if entry is None:
        # ⚠ Only students on this session's roster. The posted id is client data,
        # and without this the screen becomes a way to mark anyone in the org.
        return JsonResponse({"error": "not_on_roster"}, status=404)

    status = request.POST.get("status") or AttendanceRecord.Status.PRESENT
    if status not in {AttendanceRecord.Status.PRESENT, AttendanceRecord.Status.ABSENT}:
        return JsonResponse({"error": "invalid_status"}, status=400)

    record, _created = mark_attendance(
        session=session,
        student=entry.student,
        status=status,
        actor=actor,
        method=AttendanceRecord.Method.SELF,
        client_generated_id=request.POST.get("client_generated_id", "")[:64],
    )
    # ⚠ A class checked in entirely on the door is still a class that was
    # taught. Without this it stays SCHEDULED for ever and 1.9.3 never drafts a
    # timesheet line for whoever ran it.
    complete_session(session, actor=actor)

    return JsonResponse(
        {
            "student_id": str(entry.student.pk),
            "status": record.status,
            "checked_in": record.status in AttendanceRecord.ATTENDED_STATUSES,
        }
    )


@login_required
@require_POST
def kiosk_exit_view(request, session_id) -> HttpResponse:
    """Leave check-in. Requires the instructor's password.

    ⚠ The friction is the feature. The device may be in a student's hands, so
    "are you the instructor" cannot be answered by the session — it is the
    session that is being protected. One password entry at the end of the line is
    the whole cost.
    """
    actor = request.actor
    session = _get_session(actor, session_id)

    # ⚠ The same policy as signing in, and for the same reason: an unattended
    # phone showing a password box is exactly where somebody sits and guesses.
    identifier = request.user.email
    try:
        enforce(KIOSK_EXIT_SCOPE, identifier, LOGIN_POLICY)
    except Throttled as exc:
        messages.error(
            request,
            _("Too many attempts. Try again in %(seconds)s seconds.")
            % {"seconds": exc.state.retry_after},
        )
        return redirect("kiosk", session_id=session.pk)

    password = request.POST.get("password", "")
    if not password or authenticate(request, email=identifier, password=password) is None:
        register_failure(KIOSK_EXIT_SCOPE, identifier, LOGIN_POLICY)
        messages.error(request, _("That password is not right."))
        return redirect("kiosk", session_id=session.pk)

    register_success(KIOSK_EXIT_SCOPE, identifier)
    kiosk.stop(request)
    return redirect("roster", session_id=session.pk)
