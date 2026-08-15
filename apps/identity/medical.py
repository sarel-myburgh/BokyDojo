"""Permission-checked access to encrypted student medical data — TODO 1.1.2."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.scoping import Actor

from .models import GovernanceModel, StudentProfile
from .permissions import Action, require

TEXT_FIELDS = ("medical_notes", "allergies", "conditions", "medications")
MEDICAL_FIELDS = (*TEXT_FIELDS, "doctor_contact", "do_not_spar")
MAX_TEXT_LENGTH = 10_000
MAX_DOCTOR_CONTACT_LENGTH = 255


@dataclass(frozen=True)
class MedicalDetails:
    medical_notes: str
    allergies: str
    conditions: str
    medications: str
    doctor_contact: str
    do_not_spar: bool


def _governance(profile: StudentProfile) -> str:
    return profile.person.organization.governance_model or GovernanceModel.CENTRAL


def _audit(action: str, profile: StudentProfile, actor: Actor, *, fields=()) -> None:
    note = f"fields: {', '.join(sorted(fields))}" if fields else ""
    audit.record(
        action,
        actor=actor,
        subject=profile,
        note=note,
        strict=True,
    )


def view_medical(*, profile: StudentProfile, actor: Actor) -> MedicalDetails:
    """Return medical data only after permission and strict access logging."""
    require(actor, Action.MEDICAL_VIEW, profile, governance_model=_governance(profile))
    _audit("view_medical", profile, actor)
    return MedicalDetails(**{field: getattr(profile, field) for field in MEDICAL_FIELDS})


def view_do_not_spar(*, profile: StudentProfile, actor: Actor) -> bool:
    """Read only the operational sparring restriction, without decrypting medical text."""
    require(actor, Action.MEDICAL_VIEW, profile, governance_model=_governance(profile))
    _audit("view_medical", profile, actor, fields=("do_not_spar",))
    return profile.do_not_spar


def _validated(changes: dict) -> dict:
    unknown = set(changes) - set(MEDICAL_FIELDS)
    if unknown:
        raise ValidationError({field: _("Unknown medical field.") for field in sorted(unknown)})

    cleaned = {}
    for field, value in changes.items():
        if field == "do_not_spar":
            if not isinstance(value, bool):
                raise ValidationError({field: _("Must be true or false.")})
            cleaned[field] = value
            continue
        if not isinstance(value, str):
            raise ValidationError({field: _("Must be text.")})
        limit = MAX_DOCTOR_CONTACT_LENGTH if field == "doctor_contact" else MAX_TEXT_LENGTH
        if len(value) > limit:
            raise ValidationError(
                {field: _("Must be at most %(limit)s characters.") % {"limit": limit}}
            )
        cleaned[field] = value
    return cleaned


@transaction.atomic
def update_medical(*, profile: StudentProfile, changes: dict, actor: Actor) -> MedicalDetails:
    """Update named fields atomically without ever copying their values into audit logs."""
    require(actor, Action.MEDICAL_EDIT, profile, governance_model=_governance(profile))
    cleaned = _validated(changes)
    locked = (
        StudentProfile.objects.for_actor(actor)
        .select_for_update()
        .select_related("person", "person__organization", "home_dojo")
        .get(pk=profile.pk)
    )
    for field, value in cleaned.items():
        setattr(locked, field, value)
    if cleaned:
        locked.save(update_fields=[*cleaned, "updated_at"])
        _audit("update_medical", locked, actor, fields=cleaned)
    return MedicalDetails(**{field: getattr(locked, field) for field in MEDICAL_FIELDS})
