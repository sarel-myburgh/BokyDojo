"""Attendance screens — TODO 1.5.2, 1.11.1, 1.11.3.

The roster is the critical UX path in the whole product (plan §11 risks): if
marking a class of twenty takes longer than the paper register it replaces, the
instructor stops using it and everything downstream — grading eligibility,
billing, drop-off alerts — quietly rots.

So the roster is one screen, one column, no modals, no per-student round trips.
Every student is a row of four large targets, the whole thing posts once, and
"mark all present" plus corrections is the intended flow rather than tapping
twenty times.
"""

from __future__ import annotations

import datetime
import json
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.reports import csv_report_response
from apps.core.timezones import dojo_zone
from apps.identity.models import Dojo, Enrollment, GovernanceModel, Person
from apps.identity.permissions import (
    ROLE_ACTIONS,
    Action,
    PermissionDenied,
    can,
    require,
)
from apps.scheduling.models import ClassSession

from .models import AttendanceRecord
from .services import (
    is_catch_up_eligible,
    mark_session,
    session_roster,
    sync_session_marks,
)

#: Default silence before a student appears on the drop-off list — TODO 1.11.3.
DEFAULT_DROP_OFF_DAYS = 21


def _holds_anywhere(actor, action: str) -> bool:
    """Does this actor hold ``action`` through *any* role?

    A list screen has no single object to check, so this gates the page. It is
    not the control — every row is still checked against the object itself. Menu
    visibility is never a control (SEC §2.2).
    """
    return any(action in ROLE_ACTIONS.get(role, set()) for role, _scope, _dojo in actor.roles)


def _local_date(session: ClassSession) -> datetime.date:
    return session.starts_at.astimezone(dojo_zone(session.dojo)).date()


def _local_today(dojo: Dojo) -> datetime.date:
    return timezone.now().astimezone(dojo_zone(dojo)).date()


def _governance_of(dojo: Dojo) -> str:
    return dojo.organization.governance_model or GovernanceModel.CENTRAL


@login_required
@require_http_methods(["GET"])
def today_view(request) -> HttpResponse:
    """Every class happening today, at any dojo this actor can see — TODO 1.5.2."""
    actor = request.actor
    if not _holds_anywhere(actor, Action.ATTENDANCE_VIEW):
        raise PermissionDenied(action=Action.ATTENDANCE_VIEW, actor=actor)

    now = timezone.now()
    # A day in the dojo's timezone can sit up to ~14h either side of a UTC day;
    # 36h is a safe bounded window to fetch before filtering by local date.
    candidates = (
        ClassSession.objects.for_actor(actor)
        .select_related("dojo", "dojo__organization", "template")
        .filter(
            starts_at__range=(
                now - datetime.timedelta(hours=36),
                now + datetime.timedelta(hours=36),
            )
        )
        .annotate(marked_count=Count("attendance_records"))
        .order_by("starts_at")
    )

    sessions = [
        session
        for session in candidates
        if _local_date(session) == _local_today(session.dojo)
        and can(
            actor,
            Action.ATTENDANCE_VIEW,
            session,
            governance_model=_governance_of(session.dojo),
        )
    ]

    # Nag about classes taught but never marked — plan §12.7. The full catch-up
    # flow is 1.5.6; this is the prompt that makes it obvious it is needed.
    # Today's own classes are excluded: they are already listed above, and a
    # class that finished twenty minutes ago is not yet a backlog.
    unmarked_candidates = (
        ClassSession.objects.for_actor(actor)
        .select_related("dojo", "dojo__organization", "template")
        .filter(
            starts_at__gte=now - datetime.timedelta(days=365),
            starts_at__lt=now,
            status=ClassSession.Status.SCHEDULED,
        )
        .annotate(marked_count=Count("attendance_records"))
        .filter(marked_count=0)
        .order_by("-starts_at")
    )
    unmarked = [
        session
        for session in unmarked_candidates
        if is_catch_up_eligible(session=session, unmarked=True)
        and can(
            actor,
            Action.ATTENDANCE_VIEW,
            session,
            governance_model=_governance_of(session.dojo),
        )
    ][:20]

    return render(
        request,
        "attendance/today.html",
        {
            "sessions": sessions,
            "unmarked": unmarked,
        },
    )


def _get_session(actor, session_id) -> ClassSession:
    """Fetch a session this actor may see, or 404.

    Scoping does the work: a session at another tenant's dojo is not "forbidden",
    it does not exist as far as this actor is concerned, which is also the answer
    that leaks the least. The URL pattern is ``<uuid:...>``, so a malformed id
    never reaches here.
    """
    return get_object_or_404(
        ClassSession.objects.for_actor(actor).select_related(
            "dojo", "dojo__organization", "template"
        ),
        pk=session_id,
    )


@login_required
@require_http_methods(["GET", "POST"])
def roster_view(request, session_id) -> HttpResponse:
    """Mark a class — TODO 1.5.2/1.5.3."""
    actor = request.actor
    session = _get_session(actor, session_id)

    if request.method == "POST":
        require(
            actor,
            Action.ATTENDANCE_RECORD,
            session,
            governance_model=_governance_of(session.dojo),
        )

        valid = set(AttendanceRecord.Status.values) - {AttendanceRecord.Status.VISITING}
        statuses = {}
        for key, value in request.POST.items():
            if not key.startswith("status_") or value not in valid:
                continue
            statuses[key.removeprefix("status_")] = value

        catch_up = is_catch_up_eligible(session=session)
        result = mark_session(
            session=session,
            statuses=statuses,
            actor=actor,
            allow_catch_up=catch_up,
        )
        messages.success(
            request,
            _("Saved: %(created)s new, %(updated)s updated.")
            % {"created": result["created"], "updated": result["updated"]},
        )
        if catch_up:
            return redirect("catch-up")
        return redirect("roster", session_id=session.pk)

    entries = session_roster(session=session, actor=actor)
    may_record = can(
        actor,
        Action.ATTENDANCE_RECORD,
        session,
        governance_model=_governance_of(session.dojo),
    )

    return render(
        request,
        "attendance/roster.html",
        {
            "session": session,
            "entries": entries,
            "statuses": AttendanceRecord.Status,
            "may_record": may_record,
            "marked_count": sum(1 for entry in entries if entry.is_marked),
            "is_catch_up": is_catch_up_eligible(
                session=session, unmarked=not any(entry.is_marked for entry in entries)
            ),
        },
    )


@login_required
@require_http_methods(["GET"])
def catch_up_view(request) -> HttpResponse:
    """List missed classes an instructor may still fill in — TODO 1.5.6."""
    actor = request.actor
    if not _holds_anywhere(actor, Action.ATTENDANCE_VIEW):
        raise PermissionDenied(action=Action.ATTENDANCE_VIEW, actor=actor)

    now = timezone.now()
    candidates = (
        ClassSession.objects.for_actor(actor)
        .select_related("dojo", "dojo__organization", "template")
        .filter(
            starts_at__gte=now - datetime.timedelta(days=365),
            starts_at__lt=now,
            status=ClassSession.Status.SCHEDULED,
        )
        .annotate(marked_count=Count("attendance_records"))
        .filter(marked_count=0)
        .order_by("-starts_at")
    )
    sessions = [
        session
        for session in candidates
        if is_catch_up_eligible(session=session, unmarked=True)
        and can(
            actor,
            Action.ATTENDANCE_VIEW,
            session,
            governance_model=_governance_of(session.dojo),
        )
    ]
    return render(
        request,
        "attendance/catch_up.html",
        {"sessions": sessions},
    )


@login_required
@require_POST
def attendance_sync_view(request, session_id) -> JsonResponse:
    """Synchronise an offline roster with idempotency and conflict detection."""
    if request.content_type != "application/json":
        return JsonResponse({"error": "content_type"}, status=415)
    try:
        content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return JsonResponse({"error": "invalid_content_length"}, status=400)
    if content_length < 0 or content_length > 64 * 1024:
        return JsonResponse({"error": "payload_too_large"}, status=413)
    body = request.body
    if len(body) > 64 * 1024:
        return JsonResponse({"error": "payload_too_large"}, status=413)
    try:
        payload = json.loads(body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=400)

    raw_marks = payload.get("marks") if isinstance(payload, dict) else None
    if not isinstance(raw_marks, list) or not 1 <= len(raw_marks) <= 200:
        return JsonResponse({"error": "invalid_marks"}, status=400)

    valid_statuses = set(AttendanceRecord.Status.values) - {AttendanceRecord.Status.VISITING}
    marks = []
    seen_students = set()
    seen_client_ids = set()
    for raw in raw_marks:
        if not isinstance(raw, dict):
            return JsonResponse({"error": "invalid_mark"}, status=400)
        try:
            student_id = uuid.UUID(str(raw.get("student_id", "")))
            client_id = str(uuid.UUID(str(raw.get("client_generated_id", ""))))
        except (ValueError, AttributeError):
            return JsonResponse({"error": "invalid_identifier"}, status=400)
        status = raw.get("status")
        base_version = raw.get("base_version", "")
        if (
            status not in valid_statuses
            or not isinstance(base_version, str)
            or len(base_version) > 64
        ):
            return JsonResponse({"error": "invalid_mark"}, status=400)
        if student_id in seen_students or client_id in seen_client_ids:
            return JsonResponse({"error": "duplicate_mark"}, status=400)
        seen_students.add(student_id)
        seen_client_ids.add(client_id)
        marks.append(
            {
                "student_id": student_id,
                "status": status,
                "client_generated_id": client_id,
                "base_version": base_version,
            }
        )

    session = _get_session(request.actor, session_id)
    results = sync_session_marks(session=session, marks=marks, actor=request.actor)
    conflicts = sum(result["state"] == "conflict" for result in results)
    return JsonResponse({"results": results, "conflicts": conflicts})


# -- reports ------------------------------------------------------------------


def _report_window(request) -> tuple[datetime.date, datetime.date]:
    today = timezone.localdate()
    default_from = today - datetime.timedelta(days=30)

    def parse(name, fallback):
        raw = request.GET.get(name)
        if not raw:
            return fallback
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError:
            return fallback

    date_from = parse("from", default_from)
    date_to = parse("to", today)
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return date_from, date_to


@login_required
@require_http_methods(["GET"])
def attendance_summary_view(request) -> HttpResponse:
    """Attendance by dojo and class over a period — TODO 1.11.1, with CSV (1.11.4)."""
    actor = request.actor
    if not _holds_anywhere(actor, Action.REPORT_VIEW):
        raise PermissionDenied(action=Action.REPORT_VIEW, actor=actor)

    date_from, date_to = _report_window(request)

    rows = (
        AttendanceRecord.objects.for_actor(actor)
        .filter(
            session__starts_at__date__gte=date_from,
            session__starts_at__date__lte=date_to,
        )
        .values("session__dojo__name", "session__template__name")
        .annotate(
            total=Count("id"),
            attended=Count("id", filter=Q(status__in=AttendanceRecord.ATTENDED_STATUSES)),
            absent=Count("id", filter=Q(status=AttendanceRecord.Status.ABSENT)),
            excused=Count("id", filter=Q(status=AttendanceRecord.Status.EXCUSED)),
            visiting=Count("id", filter=Q(status=AttendanceRecord.Status.VISITING)),
        )
        .order_by("session__dojo__name", "session__template__name")
    )

    summary = [
        {
            "dojo": row["session__dojo__name"],
            "class_name": row["session__template__name"] or _("One-off session"),
            "total": row["total"],
            "attended": row["attended"],
            "absent": row["absent"],
            "excused": row["excused"],
            "visiting": row["visiting"],
            "rate": round(100 * row["attended"] / row["total"]) if row["total"] else 0,
        }
        for row in rows
    ]

    if request.GET.get("format") == "csv":
        return csv_report_response(
            filename=f"attendance-{date_from}-to-{date_to}.csv",
            header=[
                "Dojo",
                "Class",
                "Records",
                "Attended",
                "Absent",
                "Excused",
                "Visiting",
                "Rate %",
            ],
            rows=[
                [
                    item["dojo"],
                    item["class_name"],
                    item["total"],
                    item["attended"],
                    item["absent"],
                    item["excused"],
                    item["visiting"],
                    item["rate"],
                ]
                for item in summary
            ],
            actor=actor,
        )

    totals = {
        "total": sum(item["total"] for item in summary),
        "attended": sum(item["attended"] for item in summary),
    }
    totals["rate"] = round(100 * totals["attended"] / totals["total"]) if totals["total"] else 0

    return render(
        request,
        "attendance/summary.html",
        {
            "summary": summary,
            "totals": totals,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@login_required
@require_http_methods(["GET"])
def drop_off_view(request) -> HttpResponse:
    """Students who have stopped turning up — TODO 1.11.3.

    The single most commercially useful report in the product: a student who has
    missed three weeks is about to quit, and nobody notices from a spreadsheet.
    """
    actor = request.actor
    if not _holds_anywhere(actor, Action.REPORT_VIEW):
        raise PermissionDenied(action=Action.REPORT_VIEW, actor=actor)

    try:
        days = int(request.GET.get("days", DEFAULT_DROP_OFF_DAYS))
    except ValueError:
        days = DEFAULT_DROP_OFF_DAYS
    days = max(1, min(days, 365))
    cutoff = timezone.now() - datetime.timedelta(days=days)

    # ⚠ Person carries no dojo scoping path — it is org-wide by design, because
    # one human has one record however many dojos they touch. So the dojo
    # restriction has to be applied here, in the same filter() call as the rest of
    # the enrolment lookup (a second call would join enrollments twice and let a
    # student qualify through a dojo this actor cannot see).
    enrolment_filter = {
        "enrollments__ended_on__isnull": True,
        "enrollments__status": Enrollment.Status.ACTIVE,
    }
    if actor.dojo_ids is not None:
        enrolment_filter["enrollments__dojo_id__in"] = list(actor.dojo_ids)

    students = (
        Person.objects.for_actor(actor)
        .filter(**enrolment_filter)
        .annotate(
            last_seen=Max(
                "attendance_records__session__starts_at",
                filter=Q(attendance_records__status__in=AttendanceRecord.ATTENDED_STATUSES),
            )
        )
        .filter(Q(last_seen__lt=cutoff) | Q(last_seen__isnull=True))
        .order_by("last_seen", "family_name", "given_name")
        .distinct()
    )

    rows = [
        {
            "person": person,
            "last_seen": person.last_seen,
            "days_since": (timezone.now() - person.last_seen).days if person.last_seen else None,
        }
        for person in students
    ]

    if request.GET.get("format") == "csv":
        return csv_report_response(
            filename=f"drop-off-{days}-days.csv",
            header=["Student", "Email", "Phone", "Last attended", "Days since"],
            rows=[
                [
                    row["person"].full_name,
                    row["person"].email,
                    row["person"].phone,
                    row["last_seen"].date().isoformat() if row["last_seen"] else "never",
                    row["days_since"] if row["days_since"] is not None else "",
                ]
                for row in rows
            ],
            actor=actor,
        )

    return render(request, "attendance/drop_off.html", {"rows": rows, "days": days})
