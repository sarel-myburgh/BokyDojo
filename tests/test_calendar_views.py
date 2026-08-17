"""Week and month timetables — TODO 1.4.9.

Two layers, tested differently on purpose.

The date arithmetic and the bucketing are tested against ``build_page`` directly,
because the question "which cell is this class in" has an exact answer and
grepping rendered HTML for it does not have one. The permission, scoping and
filter behaviour is tested through the client, because that is where it has to
hold.

⚠ The bucketing tests were each checked against a broken variant before being
trusted — see the module docstring of ``apps/scheduling/calendars.py``. A
calendar test written with the viewer and the dojo in the same timezone passes
against every implementation, correct or not, which is precisely the bug.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    Dojo,
    InstructorAssignment,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)
from apps.scheduling import calendars
from apps.scheduling.models import ClassSession, ClosurePeriod, SessionInstructor

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"

#: A Wednesday, so a week runs Mon 8th – Sun 14th June 2026.
WEDNESDAY = datetime.date(2026, 6, 10)


def make_org(slug="test-org", name="Test Org", *, default_timezone="Asia/Phnom_Penh"):
    with allow_unscoped("test setup"):
        return Organization.objects.create(name=name, slug=slug, default_timezone=default_timezone)


def make_dojo(org, slug="dojo-a", name="Dojo A", *, tz="Asia/Phnom_Penh"):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name=name, slug=slug, timezone=tz)


def make_staff(org, dojo, role, *, email, given="Staff", family="Member"):
    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name=given, family_name=family)
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=role,
            scope_type=ScopeType.DOJO if dojo else ScopeType.ORG,
            dojo=dojo,
        )
        user = User.objects.create_user(email=email, password=PASSWORD, person=person)
        return user


def make_instructor(org, dojo, *, email, given="Takeshi", family="Sensei"):
    user = make_staff(org, dojo, Role.INSTRUCTOR, email=email, given=given, family=family)
    with allow_unscoped("test setup"):
        InstructorAssignment.objects.create(
            dojo=dojo, person=user.person, started_on=datetime.date(2026, 1, 1)
        )
    return user


def make_session(dojo, when: datetime.datetime, *, minutes=60, status=None):
    """``when`` is UTC — the timezone the column lands in is the point of the test."""
    with allow_unscoped("test setup"):
        return ClassSession.objects.create(
            dojo=dojo,
            starts_at=when,
            ends_at=when + datetime.timedelta(minutes=minutes),
            status=status or ClassSession.Status.SCHEDULED,
        )


def actor_for(user, *, dojo_ids=None):
    person = user.person
    with allow_unscoped("test setup"):
        assignments = list(RoleAssignment.objects.filter(person=person))
    return Actor(
        user_id=user.pk,
        person_id=person.pk,
        organization_id=person.organization_id,
        dojo_ids=dojo_ids,
        roles=frozenset((a.role, a.scope_type, a.dojo_id) for a in assignments),
    )


@pytest.fixture
def world():
    """One org, two dojos, a dojo-A instructor, and an org admin who sees both."""
    org = make_org()
    dojo_a = make_dojo(org)
    dojo_b = make_dojo(org, slug="dojo-b", name="Dojo B")
    return {
        "org": org,
        "dojo_a": dojo_a,
        "dojo_b": dojo_b,
        "instructor": make_instructor(org, dojo_a, email="sensei@example.com"),
        "admin": make_staff(org, None, Role.ORG_ADMIN, email="boss@example.com", given="Sokha"),
    }


# -- date arithmetic ----------------------------------------------------------


def test_week_runs_monday_to_sunday():
    first, last = calendars.week_bounds(WEDNESDAY)

    assert first == datetime.date(2026, 6, 8)
    assert last == datetime.date(2026, 6, 14)
    assert first.weekday() == 0


def test_month_bounds_cover_the_whole_month():
    first, last = calendars.month_bounds(datetime.date(2026, 2, 17))

    assert first == datetime.date(2026, 2, 1)
    assert last == datetime.date(2026, 2, 28)


def test_month_grid_is_padded_to_whole_weeks():
    first, last = calendars.grid_bounds(calendars.MONTH, WEDNESDAY)

    assert first.weekday() == 0
    assert last.weekday() == 6
    assert first <= datetime.date(2026, 6, 1)
    assert last >= datetime.date(2026, 6, 30)
    assert ((last - first).days + 1) % 7 == 0


def test_stepping_a_month_from_the_31st_does_not_skip_february():
    """⚠ The naive +30 days lands in March. This is why ``step`` normalises."""
    assert calendars.step(calendars.MONTH, datetime.date(2026, 1, 31), +1) == datetime.date(
        2026, 2, 1
    )
    assert calendars.step(calendars.MONTH, datetime.date(2026, 3, 31), -1) == datetime.date(
        2026, 2, 1
    )


def test_stepping_a_week_moves_seven_days():
    assert calendars.step(calendars.WEEK, WEDNESDAY, +1) == datetime.date(2026, 6, 17)
    assert calendars.step(calendars.WEEK, WEDNESDAY, -1) == datetime.date(2026, 6, 3)


def test_view_defaults_to_week_and_rejects_junk():
    assert calendars.normalise_view(None) == calendars.WEEK
    assert calendars.normalise_view("") == calendars.WEEK
    assert calendars.normalise_view("year") == calendars.WEEK
    assert calendars.normalise_view("month") == calendars.MONTH


def test_a_bad_date_lands_on_the_fallback_rather_than_erroring():
    """A position, not a resource — contrast the dojo filter, which 404s."""
    assert calendars.parse_anchor("not-a-date", fallback=WEDNESDAY) == WEDNESDAY
    assert calendars.parse_anchor(None, fallback=WEDNESDAY) == WEDNESDAY
    assert calendars.parse_anchor("2026-06-17", fallback=WEDNESDAY) == datetime.date(2026, 6, 17)


# -- bucketing ----------------------------------------------------------------


def _day(page, date):
    return next(day for day in page.days if day.date == date)


def test_a_class_lands_on_its_dojos_local_date_not_the_viewers(world):
    """⚠ The test this whole module exists for.

    23:00 UTC on Tuesday is 06:00 Wednesday in Phnom Penh. The viewer is an org
    admin whose organisation default is UTC, so the two disagree — which is the
    only configuration in which a wrong implementation is visible at all.
    """
    org = make_org(slug="utc-org", name="UTC Org", default_timezone="UTC")
    dojo = make_dojo(org, slug="pp", name="Phnom Penh", tz="Asia/Phnom_Penh")
    admin = make_staff(org, None, Role.ORG_ADMIN, email="utc-boss@example.com")
    session = make_session(dojo, datetime.datetime(2026, 6, 9, 23, 0, tzinfo=datetime.UTC))

    page = calendars.build_page(actor=actor_for(admin), view=calendars.WEEK, anchor=WEDNESDAY)

    assert session in _day(page, datetime.date(2026, 6, 10)).sessions
    assert _day(page, datetime.date(2026, 6, 9)).sessions == []


def test_an_early_class_on_the_first_day_is_not_lost_to_the_query_window(world):
    """⚠ This is what ``_OFFSET_MARGIN`` is for, and the only test that proves it.

    A 06:00 Monday class in Phnom Penh happened at 23:00 the *previous* Sunday in
    UTC — before the unpadded window even opens. Without the margin the query
    never fetches it and the week silently starts an hour late, every week, for
    every dojo east of Greenwich.
    """
    early = make_session(world["dojo_a"], datetime.datetime(2026, 6, 7, 23, 0, tzinfo=datetime.UTC))

    page = calendars.build_page(
        actor=actor_for(world["admin"]), view=calendars.WEEK, anchor=WEDNESDAY
    )

    assert early in _day(page, datetime.date(2026, 6, 8)).sessions


def test_a_class_just_past_local_midnight_belongs_to_the_next_week(world):
    """The mirror of the above: 17:30 UTC Sunday is 00:30 *Monday* in Phnom Penh.

    The padded query fetches it; the local-date test is what keeps it out. Bucket
    this one by its UTC date and it lands on Sunday, one week early.
    """
    next_week = make_session(
        world["dojo_a"], datetime.datetime(2026, 6, 14, 17, 30, tzinfo=datetime.UTC)
    )

    page = calendars.build_page(
        actor=actor_for(world["admin"]), view=calendars.WEEK, anchor=WEDNESDAY
    )

    assert next_week not in [s for day in page.days for s in day.sessions]
    assert page.session_count == 0


def test_month_grid_marks_borrowed_days_out_of_focus(world):
    make_session(world["dojo_a"], datetime.datetime(2026, 6, 10, 3, 0, tzinfo=datetime.UTC))

    page = calendars.build_page(
        actor=actor_for(world["admin"]), view=calendars.MONTH, anchor=WEDNESDAY
    )

    assert all(len(week) == 7 for week in page.weeks)
    in_focus = {day.date for day in page.days if day.in_focus}
    assert min(in_focus) == datetime.date(2026, 6, 1)
    assert max(in_focus) == datetime.date(2026, 6, 30)
    # Borrowed days exist but do not count toward the heading's tally.
    assert any(not day.in_focus for day in page.days)
    assert page.session_count == 1


# -- filters ------------------------------------------------------------------


def test_dojo_filter_narrows_to_one_dojo(world):
    keep = make_session(world["dojo_a"], datetime.datetime(2026, 6, 10, 3, 0, tzinfo=datetime.UTC))
    drop = make_session(world["dojo_b"], datetime.datetime(2026, 6, 10, 4, 0, tzinfo=datetime.UTC))

    page = calendars.build_page(
        actor=actor_for(world["admin"]),
        view=calendars.WEEK,
        anchor=WEDNESDAY,
        dojo=world["dojo_a"],
    )

    found = [s for day in page.days for s in day.sessions]
    assert keep in found
    assert drop not in found


def test_instructor_filter_reads_who_is_on_the_class(world):
    """⚠ SessionInstructor, not the template's default — 1.4.8's whole point."""
    teaching = make_session(
        world["dojo_a"], datetime.datetime(2026, 6, 10, 3, 0, tzinfo=datetime.UTC)
    )
    not_teaching = make_session(
        world["dojo_a"], datetime.datetime(2026, 6, 11, 3, 0, tzinfo=datetime.UTC)
    )
    with allow_unscoped("test setup"):
        SessionInstructor.objects.create(session=teaching, person=world["instructor"].person)

    page = calendars.build_page(
        actor=actor_for(world["admin"]),
        view=calendars.WEEK,
        anchor=WEDNESDAY,
        instructor=world["instructor"].person,
    )

    found = [s for day in page.days for s in day.sessions]
    assert teaching in found
    assert not_teaching not in found


def test_filtering_by_a_substitute_finds_the_class_they_covered(world):
    """A substitution is the case the filter has to get right.

    Filtering by the stand-in must find the class; filtering by the person they
    covered for must not, because that person did not teach it.
    """
    org, dojo = world["org"], world["dojo_a"]
    covered_for = world["instructor"]
    stand_in = make_instructor(org, world["dojo_b"], email="dara@example.com", given="Dara")
    session = make_session(dojo, datetime.datetime(2026, 6, 10, 3, 0, tzinfo=datetime.UTC))
    with allow_unscoped("test setup"):
        SessionInstructor.objects.create(
            session=session,
            person=stand_in.person,
            is_substitute=True,
            replaces=covered_for.person,
        )

    actor = actor_for(world["admin"])

    covered = calendars.build_page(
        actor=actor, view=calendars.WEEK, anchor=WEDNESDAY, instructor=stand_in.person
    )
    absent = calendars.build_page(
        actor=actor, view=calendars.WEEK, anchor=WEDNESDAY, instructor=covered_for.person
    )

    assert session in _day(covered, datetime.date(2026, 6, 10)).sessions
    assert absent.session_count == 0


def test_instructor_choices_are_not_narrowed_to_one_dojo(world):
    """A substitute usually comes from another dojo — see instructors.py."""
    make_instructor(world["org"], world["dojo_b"], email="mei@example.com", given="Mei")

    names = {
        person.given_name for person in calendars.instructor_choices(actor_for(world["admin"]))
    }

    assert {"Takeshi", "Mei"} <= names


def test_ended_assignments_drop_out_of_the_instructor_list(world):
    leaver = make_instructor(world["org"], world["dojo_a"], email="gone@example.com", given="Gone")
    with allow_unscoped("test setup"):
        InstructorAssignment.objects.filter(person=leaver.person).update(
            ended_on=datetime.date(2026, 5, 1)
        )

    names = {
        person.given_name for person in calendars.instructor_choices(actor_for(world["admin"]))
    }

    assert "Gone" not in names


# -- closures -----------------------------------------------------------------


def test_a_closed_day_says_why_rather_than_looking_empty(world):
    """Materialisation already leaves closed days blank; this is the only screen
    left that can explain the blank."""
    with allow_unscoped("test setup"):
        ClosurePeriod.objects.create(
            organization=world["org"],
            dojo=world["dojo_a"],
            starts_on=datetime.date(2026, 6, 10),
            ends_on=datetime.date(2026, 6, 11),
            reason="Khmer New Year",
        )

    page = calendars.build_page(
        actor=actor_for(world["admin"]),
        view=calendars.WEEK,
        anchor=WEDNESDAY,
        dojo=world["dojo_a"],
    )

    assert _day(page, datetime.date(2026, 6, 10)).is_closed
    assert _day(page, datetime.date(2026, 6, 11)).is_closed
    assert not _day(page, datetime.date(2026, 6, 12)).is_closed
    assert _day(page, datetime.date(2026, 6, 10)).closures[0].reason == "Khmer New Year"


def test_an_org_wide_closure_shows_against_every_dojo(world):
    with allow_unscoped("test setup"):
        ClosurePeriod.objects.create(
            organization=world["org"],
            dojo=None,
            starts_on=datetime.date(2026, 6, 10),
            ends_on=datetime.date(2026, 6, 10),
            reason="Founder's day",
        )

    page = calendars.build_page(
        actor=actor_for(world["admin"]),
        view=calendars.WEEK,
        anchor=WEDNESDAY,
        dojo=world["dojo_b"],
    )

    assert _day(page, datetime.date(2026, 6, 10)).is_closed


def test_another_dojos_closure_does_not_leak_onto_this_one(world):
    with allow_unscoped("test setup"):
        ClosurePeriod.objects.create(
            organization=world["org"],
            dojo=world["dojo_b"],
            starts_on=datetime.date(2026, 6, 10),
            ends_on=datetime.date(2026, 6, 10),
            reason="Floor resurfacing",
        )

    page = calendars.build_page(
        actor=actor_for(world["admin"]),
        view=calendars.WEEK,
        anchor=WEDNESDAY,
        dojo=world["dojo_a"],
    )

    assert not _day(page, datetime.date(2026, 6, 10)).is_closed


# -- the screen ---------------------------------------------------------------


def test_the_page_renders_the_dojos_wall_clock_time(client, world):
    """18:30 in Phnom Penh must not render as 11:30 — the bug from 1.5.2."""
    make_session(world["dojo_a"], datetime.datetime(2026, 6, 10, 11, 30, tzinfo=datetime.UTC))
    client.force_login(world["instructor"])

    body = client.get(reverse("calendar"), {"date": "2026-06-10"}).content.decode()

    assert "18:30" in body
    assert "11:30" not in body


def test_month_view_renders(client, world):
    make_session(world["dojo_a"], datetime.datetime(2026, 6, 10, 11, 30, tzinfo=datetime.UTC))
    client.force_login(world["instructor"])

    response = client.get(reverse("calendar"), {"date": "2026-06-10", "view": "month"})

    assert response.status_code == 200
    assert "June 2026" in response.content.decode()


def test_a_dojo_scoped_instructor_never_sees_another_dojos_classes(client, world):
    elsewhere = make_session(
        world["dojo_b"], datetime.datetime(2026, 6, 10, 11, 30, tzinfo=datetime.UTC)
    )
    client.force_login(world["instructor"])

    body = client.get(reverse("calendar"), {"date": "2026-06-10"}).content.decode()

    assert str(elsewhere.pk) not in body
    assert "Dojo B" not in body


def test_another_organisations_classes_are_absent(client, world):
    other_org = make_org(slug="other-org", name="Other Org")
    other_dojo = make_dojo(other_org, slug="other-dojo", name="Other Dojo")
    foreign = make_session(other_dojo, datetime.datetime(2026, 6, 10, 11, 30, tzinfo=datetime.UTC))
    client.force_login(world["admin"])

    body = client.get(reverse("calendar"), {"date": "2026-06-10"}).content.decode()

    assert str(foreign.pk) not in body
    assert "Other Dojo" not in body


def test_filtering_by_another_tenants_dojo_is_a_404(client, world):
    """⚠ Not a silently-ignored parameter: dropping the filter would widen the
    page to every dojo the actor can see, which is not what was asked for."""
    other_org = make_org(slug="other-org", name="Other Org")
    other_dojo = make_dojo(other_org, slug="other-dojo", name="Other Dojo")
    client.force_login(world["admin"])

    response = client.get(reverse("calendar"), {"dojo": str(other_dojo.pk)})

    assert response.status_code == 404


def test_a_malformed_dojo_id_is_a_404_not_a_500(client, world):
    client.force_login(world["admin"])

    assert client.get(reverse("calendar"), {"dojo": "../../etc/passwd"}).status_code == 404
    assert client.get(reverse("calendar"), {"instructor": "1 OR 1=1"}).status_code == 404


def test_a_dojo_scoped_actor_cannot_filter_to_a_dojo_they_do_not_hold(client, world):
    client.force_login(world["instructor"])

    response = client.get(reverse("calendar"), {"dojo": str(world["dojo_b"].pk)})

    assert response.status_code == 404


def test_a_guardian_is_refused(client, world):
    guardian = make_staff(world["org"], world["dojo_a"], Role.GUARDIAN, email="parent@example.com")
    client.force_login(guardian)

    assert client.get(reverse("calendar")).status_code == 403


def test_anonymous_is_redirected_to_login(client):
    response = client.get(reverse("calendar"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_navigation_links_keep_the_filters(client, world):
    client.force_login(world["admin"])

    body = client.get(
        reverse("calendar"),
        {"date": "2026-06-10", "dojo": str(world["dojo_a"].pk)},
    ).content.decode()

    # Previous week, still filtered to dojo A.
    assert "date=2026-06-03" in body
    assert "date=2026-06-17" in body
    assert body.count(f"dojo={world['dojo_a'].pk}") >= 2


def test_cancelled_classes_are_shown_not_hidden(client, world):
    """A cancellation is a record of something somebody did — 1.4.5."""
    make_session(
        world["dojo_a"],
        datetime.datetime(2026, 6, 10, 11, 30, tzinfo=datetime.UTC),
        status=ClassSession.Status.CANCELLED,
    )
    client.force_login(world["instructor"])

    body = client.get(reverse("calendar"), {"date": "2026-06-10"}).content.decode()

    assert "cancelled" in body.lower()


def test_the_page_does_not_walk_scoped_reverse_relations(client, world):
    """⚠ ``session.session_instructors`` in a loop raises UnscopedAccessError.

    Instructors are gathered in one for_actor query and attached instead. This
    renders a page with several sessions each carrying a teacher, which is the
    shape that would blow up if a template ever reached for the relation.
    """
    for hour in (2, 4, 6):
        session = make_session(
            world["dojo_a"], datetime.datetime(2026, 6, 10, hour, 0, tzinfo=datetime.UTC)
        )
        with allow_unscoped("test setup"):
            SessionInstructor.objects.create(session=session, person=world["instructor"].person)
    client.force_login(world["admin"])

    response = client.get(reverse("calendar"), {"date": "2026-06-10"})

    assert response.status_code == 200
    assert response.content.decode().count("Takeshi Sensei") >= 3
