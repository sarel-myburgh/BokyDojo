"""Holiday import, observance, and provider behaviour — TODO 1.4.4, plan §12.2."""

from __future__ import annotations

import datetime
import io

import pytest
from django.core.exceptions import ValidationError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Dojo, Organization
from apps.scheduling.holidays import (
    BuiltinProvider,
    CsvProvider,
    HolidayImportError,
    NagerDateProvider,
    import_holidays,
)
from apps.scheduling.models import ClosurePeriod, Holiday, HolidayObservance

pytestmark = pytest.mark.django_db


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
        return Dojo.objects.create(
            organization=other_org, name="Other Dojo", slug="other-dojo"
        )


def test_importing_creates_holidays_and_no_closures(org):
    holidays = import_holidays(org, "KH", 2025)

    assert len(holidays) > 0
    assert Holiday.objects.for_organization(org.pk).count() == len(holidays)
    assert ClosurePeriod.objects.for_organization(org.pk).count() == 0


def test_holiday_creation_does_not_create_closure(org):
    with allow_unscoped("test setup"):
        holiday = Holiday.objects.create(
            organization=org,
            name="Arbitrary Day",
            date=datetime.date(2025, 6, 1),
        )

    assert holiday.pk is not None
    assert ClosurePeriod.objects.for_organization(org.pk).count() == 0


def test_two_dojos_can_hold_different_observances(org, dojo, dojo_b):
    with allow_unscoped("test setup"):
        holiday = Holiday.objects.create(
            organization=org,
            name="Pchum Ben",
            date=datetime.date(2025, 9, 21),
        )

    closed = HolidayObservance.objects.create(
        holiday=holiday,
        dojo=dojo,
        observance=HolidayObservance.Observance.CLOSED,
    )
    closed.apply()

    open_obs = HolidayObservance.objects.create(
        holiday=holiday,
        dojo=dojo_b,
        observance=HolidayObservance.Observance.OPEN,
    )
    open_obs.apply()

    closed.refresh_from_db()
    open_obs.refresh_from_db()

    assert closed.closure is not None
    assert open_obs.closure is None
    assert ClosurePeriod.objects.for_organization(org.pk).count() == 1


def test_switching_closed_to_open_removes_closure(org, dojo):
    with allow_unscoped("test setup"):
        holiday = Holiday.objects.create(
            organization=org,
            name="Special Day",
            date=datetime.date(2025, 6, 1),
        )

    observance = HolidayObservance.objects.create(
        holiday=holiday,
        dojo=dojo,
        observance=HolidayObservance.Observance.CLOSED,
    )
    observance.apply()
    observance.refresh_from_db()
    closure_id = observance.closure_id
    assert closure_id is not None

    observance.observance = HolidayObservance.Observance.OPEN
    observance.save()
    observance.apply()
    observance.refresh_from_db()

    assert observance.closure_id is None
    assert not ClosurePeriod.objects.for_organization(org.pk).filter(pk=closure_id).exists()


def test_apply_is_idempotent(org, dojo):
    with allow_unscoped("test setup"):
        holiday = Holiday.objects.create(
            organization=org,
            name="Repeat Day",
            date=datetime.date(2025, 6, 2),
        )

    observance = HolidayObservance.objects.create(
        holiday=holiday,
        dojo=dojo,
        observance=HolidayObservance.Observance.CLOSED,
    )
    observance.apply()
    observance.apply()
    observance.apply()

    assert ClosurePeriod.objects.for_organization(org.pk).count() == 1


def test_re_import_is_idempotent(org):
    first = import_holidays(org, "KH", 2025)
    second = import_holidays(org, "KH", 2025)

    assert len(first) == len(second)
    assert Holiday.objects.for_organization(org.pk).count() == len(first)


def test_import_holidays_matches_by_external_id(org):
    provider = NagerDateProvider(
        fetch=lambda _url: [
            {
                "name": "API Holiday",
                "date": "2025-06-01",
                "code": "api-001",
                "fixed": True,
            }
        ]
    )

    first = import_holidays(org, "KH", 2025, provider=provider)
    assert len(first) == 1
    assert first[0].external_id == "api-001"

    # Same provider with a renamed holiday but identical external_id updates row.
    provider_renamed = NagerDateProvider(
        fetch=lambda _url: [
            {
                "name": "Renamed API Holiday",
                "date": "2025-06-01",
                "code": "api-001",
                "fixed": True,
            }
        ]
    )
    second = import_holidays(org, "KH", 2025, provider=provider_renamed)
    assert len(second) == 1
    second[0].refresh_from_db()
    assert second[0].name == "Renamed API Holiday"


def test_nager_provider_network_failure_raises(org):
    def failing_fetch(_url):
        raise RuntimeError("network down")

    provider = NagerDateProvider(fetch=failing_fetch)

    with pytest.raises(HolidayImportError):
        import_holidays(org, "KH", 2025, provider=provider)


def test_nager_provider_malformed_response_raises(org):
    provider = NagerDateProvider(fetch=lambda _url: {"unexpected": "object"})

    with pytest.raises(HolidayImportError):
        import_holidays(org, "KH", 2025, provider=provider)


def test_nager_provider_missing_fields_raises(org):
    provider = NagerDateProvider(fetch=lambda _url: [{"date": "2025-06-01"}])

    with pytest.raises(HolidayImportError):
        import_holidays(org, "KH", 2025, provider=provider)


def test_nager_provider_invalid_date_raises(org):
    provider = NagerDateProvider(
        fetch=lambda _url: [{"name": "Bad Date", "date": "not-a-date"}]
    )

    with pytest.raises(HolidayImportError):
        import_holidays(org, "KH", 2025, provider=provider)


def test_csv_provider_reads_holidays(org):
    csv_file = io.StringIO("name,date,external_id\nCSV Day,2025-06-01,csv-001\n")
    provider = CsvProvider(file=csv_file)

    holidays = import_holidays(org, "KH", 2025, provider=provider)

    assert len(holidays) == 1
    assert holidays[0].name == "CSV Day"
    assert holidays[0].external_id == "csv-001"


def test_csv_provider_empty_file_raises(org):
    provider = CsvProvider(file=io.StringIO(""))

    with pytest.raises(HolidayImportError):
        import_holidays(org, "KH", 2025, provider=provider)


def test_builtin_provider_only_includes_kh_fixed_holidays(org):
    provider = BuiltinProvider()

    holidays = provider.fetch(org, "KH", 2025)
    names = {h.name for h in holidays}

    assert "International New Year" in names
    assert "Constitution Day" in names
    assert "Khmer New Year" not in names
    assert "Pchum Ben" not in names
    assert "Water Festival" not in names


def test_builtin_provider_returns_empty_for_non_kh_country(org):
    provider = BuiltinProvider()
    assert provider.fetch(org, "ZZ", 2025) == []


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


def test_holiday_observance_rejects_other_org_closure(org, dojo, other_org):
    with allow_unscoped("test setup"):
        other_dojo = Dojo.objects.create(
            organization=other_org, name="Other Dojo", slug="other-dojo"
        )
        holiday = Holiday.objects.create(
            organization=org,
            name="Test Holiday",
            date=datetime.date(2025, 1, 1),
        )
        closure = ClosurePeriod.objects.create(
            organization=other_org,
            dojo=other_dojo,
            starts_on=datetime.date(2025, 1, 1),
            ends_on=datetime.date(2025, 1, 1),
            reason="Other closure",
        )

    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        HolidayObservance.objects.create(
            holiday=holiday,
            dojo=dojo,
            closure=closure,
            observance=HolidayObservance.Observance.CLOSED,
        )


def test_holiday_tenant_isolation(org, other_org):
    with allow_unscoped("test setup"):
        Holiday.objects.create(
            organization=org,
            name="Org Holiday",
            date=datetime.date(2025, 1, 1),
        )
        Holiday.objects.create(
            organization=other_org,
            name="Other Holiday",
            date=datetime.date(2025, 1, 2),
        )

    actor = Actor(user_id=None, person_id=None, organization_id=org.pk)
    other_actor = Actor(user_id=None, person_id=None, organization_id=other_org.pk)

    assert Holiday.objects.for_actor(actor).count() == 1
    assert Holiday.objects.for_actor(other_actor).count() == 1


def test_holiday_observance_dojo_isolation(org, dojo, dojo_b):
    with allow_unscoped("test setup"):
        holiday = Holiday.objects.create(
            organization=org,
            name="Test Holiday",
            date=datetime.date(2025, 1, 1),
        )
        HolidayObservance.objects.create(
            holiday=holiday,
            dojo=dojo,
            observance=HolidayObservance.Observance.CLOSED,
        )

    scoped = Actor(
        user_id=None,
        person_id=None,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo_b.pk}),
    )
    assert HolidayObservance.objects.for_actor(scoped).count() == 0

    scoped_a = Actor(
        user_id=None,
        person_id=None,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
    )
    assert HolidayObservance.objects.for_actor(scoped_a).count() == 1
