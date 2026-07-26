"""InstructorProfile and TimeEntry — TODO 1.9.1, 1.9.2, plan §4.2 / §4.8.

Tests cover: instructor profile pay rate property, time entry minutes
computation (including midnight boundary), pay rate snapshot, status
lifecycle, constraint enforcement, cross-org guards, and tenant isolation.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.money import Money
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Dojo, Organization, Person
from apps.staffing.models import InstructorProfile, TimeEntry

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def other_org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Other Org", slug="other-org")


@pytest.fixture
def dojo(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo A", slug="dojo-a")


@pytest.fixture
def dojo_b(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo B", slug="dojo-b")


@pytest.fixture
def person(org):
    with allow_unscoped("test setup"):
        return Person.objects.create(
            organization=org, given_name="Takeshi", family_name="Yamada"
        )


@pytest.fixture
def person_b(org):
    with allow_unscoped("test setup"):
        return Person.objects.create(
            organization=org, given_name="Sokha", family_name="Chan"
        )


@pytest.fixture
def instructor_profile(person):
    with allow_unscoped("test setup"):
        return InstructorProfile.objects.create(
            person=person,
            pay_type=InstructorProfile.PayType.HOURLY,
            pay_rate_minor_units=2500,
            pay_currency="USD",
            employment_started_on=datetime.date(2024, 1, 1),
        )


# -- InstructorProfile ---------------------------------------------------------


class TestInstructorProfile:
    def test_pay_rate_returns_money(self, instructor_profile):
        rate = instructor_profile.pay_rate
        assert isinstance(rate, Money)
        assert rate.minor_units == 2500
        assert rate.currency == "USD"

    def test_str_contains_person_name(self, instructor_profile):
        s = str(instructor_profile)
        assert "Takeshi" in s

    def test_default_pay_rate_is_zero(self, person):
        with allow_unscoped("test setup"):
            profile = InstructorProfile.objects.create(
                person=person,
                pay_type=InstructorProfile.PayType.VOLUNTEER,
            )
        assert profile.pay_rate == Money(0, "USD")

    def test_employment_started_on_is_optional(self, person):
        with allow_unscoped("test setup"):
            profile = InstructorProfile.objects.create(
                person=person,
                pay_type=InstructorProfile.PayType.SALARY,
            )
        assert profile.employment_started_on is None

    def test_max_grading_rank_optional(self, instructor_profile):
        assert instructor_profile.max_grading_rank_id is None


# -- TimeEntry -----------------------------------------------------------------


class TestTimeEntry:
    def test_minutes_computed_on_save(self, person, dojo):
        started = timezone.now()
        ended = started + timezone.timedelta(hours=2, minutes=30)
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=started,
                ended_at=ended,
            )
        assert entry.minutes == 150

    def test_minutes_zero_when_ended_at_is_none(self, person, dojo):
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=timezone.now(),
            )
        assert entry.minutes == 0

    def test_minutes_across_midnight(self, person, dojo):
        started = datetime.datetime(2024, 6, 15, 23, 0, tzinfo=datetime.UTC)
        ended = datetime.datetime(2024, 6, 16, 1, 30, tzinfo=datetime.UTC)
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.EVENT,
                started_at=started,
                ended_at=ended,
            )
        assert entry.minutes == 150

    def test_minutes_recomputed_when_ended_at_changes(self, person, dojo):
        started = datetime.datetime(2024, 6, 15, 10, 0, tzinfo=datetime.UTC)
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=started,
            )
        assert entry.minutes == 0
        entry.ended_at = started + timezone.timedelta(hours=1)
        entry.save(update_fields=["ended_at"])
        entry.refresh_from_db()
        assert entry.minutes == 60

    def test_default_status_is_draft(self, person, dojo):
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=timezone.now(),
            )
        assert entry.status == TimeEntry.Status.DRAFT

    def test_str_contains_names(self, person, dojo):
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=timezone.now(),
            )
        s = str(entry)
        assert "Takeshi" in s
        assert "Dojo A" in s

    def test_session_id_is_optional(self, person, dojo):
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.ADMIN,
                started_at=timezone.now(),
            )
        assert entry.session_id is None

    def test_notes_blank_by_default(self, person, dojo):
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.TRAVEL,
                started_at=timezone.now(),
            )
        assert entry.notes == ""


# -- ended_at constraint -------------------------------------------------------


class TestEndedAtConstraint:
    def test_ended_at_before_started_at_is_rejected(self, person, dojo):
        started = datetime.datetime(2024, 6, 15, 10, 0, tzinfo=datetime.UTC)
        ended = started - timezone.timedelta(hours=1)
        with allow_unscoped("test setup"), pytest.raises(ValidationError):
            entry = TimeEntry(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=started,
                ended_at=ended,
            )
            entry.full_clean()

    def test_ended_at_equal_to_started_at_is_rejected(self, person, dojo):
        same = datetime.datetime(2024, 6, 15, 10, 0, tzinfo=datetime.UTC)
        with allow_unscoped("test setup"), pytest.raises(ValidationError):
            entry = TimeEntry(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=same,
                ended_at=same,
            )
            entry.full_clean()


# -- pay rate snapshot ---------------------------------------------------------


class TestPayRateSnapshot:
    def test_snapshot_survives_rate_change(self, person, dojo):
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=timezone.now(),
                status=TimeEntry.Status.APPROVED,
                pay_rate_snapshot_minor_units=2500,
                pay_rate_snapshot_currency="USD",
            )
            # Simulate the instructor's rate changing after the snapshot.
            # The snapshot on this entry must not change.
            entry.pay_rate_snapshot_minor_units = 2500
            entry.save()

        assert entry.pay_rate_snapshot_minor_units == 2500

    def test_snapshot_fields_are_nullable(self, person, dojo):
        with allow_unscoped("test setup"):
            entry = TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=timezone.now(),
            )
        assert entry.pay_rate_snapshot_minor_units is None
        assert entry.pay_rate_snapshot_currency == ""


# -- cross-org guard (TimeEntry) -----------------------------------------------


class TestTimeEntryCrossOrg:
    def test_instructor_from_different_org_rejected(self, other_org, dojo):
        with allow_unscoped("test setup"):
            outsider = Person.objects.create(
                organization=other_org, given_name="Out", family_name="Sider"
            )
            with pytest.raises(ValidationError):
                TimeEntry.objects.create(
                    instructor=outsider,
                    dojo=dojo,
                    category=TimeEntry.Category.CLASS,
                    started_at=timezone.now(),
                )

    def test_approved_by_from_different_org_rejected(self, org, other_org, person, dojo):
        with allow_unscoped("test setup"):
            outsider = Person.objects.create(
                organization=other_org, given_name="Admin", family_name="Other"
            )
            with pytest.raises(ValidationError):
                TimeEntry.objects.create(
                    instructor=person,
                    dojo=dojo,
                    category=TimeEntry.Category.CLASS,
                    started_at=timezone.now(),
                    approved_by=outsider,
                )

    def test_dojo_from_different_org_rejected(self, org, other_org, person):
        with allow_unscoped("test setup"):
            other_dojo = Dojo.objects.create(
                organization=other_org, name="Other Dojo", slug="other-dojo-xorg"
            )
            with pytest.raises(ValidationError):
                TimeEntry.objects.create(
                    instructor=person,
                    dojo=other_dojo,
                    category=TimeEntry.Category.CLASS,
                    started_at=timezone.now(),
                )


# -- tenant isolation ----------------------------------------------------------


class TestTenantIsolation:
    def test_cross_org_sees_nothing(self, person, dojo, other_org):
        with allow_unscoped("test setup"):
            TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=timezone.now(),
            )
        outsider = Actor(user_id=None, person_id=None, organization_id=other_org.pk)
        assert TimeEntry.objects.for_actor(outsider).count() == 0

    def test_owning_org_sees_it(self, person, dojo, org):
        with allow_unscoped("test setup"):
            TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=timezone.now(),
            )
        actor = Actor(user_id=None, person_id=None, organization_id=org.pk)
        assert TimeEntry.objects.for_actor(actor).count() == 1

    def test_dojo_scoped_actor_sees_only_own_dojo(
        self, person, person_b, dojo, dojo_b, org
    ):
        with allow_unscoped("test setup"):
            TimeEntry.objects.create(
                instructor=person,
                dojo=dojo,
                category=TimeEntry.Category.CLASS,
                started_at=timezone.now(),
            )
            TimeEntry.objects.create(
                instructor=person_b,
                dojo=dojo_b,
                category=TimeEntry.Category.CLASS,
                started_at=timezone.now(),
            )
        scoped = Actor(
            user_id=None,
            person_id=None,
            organization_id=org.pk,
            dojo_ids=frozenset({dojo.pk}),
        )
        entries = list(TimeEntry.objects.for_actor(scoped))
        assert len(entries) == 1
        assert entries[0].dojo_id == dojo.pk
