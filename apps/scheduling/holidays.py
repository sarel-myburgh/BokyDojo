"""Public holiday seeding for closure periods — TODO 1.4.4, plan §12.2.

Holiday data is keyed by country code so adding another country's set does not
require changing the seeder logic. Cambodia's lunar/variable holidays are kept
as an explicit year-by-year table: only years whose dates have been confirmed
from an official source are populated. For years not in the table the variable
holidays are skipped rather than guessed.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from .models import ClosurePeriod

if TYPE_CHECKING:
    from apps.identity.models import Organization


#: Holiday reasons are data values stored in the database, not UI labels, so they
#: are plain strings. Translating them is a display concern.
_HOLIDAY_DATA: dict[str, dict] = {
    "KH": {
        "fixed": [
            {"month": 1, "day": 1, "reason": "International New Year"},
            {"month": 1, "day": 7, "reason": "Victory over Genocide Day"},
            {"month": 3, "day": 8, "reason": "International Women's Day"},
            {"month": 5, "day": 1, "reason": "International Labour Day"},
            {"month": 9, "day": 24, "reason": "Constitution Day"},
            {"month": 10, "day": 29, "reason": "Coronation Day"},
            {"month": 11, "day": 9, "reason": "Independence Day"},
        ],
        # Lunar / variable holidays: only years with confirmed official dates.
        "variable": [
            {
                "reason": "Khmer New Year",
                "dates": {
                    2024: [(4, 13), (4, 14), (4, 15)],
                    2025: [(4, 14), (4, 15), (4, 16)],
                    2026: [(4, 14), (4, 15), (4, 16)],
                },
            },
            {
                "reason": "Royal Ploughing Ceremony",
                "dates": {
                    2024: [(5, 26)],
                    2025: [(5, 15)],
                    2026: [(5, 5)],
                },
            },
            {
                "reason": "Pchum Ben",
                "dates": {
                    2024: [(10, 1), (10, 2), (10, 3)],
                    2025: [(9, 21), (9, 22), (9, 23)],
                    2026: [(10, 10), (10, 11), (10, 12)],
                },
            },
            {
                "reason": "Water Festival",
                "dates": {
                    2024: [(11, 14), (11, 15), (11, 16)],
                    2025: [(11, 4), (11, 5), (11, 6)],
                    2026: [(11, 23), (11, 24), (11, 25)],
                },
            },
        ],
    }
}


def seed_public_holidays(organization: Organization, year: int) -> list[ClosurePeriod]:
    """Create org-wide closure periods for an organisation's public holidays.

    Idempotent: repeated calls for the same organisation and year return the
    existing rows without creating duplicates.
    """
    country = organization.country
    data = _HOLIDAY_DATA.get(country)
    if data is None:
        return []

    created: list[ClosurePeriod] = []
    org_id = organization.pk

    for item in data["fixed"]:
        date = datetime.date(year, item["month"], item["day"])
        closure, _ = ClosurePeriod.objects.for_organization(org_id).get_or_create(
            organization=organization,
            starts_on=date,
            ends_on=date,
            reason=item["reason"],
            defaults={"suppress_billing": False},
        )
        created.append(closure)

    for item in data["variable"]:
        dates = item["dates"].get(year)
        if dates is None:
            # Variable holidays for this year are not in the confirmed table;
            # skip them rather than computing a plausible but wrong date.
            continue
        for month, day in dates:
            date = datetime.date(year, month, day)
            closure, _ = ClosurePeriod.objects.for_organization(org_id).get_or_create(
                organization=organization,
                starts_on=date,
                ends_on=date,
                reason=item["reason"],
                defaults={"suppress_billing": False},
            )
            created.append(closure)

    return created
