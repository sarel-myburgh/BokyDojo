"""Consent-gated student photograph storage and selection — TODO 1.1.14."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.core.documents import may_read, store
from apps.core.models import Document
from apps.core.scoping import Actor
from apps.core.uploads import validate_upload

from .consent import current_consent
from .models import (
    ConsentPolicy,
    ConsentRecord,
    GovernanceModel,
    StudentProfile,
)
from .permissions import Action, require
from .visibility import is_org_scoped_only


def _governance(profile: StudentProfile) -> str:
    return profile.person.organization.governance_model or GovernanceModel.CENTRAL


def active_photo_policy(profile: StudentProfile) -> ConsentPolicy | None:
    return (
        ConsentPolicy.objects.for_organization(profile.organization_id)
        .filter(consent_type=ConsentRecord.Type.PHOTO, is_active=True)
        .first()
    )


def current_photo_consent(*, profile: StudentProfile, actor: Actor) -> ConsentRecord | None:
    """Return the exact-current photo decision, access-logged by the consent service."""
    policy = active_photo_policy(profile)
    if policy is None:
        return None
    return current_consent(
        person=profile.person,
        consent_type=ConsentRecord.Type.PHOTO,
        version=policy.version,
        actor=actor,
    )


def current_student_photo(
    *,
    profile: StudentProfile,
    actor: Actor,
    consent: ConsentRecord | None = None,
) -> Document | None:
    """Return the latest photo only while current consent remains granted."""
    governance = _governance(profile)
    require(actor, Action.PERSON_VIEW, profile, governance_model=governance)
    if governance == GovernanceModel.FEDERATED and is_org_scoped_only(actor):
        return None

    if consent is None:
        consent = current_photo_consent(profile=profile, actor=actor)
    if consent is None or not consent.granted:
        return None

    photo = (
        Document.objects.for_organization(profile.organization_id)
        .filter(subject_person=profile.person, kind=Document.Kind.PHOTO)
        .order_by("-created_at")
        .first()
    )
    if photo is None or not may_read(actor, photo, governance_model=governance):
        return None
    return photo


def upload_student_photo(*, profile: StudentProfile, uploaded_file, actor: Actor) -> Document:
    """Validate, re-encode, and store a photo only under current explicit consent."""
    governance = _governance(profile)
    require(actor, Action.PERSON_EDIT, profile, governance_model=governance)

    consent = current_photo_consent(profile=profile, actor=actor)
    if consent is None or not consent.granted:
        raise ValidationError(
            {
                "photo": _(
                    "Current explicit photo consent is required before uploading a photograph."
                )
            }
        )

    file_kind = validate_upload(uploaded_file)
    if not file_kind.is_image:
        raise ValidationError({"photo": _("Upload a JPEG, PNG, GIF, or WebP image.")})

    return store(
        uploaded_file,
        organization=profile.person.organization,
        kind=Document.Kind.PHOTO,
        actor=actor,
        subject_person=profile.person,
    )
