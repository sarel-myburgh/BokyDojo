"""Roster and report screens — TODO 1.5.2, 1.11.1, 1.11.3, 1.11.4.

Every view gets an object-level permission test as well as a "does it render"
test. Menu-level hiding is not a control (SEC §2.2), so these check the responses
of actors who should be refused, not just of the happy path.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import AttendanceRecord
from apps.attendance.services import mark_attendance
from apps.core.models import AuditLog
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
from apps.scheduling.models import ClassSession

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


def make_org(slug="test-org", name="Test Org"):
    with allow_unscoped("test setup"):
        return Organization.objects.create(name=name, slug=slug)


def make_dojo(org, slug="dojo-a", name="Dojo A"):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(
            organization=org, name=name, slug=slug, timezone="Asia/Phnom_Penh"
        )


def make_student(org, given, dojo, *, family="Pupil"):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name=given, family_name=family)
        StudentProfile.objects.create(person=person, status=StudentProfile.Status.ACTIVE)
        Enrollment.objects.create(
            student=person,
            dojo=dojo,
            started_on=datetime.date(2026, 1, 1),
            is_primary=True,
        )
        return person


def make_staff(org, dojo, role, *, email, given="Staff"):
    """A Person + User + RoleAssignment, so the real actor pipeline is exercised."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name=given, family_name="Member")
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=role,
            scope_type=ScopeType.DOJO if dojo else ScopeType.ORG,
            dojo=dojo,
        )
        return User.objects.create_user(email=email, password=PASSWORD, person=person)


def make_session(dojo, *, hours_ago=1):
    starts = timezone.now() - datetime.timedelta(hours=hours_ago)
    with allow_unscoped("test setup"):
        return ClassSession.objects.create(
            dojo=dojo, starts_at=starts, ends_at=starts + datetime.timedelta(hours=1)
        )


@pytest.fixture
def world():
    """One org, two dojos, an instructor at A, two students at A, one class at A."""
    org = make_org()
    dojo_a = make_dojo(org)
    dojo_b = make_dojo(org, slug="dojo-b", name="Dojo B")
    return {
        "org": org,
        "dojo_a": dojo_a,
        "dojo_b": dojo_b,
        "instructor": make_staff(
            org, dojo_a, Role.INSTRUCTOR, email="sensei@example.com", given="Takeshi"
        ),
        "students": [
            make_student(org, "Bopha", dojo_a, family="Aa"),
            make_student(org, "Chan", dojo_a, family="Bb"),
        ],
        "session": make_session(dojo_a),
    }


# -- today --------------------------------------------------------------------


def test_today_lists_the_session(client, world):
    client.force_login(world["instructor"])

    response = client.get(reverse("today"))

    assert response.status_code == 200
    assert world["dojo_a"].name in response.content.decode()


def test_today_hides_another_dojos_session(client, world):
    other_session = make_session(world["dojo_b"])
    client.force_login(world["instructor"])

    response = client.get(reverse("today"))

    assert str(other_session.pk) not in response.content.decode()


def test_today_hides_another_organisations_session(client, world):
    other_org = make_org(slug="other-org", name="Other Org")
    other_dojo = make_dojo(other_org, slug="other-dojo", name="Other Dojo")
    foreign_session = make_session(other_dojo)
    client.force_login(world["instructor"])

    body = client.get(reverse("today")).content.decode()

    assert str(foreign_session.pk) not in body
    assert "Other Dojo" not in body


def test_guardian_is_refused_the_attendance_screens(client, world):
    guardian = make_staff(
        world["org"], world["dojo_a"], Role.GUARDIAN, email="parent@example.com"
    )
    client.force_login(guardian)

    assert client.get(reverse("today")).status_code == 403


# -- roster -------------------------------------------------------------------


def test_roster_lists_enrolled_students(client, world):
    client.force_login(world["instructor"])

    response = client.get(reverse("roster", args=[world["session"].pk]))

    body = response.content.decode()
    assert response.status_code == 200
    assert "Bopha" in body
    assert "Chan" in body


def test_roster_of_another_organisations_session_is_not_found(client, world):
    other_org = make_org(slug="other-org", name="Other Org")
    other_dojo = make_dojo(other_org, slug="other-dojo")
    foreign_session = make_session(other_dojo)
    client.force_login(world["instructor"])

    response = client.get(reverse("roster", args=[foreign_session.pk]))

    assert response.status_code == 404, "scoping should make it not exist, not merely forbid it"


def test_roster_of_another_dojos_session_is_not_found(client, world):
    other_session = make_session(world["dojo_b"])
    client.force_login(world["instructor"])

    assert client.get(reverse("roster", args=[other_session.pk])).status_code == 404


def test_posting_the_roster_saves_attendance(client, world):
    client.force_login(world["instructor"])
    first, second = world["students"]

    response = client.post(
        reverse("roster", args=[world["session"].pk]),
        {
            f"status_{first.pk}": AttendanceRecord.Status.PRESENT,
            f"status_{second.pk}": AttendanceRecord.Status.ABSENT,
        },
    )

    assert response.status_code == 302
    with allow_unscoped("assertion"):
        records = dict(
            AttendanceRecord.objects.unscoped("assertion")
            .filter(session=world["session"])
            .values_list("student_id", "status")
        )
    assert records[first.pk] == AttendanceRecord.Status.PRESENT
    assert records[second.pk] == AttendanceRecord.Status.ABSENT


def test_posting_an_unknown_status_is_ignored(client, world):
    client.force_login(world["instructor"])
    student = world["students"][0]

    client.post(
        reverse("roster", args=[world["session"].pk]),
        {f"status_{student.pk}": "teleported"},
    )

    with allow_unscoped("assertion"):
        assert not AttendanceRecord.objects.unscoped("assertion").exists()


def test_posting_a_student_from_another_organisation_is_ignored(client, world):
    other_org = make_org(slug="other-org", name="Other Org")
    other_dojo = make_dojo(other_org, slug="other-dojo")
    outsider = make_student(other_org, "Foreign", other_dojo)
    client.force_login(world["instructor"])

    client.post(
        reverse("roster", args=[world["session"].pk]),
        {f"status_{outsider.pk}": AttendanceRecord.Status.PRESENT},
    )

    with allow_unscoped("assertion"):
        assert not AttendanceRecord.objects.unscoped("assertion").exists()


def test_safeguarding_officer_is_refused_the_roster(client, world):
    """Attendance is outside the safeguarding remit — see permissions.ROLE_ACTIONS.

    Worth pinning: the role sees medical and protection notes, which makes it
    tempting to assume it sees everything. It does not, and the resolver is the
    place that decides, not the template.
    """
    officer = make_staff(
        world["org"], world["dojo_a"], Role.SAFEGUARDING, email="officer@example.com"
    )
    client.force_login(officer)

    assert client.get(reverse("roster", args=[world["session"].pk])).status_code == 403


def test_an_actor_refused_the_roster_cannot_post_to_it_either(client, world):
    officer = make_staff(
        world["org"], world["dojo_a"], Role.SAFEGUARDING, email="officer@example.com"
    )
    client.force_login(officer)

    response = client.post(
        reverse("roster", args=[world["session"].pk]),
        {f"status_{world['students'][0].pk}": AttendanceRecord.Status.PRESENT},
    )

    assert response.status_code == 403
    with allow_unscoped("assertion"):
        assert not AttendanceRecord.objects.unscoped("assertion").exists()


def test_assistant_instructor_may_mark(client, world):
    """The other end of the same question: assistants do record attendance."""
    assistant = make_staff(
        world["org"], world["dojo_a"], Role.ASSISTANT_INSTRUCTOR, email="assistant@example.com"
    )
    client.force_login(assistant)

    response = client.post(
        reverse("roster", args=[world["session"].pk]),
        {f"status_{world['students'][0].pk}": AttendanceRecord.Status.PRESENT},
    )

    assert response.status_code == 302
    with allow_unscoped("assertion"):
        assert AttendanceRecord.objects.unscoped("assertion").count() == 1


def test_roster_requires_authentication(client, world):
    response = client.get(reverse("roster", args=[world["session"].pk]))
    assert response.status_code == 302
    assert reverse("login") in response.url


# -- reports ------------------------------------------------------------------


@pytest.fixture
def marked_world(world):
    """The instructor's own actor, used to lay down some attendance."""
    person = world["instructor"].person
    actor = Actor(
        user_id=world["instructor"].pk,
        person_id=person.pk,
        organization_id=world["org"].pk,
        dojo_ids=frozenset({world["dojo_a"].pk}),
        roles=frozenset({(Role.INSTRUCTOR, ScopeType.DOJO, world["dojo_a"].pk)}),
    )
    mark_attendance(
        session=world["session"],
        student=world["students"][0],
        status=AttendanceRecord.Status.PRESENT,
        actor=actor,
    )
    mark_attendance(
        session=world["session"],
        student=world["students"][1],
        status=AttendanceRecord.Status.ABSENT,
        actor=actor,
    )
    return world


def test_summary_reports_the_rate(client, marked_world):
    client.force_login(marked_world["instructor"])

    response = client.get(reverse("attendance-summary"))

    assert response.status_code == 200
    assert "50%" in response.content.decode()


def test_summary_csv_export(client, marked_world):
    client.force_login(marked_world["instructor"])

    response = client.get(reverse("attendance-summary"), {"format": "csv"})

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]
    assert "Dojo" in response.content.decode()


def test_csv_export_is_audited(client, marked_world):
    """SEC §2.6 — "who took a copy of the student list" must be answerable."""
    client.force_login(marked_world["instructor"])

    client.get(reverse("attendance-summary"), {"format": "csv"})

    assert AuditLog.objects.filter(action="export", subject_type="report").exists()


def test_summary_excludes_other_dojos(client, marked_world):
    """A dojo-scoped instructor must not see dojo B's numbers."""
    session_b = make_session(marked_world["dojo_b"])
    student_b = make_student(marked_world["org"], "Elsewhere", marked_world["dojo_b"])
    with allow_unscoped("test setup"):
        AttendanceRecord.objects.create(
            session=session_b, student=student_b, status=AttendanceRecord.Status.PRESENT
        )

    client.force_login(marked_world["instructor"])
    body = client.get(reverse("attendance-summary")).content.decode()

    assert "Dojo B" not in body


def test_drop_off_lists_a_silent_student(client, world):
    client.force_login(world["instructor"])

    response = client.get(reverse("drop-off"), {"days": 7})

    body = response.content.decode()
    assert response.status_code == 200
    assert "Bopha" in body, "a student who has never attended belongs on the list"


def test_drop_off_excludes_a_recent_attender(client, marked_world):
    client.force_login(marked_world["instructor"])

    body = client.get(reverse("drop-off"), {"days": 7}).content.decode()

    assert "Bopha" not in body, "attended today, so not dropped off"
    assert "Chan" in body, "marked absent, so still silent"


def test_drop_off_excludes_other_dojos_students(client, world):
    make_student(world["org"], "Elsewhere", world["dojo_b"])
    client.force_login(world["instructor"])

    body = client.get(reverse("drop-off"), {"days": 7}).content.decode()

    assert "Elsewhere" not in body


def test_drop_off_csv_export(client, world):
    client.force_login(world["instructor"])

    response = client.get(reverse("drop-off"), {"days": 7, "format": "csv"})

    assert response["Content-Type"].startswith("text/csv")
    assert "never" in response.content.decode()


def test_guardian_is_refused_the_reports(client, world):
    guardian = make_staff(
        world["org"], world["dojo_a"], Role.GUARDIAN, email="parent@example.com"
    )
    client.force_login(guardian)

    assert client.get(reverse("attendance-summary")).status_code == 403
    assert client.get(reverse("drop-off")).status_code == 403


def test_reports_require_authentication(client):
    response = client.get(reverse("attendance-summary"))
    assert response.status_code == 302
    assert reverse("login") in response.url


# -- regressions found by clicking through the app ---------------------------


def test_today_does_not_list_a_class_twice(client, world):
    """Today's unmarked class belongs in "today", not also in the catch-up nag."""
    client.force_login(world["instructor"])

    body = client.get(reverse("today")).content.decode()

    assert body.count(str(world["session"].pk)) == 1


def test_pages_contain_no_leaked_template_comments(client, world):
    """Django's {# #} is single-line only; a multi-line one renders as text."""
    client.force_login(world["instructor"])

    for url in (
        reverse("today"),
        reverse("roster", args=[world["session"].pk]),
        reverse("attendance-summary"),
        reverse("drop-off"),
    ):
        body = client.get(url).content.decode()
        assert "{#" not in body, f"{url} leaked a template comment"
        assert "Mobile-first" not in body, f"{url} leaked a template comment"
