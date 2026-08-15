"""Versioned, revocable consent evidence — TODO 1.1.6 / SEC 6.5."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.models import Document
from apps.core.scoping import Actor

from .models import (
    ConsentPolicy,
    ConsentRecord,
    GovernanceModel,
    GuardianLink,
    Person,
    StudentProfile,
)
from .permissions import Action, require

MIN_SELF_CONSENT_AGE_FLOOR = 13
MIN_SELF_CONSENT_AGE_CEILING = 18


def _target(person: Person):
    try:
        return person.student_profile
    except StudentProfile.DoesNotExist:
        return person


def _governance(person: Person) -> str:
    return person.organization.governance_model or GovernanceModel.CENTRAL


def _require_record_permission(actor: Actor, person: Person, consent_type: str) -> None:
    action = (
        Action.MEDICAL_EDIT if consent_type == ConsentRecord.Type.MEDICAL else Action.PERSON_EDIT
    )
    require(actor, action, _target(person), governance_model=_governance(person))


def _verify_signer(
    *,
    person: Person,
    granted_by: Person,
    capacity: str,
    minimum_self_consent_age: int,
) -> None:
    if not MIN_SELF_CONSENT_AGE_FLOOR <= minimum_self_consent_age <= MIN_SELF_CONSENT_AGE_CEILING:
        raise ValidationError(
            {
                "minimum_self_consent_age": _(
                    "The configured self-consent age must be between 13 and 18."
                )
            }
        )
    if capacity == ConsentRecord.Capacity.SELF:
        if granted_by.pk != person.pk:
            raise ValidationError({"granted_by": _("Self-consent must be signed by the subject.")})
        if person.age is None or person.age < minimum_self_consent_age:
            raise ValidationError(
                {"capacity": _("This person is too young to consent for themselves.")}
            )
        return

    if capacity not in (ConsentRecord.Capacity.PARENT, ConsentRecord.Capacity.GUARDIAN):
        raise ValidationError({"capacity": _("Unsupported consent capacity.")})
    link = (
        GuardianLink.objects.for_organization(person.organization_id)
        .filter(student=person, guardian=granted_by, has_custody=True)
        .first()
    )
    if link is None:
        raise ValidationError({"granted_by": _("The signer is not a verified custodial guardian.")})
    if capacity == ConsentRecord.Capacity.PARENT and link.relationship not in (
        GuardianLink.Relationship.MOTHER,
        GuardianLink.Relationship.FATHER,
    ):
        raise ValidationError({"capacity": _("The signer is not recorded as a parent.")})


def _latest_consent(*, person: Person, consent_type: str, version: str) -> ConsentRecord | None:
    return (
        ConsentRecord.objects.for_organization(person.organization_id)
        .filter(person=person, consent_type=consent_type, version=version)
        .order_by("-granted_at", "-created_at")
        .first()
    )


def current_consent(
    *, person: Person, consent_type: str, version: str, actor: Actor
) -> ConsentRecord | None:
    """Permission-check and access-log the latest exact-version decision."""
    action = (
        Action.MEDICAL_VIEW if consent_type == ConsentRecord.Type.MEDICAL else Action.PERSON_VIEW
    )
    require(actor, action, _target(person), governance_model=_governance(person))
    record = _latest_consent(person=person, consent_type=consent_type, version=version)
    audit.record(
        "view_consent",
        actor=actor,
        subject=record,
        subject_type="identity.ConsentRecord" if record is None else "",
        subject_id=str(person.pk) if record is None else "",
        organization_id=person.organization_id,
        note=f"{consent_type} {version}",
        strict=True,
    )
    return record


@transaction.atomic
def record_consent(
    *,
    person: Person,
    consent_type: str,
    version: str,
    granted: bool,
    granted_by: Person,
    capacity: str,
    ip_address: str,
    actor: Actor,
    minimum_self_consent_age: int,
    signature_name: str,
    document: Document | None = None,
    policy: ConsentPolicy | None = None,
    user_agent: str = "",
) -> ConsentRecord:
    """Append one explicit consent or revocation decision and its evidence."""
    if consent_type not in ConsentRecord.Type.values:
        raise ValidationError({"consent_type": _("Unknown consent type.")})
    if policy is not None:
        if policy.organization_id != person.organization_id:
            raise ValidationError({"policy": _("The policy belongs to another organisation.")})
        if policy.consent_type != consent_type or policy.version != version:
            raise ValidationError({"policy": _("The policy type or version does not match.")})
        if granted and not policy.is_active:
            raise ValidationError({"policy": _("This policy version is no longer active.")})
        document = policy.document
    version = version.strip()
    signature_name = signature_name.strip()
    if not version or len(version) > 64:
        raise ValidationError({"version": _("A version of at most 64 characters is required.")})
    if not ip_address:
        raise ValidationError({"ip_address": _("An IP address is required.")})
    if not signature_name:
        raise ValidationError({"signature_name": _("The signer must enter their name.")})
    if len(signature_name) > 200:
        raise ValidationError({"signature_name": _("Signature name is too long.")})
    if consent_type == ConsentRecord.Type.WAIVER:
        if policy is None or (not policy.body.strip() and document is None):
            raise ValidationError({"document": _("A versioned waiver document is required.")})
        if document is not None and document.kind != Document.Kind.WAIVER:
            raise ValidationError({"document": _("A versioned waiver document is required.")})
    elif document is not None and document.kind == Document.Kind.WAIVER:
        raise ValidationError(
            {"document": _("A waiver document may only evidence waiver consent.")}
        )

    _require_record_permission(actor, person, consent_type)
    person = (
        Person.objects.for_organization(person.organization_id)
        .select_for_update()
        .select_related("organization")
        .get(pk=person.pk)
    )
    _verify_signer(
        person=person,
        granted_by=granted_by,
        capacity=capacity,
        minimum_self_consent_age=minimum_self_consent_age,
    )
    previous = _latest_consent(person=person, consent_type=consent_type, version=version)
    if not granted and (previous is None or not previous.granted):
        raise ValidationError({"granted": _("There is no prior consent to revoke.")})

    record = ConsentRecord(
        person=person,
        consent_type=consent_type,
        version=version,
        granted=granted,
        granted_at=timezone.now(),
        granted_by=granted_by,
        capacity=capacity,
        ip_address=ip_address,
        user_agent=user_agent[:512],
        signature_name=signature_name,
        document=document,
        policy=policy,
        supersedes=previous,
        created_by_id=actor.person_id,
    )
    record.save()
    audit.record(
        "record_consent",
        actor=actor,
        subject=record,
        ip_address=ip_address,
        user_agent=user_agent,
        note=f"{consent_type} {version}: {'granted' if granted else 'revoked'}",
        strict=True,
    )
    return record
