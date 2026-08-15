"""Scheduling models — TODO 1.4.1, 1.4.3, 1.4.4, 1.4.6, 1.4.7."""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Dojo, Organization
from apps.ranks.models import Rank, RankLadder, Style
from apps.scheduling.holidays import import_holidays, set_holiday_observance
from apps.scheduling.models import (
    ClassSession,
    ClassTemplate,
    ClosurePeriod,
    Holiday,
    HolidayObservance,
)

pytestmark = pytest.mark.django_db

UTC = datetime.UTC


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def dojo(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo A", slug="dojo-a")


@pytest.fixture
def dojo_b(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo B", slug="dojo-b")


@pytest.fixture
def other_org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Other Org", slug="other-org")


@pytest.fixture
def other_dojo(other_org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=other_org, name="Other Dojo", slug="other-dojo")


@pytest.fixture
def style(dojo):
    with allow_unscoped("test setup"):
        return Style.objects.create(organization=dojo.organization, name="Shotokan")


@pytest.fixture
def ladder(style):
    with allow_unscoped("test setup"):
        return RankLadder.objects.create(style=style, name="Adult", applies_to="adult")


@pytest.fixture
def rank(ladder):
    with allow_unscoped("test setup"):
        return Rank.objects.create(ladder=ladder, order=1, name="9th Kyu")


@pytest.fixture
def other_rank(other_org):
    with allow_unscoped("test setup"):
        style = Style.objects.create(organization=other_org, name="Other Style")
        ladder = RankLadder.objects.create(style=style, name="Other Ladder", applies_to="adult")
        return Rank.objects.create(ladder=ladder, order=1, name="Other Rank")


@pytest.fixture
def class_template(dojo, style):
    with allow_unscoped("test setup"):
        return ClassTemplate.objects.create(
            dojo=dojo,
            name="Beginners",
            style=style,
            rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
            start_time=datetime.time(18, 0),
            duration_minutes=60,
            active_from=datetime.date(2025, 1, 1),
        )


@pytest.fixture
def class_session(dojo, class_template):
    with allow_unscoped("test setup"):
        return ClassSession.objects.create(
            template=class_template,
            dojo=dojo,
            starts_at=datetime.datetime(2025, 1, 6, 18, 0, tzinfo=UTC),
            ends_at=datetime.datetime(2025, 1, 6, 19, 0, tzinfo=UTC),
        )


@pytest.fixture
def closure(org):
    with allow_unscoped("test setup"):
        return ClosurePeriod.objects.create(
            organization=org,
            starts_on=datetime.date(2025, 1, 1),
            ends_on=datetime.date(2025, 1, 7),
            reason="Closed for holidays",
        )


# ---- ClassTemplate basics ---------------------------------------------------


def test_class_template_creation(class_template):
    assert class_template.name == "Beginners"
    assert class_template.dojo is not None


def test_class_template_str(class_template):
    assert "Beginners" in str(class_template)
    assert "Dojo A" in str(class_template)


def test_class_template_counts_toward_defaults_to_empty(dojo):
    with allow_unscoped("test setup"):
        template = ClassTemplate.objects.create(
            dojo=dojo,
            name="Kata Class",
            rrule="FREQ=WEEKLY;BYDAY=FR",
            start_time=datetime.time(18, 0),
            duration_minutes=60,
            active_from=datetime.date(2025, 1, 1),
        )
    assert template.counts_toward == []


# ---- ClassTemplate cross-organisation guards --------------------------------


def test_class_template_accepts_same_org_style(dojo, style):
    with allow_unscoped("test setup"):
        template = ClassTemplate.objects.create(
            dojo=dojo,
            name="Beginners",
            style=style,
            rrule="FREQ=WEEKLY;BYDAY=MO",
            start_time=datetime.time(18, 0),
            duration_minutes=60,
            active_from=datetime.date(2025, 1, 1),
        )
    assert template.pk is not None


def test_class_template_rejects_other_org_style(dojo, other_org):
    with allow_unscoped("test setup"):
        other_style = Style.objects.create(organization=other_org, name="Other Style")
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        ClassTemplate.objects.create(
            dojo=dojo,
            name="Beginners",
            style=other_style,
            rrule="FREQ=WEEKLY;BYDAY=MO",
            start_time=datetime.time(18, 0),
            duration_minutes=60,
            active_from=datetime.date(2025, 1, 1),
        )


def test_class_template_rejects_other_org_rank_min(dojo, style, other_rank):
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        ClassTemplate.objects.create(
            dojo=dojo,
            name="Beginners",
            style=style,
            rank_min=other_rank,
            rrule="FREQ=WEEKLY;BYDAY=MO",
            start_time=datetime.time(18, 0),
            duration_minutes=60,
            active_from=datetime.date(2025, 1, 1),
        )


def test_class_template_rejects_other_org_rank_max(dojo, style, other_rank):
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        ClassTemplate.objects.create(
            dojo=dojo,
            name="Beginners",
            style=style,
            rank_max=other_rank,
            rrule="FREQ=WEEKLY;BYDAY=MO",
            start_time=datetime.time(18, 0),
            duration_minutes=60,
            active_from=datetime.date(2025, 1, 1),
        )


def test_class_template_accepts_same_org_rank_bounds(dojo, style, rank):
    with allow_unscoped("test setup"):
        template = ClassTemplate.objects.create(
            dojo=dojo,
            name="Beginners",
            style=style,
            rank_min=rank,
            rank_max=rank,
            rrule="FREQ=WEEKLY;BYDAY=MO",
            start_time=datetime.time(18, 0),
            duration_minutes=60,
            active_from=datetime.date(2025, 1, 1),
        )
    assert template.pk is not None


# ---- ClosurePeriod basics ---------------------------------------------------


def test_closure_period_creation(closure):
    assert closure.starts_on == datetime.date(2025, 1, 1)
    assert closure.ends_on == datetime.date(2025, 1, 7)


def test_closure_period_covers_inclusive_boundaries(closure):
    assert closure.covers(closure.starts_on) is True
    assert closure.covers(closure.ends_on) is True
    assert closure.covers(datetime.date(2024, 12, 31)) is False
    assert closure.covers(datetime.date(2025, 1, 8)) is False


def test_closure_period_dojo_null_means_org_wide(closure):
    assert closure.dojo_id is None


def test_closure_period_ends_on_before_starts_on_rejected(org):
    with allow_unscoped("test setup"), pytest.raises(IntegrityError):
        ClosurePeriod.objects.create(
            organization=org,
            starts_on=datetime.date(2025, 1, 2),
            ends_on=datetime.date(2025, 1, 1),
            reason="Invalid",
        )


# ---- ClosurePeriod cross-organisation guard ---------------------------------


def test_closure_period_rejects_other_org_dojo(org, other_dojo):
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        ClosurePeriod.objects.create(
            organization=org,
            dojo=other_dojo,
            starts_on=datetime.date(2025, 1, 1),
            ends_on=datetime.date(2025, 1, 2),
            reason="Test",
        )


def test_closure_period_accepts_same_org_dojo(org, dojo):
    with allow_unscoped("test setup"):
        closure = ClosurePeriod.objects.create(
            organization=org,
            dojo=dojo,
            starts_on=datetime.date(2025, 1, 1),
            ends_on=datetime.date(2025, 1, 2),
            reason="Dojo closure",
        )
    assert closure.pk is not None


# ---- ClassSession basics ----------------------------------------------------


def test_class_session_creation(class_session):
    assert class_session.status == ClassSession.Status.SCHEDULED
    assert class_session.duration_minutes == 60


def test_class_session_ends_at_must_be_after_starts_at(dojo):
    with allow_unscoped("test setup"), pytest.raises(IntegrityError):
        ClassSession.objects.create(
            dojo=dojo,
            starts_at=datetime.datetime(2025, 1, 6, 18, 0, tzinfo=UTC),
            ends_at=datetime.datetime(2025, 1, 6, 18, 0, tzinfo=UTC),
        )


def test_class_session_one_off_has_no_template(dojo):
    with allow_unscoped("test setup"):
        session = ClassSession.objects.create(
            dojo=dojo,
            starts_at=datetime.datetime(2025, 1, 6, 18, 0, tzinfo=UTC),
            ends_at=datetime.datetime(2025, 1, 6, 19, 0, tzinfo=UTC),
        )
    assert session.template_id is None


def test_class_session_cancel_preserves_row(class_session):
    pk = class_session.pk
    class_session.cancel("instructor illness")
    class_session.refresh_from_db()
    assert class_session.pk == pk
    assert class_session.status == ClassSession.Status.CANCELLED
    assert class_session.cancellation_reason == "instructor illness"


# ---- ClassSession cross-organisation guard ----------------------------------


def test_class_session_rejects_other_org_template(dojo, other_dojo):
    with allow_unscoped("test setup"):
        other_template = ClassTemplate.objects.create(
            dojo=other_dojo,
            name="Other Class",
            rrule="FREQ=WEEKLY;BYDAY=MO",
            start_time=datetime.time(10, 0),
            duration_minutes=60,
            active_from=datetime.date(2025, 1, 1),
        )
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        ClassSession.objects.create(
            template=other_template,
            dojo=dojo,
            starts_at=datetime.datetime(2025, 1, 6, 18, 0, tzinfo=UTC),
            ends_at=datetime.datetime(2025, 1, 6, 19, 0, tzinfo=UTC),
        )


def test_class_session_accepts_same_org_template(class_session):
    assert class_session.template is not None
    assert class_session.dojo.organization_id == class_session.template.dojo.organization_id


# ---- Public holiday import --------------------------------------------------


def test_import_holidays_creates_fixed_holidays_only(org):
    holidays = import_holidays(org, "KH", 2025)
    names = {h.name for h in holidays}
    assert "International New Year" in names
    assert "Independence Day" in names
    assert not ClosurePeriod.objects.for_organization(org.pk).exists()


def test_import_holidays_is_idempotent(org):
    first = import_holidays(org, "KH", 2025)
    second = import_holidays(org, "KH", 2025)
    assert len(first) == len(second)
    assert (
        Holiday.objects.for_organization(org.pk)
        .filter(name="International New Year", date=datetime.date(2025, 1, 1))
        .count()
        == 1
    )


def test_import_holidays_returns_empty_for_unknown_country(org):
    assert import_holidays(org, "ZZ", 2025) == []


# ---- Holiday observance -----------------------------------------------------


def test_holiday_observance_closed_creates_closure(org, dojo):
    with allow_unscoped("test setup"):
        holiday = Holiday.objects.create(
            organization=org,
            name="Test Holiday",
            date=datetime.date(2025, 1, 1),
        )
    observance = set_holiday_observance(
        holiday,
        dojo,
        HolidayObservance.Observance.CLOSED,
    )
    assert observance.closure is not None
    assert observance.closure.covers(datetime.date(2025, 1, 1))


def test_holiday_observance_open_removes_closure(org, dojo):
    with allow_unscoped("test setup"):
        holiday = Holiday.objects.create(
            organization=org,
            name="Test Holiday",
            date=datetime.date(2025, 1, 1),
        )
    observance = set_holiday_observance(
        holiday,
        dojo,
        HolidayObservance.Observance.CLOSED,
    )
    observance.refresh_from_db()
    closure_id = observance.closure_id

    set_holiday_observance(
        holiday,
        dojo,
        HolidayObservance.Observance.OPEN,
    )

    observance.refresh_from_db()
    assert observance.closure_id is None
    assert not ClosurePeriod.objects.for_organization(org.pk).filter(pk=closure_id).exists()


def test_holiday_observance_cross_organisation_guard(org, dojo, other_dojo):
    with allow_unscoped("test setup"):
        holiday = Holiday.objects.create(
            organization=org,
            name="Test Holiday",
            date=datetime.date(2025, 1, 1),
        )
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        HolidayObservance.objects.create(
            holiday=holiday,
            dojo=other_dojo,
            observance=HolidayObservance.Observance.CLOSED,
        )


# ---- Tenant isolation -------------------------------------------------------


def test_class_template_tenant_isolation(org, other_org, class_template):
    actor = Actor(user_id=None, person_id=None, organization_id=org.pk)
    other_actor = Actor(user_id=None, person_id=None, organization_id=other_org.pk)
    assert ClassTemplate.objects.for_actor(actor).count() == 1
    assert ClassTemplate.objects.for_actor(other_actor).count() == 0


def test_class_template_dojo_scoped_isolation(org, dojo, dojo_b, class_template):
    scoped = Actor(
        user_id=None,
        person_id=None,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo_b.pk}),
    )
    assert ClassTemplate.objects.for_actor(scoped).count() == 0


def test_class_session_tenant_isolation(org, other_org, class_session):
    actor = Actor(user_id=None, person_id=None, organization_id=org.pk)
    other_actor = Actor(user_id=None, person_id=None, organization_id=other_org.pk)
    assert ClassSession.objects.for_actor(actor).count() == 1
    assert ClassSession.objects.for_actor(other_actor).count() == 0


def test_closure_period_tenant_isolation(org, other_org, closure):
    actor = Actor(user_id=None, person_id=None, organization_id=org.pk)
    other_actor = Actor(user_id=None, person_id=None, organization_id=other_org.pk)
    assert ClosurePeriod.objects.for_actor(actor).count() == 1
    assert ClosurePeriod.objects.for_actor(other_actor).count() == 0
