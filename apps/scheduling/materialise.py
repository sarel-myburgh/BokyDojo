"""Materialise ClassSessions from templates — TODO 1.4.2, plan §4.5.

Sessions are rows, not a query over recurrence rules. Attendance, lesson plans,
cancellations and instructor assignments all need something to point at, and
"what happened on the 3rd of March" must not change because somebody later
edited the template.

⚠ **The DST rule.** A class at 18:00 is at 18:00 *local wall-clock time*, in the
dojo's own timezone, on both sides of a daylight-saving transition. So the rrule
is expanded over naive local datetimes and each occurrence is attached to the
dojo's timezone at the end. Expanding in UTC instead would silently move every
class by an hour twice a year — which is the specific bug plan §4.5 warns about.
Cambodia has no DST, but the target market does not end at Cambodia.

Idempotency: a session already existing for (template, starts_at) is left
completely alone. Re-running is therefore safe, and cancelled sessions are never
resurrected — their row exists, so it is skipped.

This function deliberately **never deletes** a materialised session that no
longer matches its template. Removing occurrences on a template edit is
"this occurrence vs this and future" semantics — TODO 1.4.5 — and doing it
implicitly here would destroy attendance history attached to those sessions.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from dateutil.rrule import rrulestr
from django.utils import timezone

from apps.core.scoping import Actor
from apps.core.timezones import dojo_zone
from apps.identity.models import Dojo

from .models import ClassSession, ClassTemplate, ClosurePeriod

logger = logging.getLogger(__name__)

#: Plan §4.5 — far enough ahead that parents can see next term, close enough
#: that a template edit does not have to rewrite a year of rows.
DEFAULT_HORIZON_DAYS = 90

#: A malformed rule (FREQ=SECONDLY, or a COUNT in the millions) must not spin
#: forever. 90 days of even four-a-day classes is under 400 occurrences.
_MAX_OCCURRENCES_PER_TEMPLATE = 5000


@dataclass
class MaterialisationResult:
    """What a run did, per template, for the command's output and for tests."""

    created: int = 0
    skipped_existing: int = 0
    skipped_closed: int = 0
    templates: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"{self.templates} template(s): {self.created} created, "
            f"{self.skipped_existing} already present, "
            f"{self.skipped_closed} skipped for closures"
        )


def _local_rrule_text(rule_text: str) -> str:
    """Make an rrule safe to expand over naive local datetimes.

    ``UNTIL`` is allowed to carry a ``Z`` (UTC) suffix, which makes dateutil
    produce aware datetimes and then refuse to compare them with a naive
    ``dtstart``. The cut-off is a date in the dojo's own calendar here, so the
    suffix is dropped rather than converted.
    """
    cleaned = rule_text.strip().replace("\\n", "\n")
    parts = []
    for chunk in cleaned.replace(";", "\n").splitlines():
        piece = chunk.strip()
        if piece.upper().startswith("UNTIL=") and piece.upper().endswith("Z"):
            piece = piece[:-1]
        if piece:
            parts.append(piece)
    return ";".join(parts)


def _closure_dates(template: ClassTemplate, start: datetime.date, end: datetime.date) -> set:
    """Dates in range closed for this template's dojo — plan §12.2, TODO 1.4.3."""
    periods = ClosurePeriod.objects.for_organization(template.dojo.organization_id).filter(
        starts_on__lte=end,
        ends_on__gte=start,
    )
    closed = set()
    for period in periods:
        if period.dojo_id not in (None, template.dojo_id):
            continue  # another dojo's closure
        day = max(period.starts_on, start)
        last = min(period.ends_on, end)
        while day <= last:
            closed.add(day)
            day += datetime.timedelta(days=1)
    return closed


def materialise_template(
    template: ClassTemplate,
    *,
    from_date: datetime.date,
    to_date: datetime.date,
    result: MaterialisationResult | None = None,
) -> MaterialisationResult:
    """Create missing sessions for one template between two local dates."""
    result = result or MaterialisationResult()
    result.templates += 1

    window_start = max(from_date, template.active_from)
    window_end = min(to_date, template.active_to) if template.active_to else to_date
    if window_start > window_end:
        return result

    tz = dojo_zone(template.dojo)
    closed = _closure_dates(template, window_start, window_end)

    # Existing rows for this template inside the window, keyed by local date and
    # time so a session that moved by an hour under DST is still recognised as
    # the same occurrence.
    existing = {
        session.starts_at.astimezone(tz).replace(tzinfo=None)
        for session in ClassSession.objects.for_organization(template.dojo.organization_id).filter(
            template=template,
            starts_at__gte=datetime.datetime.combine(window_start, datetime.time.min, tzinfo=tz),
            starts_at__lte=datetime.datetime.combine(window_end, datetime.time.max, tzinfo=tz),
        )
    }

    dtstart = datetime.datetime.combine(template.active_from, template.start_time)
    try:
        rule = rrulestr(_local_rrule_text(template.rrule), dtstart=dtstart)
    except (ValueError, TypeError) as exc:
        message = f"{template.pk} ({template.name}): unusable rrule {template.rrule!r}: {exc}"
        logger.warning("SESSION MATERIALISATION SKIPPED %s", message)
        result.errors.append(message)
        return result

    pending = []
    horizon = datetime.datetime.combine(window_end, datetime.time.max)
    for index, occurrence in enumerate(rule):
        if index >= _MAX_OCCURRENCES_PER_TEMPLATE:
            message = f"{template.pk} ({template.name}): stopped at the occurrence cap"
            logger.warning("SESSION MATERIALISATION CAPPED %s", message)
            result.errors.append(message)
            break
        if occurrence > horizon:
            break
        if occurrence.date() < window_start:
            continue
        if occurrence.date() in closed:
            result.skipped_closed += 1
            continue

        # Naive local wall-clock time, matched against what already exists
        # before being given a timezone. This is the DST-correct order.
        local_naive = datetime.datetime.combine(occurrence.date(), template.start_time)
        if local_naive in existing:
            result.skipped_existing += 1
            continue

        starts_at = local_naive.replace(tzinfo=tz)
        pending.append(
            ClassSession(
                template=template,
                dojo=template.dojo,
                starts_at=starts_at,
                ends_at=starts_at + datetime.timedelta(minutes=template.duration_minutes),
                room=template.room,
                status=ClassSession.Status.SCHEDULED,
            )
        )
        existing.add(local_naive)

    if pending:
        # bulk_create still runs the same-organisation check — see
        # ScopedQuerySet.bulk_create.
        ClassSession.objects.bulk_create(pending)
        result.created += len(pending)
    return result


def materialise_sessions(
    *,
    actor: Actor,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: datetime.date | None = None,
    dojo: Dojo | None = None,
) -> MaterialisationResult:
    """Top up every active template to the rolling horizon — TODO 1.4.2.

    ``actor`` is normally ``Actor.system()`` from the scheduled job. Passing a
    real actor scopes the run to what that actor can see, which is what makes
    "regenerate my dojo's calendar" safe to expose later.
    """
    today = today or timezone.localdate()
    to_date = today + datetime.timedelta(days=horizon_days)

    templates = (
        ClassTemplate.objects.for_actor(actor)
        .select_related("dojo")
        .filter(active_from__lte=to_date)
        .exclude(active_to__lt=today)
    )
    if dojo is not None:
        templates = templates.filter(dojo=dojo)

    result = MaterialisationResult()
    for template in templates:
        materialise_template(template, from_date=today, to_date=to_date, result=result)
    return result
