"""Money value type — TODO 0.3.6, plan §6.

Money is always an integer count of minor units plus an explicit currency.
Never a float, never a bare number. Cambodia runs dual USD/KHR, so an amount
without a currency attached is meaningless here, not merely sloppy.

KHR has no minor unit in practice (exponent 0); USD has two. The exponent table
drives parsing, formatting and rounding.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

#: ISO 4217 minor-unit exponents.
#:
#: Anything not listed here defaults to 2, which is correct for the large
#: majority of world currencies — so an organisation can adopt a currency we
#: have never seen without a code change. Only the exceptions need naming.
DEFAULT_EXPONENT = 2

CURRENCY_EXPONENTS: dict[str, int] = {
    # Zero-decimal currencies. KHR is the one that matters here: riel has no
    # subdivision in practice, so 4000 KHR is four thousand riel, not forty.
    "KHR": 0,
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "IDR": 0,
    "LAK": 0,
    "MMK": 0,
    "CLP": 0,
    "ISK": 0,
    "PYG": 0,
    "RWF": 0,
    "UGX": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
    # Three-decimal currencies.
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
}

#: Currencies offered in the UI by default. Not a restriction — any valid
#: three-letter code is accepted.
COMMON_CURRENCIES: tuple[str, ...] = (
    "USD",
    "KHR",
    "THB",
    "VND",
    "SGD",
    "MYR",
    "EUR",
    "GBP",
    "AUD",
    "JPY",
    "CNY",
)


class CurrencyMismatch(TypeError):
    """Arithmetic was attempted between two different currencies."""


def exponent_for(currency: str) -> int:
    """Minor-unit exponent for a currency code.

    Unlisted codes get the two-decimal default rather than an error: refusing
    an unfamiliar currency would mean a code change every time an organisation
    outside our launch market signs up. The shape of the code is still checked.
    """
    code = (currency or "").upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError(
            f"Invalid currency {currency!r}: expected a three-letter ISO 4217 code"
        )
    return CURRENCY_EXPONENTS.get(code, DEFAULT_EXPONENT)


@dataclass(frozen=True, order=False)
class Money:
    """An exact monetary amount in minor units."""

    minor_units: int
    currency: str

    def __post_init__(self):
        if not isinstance(self.minor_units, int) or isinstance(self.minor_units, bool):
            raise TypeError("Money.minor_units must be an int (minor units, not a float)")
        object.__setattr__(self, "currency", self.currency.upper())
        exponent_for(self.currency)

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_decimal(cls, amount: Decimal | str | int, currency: str) -> Money:
        exponent = exponent_for(currency)
        quantum = Decimal(1).scaleb(-exponent)
        value = Decimal(str(amount)).quantize(quantum, rounding=ROUND_HALF_UP)
        return cls(int(value.scaleb(exponent)), currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    # -- conversion -----------------------------------------------------------

    def to_decimal(self) -> Decimal:
        return Decimal(self.minor_units).scaleb(-exponent_for(self.currency))

    def __str__(self) -> str:
        exponent = exponent_for(self.currency)
        return f"{self.to_decimal():.{exponent}f} {self.currency}"

    def __repr__(self) -> str:
        return f"Money({self.minor_units}, {self.currency!r})"

    # -- arithmetic -----------------------------------------------------------

    def _check(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"Cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(
                f"Cannot combine {self.currency} and {other.currency} without an "
                f"explicit exchange rate"
            )

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor_units, self.currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError(
                "Money can only be multiplied by an int. For percentages or rates use "
                "Money.apply_rate(), which states its rounding."
            )
        return Money(self.minor_units * factor, self.currency)

    __rmul__ = __mul__

    def apply_rate(self, rate: Decimal | str, *, rounding=ROUND_HALF_UP) -> Money:
        """Multiply by a non-integer rate (tax, discount) with explicit rounding."""
        value = (Decimal(self.minor_units) * Decimal(str(rate))).quantize(
            Decimal(1), rounding=rounding
        )
        return Money(int(value), self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units <= other.minor_units

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units > other.minor_units

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.minor_units >= other.minor_units

    @property
    def is_zero(self) -> bool:
        return self.minor_units == 0

    @property
    def is_negative(self) -> bool:
        return self.minor_units < 0
