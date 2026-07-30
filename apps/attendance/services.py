"""Attendance services — TODO 1.5.1/1.5.3/1.5.4/1.5.5, plan §4.5.

``mark_attendance`` is the only sanctioned way to write an ``AttendanceRecord``.
It is idempotent, it is the place the retroactive-edit permission is enforced,
and it is what the roster UI, the kiosk (TODO 1.7) and the offline sync endpoint
(TODO 1.6.3) all call, so those three cannot drift apart.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.core import audit
from apps.core.scoping import Actor
from apps.identity.models import Enrollment, GovernanceModel, Person
from apps.identity.permissions import Action, require
from apps.scheduling.models import ClassSession

from .models import AttendanceRecord


@dataclass
class RosterEntry:
    """One line of the roster: a student and their mark, if any yet."""

    student: Person
    record: AttendanceRecord | None
    is_visitor: bool = False

    @property
    def status(self) -> str:
        return self.record.status if self.record else ""

    @property
    def is_marked(self) -> bool:
        return self.record is not None


def _governance_of(session: ClassSession) -> str:
    return session.dojo.organization.governance_model or GovernanceModel.CENTRAL


def _session_local_date(session: ClassSession) -> datetime.date:
    return session.starts_at.astimezone(ZoneInfo(session.dojo.timezone or "UTC")).date()


def _is_retroactive(session: ClassSession) -> bool:
    """True once the class's own day is over in its own timezone.

    Marking a class you have just taught is the normal path and must not need an
    elevated permission; going back to last Tuesday is a different act.
    """
    today = timezone.now().astimezone(ZoneInfo(session.dojo.timezone or "UTC")).date()
    return _session_local_date(session) < today


def _has_live_enrollment(session: ClassSession, student: Person) -> bool:
    return (
        Enrollment.objects.for_organization(session.dojo.organization_id)
        .filter(student=student, dojo=session.dojo, ended_on__isnull=True)
        .exists()
    )


def session_roster(*, session: ClassSession, actor: Actor) -> list[RosterEntry]:
    """Who to show on the roster for this session — TODO 1.5.2.

    Students with a live enrolment at the session's dojo, plus anyone already
    marked (a visitor from another dojo, or somebody who has since left, whose
    mark must not vanish from the screen it was made on).
    """
    require(actor, Action.ATTENDANCE_VIEW, session, governance_model=_governance_of(session))

    records = {
        record.student_id: record
        for record in AttendanceRecord.objects.for_actor(actor)
        .filter(session=session)
        .select_related("student")
    }

    enrolled = list(
        Person.objects.for_actor(actor)
        .filter(
            enrollments__dojo=session.dojo,
            enrollments__ended_on__isnull=True,
            enrollments__status=Enrollment.Status.ACTIVE,
        )
        .order_by("family_name", "given_name")
    )
    enrolled_ids = {person.pk for person in enrolled}

    entries = [RosterEntry(student=person, record=records.get(person.pk)) for person in enrolled]
    entries.extend(
        RosterEntry(student=record.student, record=record, is_visitor=True)
        for student_id, record in records.items()
        if student_id not in enrolled_ids
    )
    return entries


@transaction.atomic
def mark_attendance(
    *,
    session: ClassSession,
    student: Person,
    status: str,
    actor: Actor,
    method: str = AttendanceRecord.Method.ROSTER,
    client_generated_id: str = "",
    note: str = "",
    marked_at: datetime.datetime | None = None,
) -> tuple[AttendanceRecord, bool]:
    """Record or correct one student's attendance. Returns ``(record, created)``.

    Idempotent twice over:

    * A replayed ``client_generated_id`` returns the stored record untouched, so
      an offline queue flushed twice does not double-write or overwrite a later
      correction.
    * Absent that id, the (session, student) pair is updated in place rather than
      duplicated — there is exactly one answer to "was this student here".
    """
    require(actor, Action.ATTENDANCE_RECORD, session, governance_model=_governance_of(session))

    if client_generated_id:
        replay = (
            AttendanceRecord.objects.for_actor(actor)
            .filter(client_generated_id=client_generated_id)
            .first()
        )
        if replay is not None:
            return replay, False

    # Marking someone who is not enrolled here is legitimate — a seminar guest,
    # a student from the other branch — but it is recorded as visiting so the
    # host dojo's own numbers do not quietly absorb them (plan §4.3).
    if status in (
        AttendanceRecord.Status.PRESENT,
        AttendanceRecord.Status.LATE,
    ) and not _has_live_enrollment(session, student):
        status = AttendanceRecord.Status.VISITING

    existing = (
        AttendanceRecord.objects.for_actor(actor)
        .filter(session=session, student=student)
        .first()
    )

    if existing is None:
        if _is_retroactive(session):
            require(
                actor,
                Action.ATTENDANCE_EDIT_RETROACTIVE,
                session,
                governance_model=_governance_of(session),
            )
        record = AttendanceRecord(
            session=session,
            student=student,
            status=status,
            method=method,
            marked_by_id=actor.person_id,
            marked_at=marked_at or timezone.now(),
            client_generated_id=client_generated_id,
            note=note[:255],
            created_by_id=actor.person_id,
        )
        # Constraint validation is left to the database: it queries through the
        # tenant-scoped default manager, which refuses an unscoped read.
        record.full_clean(validate_unique=False, validate_constraints=False)
        record.save()
        audit.record_change("create", record, actor=actor)
        return record, True

    unchanged = existing.status == status and existing.note == note[:255]
    if unchanged:
        return existing, False

    # Changing an answer already given is the sensitive act, and after the day is
    # over it is a different permission (TODO 1.5.5).
    if _is_retroactive(session):
        require(
            actor,
            Action.ATTENDANCE_EDIT_RETROACTIVE,
            session,
            governance_model=_governance_of(session),
        )

    before = audit.snapshot(existing)
    existing.status = status
    existing.method = method
    existing.marked_by_id = actor.person_id
    existing.marked_at = marked_at or timezone.now()
    existing.note = note[:255]
    existing.save(
        update_fields=["status", "method", "marked_by", "marked_at", "note", "updated_at"]
    )
    audit.record_change("update", existing, before=before, actor=actor, note="attendance corrected")
    return existing, False


def mark_session(
    *,
    session: ClassSession,
    statuses: dict,
    actor: Actor,
    method: str = AttendanceRecord.Method.ROSTER,
    client_ids: dict | None = None,
) -> dict:
    """Apply a whole roster in one call — the roster UI's save path.

    ``statuses`` maps student id to status. Anything omitted is left exactly as
    it was: a half-finished roster must not silently mark the rest absent.
    """
    client_ids = client_ids or {}
    roster = {entry.student.pk: entry.student for entry in session_roster(session=session, actor=actor)}

    created = updated = 0
    for student_id, status in statuses.items():
        student = roster.get(student_id)
        if student is None:
            # Not on the roster and not already marked: resolve within the
            # organisation so a visitor can still be added, but never accept a
            # person id from another tenant.
            student = (
                Person.objects.for_organization(session.dojo.organization_id)
                .filter(pk=student_id)
                .first()
            )
            if student is None:
                continue
        _record, was_created = mark_attendance(
            session=session,
            student=student,
            status=status,
            actor=actor,
            method=method,
            client_generated_id=client_ids.get(student_id, ""),
        )
        created += int(was_created)
        updated += int(not was_created)

    # "Completed" means attendance has been taken, which is what the catch-up
    # flow (TODO 1.5.6) will look for. A cancelled session stays cancelled.
    if session.status == ClassSession.Status.SCHEDULED:
        session.status = ClassSession.Status.COMPLETED
        session.save(update_fields=["status", "updated_at"])

    return {"created": created, "updated": updated}
