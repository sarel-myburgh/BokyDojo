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

import csv
import datetime
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

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
from .services import mark_session, session_roster

#: How far back the catch-up prompt looks — plan §12.7, TODO 1.5.6.
CATCH_UP_DAYS = 14
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
    return session.starts_at.astimezone(ZoneInfo(session.dojo.timezone or "UTC")).date()


def _local_today(dojo: Dojo) -> datetime.date:
    return timezone.now().astimezone(ZoneInfo(dojo.timezone or "UTC")).date()


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
        .filter(starts_at__range=(now - datetime.timedelta(hours=36), now + datetime.timedelta(hours=36)))
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
    unmarked = (
        ClassSession.objects.for_actor(actor)
        .select_related("dojo", "template")
        .filter(
            starts_at__gte=now - datetime.timedelta(days=CATCH_UP_DAYS),
            starts_at__lt=now,
            status=ClassSession.Status.SCHEDULED,
        )
        .annotate(marked_count=Count("attendance_records"))
        .filter(marked_count=0)
        .order_by("-starts_at")[:20]
    )

    return render(
        request,
        "attendance/today.html",
        {
            "sessions": sessions,
            "unmarked": list(unmarked),
            "catch_up_days": CATCH_UP_DAYS,
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

        valid = set(AttendanceRecord.Status.values)
        statuses = {}
        for key, value in request.POST.items():
            if not key.startswith("status_") or value not in valid:
                continue
            statuses[key.removeprefix("status_")] = value

        result = mark_session(session=session, statuses=statuses, actor=actor)
        messages.success(
            request,
            _("Saved: %(created)s new, %(updated)s updated.")
            % {"created": result["created"], "updated": result["updated"]},
        )
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
        },
    )


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
        return _csv_response(
            filename=f"attendance-{date_from}-to-{date_to}.csv",
            header=["Dojo", "Class", "Records", "Attended", "Absent", "Excused", "Visiting", "Rate %"],
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
    totals["rate"] = (
        round(100 * totals["attended"] / totals["total"]) if totals["total"] else 0
    )

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
        return _csv_response(
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


def _csv_response(*, filename: str, header: list, rows: list, actor) -> HttpResponse:
    """CSV export — TODO 1.11.4.

    Exports of personal data are audited: SEC §2.6 treats "who took a copy of the
    student list" as one of the questions the log has to be able to answer.
    """
    from apps.core import audit

    audit.record(
        "export",
        actor=actor,
        subject_type="report",
        subject_id=filename,
        note=f"{len(rows)} row(s)",
    )

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response
