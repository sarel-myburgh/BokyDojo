"""Hand-around check-in — TODO 1.7, decision D1 (2026-08-17).

The decided scenario is the instructor's own phone or tablet, carried, with
students queuing to tap their face while the instructor watches. Not a tablet
bolted to the wall — so no device token, no PINs.

⚠ The load-bearing test here is ``test_a_locked_session_cannot_reach_anything_else``.
The whole feature rests on it: the instructor is signed in with every right they
hold, and the device is in a child's hands. If the lock leaks, a nine-year-old is
two taps from a medical alert.
"""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.attendance import kiosk
from apps.attendance.models import AttendanceRecord
from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
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
        students = []
        for name in ("Bopha", "Sokha"):
            person = Person.objects.create(organization=org, given_name=name, family_name="Chan")
            StudentProfile.objects.create(
                person=person, status=StudentProfile.Status.ACTIVE, home_dojo=dojo
            )
            Enrollment.objects.create(
                student=person,
                dojo=dojo,
                started_on=datetime.date(2024, 1, 1),
                is_primary=True,
            )
            students.append(person)
        starts = timezone.now() - datetime.timedelta(minutes=10)
        session = ClassSession.objects.create(
            dojo=dojo, starts_at=starts, ends_at=starts + datetime.timedelta(hours=1)
        )
    return {
        "org": org,
        "dojo": dojo,
        "user": user,
        "students": students,
        "session": session,
    }


def open_kiosk(client, session):
    return client.post(reverse("kiosk", args=[session.pk]))


# -- the grid -----------------------------------------------------------------


def test_the_grid_lists_the_class(client, world):
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    body = client.get(reverse("kiosk", args=[world["session"].pk])).content.decode()

    assert "Bopha" in body
    assert "Sokha" in body


def test_the_grid_shows_no_surname_or_other_pii(client, world):
    """⚠ A queue of children and the parents behind them can all read this."""
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    body = client.get(reverse("kiosk", args=[world["session"].pk])).content.decode()

    assert "Chan" not in body, "family names must not appear on the check-in grid"


def test_the_grid_has_no_navigation_out_of_it(client, world):
    """⚠ It extends base.html, not the signed-in shell. The nav bar would be a
    route to the student list from a phone in a student's hands."""
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    body = client.get(reverse("kiosk", args=[world["session"].pk])).content.decode()

    assert reverse("student-list") not in body
    assert reverse("today") not in body


def test_a_tap_marks_the_student_present(client, world):
    client.force_login(world["user"])
    open_kiosk(client, world["session"])
    student = world["students"][0]

    response = client.post(
        reverse("kiosk-mark", args=[world["session"].pk]),
        {"student_id": str(student.pk), "client_generated_id": "tap-1"},
    )

    assert response.status_code == 200
    assert response.json()["checked_in"] is True
    with allow_unscoped("test read"):
        record = AttendanceRecord.objects.get(student=student)
    assert record.status == AttendanceRecord.Status.PRESENT


def test_a_repeated_tap_is_idempotent(client, world):
    """The same client id replays through mark_attendance rather than double-writing."""
    client.force_login(world["user"])
    open_kiosk(client, world["session"])
    student = world["students"][0]
    payload = {"student_id": str(student.pk), "client_generated_id": "tap-1"}

    client.post(reverse("kiosk-mark", args=[world["session"].pk]), payload)
    client.post(reverse("kiosk-mark", args=[world["session"].pk]), payload)

    with allow_unscoped("test read"):
        assert AttendanceRecord.objects.filter(student=student).count() == 1


def test_marking_somebody_not_on_this_roster_is_refused(client, world):
    """⚠ The posted id is client data. Without the roster check this screen is a
    way to mark anyone in the organisation."""
    with allow_unscoped("test setup"):
        other_dojo = Dojo.objects.create(
            organization=world["org"], name="B", slug="dojo-b", timezone="UTC"
        )
        outsider = Person.objects.create(
            organization=world["org"], given_name="Elsewhere", family_name="Person"
        )
        StudentProfile.objects.create(person=outsider, status=StudentProfile.Status.ACTIVE)
        Enrollment.objects.create(
            student=outsider, dojo=other_dojo, started_on=datetime.date(2024, 1, 1)
        )
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    response = client.post(
        reverse("kiosk-mark", args=[world["session"].pk]),
        {"student_id": str(outsider.pk)},
    )

    assert response.status_code == 404
    with allow_unscoped("test read"):
        assert not AttendanceRecord.objects.filter(student=outsider).exists()


def test_a_malformed_student_id_is_a_400_not_a_500(client, world):
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    response = client.post(
        reverse("kiosk-mark", args=[world["session"].pk]), {"student_id": "../../etc"}
    )

    assert response.status_code == 400


# -- QR cards -----------------------------------------------------------------


def test_printable_qr_cards_show_first_names_only(client, world):
    client.force_login(world["user"])

    response = client.get(reverse("student-qr-cards"))
    body = response.content.decode()

    assert response.status_code == 200
    assert body.count("<svg") == 2
    assert "Bopha" in body
    assert "Sokha" in body
    assert "Bopha Chan" not in body
    assert "Sokha Chan" not in body


def test_printing_qr_cards_is_audited(client, world):
    client.force_login(world["user"])

    response = client.get(reverse("student-qr-cards"))

    assert response.status_code == 200
    entry = AuditLog.objects.get(
        action="export",
        subject_type="student_qr_cards",
        subject_id=str(world["org"].pk),
    )
    assert entry.actor_person_id == world["user"].person_id
    assert entry.note == "2 card(s)"


def test_printable_qr_cards_require_attendance_permission(client, world):
    with allow_unscoped("test setup"):
        officer = Person.objects.create(
            organization=world["org"], given_name="Safe", family_name="Guard"
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=officer,
            role=Role.SAFEGUARDING,
            scope_type=ScopeType.DOJO,
            dojo=world["dojo"],
        )
        user = User.objects.create_user(
            email="safeguarding@example.com", password=PASSWORD, person=officer
        )
    client.force_login(user)

    assert client.get(reverse("student-qr-cards")).status_code == 403


def test_scanning_a_qr_card_confirms_the_student(client, world):
    client.force_login(world["user"])
    open_kiosk(client, world["session"])
    student = world["students"][0]

    response = client.get(reverse("kiosk-scan", args=[student.pk]))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Bopha" in body
    assert "Chan" not in body
    assert reverse("kiosk-scan-confirm", args=[student.pk]) in body
    assert reverse("student-list") not in body
    assert reverse("today") not in body

    response = client.post(reverse("kiosk-scan-confirm", args=[student.pk]))

    assert response.status_code == 302
    assert response["Location"] == reverse("kiosk", args=[world["session"].pk])
    with allow_unscoped("test read"):
        record = AttendanceRecord.objects.get(session=world["session"], student=student)
    assert record.status == AttendanceRecord.Status.PRESENT
    assert record.method == AttendanceRecord.Method.KIOSK_QR


def test_qr_scan_requires_the_active_kiosk_lock(client, world):
    client.force_login(world["user"])

    response = client.get(reverse("kiosk-scan", args=[world["students"][0].pk]))

    assert response.status_code == 404


def test_qr_pages_do_not_leak_template_comments(client, world):
    client.force_login(world["user"])

    card_body = client.get(reverse("student-qr-cards")).content.decode()
    open_kiosk(client, world["session"])
    scan_body = client.get(reverse("kiosk-scan", args=[world["students"][0].pk])).content.decode()

    for body in (card_body, scan_body):
        assert "{#" not in body
        assert "Mobile-first" not in body


# -- the lock -----------------------------------------------------------------


def test_opening_check_in_locks_the_session(client, world):
    client.force_login(world["user"])

    open_kiosk(client, world["session"])

    assert client.session[kiosk.LOCK_KEY] == str(world["session"].pk)


@pytest.mark.parametrize(
    "route,args",
    [
        ("student-list", ()),
        ("today", ()),
        ("attendance-summary", ()),
        ("calendar", ()),
        ("import-wizard", ()),
        ("mfa-setup", ()),
    ],
)
def test_a_locked_session_cannot_reach_anything_else(client, world, route, args):
    """⚠ The test the whole feature rests on.

    The instructor is signed in and holds every right this blocks; the lock is
    about *who is holding the device*, which no permission check models. If it
    leaks, a child with the phone is two taps from a medical alert.
    """
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    response = client.get(reverse(route, args=args))

    assert response.status_code == 302
    assert response["Location"] == reverse("kiosk", args=[world["session"].pk])


def test_the_lock_does_not_block_the_grid_or_its_marks(client, world):
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    assert client.get(reverse("kiosk", args=[world["session"].pk])).status_code == 200
    assert (
        client.post(
            reverse("kiosk-mark", args=[world["session"].pk]),
            {"student_id": str(world["students"][0].pk)},
        ).status_code
        == 200
    )


def test_the_lock_leaves_other_sessions_alone(client, world):
    """It is a property of one signed-in session, not of the deployment."""
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    other = Client()
    other.force_login(world["user"])

    assert other.get(reverse("today")).status_code == 200


def test_leaving_requires_the_password(client, world):
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    client.post(reverse("kiosk-exit", args=[world["session"].pk]), {"password": "wrong"})

    assert kiosk.LOCK_KEY in client.session
    assert client.get(reverse("today")).status_code == 302


def test_the_right_password_ends_check_in(client, world):
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    response = client.post(
        reverse("kiosk-exit", args=[world["session"].pk]), {"password": PASSWORD}
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("roster", args=[world["session"].pk])
    assert kiosk.LOCK_KEY not in client.session
    assert client.get(reverse("today")).status_code == 200


def test_password_guessing_is_throttled(client, world):
    """An unattended phone showing a password box is exactly where somebody sits
    and guesses, so it uses the same policy as signing in."""
    client.force_login(world["user"])
    open_kiosk(client, world["session"])

    for _ in range(8):
        client.post(reverse("kiosk-exit", args=[world["session"].pk]), {"password": "wrong"})

    response = client.post(
        reverse("kiosk-exit", args=[world["session"].pk]), {"password": PASSWORD}
    )

    # Still locked: the correct password is refused while the lockout stands.
    assert kiosk.LOCK_KEY in client.session
    assert response.status_code == 302


def test_check_in_cannot_be_opened_by_a_get(client, world):
    """⚠ A GET would let a bookmark, a prefetch or a stray link lock the device."""
    client.force_login(world["user"])

    client.get(reverse("kiosk", args=[world["session"].pk]))

    assert kiosk.LOCK_KEY not in client.session


def test_another_tenants_session_is_a_404(client, world):
    other_org = Organization.objects.create(name="Other", slug="other-org")
    with allow_unscoped("test setup"):
        other_dojo = Dojo.objects.create(
            organization=other_org, name="Other", slug="other-dojo", timezone="UTC"
        )
        starts = timezone.now()
        foreign = ClassSession.objects.create(
            dojo=other_dojo, starts_at=starts, ends_at=starts + datetime.timedelta(hours=1)
        )
    client.force_login(world["user"])

    assert client.post(reverse("kiosk", args=[foreign.pk])).status_code == 404


def test_anonymous_is_redirected_to_login(client, world):
    response = client.get(reverse("kiosk", args=[world["session"].pk]))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]
