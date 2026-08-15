"""Template edit semantics — TODO 1.4.5, plan §4.5.

*"Editing a template offers 'this occurrence / this and future' semantics —
decide this early, it's the classic calendar-app trap."*

The trap has two jaws. Edit the template in place and every past session silently
becomes a lie about what happened. Regenerate the future and you delete rows that
attendance, cancellations and lesson plans point at.

So neither operation here edits history, and neither deletes anything that
carries a decision:

**This occurrence** moves one session and records the slot it vacated. The
generator treats a vacated slot as occupied, so the class does not reappear at
its old time on the next run.

**This and future** splits the template in two, the way a calendar application
does: the original is closed off the day before the change, a new one starts on
the day of it, and past sessions keep pointing at the template that actually
produced them.

⚠ Only *untouched* future sessions are regenerated. A session that is cancelled,
has attendance, or was individually moved is a record of something somebody did.
It keeps its own time and is handed to the successor template, where it claims
its date's new slot so the generator does not add a second class that day. The
visible consequence is that a date you had already cancelled keeps the cancelled
class at its *old* time — the cancellation survives, which matters more than the
tidiness.

⚠ Handing the row over is the part that is easy to get wrong. Deleting the
duplicate after materialising looks correct and is not: the slot is empty again
by the next nightly run, which refills it. `existing` is computed per template,
so a preserved session still pointing at the closed template blocks nothing.
"""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.scoping import Actor
from apps.core.timezones import dojo_zone

from .materialise import materialise_template
from .models import ClassSession, ClassTemplate

#: Fields "this and future" may carry onto the new template. Deliberately not
#: `dojo` — moving a class to another dojo is not a recurrence edit — and not
#: `active_from`/`active_to`, which the split itself owns.
EDITABLE_TEMPLATE_FIELDS = (
    "name",
    "style",
    "rrule",
    "start_time",
    "duration_minutes",
    "room",
    "capacity",
    "rank_min",
    "rank_max",
    "age_min",
    "age_max",
    "counts_toward",
)

#: Fields "this occurrence" may change on a single session.
EDITABLE_SESSION_FIELDS = ("starts_at", "duration_minutes", "room")


def _require_schedule_edit(actor: Actor, dojo) -> None:
    from apps.identity.models import GovernanceModel
    from apps.identity.permissions import Action, require

    governance = dojo.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.DOJO_EDIT, dojo, governance_model=governance)


def _carries_a_decision(session: ClassSession, *, marked: set) -> bool:
    """Whether this session records something a person did, not just a rule.

    Cancelling a class is a decision communicated to parents; marking attendance
    is evidence; moving one occurrence was somebody's deliberate choice. Any of
    them makes the row worth more than the template that generated it, so a
    regeneration must leave it alone.

    ⚠ ``marked`` is passed in rather than read per session. The reverse relation
    ``session.attendance_records`` goes through the tenant-scoped manager, which
    refuses to evaluate without an actor — and reading it in a loop would be N+1
    queries even if it did not.
    """
    return (
        session.status != ClassSession.Status.SCHEDULED
        or session.pk in marked
        or session.moved_from is not None
    )


@transaction.atomic
def move_occurrence(
    *,
    session: ClassSession,
    actor: Actor,
    starts_at: datetime.datetime | None = None,
    duration_minutes: int | None = None,
    room: str | None = None,
) -> ClassSession:
    """Change one session without touching its template — "this occurrence".

    ⚠ Refuses a session that has already started. Moving a class that has been
    taught rewrites history; if the record is wrong, that is an attendance
    correction, not a scheduling change.
    """
    locked = (
        ClassSession.objects.for_actor(actor)
        .select_for_update()
        .select_related("dojo", "dojo__organization", "template")
        .get(pk=session.pk)
    )
    _require_schedule_edit(actor, locked.dojo)

    if locked.starts_at <= timezone.now():
        raise ValidationError(
            {"starts_at": _("This class has already started and cannot be rescheduled.")}
        )
    if locked.status != ClassSession.Status.SCHEDULED:
        raise ValidationError({"status": _("Only a scheduled class can be moved.")})

    before = audit.snapshot(locked)
    new_start = starts_at or locked.starts_at
    if new_start <= timezone.now():
        raise ValidationError({"starts_at": _("Move the class to a time in the future.")})

    minutes = duration_minutes
    if minutes is None:
        minutes = int((locked.ends_at - locked.starts_at).total_seconds() // 60)
    if minutes <= 0:
        raise ValidationError({"duration_minutes": _("A class must last at least a minute.")})

    # ⚠ Recorded once and never overwritten. Moving an occurrence twice must keep
    # pointing at the slot the *generator* would refill, not at the intermediate
    # time the class never actually occupied.
    if locked.moved_from is None and locked.template_id is not None:
        locked.moved_from = locked.starts_at

    locked.starts_at = new_start
    locked.ends_at = new_start + datetime.timedelta(minutes=minutes)
    if room is not None:
        locked.room = room
    locked.save(update_fields=["starts_at", "ends_at", "room", "moved_from", "updated_at"])

    audit.record(
        "update",
        actor=actor,
        subject=locked,
        before=before,
        after=audit.snapshot(locked),
        note="moved this occurrence only",
        strict=True,
    )
    return locked


@transaction.atomic
def edit_this_and_future(
    *,
    template: ClassTemplate,
    from_date: datetime.date,
    changes: dict,
    actor: Actor,
    horizon_days: int = 90,
) -> ClassTemplate:
    """Split a template so the change applies from ``from_date`` onwards.

    Returns the new template. The original survives, closed the day before, so
    every past session still points at the rule that actually produced it.
    """
    locked = (
        ClassTemplate.objects.for_actor(actor)
        .select_for_update()
        .select_related("dojo", "dojo__organization")
        .get(pk=template.pk)
    )
    _require_schedule_edit(actor, locked.dojo)

    unknown = set(changes) - set(EDITABLE_TEMPLATE_FIELDS)
    if unknown:
        raise ValidationError(
            {field: _("This field cannot be changed here.") for field in sorted(unknown)}
        )
    if not changes:
        raise ValidationError({"changes": _("Nothing to change.")})

    tz = dojo_zone(locked.dojo)
    today = timezone.now().astimezone(tz).date()
    if from_date <= today:
        # ⚠ Splitting from today or earlier would regenerate sessions for days
        # that may already have been taught. "This and future" starts tomorrow at
        # the earliest; to change today's class, move that occurrence.
        raise ValidationError(
            {"from_date": _("Choose a date after today; edit today's class as one occurrence.")}
        )
    if locked.active_to and from_date > locked.active_to:
        raise ValidationError({"from_date": _("This template has already ended by then.")})

    before = audit.snapshot(locked)

    # The new rule, starting the day the change takes effect.
    successor = ClassTemplate(
        dojo=locked.dojo,
        active_from=from_date,
        active_to=locked.active_to,
        **{field: getattr(locked, field) for field in EDITABLE_TEMPLATE_FIELDS},
    )
    for field, value in changes.items():
        setattr(successor, field, value)
    # ⚠ Field validation only. `validate_unique` and `validate_constraints` issue
    # their own queries through the default manager, which is tenant-scoped and
    # refuses to run without an actor — so a plain full_clean() raises
    # UnscopedAccessError rather than validating anything. The cross-tenant check
    # that actually matters here (same_organization_fields) runs in save().
    successor.full_clean(exclude=["dojo"], validate_unique=False, validate_constraints=False)
    successor.save()

    # Close the original the day before, so the two never overlap.
    locked.active_to = from_date - datetime.timedelta(days=1)
    locked.save(update_fields=["active_to", "updated_at"])

    # Untouched future sessions are the old rule's output and nothing more, so
    # they can be regenerated. Anything carrying a decision is preserved, and its
    # date is then withheld from the new template so the day does not end up with
    # two classes on it.
    horizon = today + datetime.timedelta(days=horizon_days)
    future = ClassSession.objects.for_actor(actor).filter(
        template=locked,
        starts_at__gte=datetime.datetime.combine(from_date, datetime.time.min, tzinfo=tz),
    )
    from apps.attendance.models import AttendanceRecord

    marked = set(
        AttendanceRecord.objects.for_actor(actor)
        .filter(session__in=future)
        .values_list("session_id", flat=True)
    )
    preserved = []
    removable = []
    for session in future.select_related("dojo"):
        if _carries_a_decision(session, marked=marked):
            preserved.append(session)
        else:
            removable.append(session.pk)
    if removable:
        ClassSession.objects.for_actor(actor).filter(pk__in=removable).delete()

    # ⚠ Preserved sessions are handed to the successor *before* it materialises,
    # and each is marked as occupying its date's new slot.
    #
    # Deleting the duplicate afterwards instead is the obvious approach and it is
    # wrong: it looks right until the next nightly run, which sees the slot empty
    # and refills it. The row has to be attached to the successor — `existing` is
    # computed per template, so a session still pointing at the closed template
    # blocks nothing — and it has to claim the slot durably.
    for session in preserved:
        local_date = session.starts_at.astimezone(tz).date()
        session.template = successor
        session.moved_from = datetime.datetime.combine(local_date, successor.start_time, tzinfo=tz)
        session.save(update_fields=["template", "moved_from", "updated_at"])

    materialise_template(successor, from_date=from_date, to_date=horizon)

    audit.record(
        "update",
        actor=actor,
        subject=locked,
        before=before,
        after=audit.snapshot(locked),
        note=(
            f"split from {from_date.isoformat()}; successor {successor.pk}; "
            f"{len(removable)} future session(s) regenerated, "
            f"{len(preserved)} preserved"
        ),
        strict=True,
    )
    return successor
