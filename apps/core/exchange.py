"""Currency conversion — plan §6, §13.7.

⚠ **The rate is a business decision, not a market lookup.**

In Cambodia every business quotes its own USD/KHR rate. One dojo bills at
4000:1, the one down the road at 4100:1, and both are correct for their own
invoices. A parent handed a receipt converted at a rate their dojo never agreed
to will — rightly — dispute it.

So this module never guesses. There is no default rate, no fallback to a market
feed, no "close enough". If an organisation has not stated its rate, conversion
raises. A missing rate is a question for the dojo owner, not something software
should answer on their behalf.

Rates are **effective-dated** and never edited in place: changing the rate
creates a new row. Historic invoices must keep converting at the rate that
applied when they were issued, or last year's accounts change every time
somebody adjusts a setting.
"""

from __future__ import annotations

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .ids import uuid7
from .managers import ScopedManager
from .money import Money, exponent_for


class NoExchangeRate(LookupError):
    """No rate is configured for this pair. Deliberately fatal — see module docs."""


class ExchangeRate(models.Model):
    """One organisation's stated rate between two currencies, from a given date.

    Append-only by convention: to change a rate, add a row with a later
    ``effective_from``. The old row keeps historic invoices honest.
    """

    class Source(models.TextChoices):
        #: The dojo's own posted rate. The normal case here.
        MANUAL = "manual", _("Set by the organisation")
        #: Pulled from a feed, if one is ever wired up.
        FEED = "feed", _("Imported from a rate feed")
        #: Taken from a bank or payment gateway settlement.
        SETTLEMENT = "settlement", _("From a payment settlement")

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    organization = models.ForeignKey(
        "identity.Organization", on_delete=models.CASCADE, related_name="exchange_rates"
    )

    base_currency = models.CharField(_("from"), max_length=3)
    quote_currency = models.CharField(_("to"), max_length=3)
    #: How many units of quote_currency one unit of base_currency buys.
    #: USD→KHR at 4100:1 is stored as 4100.
    rate = models.DecimalField(_("rate"), max_digits=20, decimal_places=8)

    effective_from = models.DateField(_("effective from"), default=datetime.date.today)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    note = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.ForeignKey(
        "identity.Person", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    tenant_org_path = "organization_id"
    objects = ScopedManager()

    class Meta:
        verbose_name = _("exchange rate")
        verbose_name_plural = _("exchange rates")
        ordering = ("-effective_from",)
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "base_currency", "quote_currency", "effective_from"],
                name="unique_rate_per_pair_and_date",
            ),
            models.CheckConstraint(condition=models.Q(rate__gt=0), name="rate_is_positive"),
        ]
        indexes = [
            models.Index(fields=["organization", "base_currency", "quote_currency", "-effective_from"])
        ]

    def __str__(self) -> str:
        return f"1 {self.base_currency} = {self.rate} {self.quote_currency} from {self.effective_from}"

    def save(self, *args, **kwargs):
        self.base_currency = (self.base_currency or "").upper()
        self.quote_currency = (self.quote_currency or "").upper()
        exponent_for(self.base_currency)
        exponent_for(self.quote_currency)
        if self.base_currency == self.quote_currency:
            raise ValidationError({"quote_currency": _("A currency cannot convert to itself.")})
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.base_currency.upper() == self.quote_currency.upper():
            raise ValidationError({"quote_currency": _("A currency cannot convert to itself.")})

    @classmethod
    def tenant_scope_q(cls, actor):
        return models.Q(organization_id=actor.organization_id)


def rate_for(
    organization_id,
    base: str,
    quote: str,
    *,
    on_date: datetime.date | None = None,
) -> Decimal:
    """The organisation's rate for this pair on this date.

    Falls back to the inverse of the opposite pair if only that is configured,
    so an organisation storing USD→KHR does not also have to store KHR→USD.
    Raises ``NoExchangeRate`` if neither exists — never guesses.
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return Decimal(1)

    on_date = on_date or timezone.localdate()

    def _lookup(from_currency: str, to_currency: str):
        return (
            ExchangeRate.objects.for_organization(organization_id)
            .filter(
                base_currency=from_currency,
                quote_currency=to_currency,
                effective_from__lte=on_date,
            )
            .order_by("-effective_from")
            .first()
        )

    direct = _lookup(base, quote)
    if direct is not None:
        return direct.rate

    inverse = _lookup(quote, base)
    if inverse is not None:
        return Decimal(1) / inverse.rate

    raise NoExchangeRate(
        f"No {base}→{quote} rate configured for this organisation as at {on_date}. "
        f"Rates are set by the dojo, not looked up — add one before converting."
    )


def convert(
    amount: Money,
    to_currency: str,
    *,
    organization_id,
    on_date: datetime.date | None = None,
    rounding=ROUND_HALF_UP,
) -> Money:
    """Convert using the organisation's own stated rate.

    Rounds to the target currency's minor unit. USD→KHR lands on whole riel
    because KHR has no minor unit; KHR→USD lands on cents.
    """
    to_currency = to_currency.upper()
    if amount.currency == to_currency:
        return amount

    rate = rate_for(
        organization_id, amount.currency, to_currency, on_date=on_date
    )

    source_exponent = exponent_for(amount.currency)
    target_exponent = exponent_for(to_currency)

    major = Decimal(amount.minor_units).scaleb(-source_exponent)
    converted_major = major * rate
    minor = (converted_major.scaleb(target_exponent)).quantize(Decimal(1), rounding=rounding)

    return Money(int(minor), to_currency)


def set_rate(
    organization,
    base: str,
    quote: str,
    rate,
    *,
    effective_from: datetime.date | None = None,
    source: str = ExchangeRate.Source.MANUAL,
    note: str = "",
    actor=None,
) -> ExchangeRate:
    """Record a rate. Changing a rate adds a row; it never edits an old one."""
    return ExchangeRate.objects.create(
        organization=organization,
        base_currency=base.upper(),
        quote_currency=quote.upper(),
        rate=Decimal(str(rate)),
        effective_from=effective_from or timezone.localdate(),
        source=source,
        note=note,
        created_by_id=getattr(actor, "person_id", None),
    )
