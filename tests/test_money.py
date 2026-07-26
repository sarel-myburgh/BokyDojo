"""Money value type — TODO 0.3.6."""

from decimal import Decimal

import pytest

from apps.core.money import CurrencyMismatch, Money


def test_construction_from_decimal_usd():
    assert Money.from_decimal("12.34", "USD") == Money(1234, "USD")


def test_construction_from_decimal_khr_has_no_minor_units():
    assert Money.from_decimal("4000", "KHR") == Money(4000, "KHR")


def test_rounding_is_half_up():
    assert Money.from_decimal("0.005", "USD").minor_units == 1
    assert Money.from_decimal("0.004", "USD").minor_units == 0


def test_to_decimal_roundtrip():
    assert Money(1234, "USD").to_decimal() == Decimal("12.34")


def test_float_is_rejected():
    """Floats are how money quietly goes wrong. They are refused outright."""
    with pytest.raises(TypeError):
        Money(12.34, "USD")


def test_bool_is_rejected_as_minor_units():
    with pytest.raises(TypeError):
        Money(True, "USD")


def test_unknown_currency_rejected():
    with pytest.raises(ValueError):
        Money(100, "XYZ")


def test_currency_is_normalised_to_upper():
    assert Money(100, "usd").currency == "USD"


def test_addition_same_currency():
    assert Money(100, "USD") + Money(250, "USD") == Money(350, "USD")


def test_addition_across_currencies_raises():
    """KHR/USD dual pricing means a silent mixed-currency sum is a real bug."""
    with pytest.raises(CurrencyMismatch):
        Money(100, "USD") + Money(100, "KHR")


def test_subtraction_across_currencies_raises():
    with pytest.raises(CurrencyMismatch):
        Money(100, "USD") - Money(100, "KHR")


def test_comparison_across_currencies_raises():
    with pytest.raises(CurrencyMismatch):
        _ = Money(100, "USD") < Money(100, "KHR")


def test_multiplication_by_int():
    assert Money(150, "USD") * 3 == Money(450, "USD")
    assert 3 * Money(150, "USD") == Money(450, "USD")


def test_multiplication_by_float_rejected():
    with pytest.raises(TypeError):
        Money(150, "USD") * 1.5


def test_apply_rate_for_tax():
    assert Money(10000, "USD").apply_rate("0.10") == Money(1000, "USD")


def test_apply_rate_rounds_half_up():
    assert Money(101, "USD").apply_rate("0.5") == Money(51, "USD")


def test_str_formats_with_currency_exponent():
    assert str(Money(1234, "USD")) == "12.34 USD"
    assert str(Money(4000, "KHR")) == "4000 KHR"


def test_negative_and_zero_helpers():
    assert Money.zero("USD").is_zero
    assert (-Money(100, "USD")).is_negative
