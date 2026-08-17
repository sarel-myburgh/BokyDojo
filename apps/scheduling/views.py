"""Scheduling screens — TODO 1.4.9.

The first UI scheduling has had. Everything before this — materialisation
(`1.4.2`), the occurrence/series edits (`1.4.5`), instructor assignment
(`1.4.8`) — is service-level, so the timetable existed and nobody could look at
it. This is the looking.

Read-only on purpose. Editing a schedule from a calendar grid is a much larger
screen than this, and the services underneath it already refuse the dangerous
cases; putting a thin, honest viewer in front of them first is worth more than a
half-built editor.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.core.timezones import dojo_zone
from apps.identity.models import Dojo, Person
from apps.identity.permissions import ROLE_ACTIONS, Action, PermissionDenied

from . import calendars


def _holds_anywhere(actor, action: str) -> bool:
    """Does this actor hold ``action`` through *any* role?

    Gates the page, nothing more — every session on it is still checked against
    the object itself in ``calendars._sessions_in``. Menu-level hiding is not a
    control (SEC §2.2).
    """
    return any(action in ROLE_ACTIONS.get(role, set()) for role, _scope, _dojo in actor.roles)


@login_required
@require_http_methods(["GET"])
def calendar_view(request) -> HttpResponse:
    """Week and month timetables, per dojo, filtered by instructor — TODO 1.4.9.

    ``DOJO_VIEW`` rather than ``ATTENDANCE_VIEW``: this is what the dojo has on,
    which is not attendance data. It is also the one action every staff role
    holds, including the safeguarding officer, who has a real reason to ask who
    was teaching on a given evening and no business on the roster screen.
    """
    actor = request.actor
    if not _holds_anywhere(actor, Action.DOJO_VIEW):
        raise PermissionDenied(action=Action.DOJO_VIEW, actor=actor)

    try:
        dojo = calendars.resolve_dojo(actor, request.GET.get("dojo"))
        instructor = calendars.resolve_instructor(actor, request.GET.get("instructor"))
    except (Dojo.DoesNotExist, Person.DoesNotExist) as exc:
        # A filter naming a record outside this actor's scope is a 404, not a
        # silently-ignored parameter: dropping it would widen the page to every
        # dojo they can see, which is the opposite of what was asked for.
        raise Http404("no such filter target") from exc

    view = calendars.normalise_view(request.GET.get("view"))
    anchor = calendars.parse_anchor(
        request.GET.get("date"),
        # "Today" for the landing position follows the same rule as the
        # highlight: the selected dojo's own date, else the actor's.
        fallback=timezone.now()
        .astimezone(dojo_zone(dojo) if dojo is not None else timezone.get_current_timezone())
        .date(),
    )

    page = calendars.build_page(
        actor=actor,
        view=view,
        anchor=anchor,
        dojo=dojo,
        instructor=instructor,
    )

    return render(
        request,
        "scheduling/calendar.html",
        {
            "page": page,
            "dojo": dojo,
            "instructor": instructor,
            "dojo_choices": Dojo.objects.for_actor(actor).order_by("name"),
            "instructor_choices": calendars.instructor_choices(actor),
            "week_view": calendars.WEEK,
            "month_view": calendars.MONTH,
        },
    )
