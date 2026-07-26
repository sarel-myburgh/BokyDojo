"""Currency conversion at the organisation's own rate — plan §6, §13.7."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.core.exchange import (
    ExchangeRate,
    NoExchangeRate,
    convert,
    rate_for,
    set_rate,
)
from apps.core.money import Money, exponent_for
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Organization

pytestmark = pytest.mark.django_db

JAN = datetime.date(2025, 1, 1)
JUN = datetime.date(2025, 6, 1)


@pytest.fixture
def orgs():
    with allow_unscoped("test setup"):
        return {
            "riverside": Organization.objects.create(name="Riverside", slug="fx-riverside"),
            "downtown": Organization.objects.create(name="Downtown", slug="fx-downtown"),
        }


# -- the rate is the dojo's, not the market's ---------------------------------


def test_two_dojos_may_use_different_usd_khr_rates(orgs):
    """Every business in Cambodia quotes its own rate. One dojo bills at
    4000:1, the one down the road at 4100:1, and both are right."""
    with allow_unscoped("test setup"):
        set_rate(orgs["riverside"], "USD", "KHR", 4000)
        set_rate(orgs["downtown"], "USD", "KHR", 4100)

    ten_dollars = Money.from_decimal("10.00", "USD")
    assert convert(ten_dollars, "KHR", organization_id=orgs["riverside"].pk) == Money(40000, "KHR")
    assert convert(ten_dollars, "KHR", organization_id=orgs["downtown"].pk) == Money(41000, "KHR")


def test_conversion_without_a_configured_rate_refuses(orgs):
    """A missing rate is a question for the dojo owner, not something to guess.
    Silently applying a market rate would produce receipts the dojo never
    agreed to."""
    with pytest.raises(NoExchangeRate, match="No USD→KHR rate configured"):
        convert(Money(1000, "USD"), "KHR", organization_id=orgs["riverside"].pk)


def test_another_organisations_rate_is_not_borrowed(orgs):
    with allow_unscoped("test setup"):
        set_rate(orgs["downtown"], "USD", "KHR", 4100)

    with pytest.raises(NoExchangeRate):
        convert(Money(1000, "USD"), "KHR", organization_id=orgs["riverside"].pk)


# -- effective dating ---------------------------------------------------------


def test_the_rate_in_force_on_the_date_is_used(orgs):
    """Historic invoices must keep converting at the rate that applied when they
    were issued, or last year's accounts move whenever somebody edits a setting."""
    with allow_unscoped("test setup"):
        set_rate(orgs["riverside"], "USD", "KHR", 4000, effective_from=JAN)
        set_rate(orgs["riverside"], "USD", "KHR", 4100, effective_from=JUN)

    org_id = orgs["riverside"].pk
    ten = Money.from_decimal("10.00", "USD")

    assert convert(ten, "KHR", organization_id=org_id, on_date=datetime.date(2025, 3, 1)) == Money(
        40000, "KHR"
    )
    assert convert(ten, "KHR", organization_id=org_id, on_date=datetime.date(2025, 9, 1)) == Money(
        41000, "KHR"
    )


def test_a_date_before_any_rate_refuses(orgs):
    with allow_unscoped("test setup"):
        set_rate(orgs["riverside"], "USD", "KHR", 4000, effective_from=JUN)

    with pytest.raises(NoExchangeRate):
        convert(
            Money(1000, "USD"),
            "KHR",
            organization_id=orgs["riverside"].pk,
            on_date=JAN,
        )


def test_changing_a_rate_adds_a_row_rather_than_editing(orgs):
    with allow_unscoped("test setup"):
        set_rate(orgs["riverside"], "USD", "KHR", 4000, effective_from=JAN)
        set_rate(orgs["riverside"], "USD", "KHR", 4100, effective_from=JUN)

    rates = ExchangeRate.objects.for_organization(orgs["riverside"].pk)
    assert rates.count() == 2


# -- inverse ------------------------------------------------------------------


def test_the_inverse_pair_is_derived_when_only_one_direction_is_set(orgs):
    """A dojo storing USD→KHR should not also have to store KHR→USD."""
    with allow_unscoped("test setup"):
        set_rate(orgs["riverside"], "USD", "KHR", 4000)

    result = convert(Money(40000, "KHR"), "USD", organization_id=orgs["riverside"].pk)
    assert result == Money(1000, "USD")


def test_an_explicit_reverse_rate_beats_the_derived_inverse(orgs):
    """Money changers buy and sell at different rates; if the dojo states both,
    use what they stated."""
    with allow_unscoped("test setup"):
        set_rate(orgs["riverside"], "USD", "KHR", 4100)
        set_rate(orgs["riverside"], "KHR", "USD", Decimal("0.00025"))  # 4000:1

    assert convert(Money(40000, "KHR"), "USD", organization_id=orgs["riverside"].pk) == Money(
        1000, "USD"
    )


# -- rounding and minor units -------------------------------------------------


def test_conversion_rounds_to_the_target_currencys_minor_unit(orgs):
    """KHR has no minor unit — the result must be whole riel, not riel-cents."""
    with allow_unscoped("test setup"):
        set_rate(orgs["riverside"], "USD", "KHR", 4100)

    result = convert(Money(1234, "USD"), "KHR", organization_id=orgs["riverside"].pk)
    assert result.currency == "KHR"
    assert result.minor_units == 50594  # 12.34 * 4100, to whole riel


def test_same_currency_conversion_is_a_no_op(orgs):
    amount = Money(1234, "USD")
    assert convert(amount, "USD", organization_id=orgs["riverside"].pk) is amount


def test_rate_for_identical_currencies_is_one(orgs):
    assert rate_for(orgs["riverside"].pk, "USD", "USD") == Decimal(1)


# -- validation ---------------------------------------------------------------


def test_a_currency_cannot_convert_to_itself(orgs):
    with allow_unscoped("test setup"), pytest.raises(ValidationError):
        set_rate(orgs["riverside"], "USD", "USD", 1)


def test_a_negative_or_zero_rate_is_refused(orgs):
    from django.db import IntegrityError

    with allow_unscoped("test setup"), pytest.raises(IntegrityError):
        set_rate(orgs["riverside"], "USD", "KHR", 0)


def test_currency_codes_are_normalised_to_upper(orgs):
    with allow_unscoped("test setup"):
        rate = set_rate(orgs["riverside"], "usd", "khr", 4000)
    assert rate.base_currency == "USD"
    assert rate.quote_currency == "KHR"


def test_an_unlisted_currency_is_accepted_with_two_decimals(orgs):
    """Refusing an unfamiliar currency would mean a code change every time an
    organisation outside the launch market signs up."""
    assert exponent_for("MYR") == 2
    assert exponent_for("PHP") == 2


def test_zero_decimal_currencies_are_known(orgs):
    assert exponent_for("KHR") == 0
    assert exponent_for("VND") == 0
    assert exponent_for("JPY") == 0


def test_three_decimal_currencies_are_known(orgs):
    assert exponent_for("KWD") == 3
    assert exponent_for("BHD") == 3


def test_a_malformed_currency_code_is_refused(orgs):
    for bad in ("US", "USDD", "12A", ""):
        with pytest.raises(ValueError):
            exponent_for(bad)


# -- tenancy ------------------------------------------------------------------


def test_rates_are_tenant_scoped(orgs):
    with allow_unscoped("test setup"):
        set_rate(orgs["riverside"], "USD", "KHR", 4000)

    outsider = Actor(user_id=None, person_id=None, organization_id=orgs["downtown"].pk)
    assert ExchangeRate.objects.for_actor(outsider).count() == 0
