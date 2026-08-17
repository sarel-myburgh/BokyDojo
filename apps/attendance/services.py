"""Attendance services — TODO 1.5.1/1.5.3/1.5.4/1.5.5, plan §4.5.

``mark_attendance`` is the only sanctioned way to write an ``AttendanceRecord``.
It is idempotent, it is the place the retroactive-edit permission is enforced,
and it is what the roster UI, the kiosk (TODO 1.7) and the offline sync endpoint
(TODO 1.6.3) all call, so those three cannot drift apart.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.core import audit
from apps.core.scoping import Actor
from apps.core.setting_registry import ATTENDANCE_CATCHUP_WINDOW_DAYS
from apps.core.setting_resolver import ScopeChain, resolve
from apps.core.timezones import dojo_zone
from apps.identity.models import Enrollment, GovernanceModel, Person
from apps.identity.permissions import Action, require
from apps.scheduling.models import ClassSession

from .models import AttendanceRecord

DEFAULT_CATCH_UP_WINDOW_DAYS = 14


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

    @property
    def version(self) -> str:
        return self.record.updated_at.isoformat() if self.record else ""


def _governance_of(session: ClassSession) -> str:
    return session.dojo.organization.governance_model or GovernanceModel.CENTRAL


def _locked_session(session: ClassSession, actor: Actor) -> ClassSession:
    """Serialize all canonical attendance writers for one class session."""
    return (
        ClassSession.objects.for_actor(actor)
        .select_for_update()
        .select_related("dojo", "dojo__organization", "template")
        .get(pk=session.pk)
    )


def _session_local_date(session: ClassSession) -> datetime.date:
    return session.starts_at.astimezone(dojo_zone(session.dojo)).date()


def _is_retroactive(session: ClassSession) -> bool:
    """True once the class's own day is over in its own timezone.

    Marking a class you have just taught is the normal path and must not need an
    elevated permission; going back to last Tuesday is a different act.
    """
    today = timezone.now().astimezone(dojo_zone(session.dojo)).date()
    return _session_local_date(session) < today


def catch_up_window_days(session: ClassSession) -> int:
    """Return this dojo's bounded window for initially marking a missed class."""
    raw = resolve(
        ATTENDANCE_CATCHUP_WINDOW_DAYS.key,
        ScopeChain(organization_id=session.dojo.organization_id, dojo_id=session.dojo_id),
    )
    try:
        return max(1, min(int(raw), 365))
    except (TypeError, ValueError):
        return DEFAULT_CATCH_UP_WINDOW_DAYS


def is_catch_up_eligible(
    *,
    session: ClassSession,
    unmarked: bool | None = None,
    now: datetime.datetime | None = None,
) -> bool:
    """Whether a still-unmarked recent session may be filled in from memory.

    This is deliberately narrower than a retroactive edit: it allows only an
    initial roster, in the configured window. Corrections still require the
    elevated ``attendance.edit_retroactive`` permission.
    """
    now = now or timezone.now()
    local_today = now.astimezone(dojo_zone(session.dojo)).date()
    session_date = _session_local_date(session)
    if (
        session.status != ClassSession.Status.SCHEDULED
        or session_date >= local_today
        or session_date < local_today - datetime.timedelta(days=catch_up_window_days(session))
    ):
        return False

    if unmarked is None:
        unmarked = (
            not AttendanceRecord.objects.for_organization(session.dojo.organization_id)
            .filter(session=session)
            .exists()
        )
    return unmarked


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
    _allow_unmarked_catch_up: bool = False,
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
    session = _locked_session(session, actor)

    if client_generated_id:
        replay = (
            AttendanceRecord.objects.for_actor(actor)
            .filter(client_generated_id=client_generated_id)
            .first()
        )
        if replay is not None:
            return replay, False

    # Visiting is derived from enrollment, never trusted from a client. Treat an
    # explicit visiting input as present and let the enrollment check decide.
    if status == AttendanceRecord.Status.VISITING:
        status = AttendanceRecord.Status.PRESENT

    # Marking someone who is not enrolled here is legitimate — a seminar guest,
    # a student from the other branch — but it is recorded as visiting so the
    # host dojo's own numbers do not quietly absorb them (plan §4.3).
    if status in (
        AttendanceRecord.Status.PRESENT,
        AttendanceRecord.Status.LATE,
    ) and not _has_live_enrollment(session, student):
        status = AttendanceRecord.Status.VISITING

    existing = (
        AttendanceRecord.objects.for_actor(actor).filter(session=session, student=student).first()
    )

    if existing is None:
        if _is_retroactive(session) and not _allow_unmarked_catch_up:
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


@transaction.atomic
def mark_session(
    *,
    session: ClassSession,
    statuses: dict,
    actor: Actor,
    method: str = AttendanceRecord.Method.ROSTER,
    client_ids: dict | None = None,
    allow_catch_up: bool = False,
) -> dict:
    """Apply a whole roster in one call — the roster UI's save path.

    ``statuses`` maps student id to status. Anything omitted is left exactly as
    it was: a half-finished roster must not silently mark the rest absent.
    """
    client_ids = client_ids or {}
    require(actor, Action.ATTENDANCE_RECORD, session, governance_model=_governance_of(session))
    session = _locked_session(session, actor)
    # Snapshot eligibility before creating the first row; catch-up never grants
    # permission to alter an attendance answer that already exists.
    catch_up = allow_catch_up and is_catch_up_eligible(session=session)
    roster = {
        entry.student.pk: entry.student for entry in session_roster(session=session, actor=actor)
    }

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
            _allow_unmarked_catch_up=catch_up,
        )
        created += int(was_created)
        updated += int(not was_created)

    # "Completed" means attendance has been taken, which is what the catch-up
    # flow (TODO 1.5.6) looks for. A cancelled session stays cancelled.
    if statuses:
        complete_session(session, actor=actor)

    return {"created": created, "updated": updated}


def complete_session(session, *, actor) -> bool:
    """Mark a class taught, and draft its timesheet lines — TODO 1.5.6, 1.9.3.

    Returns True if the session transitioned. A cancelled session stays
    cancelled; a completed one is not completed twice.

    ⚠ One place, called by every path that means "a class was just marked": the
    roster save, the offline sync, and the kiosk. The kiosk originally did not
    complete its sessions at all, so a class checked in entirely on the door
    stayed SCHEDULED for ever and never produced a timesheet line.

    ⚠ Deliberately **not** called by the importer. Historical attendance is a
    record of the past, not a payroll event — see apps/staffing/timesheets.py.
    """
    from apps.staffing.timesheets import draft_for_session

    if session.status != ClassSession.Status.SCHEDULED:
        return False
    session.status = ClassSession.Status.COMPLETED
    session.save(update_fields=["status", "updated_at"])
    draft_for_session(session, actor=actor)
    return True


def attendance_version(record: AttendanceRecord | None) -> str:
    """Opaque optimistic-concurrency version exposed to the roster client."""
    return record.updated_at.isoformat() if record is not None else ""


@transaction.atomic
def sync_session_marks(
    *,
    session: ClassSession,
    marks: list[dict],
    actor: Actor,
) -> list[dict]:
    """Apply offline marks without overwriting a newer server-side correction.

    Each mark contains the version visible when the roster was loaded. A reused
    client id is an idempotent replay. A different client id with a stale base
    version is a conflict and leaves the current record untouched.
    """
    require(actor, Action.ATTENDANCE_RECORD, session, governance_model=_governance_of(session))
    session = _locked_session(session, actor)
    catch_up = is_catch_up_eligible(session=session)
    roster = {
        entry.student.pk: entry.student for entry in session_roster(session=session, actor=actor)
    }
    results = []
    applied_any = False

    for mark in marks:
        student_id = mark["student_id"]
        requested_status = mark["status"]
        client_id = mark["client_generated_id"]
        base_version = mark.get("base_version", "")
        student = roster.get(student_id)
        if student is None:
            results.append(
                {
                    "student_id": str(student_id),
                    "state": "conflict",
                    "reason": "student_not_on_roster",
                }
            )
            continue

        replay = (
            AttendanceRecord.objects.for_actor(actor)
            .select_for_update()
            .filter(client_generated_id=client_id)
            .first()
        )
        if replay is not None:
            if replay.session_id == session.pk and replay.student_id == student_id:
                results.append(
                    {
                        "student_id": str(student_id),
                        "state": "replayed",
                        "status": replay.status,
                        "version": attendance_version(replay),
                    }
                )
            else:
                results.append(
                    {
                        "student_id": str(student_id),
                        "state": "conflict",
                        "reason": "client_id_collision",
                    }
                )
            continue

        existing = (
            AttendanceRecord.objects.for_actor(actor)
            .select_for_update()
            .filter(session=session, student_id=student_id)
            .first()
        )
        current_version = attendance_version(existing)
        if existing is not None and existing.status == requested_status:
            results.append(
                {
                    "student_id": str(student_id),
                    "state": "unchanged",
                    "status": existing.status,
                    "version": current_version,
                }
            )
            continue
        if current_version != base_version:
            results.append(
                {
                    "student_id": str(student_id),
                    "state": "conflict",
                    "reason": "record_changed",
                    "status": existing.status if existing else "",
                    "version": current_version,
                }
            )
            continue

        record, _created = mark_attendance(
            session=session,
            student=student,
            status=requested_status,
            actor=actor,
            method=AttendanceRecord.Method.ROSTER,
            client_generated_id=client_id,
            _allow_unmarked_catch_up=catch_up,
        )
        applied_any = True
        results.append(
            {
                "student_id": str(student_id),
                "state": "applied",
                "status": record.status,
                "version": attendance_version(record),
            }
        )

    if applied_any:
        complete_session(session, actor=actor)
    return results
