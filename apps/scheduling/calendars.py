"""What is on, and when — TODO 1.4.9, plan §4.5.

The data layer behind the week and month screens. Kept out of the view because
the interesting part is not the HTTP: it is deciding *which day* a class falls
on, and that decision is wrong by default.

⚠ **The grid is bucketed by each dojo's own local date, never the viewer's.**
A 19:30 class in Phnom Penh is on Tuesday for everybody, including an
organisation admin reading the page from London where that instant is Tuesday
lunchtime — and including one reading it from Auckland, where it is already
Wednesday. Bucketing in the viewer's timezone would slide classes onto the wrong
weekday for exactly the people most likely to be looking at two countries at
once, and a timetable that disagrees with the timetable on the dojo wall is
worse than no timetable.

The consequence to keep in mind: on a page showing several dojos in different
zones, two sessions in the same cell did *not* necessarily happen at the same
time, and the column header is not a single instant. That is the right trade —
"what happens at each dojo on its own calendar" is the question being asked.

⚠ Reverse relations are scoped, so ``session.session_instructors.all()`` in a
loop raises ``UnscopedAccessError``. Instructors are gathered in one
``for_actor`` query and attached to the session objects instead, which is also
the only reason this page is not N+1.
"""

from __future__ import annotations

import calendar as stdlib_calendar
import datetime
import uuid
from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone

from apps.core.timezones import dojo_zone
from apps.identity.models import Dojo, GovernanceModel
from apps.identity.permissions import Action, can

from .models import ClassSession, ClosurePeriod, SessionInstructor

WEEK = "week"
MONTH = "month"
VIEWS = (WEEK, MONTH)

#: Padding on the database window, in whole days. UTC offsets run from -12:00 to
#: +14:00, so a session whose *dojo-local* date falls inside the grid can sit up
#: to fourteen hours outside it in UTC. One day either side covers that with room
#: to spare; the exact date filter then happens in Python, per dojo.
_OFFSET_MARGIN = datetime.timedelta(days=1)


@dataclass(frozen=True)
class Day:
    """One cell of the grid."""

    date: datetime.date
    sessions: list = field(default_factory=list)
    closures: list = field(default_factory=list)
    is_today: bool = False
    #: False for the leading/trailing days a month grid borrows from its
    #: neighbours to fill whole Monday–Sunday rows. They are drawn faded, not
    #: omitted: a class on the 1st is easier to find when the 31st is visible.
    in_focus: bool = True

    @property
    def is_closed(self) -> bool:
        return bool(self.closures)


@dataclass(frozen=True)
class CalendarPage:
    view: str
    anchor: datetime.date
    #: The period the user asked for — the week, or the month proper. Used for
    #: the heading; the grid itself may extend past it.
    period_start: datetime.date
    period_end: datetime.date
    days: list[Day]
    weeks: list[list[Day]]
    previous: datetime.date
    next: datetime.date
    today: datetime.date

    @property
    def session_count(self) -> int:
        return sum(len(day.sessions) for day in self.days if day.in_focus)


def normalise_view(raw: str | None) -> str:
    """Week unless the month is explicitly asked for.

    Mobile-first per the conventions: an instructor opening this on a phone
    between classes wants this week, not a thirty-cell grid.
    """
    return MONTH if raw == MONTH else WEEK


def parse_anchor(raw: str | None, *, fallback: datetime.date) -> datetime.date:
    """A position in the calendar, not a resource — a bad value is not a 404.

    Contrast ``dojo`` and ``instructor``, which name records and *do* 404: asking
    for next Tuesday badly should land you on today, but asking for a dojo that
    is not yours should not quietly widen the page to every dojo you can see.
    """
    if not raw:
        return fallback
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return fallback


def week_bounds(anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Monday to Sunday. ``date.weekday()`` is already Monday-zero."""
    first = anchor - datetime.timedelta(days=anchor.weekday())
    return first, first + datetime.timedelta(days=6)


def month_bounds(anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
    first = anchor.replace(day=1)
    _, days_in_month = stdlib_calendar.monthrange(first.year, first.month)
    return first, first.replace(day=days_in_month)


def grid_bounds(view: str, anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
    """The dates actually drawn — a month is padded out to whole weeks."""
    if view == MONTH:
        first, last = month_bounds(anchor)
        return (
            first - datetime.timedelta(days=first.weekday()),
            last + datetime.timedelta(days=6 - last.weekday()),
        )
    return week_bounds(anchor)


def step(view: str, anchor: datetime.date, direction: int) -> datetime.date:
    """The anchor one period earlier (-1) or later (+1).

    ⚠ Months are stepped by landing on the 1st first. Adding thirty days to the
    31st of January arrives in March, silently skipping February.
    """
    if view == MONTH:
        first = anchor.replace(day=1)
        if direction < 0:
            return (first - datetime.timedelta(days=1)).replace(day=1)
        _, days_in_month = stdlib_calendar.monthrange(first.year, first.month)
        return first + datetime.timedelta(days=days_in_month)
    return anchor + datetime.timedelta(days=7 * direction)


def _governance_of(dojo) -> str:
    return dojo.organization.governance_model or GovernanceModel.CENTRAL


def _today_for(actor, dojo) -> datetime.date:
    """Which day to highlight.

    A single dojo in view has an unambiguous today — its own. Otherwise fall
    back to the actor's activated timezone, which is the same rule the report
    date boundaries use, and the only defensible answer when the page spans
    zones that disagree about the date.
    """
    if dojo is not None:
        return timezone.now().astimezone(dojo_zone(dojo)).date()
    return timezone.localdate()


def resolve_dojo(actor, raw: str | None):
    """The dojo filter, or None for "every dojo I can see".

    Returns ``None`` for an absent filter and raises ``Dojo.DoesNotExist`` for
    one that names a dojo outside this actor's scope — the caller turns that
    into a 404. Scoping does the work: another tenant's dojo does not exist
    here, which is also the answer that leaks the least.
    """
    if not raw:
        return None
    try:
        dojo_id = uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise Dojo.DoesNotExist("malformed dojo id") from exc
    return Dojo.objects.for_actor(actor).select_related("organization").get(pk=dojo_id)


def resolve_instructor(actor, raw: str | None):
    """The instructor filter, or None. Same 404-on-tamper contract as above."""
    from apps.identity.models import Person

    if not raw:
        return None
    try:
        person_id = uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise Person.DoesNotExist("malformed person id") from exc
    return Person.objects.for_actor(actor).get(pk=person_id)


def instructor_choices(actor):
    """People who could be teaching, for the filter dropdown.

    Read from ``InstructorAssignment`` — who is an instructor — rather than from
    who happens to appear in this month's sessions, so the list does not shrink
    when you page backwards into a quiet week.

    ⚠ Not narrowed to the selected dojo. A substitute usually comes from another
    dojo in the same organisation (see ``instructors.py``), so filtering the list
    down to one dojo's own staff would hide precisely the person a dojo admin is
    looking for when they ask "where is Dara teaching this week".
    """
    from apps.identity.models import InstructorAssignment

    people = {}
    assignments = (
        InstructorAssignment.objects.for_actor(actor)
        .filter(ended_on__isnull=True)
        .select_related("person")
        .order_by("person__family_name", "person__given_name")
    )
    for assignment in assignments:
        people.setdefault(assignment.person_id, assignment.person)
    return list(people.values())


def _sessions_in(actor, first, last, *, dojo=None, instructor=None):
    """Sessions whose dojo-local date falls within ``first``..``last``.

    The database window is padded and deliberately loose; the precise date test
    happens in Python because it has to be applied in a different timezone per
    row, which SQL cannot express against a per-dojo zone name.
    """
    window_start = datetime.datetime.combine(first, datetime.time.min, tzinfo=datetime.UTC)
    window_end = datetime.datetime.combine(last, datetime.time.max, tzinfo=datetime.UTC)

    queryset = (
        ClassSession.objects.for_actor(actor)
        .select_related("dojo", "dojo__organization", "template")
        .filter(
            starts_at__gte=window_start - _OFFSET_MARGIN,
            starts_at__lte=window_end + _OFFSET_MARGIN,
        )
        .order_by("starts_at")
    )
    if dojo is not None:
        queryset = queryset.filter(dojo=dojo)
    if instructor is not None:
        # ⚠ SessionInstructor, not TemplateInstructor: the question is who is on
        # *this* class, which is what makes a substitution visible here at all.
        # Safe without .distinct() — SessionInstructor is unique on
        # (session, person), so this join can match at most one row per session.
        queryset = queryset.filter(session_instructors__person=instructor)

    return [
        session
        for session in queryset
        if first <= session.starts_at.astimezone(dojo_zone(session.dojo)).date() <= last
        and can(
            actor,
            Action.DOJO_VIEW,
            session,
            governance_model=_governance_of(session.dojo),
        )
    ]


def _attach_instructors(actor, sessions) -> None:
    """One query for everybody teaching, hung off the session objects.

    ⚠ Not a prefetch: ``session.session_instructors`` is a scoped reverse
    relation and raises ``UnscopedAccessError`` when a template walks it.
    """
    by_session: dict[uuid.UUID, list] = {session.pk: [] for session in sessions}
    if not by_session:
        return
    rows = (
        SessionInstructor.objects.for_actor(actor)
        .filter(session_id__in=list(by_session))
        .select_related("person", "replaces")
        .order_by("person__family_name", "person__given_name")
    )
    for row in rows:
        by_session[row.session_id].append(row)
    for session in sessions:
        # Plain attribute, deliberately not the relation's name — shadowing the
        # descriptor would make the scoped manager unreachable on that instance.
        session.teaching = by_session[session.pk]


def _closures_in(actor, first, last, *, dojo=None):
    """Closures overlapping the window, org-wide ones included.

    Without these a closed day is just an empty day, which reads as "nothing is
    scheduled" rather than "we are shut for Khmer New Year" — and materialisation
    has already made sure those days are genuinely empty, so the calendar is the
    only place left that can say why.

    ⚠ ``ClosurePeriod`` is scoped by organisation only — it has no
    ``tenant_dojo_path`` — so a dojo-scoped actor's ``for_actor`` still returns
    other dojos' closures. Narrowed here to the dojos this actor can actually
    see, plus the org-wide ones.
    """
    if dojo is not None:
        applies = Q(dojo__isnull=True) | Q(dojo=dojo)
    else:
        visible = list(Dojo.objects.for_actor(actor).values_list("pk", flat=True))
        applies = Q(dojo__isnull=True) | Q(dojo_id__in=visible)

    return list(
        ClosurePeriod.objects.for_actor(actor)
        .select_related("dojo")
        .filter(applies, starts_on__lte=last, ends_on__gte=first)
        .order_by("starts_on", "reason")
    )


def build_page(*, actor, view: str, anchor: datetime.date, dojo=None, instructor=None):
    """Everything the calendar template needs, in four queries."""
    view = normalise_view(view)
    period_start, period_end = month_bounds(anchor) if view == MONTH else week_bounds(anchor)
    first, last = grid_bounds(view, anchor)

    sessions = _sessions_in(actor, first, last, dojo=dojo, instructor=instructor)
    _attach_instructors(actor, sessions)
    closures = _closures_in(actor, first, last, dojo=dojo)

    sessions_by_date: dict[datetime.date, list] = {}
    for session in sessions:
        local_date = session.starts_at.astimezone(dojo_zone(session.dojo)).date()
        sessions_by_date.setdefault(local_date, []).append(session)

    today = _today_for(actor, dojo)

    days = []
    cursor = first
    while cursor <= last:
        days.append(
            Day(
                date=cursor,
                sessions=sessions_by_date.get(cursor, []),
                closures=[c for c in closures if c.starts_on <= cursor <= c.ends_on],
                is_today=cursor == today,
                in_focus=period_start <= cursor <= period_end,
            )
        )
        cursor += datetime.timedelta(days=1)

    weeks = [days[index : index + 7] for index in range(0, len(days), 7)]

    return CalendarPage(
        view=view,
        anchor=anchor,
        period_start=period_start,
        period_end=period_end,
        days=days,
        weeks=weeks,
        previous=step(view, anchor, -1),
        next=step(view, anchor, +1),
        today=today,
    )
