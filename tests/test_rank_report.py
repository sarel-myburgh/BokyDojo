"""Active students by rank — TODO 1.11.2, CSV export 1.11.4."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    User,
)
from apps.ranks.models import Rank, StudentStyleTrack
from apps.ranks.promotions import promote_student
from apps.ranks.seeding import create_shotokan_ladders

pytestmark = pytest.mark.django_db
PASSWORD = "correct-horse-battery"


def _staff(org, role, email, dojo=None):
    person = Person.objects.create(
        organization=org,
        given_name=role.title(),
        family_name="ReportStaff",
    )
    RoleAssignment.objects.create(
        organization=org,
        person=person,
        role=role,
        scope_type=ScopeType.DOJO if dojo else ScopeType.ORG,
        dojo=dojo,
    )
    return User.objects.create_user(email=email, password=PASSWORD, person=person)


def _student(org, dojo, given_name, ladder, *, status=StudentProfile.Status.ACTIVE, track=True):
    person = Person.objects.create(
        organization=org,
        given_name=given_name,
        family_name="Student",
    )
    profile = StudentProfile.objects.create(person=person, home_dojo=dojo, status=status)
    if track:
        StudentStyleTrack.objects.create(
            student=person,
            style=ladder.style,
            ladder=ladder,
            started_on=timezone.localdate() - datetime.timedelta(days=365),
        )
    return profile


@pytest.fixture
def world():
    with allow_unscoped("rank report test setup"):
        org = Organization.objects.create(name="Report Org", slug="report-org")
        dojo_a = Dojo.objects.create(organization=org, name="Dojo A", slug="report-a")
        dojo_b = Dojo.objects.create(organization=org, name="Dojo B", slug="report-b")
        adult, junior = create_shotokan_ladders(org)

        ungraded = _student(org, dojo_a, "Bopha", adult)
        graded = _student(org, dojo_a, "Chan", adult)
        held = _student(org, dojo_a, "Dara", adult, status=StudentProfile.Status.ON_HOLD)
        elsewhere = _student(org, dojo_b, "Elsewhere", adult)

        ranks = list(Rank.objects.filter(ladder=adult).order_by("order"))
        org_admin = _staff(org, Role.ORG_ADMIN, "org-admin@report.test")
        instructor = _staff(org, Role.INSTRUCTOR, "instructor@report.test", dojo_a)

    # Give one student a real rank so the report has something to group by.
    with allow_unscoped("rank report test setup"):
        track = StudentStyleTrack.objects.get(student=graded.person)
    promote_student(
        profile=graded,
        track=track,
        rank=ranks[1],
        awarded_on=timezone.localdate(),
        certificate_number="",
        notes="",
        actor=actor_for_user(org_admin),
    )
    return locals()


def test_report_groups_a_graded_student_under_their_rank(client, world):
    client.force_login(world["instructor"])

    response = client.get(reverse("active-by-rank"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "Chan" in body
    assert world["ranks"][1].name in body


def test_a_student_with_no_award_is_listed_as_ungraded(client, world):
    client.force_login(world["instructor"])

    body = client.get(reverse("active-by-rank")).content.decode()

    assert "Bopha" in body, "an untested student is still an active student"
    assert "Ungraded" in body


def test_non_active_students_are_excluded(client, world):
    """The report is "active students by rank" — a held student is not one."""
    client.force_login(world["instructor"])

    body = client.get(reverse("active-by-rank")).content.decode()

    assert "Dara" not in body


def test_report_excludes_other_dojos_students(client, world):
    """A dojo-scoped instructor must not see dojo B's roster."""
    client.force_login(world["instructor"])

    body = client.get(reverse("active-by-rank")).content.decode()

    assert "Elsewhere" not in body


def test_org_admin_sees_every_dojo(client, world):
    client.force_login(world["org_admin"])

    body = client.get(reverse("active-by-rank")).content.decode()

    assert "Elsewhere" in body
    assert "Chan" in body


def test_groups_are_ordered_by_the_ladder_not_the_rank_name(client, world):
    """Grades count *down* towards seniority, so alphabetical order is wrong.

    The mon ladder is where a name sort visibly breaks: "10th Mon" is the most
    junior grade of all, but it sorts before "9th Mon" as a string. The report
    must follow ``Rank.order`` — seniors first, ungraded last.
    """
    with allow_unscoped("rank report ordering setup"):
        junior_ladder = world["junior"]
        mon = list(Rank.objects.filter(ladder=junior_ladder).order_by("order"))
        most_junior, more_senior = mon[0], mon[1]  # 10th Mon, then 9th Mon
        assert most_junior.name == "10th Mon" and more_senior.name == "9th Mon"

        upper = _student(world["org"], world["dojo_a"], "Sen", junior_ladder)
        lower = _student(world["org"], world["dojo_a"], "Jun", junior_ladder)
        upper_track = StudentStyleTrack.objects.get(student=upper.person)
        lower_track = StudentStyleTrack.objects.get(student=lower.person)

    actor = actor_for_user(world["org_admin"])
    promote_student(
        profile=upper,
        track=upper_track,
        rank=more_senior,
        awarded_on=timezone.localdate(),
        actor=actor,
    )
    promote_student(
        profile=lower,
        track=lower_track,
        rank=most_junior,
        awarded_on=timezone.localdate(),
        actor=actor,
    )

    client.force_login(world["org_admin"])
    body = client.get(reverse("active-by-rank")).content.decode()

    assert body.index(more_senior.name) < body.index(most_junior.name), (
        "9th Mon outranks 10th Mon and must be listed first; an alphabetical sort inverts them"
    )


def test_csv_export(client, world):
    client.force_login(world["instructor"])

    response = client.get(reverse("active-by-rank"), {"format": "csv"})

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]
    body = response.content.decode()
    assert "Dojo,Student,Style,Ladder,Rank" in body
    assert "Chan" in body
    assert "Elsewhere" not in body, "the export must honour the same scope as the page"


def test_csv_export_is_audited(client, world):
    """SEC §2.6 — "who took a copy of the student list" must be answerable."""
    client.force_login(world["instructor"])

    client.get(reverse("active-by-rank"), {"format": "csv"})

    assert AuditLog.objects.filter(
        action="export", subject_id="active-students-by-rank.csv"
    ).exists()


def test_guardian_is_refused_the_report(client, world):
    guardian = _staff(world["org"], Role.GUARDIAN, "parent@report.test", world["dojo_a"])
    client.force_login(guardian)

    assert client.get(reverse("active-by-rank")).status_code == 403


def test_report_requires_authentication(client):
    response = client.get(reverse("active-by-rank"))

    assert response.status_code == 302
    assert reverse("login") in response.url


def test_page_leaks_no_template_comments(client, world):
    """Django's {# #} is single-line only; a multi-line one renders as text."""
    client.force_login(world["instructor"])

    body = client.get(reverse("active-by-rank")).content.decode()

    assert "{#" not in body
