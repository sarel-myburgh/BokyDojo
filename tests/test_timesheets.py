"""Auto-drafted time entries and the weekly timesheet — TODO 1.9.3, 1.9.4.

⚠ Two tests here guard decisions that are easy to reverse by accident:

* ``test_re_marking_a_roster_does_not_reset_an_edited_draft`` — an entry is never
  overwritten, or a correction is silently discarded.
* ``test_the_importer_does_not_draft_timesheets`` — historical attendance is a
  record of the past, not a payroll event. Without this, importing two years of
  registers manufactures thousands of draft lines for classes taught by people
  who may have left.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import AttendanceRecord
from apps.attendance.services import mark_session
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    Dojo,
    Enrollment,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    User,
)
from apps.scheduling.models import ClassSession, ClassTemplate, SessionInstructor
from apps.staffing.models import InstructorProfile, TimeEntry
from apps.staffing.timesheets import draft_for_session

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def world():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Test Org", slug="test-org")
        dojo = Dojo.objects.create(
            organization=org, name="Dojo A", slug="dojo-a", timezone="Asia/Phnom_Penh"
        )
        teacher = Person.objects.create(organization=org, given_name="Mei", family_name="Kato")
        RoleAssignment.objects.create(
            organization=org,
            person=teacher,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        user = User.objects.create_user(
            email="sensei@example.com", password=PASSWORD, person=teacher
        )
        InstructorProfile.objects.create(
            person=teacher,
            pay_type=InstructorProfile.PayType.PER_CLASS,
            pay_rate_minor_units=1500,
            pay_currency="USD",
        )
        student = Person.objects.create(organization=org, given_name="Bopha", family_name="Chan")
        StudentProfile.objects.create(
            person=student, status=StudentProfile.Status.ACTIVE, home_dojo=dojo
        )
        Enrollment.objects.create(
            student=student, dojo=dojo, started_on=datetime.date(2024, 1, 1), is_primary=True
        )
        template = ClassTemplate.objects.create(
            dojo=dojo,
            name="Adults",
            rrule="FREQ=WEEKLY;BYDAY=MO",
            start_time=datetime.time(18, 30),
            duration_minutes=90,
            active_from=datetime.date(2024, 1, 1),
        )
        starts = timezone.now() - datetime.timedelta(minutes=30)
        session = ClassSession.objects.create(
            dojo=dojo,
            template=template,
            starts_at=starts,
            ends_at=starts + datetime.timedelta(minutes=90),
        )
        SessionInstructor.objects.create(session=session, person=teacher)
    actor = Actor(
        user_id=user.pk,
        person_id=teacher.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
        roles=frozenset({(Role.INSTRUCTOR, ScopeType.DOJO, dojo.pk)}),
    )
    return {
        "org": org,
        "dojo": dojo,
        "teacher": teacher,
        "user": user,
        "student": student,
        "session": session,
        "actor": actor,
    }


def entries(org):
    with allow_unscoped("test read"):
        return list(TimeEntry.objects.filter(dojo__organization=org))


# -- drafting -----------------------------------------------------------------


def test_marking_a_roster_drafts_a_timesheet_line(world):
    mark_session(
        session=world["session"],
        statuses={world["student"].pk: AttendanceRecord.Status.PRESENT},
        actor=world["actor"],
    )

    rows = entries(world["org"])
    assert len(rows) == 1
    assert rows[0].instructor_id == world["teacher"].pk
    assert rows[0].minutes == 90
    assert rows[0].status == TimeEntry.Status.DRAFT
    assert rows[0].category == TimeEntry.Category.CLASS


def test_the_pay_rate_is_snapshotted(world):
    """⚠ Not looked up at approval — February must not be revalued in March."""
    mark_session(
        session=world["session"],
        statuses={world["student"].pk: AttendanceRecord.Status.PRESENT},
        actor=world["actor"],
    )

    row = entries(world["org"])[0]
    assert row.pay_rate_snapshot_minor_units == 1500
    assert row.pay_rate_snapshot_currency == "USD"


def test_a_class_with_nobody_assigned_drafts_nothing(world):
    """No instructor on the session is a real state — the calendar shows it."""
    with allow_unscoped("test setup"):
        SessionInstructor.objects.all().delete()

    mark_session(
        session=world["session"],
        statuses={world["student"].pk: AttendanceRecord.Status.PRESENT},
        actor=world["actor"],
    )

    assert entries(world["org"]) == []


def test_drafting_is_idempotent(world):
    draft_for_session(world["session"], actor=world["actor"])
    draft_for_session(world["session"], actor=world["actor"])

    assert len(entries(world["org"])) == 1


def test_re_marking_a_roster_does_not_reset_an_edited_draft(world):
    """⚠ A late offline sync or a corrected roster must not discard the
    instructor's own correction, or reopen an approved line.

    ⚠ The correction changes ``ended_at``, not ``minutes``: TimeEntry.save
    recomputes minutes from the interval whenever ended_at is set (1.9.2), so
    assigning minutes directly is silently discarded. Any future edit screen has
    to edit the times.
    """
    mark_session(
        session=world["session"],
        statuses={world["student"].pk: AttendanceRecord.Status.PRESENT},
        actor=world["actor"],
    )
    with allow_unscoped("test setup"):
        row = TimeEntry.objects.get()
        row.ended_at = row.started_at + datetime.timedelta(minutes=120)
        row.status = TimeEntry.Status.APPROVED
        row.save(update_fields=["ended_at", "status"])
        row.refresh_from_db()
        assert row.minutes == 120, "the correction itself must stick"

    mark_session(
        session=world["session"],
        statuses={world["student"].pk: AttendanceRecord.Status.ABSENT},
        actor=world["actor"],
    )

    with allow_unscoped("test read"):
        row.refresh_from_db()
    assert row.minutes == 120
    assert row.status == TimeEntry.Status.APPROVED


def test_a_substitute_is_paid_not_the_person_covered_for(world):
    """⚠ SessionInstructor, never the template — the whole point of 1.4.8."""
    with allow_unscoped("test setup"):
        stand_in = Person.objects.create(
            organization=world["org"], given_name="Dara", family_name="Sok"
        )
        SessionInstructor.objects.all().delete()
        SessionInstructor.objects.create(
            session=world["session"],
            person=stand_in,
            is_substitute=True,
            replaces=world["teacher"],
        )

    mark_session(
        session=world["session"],
        statuses={world["student"].pk: AttendanceRecord.Status.PRESENT},
        actor=world["actor"],
    )

    rows = entries(world["org"])
    assert len(rows) == 1
    assert rows[0].instructor_id == stand_in.pk


def test_the_kiosk_completes_its_session_and_drafts(client, world):
    """⚠ A class checked in entirely on the door is still a class that was
    taught. It originally stayed SCHEDULED for ever and drafted nothing."""
    client.force_login(world["user"])
    client.post(reverse("kiosk", args=[world["session"].pk]))

    client.post(
        reverse("kiosk-mark", args=[world["session"].pk]),
        {"student_id": str(world["student"].pk)},
    )

    with allow_unscoped("test read"):
        world["session"].refresh_from_db()
    assert world["session"].status == ClassSession.Status.COMPLETED
    assert len(entries(world["org"])) == 1


def test_the_importer_does_not_draft_timesheets(world):
    """⚠ Historical attendance is a record of the past, not a payroll event.

    Two years of imported registers would otherwise manufacture thousands of
    draft lines for classes taught by people who may have left.
    """
    from apps.imports import csv_source, engine
    from apps.imports.attendance import AttendanceImporter

    day = (
        world["session"]
        .starts_at.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Phnom_Penh"))
        .date()
    )
    text = f"First name,Date,Class,Status\r\nBopha,{day},Adults,present\r\n"
    _headers, rows = csv_source.read_table(text.encode())

    with allow_unscoped("test setup"):
        RoleAssignment.objects.create(
            organization=world["org"],
            person=world["teacher"],
            role=Role.DOJO_ADMIN,
            scope_type=ScopeType.DOJO,
            dojo=world["dojo"],
        )
    admin_actor = Actor(
        user_id=world["user"].pk,
        person_id=world["teacher"].pk,
        organization_id=world["org"].pk,
        dojo_ids=frozenset({world["dojo"].pk}),
        roles=frozenset({(Role.DOJO_ADMIN, ScopeType.DOJO, world["dojo"].pk)}),
    )

    run = engine.run(
        importer=AttendanceImporter(),
        rows=rows,
        mapping={
            "First name": "given_name",
            "Date": "date",
            "Class": "class_name",
            "Status": "status",
        },
        actor=admin_actor,
        dojo=world["dojo"],
        filename="history.csv",
        dry_run=False,
    )

    assert run.created_count == 1
    assert entries(world["org"]) == [], "the importer must not draft timesheets"


# -- the screen ---------------------------------------------------------------


def test_the_timesheet_shows_this_weeks_hours(client, world):
    mark_session(
        session=world["session"],
        statuses={world["student"].pk: AttendanceRecord.Status.PRESENT},
        actor=world["actor"],
    )
    client.force_login(world["user"])

    body = client.get(reverse("timesheet")).content.decode()

    assert "1.5" in body  # 90 minutes
    assert "90 min" in body


def test_the_timesheet_shows_only_your_own_entries(client, world):
    """⚠ An instructor must not see what a colleague is paid."""
    with allow_unscoped("test setup"):
        other = Person.objects.create(
            organization=world["org"], given_name="Riku", family_name="Sasaki"
        )
        TimeEntry.objects.create(
            instructor=other,
            dojo=world["dojo"],
            category=TimeEntry.Category.CLASS,
            started_at=timezone.now(),
            minutes=240,
        )
    client.force_login(world["user"])

    body = client.get(reverse("timesheet")).content.decode()

    assert "240 min" not in body


def test_an_entry_without_a_pay_rate_says_so(client, world):
    with allow_unscoped("test setup"):
        InstructorProfile.objects.all().delete()
    mark_session(
        session=world["session"],
        statuses={world["student"].pk: AttendanceRecord.Status.PRESENT},
        actor=world["actor"],
    )
    client.force_login(world["user"])

    body = client.get(reverse("timesheet")).content.decode()

    assert "no pay rate recorded" in body


def test_a_guardian_cannot_reach_the_timesheet(client, world):
    with allow_unscoped("test setup"):
        parent = Person.objects.create(
            organization=world["org"], given_name="Sok", family_name="Ly"
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=parent,
            role=Role.GUARDIAN,
            scope_type=ScopeType.DOJO,
            dojo=world["dojo"],
        )
        parent_user = User.objects.create_user(
            email="parent@example.com", password=PASSWORD, person=parent
        )
    client.force_login(parent_user)

    assert client.get(reverse("timesheet")).status_code == 403


def test_anonymous_is_redirected(client):
    response = client.get(reverse("timesheet"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_the_week_can_be_paged(client, world):
    client.force_login(world["user"])

    body = client.get(reverse("timesheet"), {"date": "2026-06-10"}).content.decode()

    assert "8 Jun" in body  # the Monday of that week
    assert "date=2026-06-03" in body
    assert "date=2026-06-17" in body
