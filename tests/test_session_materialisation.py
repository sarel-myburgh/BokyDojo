"""ClassSession materialisation — TODO 1.4.2, plan §4.5.

The DST cases are the point of this file. Everything else about recurrence is
dateutil's problem, not ours.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Dojo, Organization, Person
from apps.scheduling.materialise import materialise_sessions, materialise_template
from apps.scheduling.models import ClassSession, ClassTemplate, ClosurePeriod

pytestmark = pytest.mark.django_db

SYSTEM = Actor.system()
JAN = datetime.date(2026, 1, 1)
MONDAY = datetime.date(2026, 1, 5)  # a Monday


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def dojo(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(
            organization=org, name="Phnom Penh", slug="pp", timezone="Asia/Phnom_Penh"
        )


@pytest.fixture
def berlin_dojo(org):
    """A dojo in a timezone that actually observes DST."""
    with allow_unscoped("test setup"):
        return Dojo.objects.create(
            organization=org, name="Berlin", slug="berlin", timezone="Europe/Berlin"
        )


def make_template(dojo, *, rrule="FREQ=WEEKLY;BYDAY=MO", start=datetime.time(18, 0), **kwargs):
    with allow_unscoped("test setup"):
        return ClassTemplate.objects.create(
            dojo=dojo,
            name=kwargs.pop("name", "Evening class"),
            rrule=rrule,
            start_time=start,
            duration_minutes=kwargs.pop("duration_minutes", 60),
            active_from=kwargs.pop("active_from", JAN),
            **kwargs,
        )


def sessions_for(template):
    return list(
        ClassSession.objects.for_organization(template.dojo.organization_id)
        .filter(template=template)
        .order_by("starts_at")
    )


# -- basics -------------------------------------------------------------------


def test_weekly_template_materialises_to_the_horizon(dojo):
    template = make_template(dojo)

    result = materialise_template(
        template, from_date=JAN, to_date=JAN + datetime.timedelta(days=27)
    )

    assert result.created == 4, "four Mondays in a four-week window"
    assert len(sessions_for(template)) == 4


def test_duration_sets_ends_at(dojo):
    template = make_template(dojo, duration_minutes=90)
    materialise_template(template, from_date=JAN, to_date=JAN + datetime.timedelta(days=7))

    session = sessions_for(template)[0]
    assert (session.ends_at - session.starts_at) == datetime.timedelta(minutes=90)


def test_session_is_stored_in_utc_but_lands_at_local_wall_clock(dojo):
    template = make_template(dojo, start=datetime.time(18, 0))
    materialise_template(template, from_date=JAN, to_date=JAN + datetime.timedelta(days=7))

    session = sessions_for(template)[0]
    local = session.starts_at.astimezone(ZoneInfo("Asia/Phnom_Penh"))
    assert (local.hour, local.minute) == (18, 0)
    assert session.starts_at.tzinfo is not None, "stored as an aware datetime"


def test_rerun_creates_nothing_new(dojo):
    template = make_template(dojo)
    window = {"from_date": JAN, "to_date": JAN + datetime.timedelta(days=27)}

    first = materialise_template(template, **window)
    second = materialise_template(template, **window)

    assert first.created == 4
    assert second.created == 0
    assert second.skipped_existing == 4
    assert len(sessions_for(template)) == 4


def test_cancelled_sessions_are_not_resurrected(dojo):
    template = make_template(dojo)
    window = {"from_date": JAN, "to_date": JAN + datetime.timedelta(days=27)}
    materialise_template(template, **window)

    session = sessions_for(template)[0]
    session.cancel("instructor ill")

    materialise_template(template, **window)

    session.refresh_from_db()
    assert session.status == ClassSession.Status.CANCELLED
    assert len(sessions_for(template)) == 4, "no duplicate created alongside the cancellation"


# -- DST ----------------------------------------------------------------------


def test_class_time_survives_a_dst_transition(berlin_dojo):
    """Europe/Berlin springs forward on 2026-03-29. A 19:00 class stays 19:00."""
    template = make_template(
        berlin_dojo,
        rrule="FREQ=WEEKLY;BYDAY=SU",
        start=datetime.time(19, 0),
        active_from=datetime.date(2026, 3, 1),
    )

    materialise_template(
        template,
        from_date=datetime.date(2026, 3, 1),
        to_date=datetime.date(2026, 4, 30),
    )

    berlin = ZoneInfo("Europe/Berlin")
    local_times = {
        session.starts_at.astimezone(berlin).strftime("%H:%M") for session in sessions_for(template)
    }
    assert local_times == {"19:00"}, f"DST moved the class: {local_times}"


def test_utc_offset_actually_changes_across_the_transition(berlin_dojo):
    """The mirror image of the test above: same local time, different UTC offset."""
    template = make_template(
        berlin_dojo,
        rrule="FREQ=WEEKLY;BYDAY=SU",
        start=datetime.time(19, 0),
        active_from=datetime.date(2026, 3, 1),
    )
    materialise_template(
        template,
        from_date=datetime.date(2026, 3, 1),
        to_date=datetime.date(2026, 4, 30),
    )

    berlin = ZoneInfo("Europe/Berlin")
    offsets = {
        session.starts_at.astimezone(berlin).utcoffset() for session in sessions_for(template)
    }
    assert offsets == {datetime.timedelta(hours=1), datetime.timedelta(hours=2)}


def test_rerun_across_a_dst_transition_creates_no_duplicates(berlin_dojo):
    """The bug this guards: matching on UTC instants would re-create the shifted ones."""
    template = make_template(
        berlin_dojo,
        rrule="FREQ=WEEKLY;BYDAY=SU",
        start=datetime.time(19, 0),
        active_from=datetime.date(2026, 3, 1),
    )
    window = {"from_date": datetime.date(2026, 3, 1), "to_date": datetime.date(2026, 4, 30)}

    materialise_template(template, **window)
    count_after_first = len(sessions_for(template))
    second = materialise_template(template, **window)

    assert second.created == 0
    assert len(sessions_for(template)) == count_after_first


# -- closures -----------------------------------------------------------------


def test_closure_period_suppresses_sessions(dojo, org):
    template = make_template(dojo)
    with allow_unscoped("test setup"):
        ClosurePeriod.objects.create(
            organization=org,
            dojo=dojo,
            starts_on=MONDAY,
            ends_on=MONDAY,
            reason="Public holiday",
        )

    result = materialise_template(
        template, from_date=JAN, to_date=JAN + datetime.timedelta(days=27)
    )

    assert result.skipped_closed == 1
    assert result.created == 3
    dates = {s.starts_at.astimezone(ZoneInfo(dojo.timezone)).date() for s in sessions_for(template)}
    assert MONDAY not in dates


def test_another_dojos_closure_is_ignored(dojo, berlin_dojo, org):
    template = make_template(dojo)
    with allow_unscoped("test setup"):
        ClosurePeriod.objects.create(
            organization=org,
            dojo=berlin_dojo,
            starts_on=MONDAY,
            ends_on=MONDAY,
            reason="Berlin only",
        )

    result = materialise_template(
        template, from_date=JAN, to_date=JAN + datetime.timedelta(days=27)
    )

    assert result.skipped_closed == 0
    assert result.created == 4


def test_org_wide_closure_applies_to_every_dojo(dojo, org):
    template = make_template(dojo)
    with allow_unscoped("test setup"):
        ClosurePeriod.objects.create(
            organization=org,
            dojo=None,
            starts_on=MONDAY,
            ends_on=MONDAY + datetime.timedelta(days=7),
            reason="Khmer New Year",
        )

    result = materialise_template(
        template, from_date=JAN, to_date=JAN + datetime.timedelta(days=27)
    )

    assert result.skipped_closed == 2


# -- template activity window -------------------------------------------------


def test_template_not_yet_active_produces_nothing(dojo):
    template = make_template(dojo, active_from=datetime.date(2027, 1, 1))
    result = materialise_template(
        template, from_date=JAN, to_date=JAN + datetime.timedelta(days=27)
    )
    assert result.created == 0


def test_expired_template_stops_at_active_to(dojo):
    template = make_template(dojo, active_to=datetime.date(2026, 1, 12))
    result = materialise_template(
        template, from_date=JAN, to_date=JAN + datetime.timedelta(days=27)
    )
    assert result.created == 2, "only the Mondays up to active_to"


def test_unusable_rrule_is_reported_not_raised(dojo):
    template = make_template(dojo, rrule="THIS IS NOT AN RRULE")
    result = materialise_template(
        template, from_date=JAN, to_date=JAN + datetime.timedelta(days=27)
    )
    assert result.created == 0
    assert result.errors, "a broken template must be reported, not silently skipped"


def test_until_with_a_utc_suffix_is_accepted(dojo):
    """A `Z`-suffixed UNTIL is legal RFC 5545 and must not crash the generator.

    UNTIL is a datetime, not a date: midnight on the 19th excludes the 18:00
    class on the 19th, leaving the 5th and the 12th.
    """
    template = make_template(dojo, rrule="FREQ=WEEKLY;BYDAY=MO;UNTIL=20260119T000000Z")
    result = materialise_template(
        template, from_date=JAN, to_date=JAN + datetime.timedelta(days=27)
    )
    assert not result.errors
    assert result.created == 2

    later = make_template(
        dojo, name="Later cut-off", rrule="FREQ=WEEKLY;BYDAY=MO;UNTIL=20260119T235900Z"
    )
    assert (
        materialise_template(
            later, from_date=JAN, to_date=JAN + datetime.timedelta(days=27)
        ).created
        == 3
    )


# -- the whole-run entry point ------------------------------------------------


def test_materialise_sessions_covers_every_active_template(dojo, berlin_dojo):
    make_template(dojo, name="PP class")
    make_template(berlin_dojo, name="Berlin class")

    result = materialise_sessions(actor=SYSTEM, horizon_days=27, today=JAN)

    assert result.templates == 2
    assert result.created == 8


def test_materialise_sessions_can_be_limited_to_one_dojo(dojo, berlin_dojo):
    make_template(dojo, name="PP class")
    make_template(berlin_dojo, name="Berlin class")

    result = materialise_sessions(actor=SYSTEM, horizon_days=27, today=JAN, dojo=dojo)

    assert result.templates == 1
    assert result.created == 4


def test_scoped_actor_only_materialises_their_own_dojo(dojo, berlin_dojo, org):
    make_template(dojo, name="PP class")
    make_template(berlin_dojo, name="Berlin class")

    with allow_unscoped("test setup"):
        staff = Person.objects.create(organization=org, given_name="Kenji", family_name="Sato")
    scoped = Actor(
        user_id=None,
        person_id=staff.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
    )

    result = materialise_sessions(actor=scoped, horizon_days=27, today=JAN)

    assert result.templates == 1
