"""Importing attendance that already happened — TODO 1.10.4, plan §12.10.

⚠ **This calls ``apps.attendance.services.mark_attendance``. It does not write
``AttendanceRecord`` rows.** The roster, the offline sync endpoint and the kiosk
are all required to go through that one service or the paths drift; import is the
fourth such path and the rule is not different for it. Everything the service
does — the enrolment/visiting rule, the row lock, the retroactive permission
check, the audit — is wanted here too.

⚠ **Sessions are never invented.** A row naming a class on a date with no session
is an error, not an instruction to create one. Attendance is evidence about a
class that happened; a class conjured from an attendance file is evidence of
nothing, and it would quietly corrupt every report built on session counts.

⚠ **Idempotency comes free from the service.** ``client_generated_id`` already
makes a replayed offline queue a no-op, so each row derives a deterministic id
from its source key and a re-import of the same file replays rather than
double-marks. No second mechanism, and nothing to keep in step.
"""

from __future__ import annotations

import datetime
import hashlib

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.attendance.models import AttendanceRecord
from apps.attendance.services import mark_attendance
from apps.core.scoping import Actor
from apps.core.timezones import dojo_zone
from apps.identity.models import GovernanceModel
from apps.identity.permissions import Action, require
from apps.scheduling.models import ClassSession

from .engine import Importer, Outcome
from .models import ImportKind
from .students import parse_date
from .subjects import resolve_student

ATTENDANCE_ENTITY = "attendance"

STATUS_SYNONYMS = {
    "present": AttendanceRecord.Status.PRESENT,
    "p": AttendanceRecord.Status.PRESENT,
    "yes": AttendanceRecord.Status.PRESENT,
    "y": AttendanceRecord.Status.PRESENT,
    "attended": AttendanceRecord.Status.PRESENT,
    "1": AttendanceRecord.Status.PRESENT,
    "late": AttendanceRecord.Status.LATE,
    "l": AttendanceRecord.Status.LATE,
    "absent": AttendanceRecord.Status.ABSENT,
    "a": AttendanceRecord.Status.ABSENT,
    "no": AttendanceRecord.Status.ABSENT,
    "n": AttendanceRecord.Status.ABSENT,
    "0": AttendanceRecord.Status.ABSENT,
    "excused": AttendanceRecord.Status.EXCUSED,
    "e": AttendanceRecord.Status.EXCUSED,
    "visiting": AttendanceRecord.Status.VISITING,
    "guest": AttendanceRecord.Status.VISITING,
}


def require_attendance_import_permission(actor: Actor, dojo) -> None:
    """⚠ Requires the *retroactive* edit right, not merely ``ATTENDANCE_RECORD``.

    A historical import is retroactive by definition — that is the whole point of
    the file — so this is exactly the power being exercised, and it should be
    held explicitly rather than acquired sideways through an importer. The
    service checks it again per row; this refuses the whole run early with a
    message rather than after fifty rows.
    """
    governance = dojo.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.ATTENDANCE_RECORD, dojo, governance_model=governance)
    require(actor, Action.ATTENDANCE_EDIT_RETROACTIVE, dojo, governance_model=governance)


def parse_status(value: str) -> str:
    if not value:
        return AttendanceRecord.Status.PRESENT
    resolved = STATUS_SYNONYMS.get(value.strip().lower())
    if resolved is None:
        raise ValidationError(
            {
                "status": _("'%(value)s' is not an attendance status. Use one of: %(allowed)s.")
                % {"value": value, "allowed": "present, late, absent, excused, visiting"}
            }
        )
    return resolved


def _sessions_on(dojo, day: datetime.date, *, actor: Actor):
    """Every session at this dojo whose *local* date is ``day``.

    ⚠ Filtered on the dojo's own timezone, not the database's. A 19:30 Phnom Penh
    class is stored at 12:30 UTC, and a naive ``starts_at__date`` filter would put
    late classes on the wrong day — the same trap the calendar (`1.4.9`) exists to
    avoid, and it would silently attach a Tuesday register to Monday's class.
    """
    zone = dojo_zone(dojo)
    window_start = datetime.datetime.combine(
        day, datetime.time.min, tzinfo=datetime.UTC
    ) - datetime.timedelta(days=1)
    window_end = datetime.datetime.combine(
        day, datetime.time.max, tzinfo=datetime.UTC
    ) + datetime.timedelta(days=1)
    candidates = (
        ClassSession.objects.for_actor(actor)
        .select_related("dojo", "dojo__organization", "template")
        .filter(dojo=dojo, starts_at__gte=window_start, starts_at__lte=window_end)
        .order_by("starts_at")
    )
    return [s for s in candidates if s.starts_at.astimezone(zone).date() == day]


def resolve_session(*, dojo, day: datetime.date, class_name: str, actor: Actor) -> ClassSession:
    sessions = _sessions_on(dojo, day, actor=actor)
    if not sessions:
        raise ValidationError(
            {
                "date": _(
                    "No class was scheduled at %(dojo)s on %(date)s. Attendance cannot "
                    "create one — check the date, or generate the sessions first."
                )
                % {"dojo": dojo.name, "date": day.isoformat()}
            }
        )

    if class_name:
        wanted = class_name.strip().casefold()
        matched = [
            s for s in sessions if s.template and s.template.name.strip().casefold() == wanted
        ]
        if not matched:
            available = ", ".join(
                sorted({s.template.name for s in sessions if s.template}) or [str(_("one-off"))]
            )
            raise ValidationError(
                {
                    "class_name": _(
                        "No class called '%(name)s' on %(date)s. That day has: %(available)s."
                    )
                    % {"name": class_name, "date": day.isoformat(), "available": available}
                }
            )
        sessions = matched

    if len(sessions) > 1:
        # ⚠ Refused rather than "the first one". Several classes a day is normal,
        # and putting the juniors' register against the adults' class is the kind
        # of error nobody finds until a grading is refused.
        available = ", ".join(
            sorted({s.template.name for s in sessions if s.template}) or [str(_("one-off"))]
        )
        raise ValidationError(
            {
                "class_name": _(
                    "%(count)s classes ran on %(date)s (%(available)s). Add a class "
                    "column so each row says which one."
                )
                % {"count": len(sessions), "date": day.isoformat(), "available": available}
            }
        )
    return sessions[0]


class AttendanceImporter(Importer):
    entity_type = ATTENDANCE_ENTITY
    kind = ImportKind.ATTENDANCE

    fields = {
        "student_external_id": False,
        "given_name": False,
        "family_name": False,
        "date_of_birth": False,
        "date": True,
        "class_name": False,
        "status": False,
        "note": False,
    }

    def natural_key(self, row: dict[str, str]) -> str:
        """One mark per student per class. The student is not resolved yet here,
        so the key uses whatever the row says and is stable for that file."""
        who = (row.get("student_external_id") or "").strip() or "|".join(
            (
                (row.get("given_name") or "").strip().casefold(),
                (row.get("family_name") or "").strip().casefold(),
                (row.get("date_of_birth") or "").strip(),
            )
        )
        day = (row.get("date") or "").strip()
        klass = (row.get("class_name") or "").strip().casefold()
        if not who.strip("|") or not day:
            return ""
        return f"{who}@{day}#{klass}"

    def apply(self, row, *, existing_id, actor: Actor, dojo):
        day = parse_date(row.get("date", ""), field="date")
        if day is None:
            raise ValidationError({"date": _("A date is required.")})
        if day > datetime.date.today():
            raise ValidationError({"date": _("Attendance cannot be recorded for the future.")})

        student = resolve_student(row, actor=actor, organization_id=dojo.organization_id)
        session = resolve_session(
            dojo=dojo,
            day=day,
            class_name=row.get("class_name", ""),
            actor=actor,
        )
        status = parse_status(row.get("status", ""))

        # Deterministic, so re-importing the same file replays through the
        # service's own idempotency rather than needing a second mechanism.
        fingerprint = hashlib.sha256(f"{session.pk}|{student.pk}".encode()).hexdigest()[:32]

        record, created = mark_attendance(
            session=session,
            student=student,
            status=status,
            actor=actor,
            method=AttendanceRecord.Method.IMPORT,
            client_generated_id=f"import:{fingerprint}",
            note=(row.get("note") or "").strip()[:255],
        )
        return record, Outcome.CREATED if created else Outcome.UPDATED
