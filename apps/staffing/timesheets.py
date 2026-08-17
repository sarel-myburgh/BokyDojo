"""Turning taught classes into timesheet lines — TODO 1.9.3, 1.9.4, plan §4.8.

A draft appears the moment a class's attendance is taken, because the alternative
is an instructor reconstructing last month from memory at the end of it. The
draft is a starting point, not an assertion: `3.7.1` adds submit/approve/reject
on top, and until then nothing here is authoritative about pay.

⚠ **Who taught is read from ``SessionInstructor``, never from the template.**
That distinction is the whole reason `1.4.8` exists: a substitute is paid for the
class they actually covered, and the person they covered for is not.

⚠ **The importer deliberately does not draft.** Historical attendance (`1.10.4`)
is a record of the past, not a payroll event — drafting from it would manufacture
thousands of draft entries for classes taught years ago, some by people who have
since left, and drop them into a timesheet nobody can sensibly review. If a
migration ever needs historical pay, that is its own import with its own consent
from whoever is signing the cheque.

⚠ **An existing entry is never overwritten.** Re-saving a roster, a late offline
sync and a kiosk tap after the fact all re-enter this code; if it reset the
minutes it would silently discard an instructor's correction, or worse, reopen an
approved line.
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from apps.core.scoping import Actor
from apps.core.timezones import dojo_zone

from .models import InstructorProfile, TimeEntry


@transaction.atomic
def draft_for_session(session, *, actor: Actor) -> list[TimeEntry]:
    """Create a draft line per instructor on this class. Idempotent.

    Returns only the entries it created, so callers can report honestly.
    """
    from apps.scheduling.instructors import instructors_for

    created: list[TimeEntry] = []
    minutes = session.duration_minutes or 0

    for assignment in instructors_for(session, actor):
        person = assignment.person
        existing = (
            TimeEntry.objects.for_actor(actor)
            .filter(instructor=person, session_id=session.pk)
            .exists()
        )
        if existing:
            # ⚠ Never touched again. See the module docstring.
            continue

        profile = (
            InstructorProfile.objects.for_organization(session.dojo.organization_id)
            .filter(person=person)
            .first()
        )
        entry = TimeEntry(
            instructor=person,
            dojo=session.dojo,
            session_id=session.pk,
            category=TimeEntry.Category.CLASS,
            started_at=session.starts_at,
            ended_at=session.ends_at,
            minutes=minutes,
            status=TimeEntry.Status.DRAFT,
            # ⚠ Snapshotted now, not looked up at approval. An instructor whose
            # rate changes in March must not have February revalued.
            pay_rate_snapshot_minor_units=(
                profile.pay_rate_minor_units if profile is not None else None
            ),
            pay_rate_snapshot_currency=(profile.pay_currency if profile is not None else ""),
        )
        entry.save()
        created.append(entry)

    return created


def week_bounds(anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Monday to Sunday — the same week the timetable draws.

    Imported rather than redefined, so "this week" cannot mean two things in one
    product.
    """
    from apps.scheduling.calendars import week_bounds as calendar_week

    return calendar_week(anchor)


def entries_for_week(*, person, actor: Actor, anchor: datetime.date):
    """One instructor's lines for the week containing ``anchor``.

    ⚠ Bucketed by the **dojo's** local date, like every other date in this
    product. An instructor teaching a 19:30 class in Phnom Penh should see it on
    the day they taught it, not the day UTC thinks it was.
    """
    first, last = week_bounds(anchor)
    # A day either side, because the local date and the UTC instant disagree by
    # up to fourteen hours — the same margin the calendar's query uses.
    window_start = datetime.datetime.combine(
        first, datetime.time.min, tzinfo=datetime.UTC
    ) - datetime.timedelta(days=1)
    window_end = datetime.datetime.combine(
        last, datetime.time.max, tzinfo=datetime.UTC
    ) + datetime.timedelta(days=1)

    candidates = (
        TimeEntry.objects.for_actor(actor)
        .filter(
            instructor=person,
            started_at__gte=window_start,
            started_at__lte=window_end,
        )
        .select_related("dojo", "dojo__organization")
        .order_by("started_at")
    )
    return [
        entry
        for entry in candidates
        if first <= entry.started_at.astimezone(dojo_zone(entry.dojo)).date() <= last
    ]


def week_summary(entries) -> dict:
    """Totals for the week. Money is deliberately absent when a rate is not set.

    ⚠ An instructor with no ``InstructorProfile`` still gets hours — the time was
    worked whether or not anybody has configured how it is paid — but no amount
    is invented for them.
    """
    minutes = sum(entry.minutes for entry in entries)
    priced = [entry for entry in entries if entry.pay_rate_snapshot_minor_units]
    currencies = {entry.pay_rate_snapshot_currency for entry in priced}
    return {
        "minutes": minutes,
        "hours": round(minutes / 60, 1),
        "classes": sum(1 for entry in entries if entry.category == TimeEntry.Category.CLASS),
        "unpriced": len(entries) - len(priced),
        # ⚠ No total across mixed currencies. A dojo group spanning USD and KHR
        # would otherwise get a number that means nothing.
        "mixed_currency": len(currencies) > 1,
    }


def days_of(entries, first: datetime.date, last: datetime.date) -> list[dict]:
    """Group entries into the seven days, empties included."""
    by_date: dict[datetime.date, list] = {}
    for entry in entries:
        local = entry.started_at.astimezone(dojo_zone(entry.dojo)).date()
        by_date.setdefault(local, []).append(entry)

    days = []
    cursor = first
    today = timezone.localdate()
    while cursor <= last:
        rows = by_date.get(cursor, [])
        days.append(
            {
                "date": cursor,
                "entries": rows,
                "minutes": sum(entry.minutes for entry in rows),
                "is_today": cursor == today,
            }
        )
        cursor += datetime.timedelta(days=1)
    return days
