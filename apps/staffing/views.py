"""The instructor's own timesheet — TODO 1.9.4, plan §4.8.

Read-only, and one week at a time. `3.7.1` adds submit/approve/reject and `3.7.2`
the payroll export; until then this answers one question — "what have I taught
this week" — which is the question an instructor actually asks, usually on a
phone, usually at the end of the month when they have forgotten.

⚠ **Own entries only.** ``TIMEENTRY_VIEW_OWN`` is what four of the six staff roles
hold, and it means what it says. An instructor must not see what a colleague is
paid, and the approval screen that *will* show other people's lines is a
different task with a different permission.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.identity.models import Person
from apps.identity.permissions import ROLE_ACTIONS, Action, PermissionDenied
from apps.scheduling import calendars

from . import timesheets


def _holds_anywhere(actor, action: str) -> bool:
    return any(action in ROLE_ACTIONS.get(role, set()) for role, _scope, _dojo in actor.roles)


@login_required
@require_http_methods(["GET"])
def timesheet_view(request) -> HttpResponse:
    """This instructor's week — TODO 1.9.4."""
    actor = request.actor
    if not _holds_anywhere(actor, Action.TIMEENTRY_VIEW_OWN):
        raise PermissionDenied(action=Action.TIMEENTRY_VIEW_OWN, actor=actor)

    if actor.person_id is None:
        raise Http404("no person for this account")
    person = Person.objects.for_actor(actor).filter(pk=actor.person_id).first()
    if person is None:
        raise Http404("no person for this account")

    anchor = calendars.parse_anchor(request.GET.get("date"), fallback=timezone.localdate())
    first, last = timesheets.week_bounds(anchor)
    entries = timesheets.entries_for_week(person=person, actor=actor, anchor=anchor)

    return render(
        request,
        "staffing/timesheet.html",
        {
            "person": person,
            "anchor": anchor,
            "first": first,
            "last": last,
            "days": timesheets.days_of(entries, first, last),
            "summary": timesheets.week_summary(entries),
            "previous": calendars.step(calendars.WEEK, anchor, -1),
            "next": calendars.step(calendars.WEEK, anchor, +1),
        },
    )
