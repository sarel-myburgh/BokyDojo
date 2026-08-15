"""Attendance core — TODO 1.5.1/1.5.3/1.5.4/1.5.5, plan §4.5.

The idempotency cases here are the contract the offline PWA (TODO 1.6) will be
built against, so they are written as if that client already existed.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.attendance.models import AttendanceRecord
from apps.attendance.services import mark_attendance, mark_session, session_roster
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    Dojo,
    Enrollment,
    Organization,
    Person,
    Role,
    ScopeType,
    StudentProfile,
)
from apps.identity.permissions import PermissionDenied
from apps.scheduling.models import ClassSession

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def dojo(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(
            organization=org, name="Dojo A", slug="dojo-a", timezone="Asia/Phnom_Penh"
        )


@pytest.fixture
def dojo_b(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(
            organization=org, name="Dojo B", slug="dojo-b", timezone="Asia/Phnom_Penh"
        )


def make_person(org, given, family="Test", *, dojo=None, status=Enrollment.Status.ACTIVE):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name=given, family_name=family)
        StudentProfile.objects.create(person=person, status=StudentProfile.Status.ACTIVE)
        if dojo is not None:
            Enrollment.objects.create(
                student=person,
                dojo=dojo,
                started_on=datetime.date(2026, 1, 1),
                is_primary=True,
                status=status,
            )
        return person


def make_session(dojo, *, hours_ago=1, duration=60):
    starts = timezone.now() - datetime.timedelta(hours=hours_ago)
    with allow_unscoped("test setup"):
        return ClassSession.objects.create(
            dojo=dojo,
            starts_at=starts,
            ends_at=starts + datetime.timedelta(minutes=duration),
        )


@pytest.fixture
def student(org, dojo):
    return make_person(org, "Sokha", "Chhorn", dojo=dojo)


@pytest.fixture
def session(dojo):
    return make_session(dojo)


@pytest.fixture
def instructor(org, dojo):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Takeshi", family_name="Yamada")
    return Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
        roles=frozenset({(Role.INSTRUCTOR, ScopeType.DOJO, dojo.pk)}),
    )


@pytest.fixture
def dojo_admin(org, dojo):
    """Has the retroactive-edit permission that a plain instructor lacks."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Head", family_name="Sensei")
    return Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
        roles=frozenset({(Role.DOJO_ADMIN, ScopeType.DOJO, dojo.pk)}),
    )


# -- marking ------------------------------------------------------------------


def test_mark_creates_a_record(session, student, instructor):
    record, created = mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.PRESENT, actor=instructor
    )

    assert created is True
    assert record.status == AttendanceRecord.Status.PRESENT
    assert record.method == AttendanceRecord.Method.ROSTER
    assert record.marked_by_id == instructor.person_id
    assert record.attended is True


def test_every_status_in_the_set_is_accepted(session, student, instructor, org, dojo):
    """TODO 1.5.3 — present / late / absent / excused / visiting."""
    for index, status in enumerate(
        [
            AttendanceRecord.Status.PRESENT,
            AttendanceRecord.Status.LATE,
            AttendanceRecord.Status.ABSENT,
            AttendanceRecord.Status.EXCUSED,
        ]
    ):
        pupil = make_person(org, f"Pupil{index}", dojo=dojo)
        record, _ = mark_attendance(session=session, student=pupil, status=status, actor=instructor)
        assert record.status == status


def test_absent_and_excused_do_not_count_as_attended(session, instructor, org, dojo):
    absent = make_person(org, "Away", dojo=dojo)
    record, _ = mark_attendance(
        session=session, student=absent, status=AttendanceRecord.Status.ABSENT, actor=instructor
    )
    assert record.attended is False


def test_re_marking_the_same_student_updates_rather_than_duplicates(session, student, instructor):
    first, created_first = mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.PRESENT, actor=instructor
    )
    second, created_second = mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.LATE, actor=instructor
    )

    assert created_first is True
    assert created_second is False
    assert first.pk == second.pk
    assert second.status == AttendanceRecord.Status.LATE
    assert AttendanceRecord.objects.for_actor(instructor).filter(session=session).count() == 1


def test_duplicate_session_student_pair_is_refused_at_the_database(session, student):
    with allow_unscoped("test setup"):
        AttendanceRecord.objects.create(session=session, student=student)
        with pytest.raises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.create(session=session, student=student)


# -- offline idempotency (the TODO 1.6.3 contract) ----------------------------


def test_replayed_client_generated_id_is_ignored(session, student, instructor):
    client_id = str(uuid.uuid4())

    first, created_first = mark_attendance(
        session=session,
        student=student,
        status=AttendanceRecord.Status.PRESENT,
        actor=instructor,
        client_generated_id=client_id,
    )
    second, created_second = mark_attendance(
        session=session,
        student=student,
        status=AttendanceRecord.Status.PRESENT,
        actor=instructor,
        client_generated_id=client_id,
    )

    assert created_first is True
    assert created_second is False
    assert first.pk == second.pk
    assert AttendanceRecord.objects.for_actor(instructor).count() == 1


def test_a_replay_does_not_overwrite_a_later_correction(session, student, instructor):
    """The offline queue flushes twice; the second flush must not undo a fix."""
    client_id = str(uuid.uuid4())
    mark_attendance(
        session=session,
        student=student,
        status=AttendanceRecord.Status.PRESENT,
        actor=instructor,
        client_generated_id=client_id,
    )
    mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.LATE, actor=instructor
    )

    replayed, created = mark_attendance(
        session=session,
        student=student,
        status=AttendanceRecord.Status.PRESENT,
        actor=instructor,
        client_generated_id=client_id,
    )

    assert created is False
    assert replayed.status == AttendanceRecord.Status.LATE, "the correction stands"


def test_two_different_client_ids_for_one_student_still_yield_one_record(
    session, student, instructor
):
    mark_attendance(
        session=session,
        student=student,
        status=AttendanceRecord.Status.PRESENT,
        actor=instructor,
        client_generated_id=str(uuid.uuid4()),
    )
    mark_attendance(
        session=session,
        student=student,
        status=AttendanceRecord.Status.LATE,
        actor=instructor,
        client_generated_id=str(uuid.uuid4()),
    )

    assert AttendanceRecord.objects.for_actor(instructor).filter(session=session).count() == 1


def test_blank_client_ids_do_not_collide(session, instructor, org, dojo):
    """The uniqueness index on client_generated_id must be partial."""
    for index in range(3):
        pupil = make_person(org, f"Online{index}", dojo=dojo)
        mark_attendance(
            session=session,
            student=pupil,
            status=AttendanceRecord.Status.PRESENT,
            actor=instructor,
        )
    assert AttendanceRecord.objects.for_actor(instructor).filter(session=session).count() == 3


# -- visiting students (TODO 1.5.4) -------------------------------------------


def test_unenrolled_student_is_recorded_as_visiting(session, instructor, org, dojo_b):
    """Cross-dojo attendance is allowed and flagged — plan §4.3."""
    visitor = make_person(org, "Guest", "FromB", dojo=dojo_b)

    record, _ = mark_attendance(
        session=session, student=visitor, status=AttendanceRecord.Status.PRESENT, actor=instructor
    )

    assert record.status == AttendanceRecord.Status.VISITING
    assert record.attended is True


def test_visiting_does_not_override_absent(session, instructor, org, dojo_b):
    visitor = make_person(org, "Guest", "FromB", dojo=dojo_b)
    record, _ = mark_attendance(
        session=session, student=visitor, status=AttendanceRecord.Status.ABSENT, actor=instructor
    )
    assert record.status == AttendanceRecord.Status.ABSENT


def test_enrolled_student_is_not_flagged_as_visiting(session, student, instructor):
    record, _ = mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.PRESENT, actor=instructor
    )
    assert record.status == AttendanceRecord.Status.PRESENT


def test_client_cannot_label_an_enrolled_student_as_visiting(session, student, instructor):
    record, _ = mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.VISITING, actor=instructor
    )
    assert record.status == AttendanceRecord.Status.PRESENT


# -- permissions --------------------------------------------------------------


def test_instructor_at_another_dojo_may_not_mark(session, student, org, dojo_b):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Other", family_name="Sensei")
    outsider = Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo_b.pk}),
        roles=frozenset({(Role.INSTRUCTOR, ScopeType.DOJO, dojo_b.pk)}),
    )

    with pytest.raises(PermissionDenied):
        mark_attendance(
            session=session,
            student=student,
            status=AttendanceRecord.Status.PRESENT,
            actor=outsider,
        )


def test_guardian_may_not_mark_attendance(session, student, org, dojo):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Parent", family_name="One")
    guardian = Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
        roles=frozenset({(Role.GUARDIAN, ScopeType.DOJO, dojo.pk)}),
    )

    with pytest.raises(PermissionDenied):
        mark_attendance(
            session=session,
            student=student,
            status=AttendanceRecord.Status.PRESENT,
            actor=guardian,
        )


def test_attendance_record_may_not_span_organisations(session, dojo):
    with allow_unscoped("test setup"):
        other_org = Organization.objects.create(name="Other", slug="other")
        outsider = Person.objects.create(
            organization=other_org, given_name="Foreign", family_name="Student"
        )
        with pytest.raises(ValidationError):
            AttendanceRecord(session=session, student=outsider).save()


# -- retroactive edits (TODO 1.5.5) -------------------------------------------


def test_marking_todays_class_needs_no_elevated_permission(dojo, student, instructor):
    session = make_session(dojo, hours_ago=1)
    record, created = mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.PRESENT, actor=instructor
    )
    assert created is True


def test_instructor_may_not_mark_a_past_class(dojo, student, instructor):
    session = make_session(dojo, hours_ago=72)
    with pytest.raises(PermissionDenied):
        mark_attendance(
            session=session,
            student=student,
            status=AttendanceRecord.Status.PRESENT,
            actor=instructor,
        )


def test_dojo_admin_may_mark_a_past_class(dojo, student, dojo_admin):
    session = make_session(dojo, hours_ago=72)
    record, created = mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.PRESENT, actor=dojo_admin
    )
    assert created is True


def test_instructor_may_not_correct_a_past_class(dojo, student, instructor, dojo_admin):
    session = make_session(dojo, hours_ago=72)
    mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.PRESENT, actor=dojo_admin
    )

    with pytest.raises(PermissionDenied):
        mark_attendance(
            session=session,
            student=student,
            status=AttendanceRecord.Status.ABSENT,
            actor=instructor,
        )


def test_repeating_an_unchanged_mark_on_a_past_class_is_not_an_edit(
    dojo, student, dojo_admin, instructor
):
    """An idle re-save must not demand a permission the instructor lacks."""
    session = make_session(dojo, hours_ago=72)
    mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.PRESENT, actor=dojo_admin
    )

    record, created = mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.PRESENT, actor=instructor
    )
    assert created is False
    assert record.status == AttendanceRecord.Status.PRESENT


def test_correction_is_audited(session, student, instructor):
    from apps.core.models import AuditLog

    mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.PRESENT, actor=instructor
    )
    mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.ABSENT, actor=instructor
    )

    entries = AuditLog.objects.filter(subject_type="attendance.AttendanceRecord")
    assert entries.filter(action="create").exists()
    correction = entries.filter(action="update").first()
    assert correction is not None
    assert correction.before["status"] == "present"
    assert correction.after["status"] == "absent"


# -- roster -------------------------------------------------------------------


def test_roster_lists_actively_enrolled_students(session, dojo, org, instructor):
    make_person(org, "Bopha", "Aa", dojo=dojo)
    make_person(org, "Chan", "Bb", dojo=dojo)

    entries = session_roster(session=session, actor=instructor)

    assert [entry.student.given_name for entry in entries] == ["Bopha", "Chan"]
    assert all(entry.is_marked is False for entry in entries)


def test_roster_excludes_students_of_another_dojo(session, dojo, dojo_b, org, instructor):
    make_person(org, "Mine", "Aa", dojo=dojo)
    make_person(org, "Theirs", "Bb", dojo=dojo_b)

    names = {
        entry.student.given_name for entry in session_roster(session=session, actor=instructor)
    }

    assert names == {"Mine"}


def test_roster_excludes_students_on_hold(session, dojo, org, instructor):
    make_person(org, "Paused", "Aa", dojo=dojo, status=Enrollment.Status.ON_HOLD)
    assert session_roster(session=session, actor=instructor) == []


def test_roster_shows_a_marked_visitor(session, dojo_b, org, instructor):
    visitor = make_person(org, "Guest", "Zz", dojo=dojo_b)
    mark_attendance(
        session=session, student=visitor, status=AttendanceRecord.Status.PRESENT, actor=instructor
    )

    entries = session_roster(session=session, actor=instructor)

    assert len(entries) == 1
    assert entries[0].is_visitor is True
    assert entries[0].status == AttendanceRecord.Status.VISITING


def test_roster_carries_existing_marks(session, student, instructor):
    mark_attendance(
        session=session, student=student, status=AttendanceRecord.Status.LATE, actor=instructor
    )

    entry = session_roster(session=session, actor=instructor)[0]

    assert entry.is_marked is True
    assert entry.status == AttendanceRecord.Status.LATE


# -- whole-roster save --------------------------------------------------------


def test_mark_session_applies_every_status(session, dojo, org, instructor):
    a = make_person(org, "Aa", "One", dojo=dojo)
    b = make_person(org, "Bb", "Two", dojo=dojo)

    result = mark_session(
        session=session,
        statuses={a.pk: AttendanceRecord.Status.PRESENT, b.pk: AttendanceRecord.Status.ABSENT},
        actor=instructor,
    )

    assert result == {"created": 2, "updated": 0}
    statuses = dict(
        AttendanceRecord.objects.for_actor(instructor)
        .filter(session=session)
        .values_list("student_id", "status")
    )
    assert statuses[a.pk] == AttendanceRecord.Status.PRESENT
    assert statuses[b.pk] == AttendanceRecord.Status.ABSENT


def test_mark_session_leaves_omitted_students_alone(session, dojo, org, instructor):
    """A half-finished roster must not mark everyone else absent."""
    a = make_person(org, "Aa", "One", dojo=dojo)
    make_person(org, "Bb", "Two", dojo=dojo)

    mark_session(
        session=session, statuses={a.pk: AttendanceRecord.Status.PRESENT}, actor=instructor
    )

    assert AttendanceRecord.objects.for_actor(instructor).filter(session=session).count() == 1


def test_mark_session_completes_the_session(session, student, instructor):
    assert session.status == ClassSession.Status.SCHEDULED

    mark_session(
        session=session,
        statuses={student.pk: AttendanceRecord.Status.PRESENT},
        actor=instructor,
    )

    session.refresh_from_db()
    assert session.status == ClassSession.Status.COMPLETED


def test_mark_session_does_not_uncancel_a_cancelled_session(session, student, instructor):
    session.cancel("typhoon")

    mark_session(
        session=session,
        statuses={student.pk: AttendanceRecord.Status.PRESENT},
        actor=instructor,
    )

    session.refresh_from_db()
    assert session.status == ClassSession.Status.CANCELLED


def test_mark_session_ignores_a_person_from_another_organisation(session, instructor):
    with allow_unscoped("test setup"):
        other_org = Organization.objects.create(name="Other", slug="other")
        outsider = Person.objects.create(
            organization=other_org, given_name="Foreign", family_name="Student"
        )

    result = mark_session(
        session=session,
        statuses={outsider.pk: AttendanceRecord.Status.PRESENT},
        actor=instructor,
    )

    assert result == {"created": 0, "updated": 0}
    assert AttendanceRecord.objects.for_actor(instructor).count() == 0
