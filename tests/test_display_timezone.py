"""Times are rendered in the dojo's timezone — plan §4.5.

Caught by looking at the Today screen: an 18:30 class in Phnom Penh was rendered
as 11:30, which is the same instant and no use whatsoever to the instructor
holding the phone. Storage was right; nothing was activating a timezone for
rendering.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.scoping import Actor, allow_unscoped
from apps.core.timezones import actor_timezone
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
PHNOM_PENH = ZoneInfo("Asia/Phnom_Penh")  # UTC+7, no DST


@pytest.fixture
def world():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
            default_timezone="Asia/Phnom_Penh",
        )
        dojo = Dojo.objects.create(
            organization=org, name="PP", slug="pp", timezone="Asia/Phnom_Penh"
        )
        staff = Person.objects.create(organization=org, given_name="Head", family_name="Sensei")
        RoleAssignment.objects.create(
            organization=org,
            person=staff,
            role=Role.DOJO_ADMIN,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        user = User.objects.create_user(
            email="sensei@example.com", password=PASSWORD, person=staff
        )
        student = Person.objects.create(organization=org, given_name="Sokha", family_name="Chhorn")
        StudentProfile.objects.create(person=student, status=StudentProfile.Status.ACTIVE)
        Enrollment.objects.create(
            student=student, dojo=dojo, started_on=datetime.date(2026, 1, 1), is_primary=True
        )

    # An 18:30 class in Phnom Penh today, whatever "today" is where the test runs.
    local_today = timezone.now().astimezone(PHNOM_PENH).date()
    starts = datetime.datetime.combine(local_today, datetime.time(18, 30), tzinfo=PHNOM_PENH)
    with allow_unscoped("test setup"):
        session = ClassSession.objects.create(
            dojo=dojo, starts_at=starts, ends_at=starts + datetime.timedelta(minutes=90)
        )

    return {"org": org, "dojo": dojo, "user": user, "session": session, "student": student}


def test_today_shows_the_local_class_time(client, world):
    client.force_login(world["user"])

    body = client.get(reverse("today")).content.decode()

    assert "18:30" in body
    assert "11:30" not in body, "11:30 is the UTC instant, not the time on the wall"


def test_roster_header_shows_the_local_class_time(client, world):
    client.force_login(world["user"])

    body = client.get(reverse("roster", args=[world["session"].pk])).content.decode()

    assert "18:30" in body
    assert "11:30" not in body


def test_a_late_class_is_not_pushed_onto_the_wrong_day(client, world):
    """23:00 in Phnom Penh is 16:00 UTC the same day; 01:00 would be the trap.

    A class after 07:00 UTC belongs to a different UTC date than local date, so
    a Today screen built in UTC drops it off the list entirely.
    """
    local_today = timezone.now().astimezone(PHNOM_PENH).date()
    starts = datetime.datetime.combine(local_today, datetime.time(6, 0), tzinfo=PHNOM_PENH)
    with allow_unscoped("test setup"):
        early = ClassSession.objects.create(
            dojo=world["dojo"], starts_at=starts, ends_at=starts + datetime.timedelta(hours=1)
        )

    client.force_login(world["user"])
    body = client.get(reverse("today")).content.decode()

    assert str(early.pk) in body, "an early-morning local class is still today"


def test_actor_timezone_prefers_the_single_dojo(world):
    actor = Actor(
        user_id=world["user"].pk,
        person_id=world["user"].person_id,
        organization_id=world["org"].pk,
        dojo_ids=frozenset({world["dojo"].pk}),
    )
    assert actor_timezone(actor) == "Asia/Phnom_Penh"


def test_actor_timezone_falls_back_to_the_organisation(world):
    """An org-wide actor has no single dojo, so the org default is used."""
    actor = Actor(
        user_id=world["user"].pk,
        person_id=world["user"].person_id,
        organization_id=world["org"].pk,
        dojo_ids=None,
    )
    assert actor_timezone(actor) == "Asia/Phnom_Penh"


def test_anonymous_actor_has_no_timezone():
    assert actor_timezone(Actor(user_id=None, person_id=None, organization_id=None)) is None


def test_a_broken_timezone_does_not_break_the_page(client, world):
    """A bad timezone string is a data problem, not a reason to 500 every page."""
    with allow_unscoped("test setup"):
        world["dojo"].timezone = "Mars/Olympus_Mons"
        world["dojo"].save(update_fields=["timezone"])

    client.force_login(world["user"])

    assert client.get(reverse("today")).status_code == 200
