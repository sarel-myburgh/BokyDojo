"""Schema guard for persisted student-directory filters."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

STUDENT_FILTER_KEYS = frozenset(
    {
        "q",
        "dojo",
        "rank",
        "status",
        "age_min",
        "age_max",
        "attendance_gap",
        "unsigned_waiver",
        "expired_licence",
    }
)
MAX_SAVED_FILTER_VALUE_LENGTH = 200


def validate_saved_student_filters(value) -> None:
    """Reject executable, nested, oversized, or unknown saved-filter data."""
    if not isinstance(value, dict):
        raise ValidationError(_("Saved filters must be an object."))
    unknown = set(value) - STUDENT_FILTER_KEYS
    if unknown:
        raise ValidationError(_("Saved filters contain unknown fields."))
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValidationError(_("Saved filter values must be text."))
        if len(item) > MAX_SAVED_FILTER_VALUE_LENGTH:
            raise ValidationError(_("A saved filter value is too long."))
