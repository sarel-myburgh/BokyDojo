"""Building a dojo's weekly timetable from its settings page — plan §1.4."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.relations import set_scoped_m2m
from apps.core.scoping import allow_unscoped
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)
from apps.ranks.models import Style
from apps.scheduling.models import ClassSession, ClassTemplate
from apps.scheduling.schedule_forms import days_from_rrule, rrule_from_days

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"  # pragma: allowlist secret


@pytest.fixture
def world():
    with allow_unscoped("schedule test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        dojo = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )
        style = Style.objects.create(organization=org, name="Goju Ryu", is_ranked=True)
        set_scoped_m2m(dojo, "styles", [style], organization_id=org.pk)
        boss = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org, person=boss, role=Role.ORG_ADMIN, scope_type=ScopeType.ORG
        )
        boss_user = User.objects.create_user("ops@example.com", PASSWORD, person=boss)

        teacher = Person.objects.create(organization=org, given_name="Mei", family_name="Kato")
        RoleAssignment.objects.create(
            organization=org,
            person=teacher,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        teacher_user = User.objects.create_user("mei@example.com", PASSWORD, person=teacher)
    return {
        "org": org,
        "dojo": dojo,
        "style": style,
        "boss_user": boss_user,
        "teacher_user": teacher_user,
    }


def class_payload(**overrides):
    today = timezone.localdate()
    payload = {
        "name": "Kids Beginners",
        "style": "",
        "days": ["MO", "WE"],
        "start_time": "17:30",
        "duration_minutes": 60,
        "room": "",
        "capacity": 0,
        "age_min": 0,
        "age_max": 0,
        "active_from": today.isoformat(),
        "active_to": "",
    }
    payload.update(overrides)
    return payload


# -- nobody types an rrule ----------------------------------------------------


def test_days_become_a_recurrence_rule(world):
    """⚠ ClassTemplate stores RFC 5545. That is right to store and wrong to ask
    a dojo owner for, so the form composes it from checkboxes."""
    assert rrule_from_days(["WE", "MO"]) == "FREQ=WEEKLY;BYDAY=MO,WE"


def test_days_come_back_out_for_editing(world):
    assert days_from_rrule("FREQ=WEEKLY;BYDAY=MO,WE,FR") == ["MO", "WE", "FR"]


def test_an_unfamiliar_rule_does_not_make_a_class_uneditable(world):
    """⚠ Templates can arrive from the importer or a fixture with rules this
    form would never produce. Returning nothing beats raising and leaving a row
    nobody can open."""
    assert days_from_rrule("FREQ=MONTHLY;BYMONTHDAY=1") == []
    assert days_from_rrule("") == []


def test_the_rrule_never_appears_on_screen(client, world):
    client.force_login(world["boss_user"])

    body = client.get(reverse("class-template-create", args=[world["dojo"].pk])).content.decode()

    assert "FREQ=" not in body
    assert "BYDAY" not in body
    assert "Monday" in body


# -- adding a class -----------------------------------------------------------


def test_an_admin_can_add_a_class_to_the_timetable(client, world):
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("class-template-create", args=[world["dojo"].pk]), class_payload()
    )

    assert response.status_code == 302
    with allow_unscoped("test"):
        template = ClassTemplate.objects.get(dojo=world["dojo"])
    assert template.rrule == "FREQ=WEEKLY;BYDAY=MO,WE"
    assert template.name == "Kids Beginners"


def test_adding_a_class_fills_the_calendar(client, world):
    """⚠ Otherwise the timetable exists and the calendar is empty, and nothing
    on screen explains why."""
    client.force_login(world["boss_user"])

    client.post(reverse("class-template-create", args=[world["dojo"].pk]), class_payload())

    with allow_unscoped("test"):
        sessions = ClassSession.objects.filter(dojo=world["dojo"]).count()
    assert sessions > 0, "no classes were written into the calendar"


def test_the_timetable_shows_on_the_dojo_page(client, world):
    client.force_login(world["boss_user"])
    client.post(reverse("class-template-create", args=[world["dojo"].pk]), class_payload())

    body = client.get(reverse("dojo-edit", args=[world["dojo"].pk])).content.decode()

    assert "Kids Beginners" in body
    assert "Monday" in body
    assert "Wednesday" in body


def test_only_styles_this_dojo_teaches_are_offered(client, world):
    """⚠ A boxing class on a karate-only timetable would produce enrolment
    tracks that never match anybody attending it."""
    with allow_unscoped("test"):
        elsewhere = Style.objects.create(organization=world["org"], name="Boxing", is_ranked=False)
    client.force_login(world["boss_user"])

    body = client.get(reverse("class-template-create", args=[world["dojo"].pk])).content.decode()

    assert "Goju Ryu" in body
    assert str(elsewhere.pk) not in body


def test_an_instructor_cannot_change_the_timetable(client, world):
    client.force_login(world["teacher_user"])

    response = client.post(
        reverse("class-template-create", args=[world["dojo"].pk]), class_payload()
    )

    assert response.status_code == 403


# -- changing and removing ----------------------------------------------------


def test_removing_a_class_keeps_what_already_happened(client, world):
    """⚠ Ended, never deleted. Past sessions carry attendance, and "was this
    child in class that evening" is a safeguarding question."""
    client.force_login(world["boss_user"])
    client.post(reverse("class-template-create", args=[world["dojo"].pk]), class_payload())
    with allow_unscoped("test"):
        template = ClassTemplate.objects.get(dojo=world["dojo"])

    client.post(reverse("class-template-end", args=[world["dojo"].pk, template.pk]))

    with allow_unscoped("test"):
        template.refresh_from_db()
        assert ClassTemplate.objects.filter(pk=template.pk).exists()
    assert template.active_to == timezone.localdate()


def test_a_session_with_attendance_survives_a_timetable_change(client, world):
    """⚠ Rewriting the timetable is not licence to erase a marked register."""
    from apps.attendance.models import AttendanceRecord

    client.force_login(world["boss_user"])
    client.post(reverse("class-template-create", args=[world["dojo"].pk]), class_payload())

    with allow_unscoped("test"):
        template = ClassTemplate.objects.get(dojo=world["dojo"])
        future = (
            ClassSession.objects.filter(template=template, starts_at__gt=timezone.now())
            .order_by("starts_at")
            .first()
        )
        student = Person.objects.create(
            organization=world["org"], given_name="Mika", family_name="Student"
        )
        AttendanceRecord.objects.create(
            session=future,
            student=student,
            status=AttendanceRecord.Status.PRESENT,
            method=AttendanceRecord.Method.ROSTER,
        )
        marked_id = future.pk

    client.post(
        reverse("class-template-edit", args=[world["dojo"].pk, template.pk]),
        class_payload(days=["TU"], start_time="18:00"),
    )

    with allow_unscoped("test"):
        assert ClassSession.objects.filter(pk=marked_id).exists(), (
            "a session with attendance marked against it was deleted"
        )


def test_changing_the_days_moves_the_untouched_classes(client, world):
    client.force_login(world["boss_user"])
    client.post(reverse("class-template-create", args=[world["dojo"].pk]), class_payload())
    with allow_unscoped("test"):
        template = ClassTemplate.objects.get(dojo=world["dojo"])

    client.post(
        reverse("class-template-edit", args=[world["dojo"].pk, template.pk]),
        class_payload(days=["FR"]),
    )

    with allow_unscoped("test"):
        template.refresh_from_db()
        weekdays = {
            s.starts_at.astimezone(datetime.UTC).weekday()
            for s in ClassSession.objects.filter(template=template, starts_at__gt=timezone.now())
        }
    assert template.rrule == "FREQ=WEEKLY;BYDAY=FR"
    assert weekdays, "the new pattern produced no classes"


def test_a_timetable_from_another_dojo_cannot_be_edited(client, world):
    with allow_unscoped("test"):
        other = Dojo.objects.create(
            organization=world["org"], name="Toul Kork", slug="tk", timezone="Asia/Phnom_Penh"
        )
        template = ClassTemplate.objects.create(
            dojo=other,
            name="Theirs",
            rrule="FREQ=WEEKLY;BYDAY=MO",
            start_time=datetime.time(18, 0),
            duration_minutes=60,
            active_from=timezone.localdate(),
        )
    client.force_login(world["boss_user"])

    response = client.get(reverse("class-template-edit", args=[world["dojo"].pk, template.pk]))

    assert response.status_code == 404


def test_the_end_date_cannot_precede_the_start(client, world):
    client.force_login(world["boss_user"])
    today = timezone.localdate()

    response = client.post(
        reverse("class-template-create", args=[world["dojo"].pk]),
        class_payload(
            active_from=today.isoformat(),
            active_to=(today - datetime.timedelta(days=1)).isoformat(),
        ),
    )

    assert response.status_code == 200
    with allow_unscoped("test"):
        assert not ClassTemplate.objects.filter(dojo=world["dojo"]).exists()
