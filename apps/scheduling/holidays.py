"""Holiday import and observance helpers — TODO 1.4.4, plan §12.2.

The canonical sources live in ``apps.scheduling.providers``; this module
re-exports the import helper and provides a tiny observance convenience.

Public holidays are no longer hardcoded into closures. Importing creates
``Holiday`` rows only; a dojo decides whether to close via
``HolidayObservance.apply()``.
"""

from __future__ import annotations

from .models import Holiday, HolidayObservance
from .providers import (
    BuiltinProvider,
    CsvProvider,
    HolidayImportError,
    HolidayProvider,
    HolidaySpec,
    NagerDateProvider,
    import_holidays,
)

__all__ = [
    "BuiltinProvider",
    "CsvProvider",
    "Holiday",
    "HolidayImportError",
    "HolidayObservance",
    "HolidayProvider",
    "HolidaySpec",
    "NagerDateProvider",
    "import_holidays",
    "set_holiday_observance",
]


def set_holiday_observance(
    holiday: Holiday,
    dojo,
    observance: HolidayObservance.Observance,
    *,
    note: str = "",
    apply: bool = True,
) -> HolidayObservance:
    """Create or update a dojo's decision for a holiday."""
    instance, _ = HolidayObservance.objects.for_organization(
        holiday.organization_id
    ).update_or_create(
        holiday=holiday,
        dojo=dojo,
        defaults={
            "observance": observance,
            "note": note,
        },
    )
    if apply:
        instance.apply()
    return instance
