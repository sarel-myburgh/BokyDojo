"""Holiday import sources — TODO 1.4.4, plan §12.2.

Providers are swappable so organisations can pull public holiday data from a
free API, upload their own CSV, or fall back to a small built-in list of
fixed-date Cambodian holidays.

All providers return ``HolidaySpec`` objects; ``import_holidays`` turns those
into ``Holiday`` rows. Importing never creates ``ClosurePeriod`` rows — closures
are created later by ``HolidayObservance.apply()``.
"""

from __future__ import annotations

import csv
import datetime
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .models import Holiday

if TYPE_CHECKING:
    from apps.identity.models import Organization


class HolidayImportError(Exception):
    """Raised when a provider cannot return usable holiday data."""


@dataclass
class HolidaySpec:
    """A single holiday as returned by a provider, before it becomes a model."""

    name: str
    date: datetime.date
    country: str
    external_id: str
    is_recurring_annually: bool


class HolidayProvider(Protocol):
    """Small interface for holiday sources."""

    source: str

    def fetch(self, organization: Organization, country: str, year: int) -> list[HolidaySpec]: ...


class BaseProvider:
    """Convenience base for providers."""

    source: str = ""

    def fetch(self, organization: Organization, country: str, year: int) -> list[HolidaySpec]:
        raise NotImplementedError


class NagerDateProvider(BaseProvider):
    """Fetches public holidays from https://date.nager.at.

    The network call is injectable via ``fetch`` so tests can mock it without
    touching the real API.
    """

    source = Holiday.Source.IMPORTED

    def __init__(self, fetch=None):
        self._fetch = fetch or self._default_fetch

    @staticmethod
    def _default_fetch(url: str):
        import urllib.request
        from urllib.parse import urlsplit

        target = urlsplit(url)
        if target.scheme != "https" or target.hostname != "date.nager.at":
            raise HolidayImportError("Holiday provider refused an untrusted URL")
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "BokyDojo/0.1"},
        )
        # The HTTPS scheme and exact host are checked above; redirects remain
        # subject to urllib's HTTP(S)-only redirect handler.
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            return json.loads(response.read())

    def fetch(self, organization: Organization, country: str, year: int) -> list[HolidaySpec]:
        url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
        try:
            data = self._fetch(url)
        except Exception as exc:
            raise HolidayImportError(
                f"Failed to fetch holidays for {country}/{year}: {exc}"
            ) from exc

        if not isinstance(data, list):
            raise HolidayImportError(
                f"Unexpected response format for {country}/{year}: expected a list"
            )

        specs: list[HolidaySpec] = []
        for item in data:
            if not isinstance(item, dict):
                raise HolidayImportError(f"Unexpected holiday item format for {country}/{year}")
            name = item.get("name")
            date_str = item.get("date")
            if not name or not date_str:
                raise HolidayImportError(f"Holiday missing name or date for {country}/{year}")
            try:
                holiday_date = datetime.date.fromisoformat(date_str)
            except ValueError as exc:
                raise HolidayImportError(f"Invalid date {date_str!r} for {country}/{year}") from exc
            specs.append(
                HolidaySpec(
                    name=name,
                    date=holiday_date,
                    country=country,
                    external_id=item.get("code", "") or "",
                    is_recurring_annually=bool(item.get("fixed")),
                )
            )
        return specs


class CsvProvider(BaseProvider):
    """Reads holidays from a CSV with columns ``name,date,external_id``."""

    source = Holiday.Source.IMPORTED

    def __init__(self, file):
        self.file = file

    def fetch(self, organization: Organization, country: str, year: int) -> list[HolidaySpec]:
        specs: list[HolidaySpec] = []
        reader = csv.DictReader(self.file)
        if reader.fieldnames is None:
            raise HolidayImportError("CSV file is empty or has no header")

        for row in reader:
            name = (row.get("name") or "").strip()
            date_str = (row.get("date") or "").strip()
            external_id = (row.get("external_id") or "").strip()
            if not name or not date_str:
                continue
            try:
                holiday_date = datetime.date.fromisoformat(date_str)
            except ValueError as exc:
                raise HolidayImportError(f"Invalid date {date_str!r} in CSV") from exc
            specs.append(
                HolidaySpec(
                    name=name,
                    date=holiday_date,
                    country=country,
                    external_id=external_id,
                    is_recurring_annually=False,
                )
            )
        return specs


class BuiltinProvider(BaseProvider):
    """Small built-in fallback of Cambodian fixed-date holidays only.

    Lunar holidays such as Khmer New Year, Pchum Ben and the Water Festival
    are deliberately not included because their dates move every year.
    """

    source = Holiday.Source.BUILTIN

    _KH_FIXED_HOLIDAYS: list[tuple[int, int, str]] = [
        (1, 1, "International New Year"),
        (1, 7, "Victory over Genocide Day"),
        (3, 8, "International Women's Day"),
        (5, 1, "International Labour Day"),
        (9, 24, "Constitution Day"),
        (10, 29, "Coronation Day"),
        (11, 9, "Independence Day"),
    ]

    def fetch(self, organization: Organization, country: str, year: int) -> list[HolidaySpec]:
        if country != "KH":
            return []
        return [
            HolidaySpec(
                name=name,
                date=datetime.date(year, month, day),
                country=country,
                external_id="",
                is_recurring_annually=True,
            )
            for month, day, name in self._KH_FIXED_HOLIDAYS
        ]


def import_holidays(
    organization: Organization, country: str, year: int, *, provider: HolidayProvider | None = None
) -> list[Holiday]:
    """Create or update ``Holiday`` rows from a provider.

    Idempotent: repeated calls for the same organisation, country and year do
    not duplicate rows. Matching is by ``(organization, date, name)``.

    This function never creates ``ClosurePeriod`` rows.
    """
    if provider is None:
        provider = BuiltinProvider()

    specs = provider.fetch(organization, country, year)
    results: list[Holiday] = []

    holidays_qs = Holiday.objects.for_organization(organization.pk)

    for spec in specs:
        holiday = None
        if spec.external_id:
            holiday = holidays_qs.filter(external_id=spec.external_id).first()
        if holiday is None:
            holiday, created = holidays_qs.get_or_create(
                organization=organization,
                date=spec.date,
                name=spec.name,
                defaults={
                    "country": spec.country,
                    "source": provider.source,
                    "external_id": spec.external_id,
                    "is_recurring_annually": spec.is_recurring_annually,
                },
            )
        else:
            created = False

        if not created:
            holiday.source = provider.source
            holiday.country = spec.country
            holiday.is_recurring_annually = spec.is_recurring_annually
            holiday.name = spec.name
            holiday.date = spec.date
            if spec.external_id:
                holiday.external_id = spec.external_id
            holiday.save(
                update_fields=[
                    "source",
                    "country",
                    "is_recurring_annually",
                    "name",
                    "date",
                    "external_id",
                    "updated_at",
                ]
            )
        results.append(holiday)

    return results
