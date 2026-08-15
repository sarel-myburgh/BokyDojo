"""Audited management of multiple independently contactable guardians."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.scoping import Actor

from .models import GovernanceModel, GuardianLink, Person, StudentProfile
from .permissions import Action, can, require

CONTACT_FIELDS = ("given_name", "family_name", "email", "phone")
LINK_FIELDS = (
    "relationship",
    "is_primary_contact",
    "is_emergency_contact",
    "is_financially_responsible",
    "has_custody",
    "notes",
)


def _governance(profile: StudentProfile) -> str:
    return profile.person.organization.governance_model or GovernanceModel.CENTRAL


def guardian_candidates(actor: Actor):
    """Existing guardians already attached to students visible in this actor's scope."""
    ids = GuardianLink.objects.for_actor(actor).values_list("guardian_id", flat=True)
    return Person.objects.for_organization(actor.organization_id).filter(pk__in=ids).distinct()


def _clean_contact(values: dict) -> dict:
    cleaned = {field: str(values.get(field, "") or "").strip() for field in CONTACT_FIELDS}
    if not cleaned["given_name"]:
        raise ValidationError({"given_name": _("Enter the guardian's given name.")})
    if not cleaned["email"] and not cleaned["phone"]:
        raise ValidationError({"email": _("Enter an email address, a phone number, or both.")})
    if cleaned["email"]:
        validate_email(cleaned["email"])
    return cleaned


def _may_edit_shared_guardian(actor: Actor, guardian: Person) -> bool:
    """Contact changes require edit rights over every child sharing this Person."""
    links = list(
        GuardianLink.objects.for_organization(guardian.organization_id)
        .filter(guardian=guardian)
        .select_related(
            "student",
            "student__organization",
            "student__student_profile",
            "student__student_profile__home_dojo",
        )
    )
    if not links:
        return False
    for link in links:
        try:
            target = link.student.student_profile
        except StudentProfile.DoesNotExist:
            return False
        if not can(actor, Action.PERSON_EDIT, target, governance_model=_governance(target)):
            return False
    return True


@transaction.atomic
def add_guardian(
    *,
    profile: StudentProfile,
    actor: Actor,
    link_values: dict,
    contact_values: dict | None = None,
    existing_guardian: Person | None = None,
) -> GuardianLink:
    require(actor, Action.PERSON_EDIT, profile, governance_model=_governance(profile))
    if len(str(link_values.get("notes", "") or "")) > 255:
        raise ValidationError({"notes": _("Notes must be at most 255 characters.")})

    if existing_guardian is not None:
        if existing_guardian.organization_id != profile.organization_id:
            raise ValidationError(
                {"existing_guardian": _("Guardian belongs to another organisation.")}
            )
        if not guardian_candidates(actor).filter(pk=existing_guardian.pk).exists():
            raise DjangoPermissionDenied
        guardian = existing_guardian
        if (
            GuardianLink.objects.for_organization(profile.organization_id)
            .filter(guardian=guardian, student=profile.person)
            .exists()
        ):
            raise ValidationError(
                {"existing_guardian": _("This guardian is already linked to the student.")}
            )
    else:
        cleaned = _clean_contact(contact_values or {})
        guardian = Person(organization=profile.person.organization, **cleaned)
        guardian.full_clean(validate_unique=False, validate_constraints=False)
        guardian.save()
        audit.record(
            "create",
            actor=actor,
            subject=guardian,
            note="guardian person created from student family record",
            strict=True,
        )

    link = GuardianLink(
        guardian=guardian,
        student=profile.person,
        created_by_id=actor.person_id,
        **{field: link_values.get(field, "") for field in LINK_FIELDS},
    )
    link.full_clean(validate_unique=False, validate_constraints=False)
    link.save()
    audit.record(
        "create",
        actor=actor,
        subject=link,
        note="guardian linked to student",
        strict=True,
    )
    return link


@transaction.atomic
def update_guardian(
    *,
    link: GuardianLink,
    profile: StudentProfile,
    actor: Actor,
    link_values: dict,
    contact_values: dict,
) -> GuardianLink:
    require(actor, Action.PERSON_EDIT, profile, governance_model=_governance(profile))
    if len(str(link_values.get("notes", "") or "")) > 255:
        raise ValidationError({"notes": _("Notes must be at most 255 characters.")})
    locked = (
        GuardianLink.objects.for_actor(actor)
        .select_for_update()
        .select_related("guardian", "student", "student__organization")
        .get(pk=link.pk, student=profile.person)
    )
    guardian = (
        Person.objects.for_organization(profile.organization_id)
        .select_for_update()
        .get(pk=locked.guardian_id)
    )
    cleaned_contact = _clean_contact(contact_values)
    changed_contact = [
        field for field in CONTACT_FIELDS if getattr(guardian, field) != cleaned_contact[field]
    ]
    if changed_contact and not _may_edit_shared_guardian(actor, guardian):
        raise DjangoPermissionDenied

    for field, value in cleaned_contact.items():
        setattr(guardian, field, value)
    guardian.full_clean(validate_unique=False, validate_constraints=False)
    if changed_contact:
        guardian.save(update_fields=[*changed_contact, "updated_at"])
        audit.record(
            "update",
            actor=actor,
            subject=guardian,
            note=f"guardian contact fields updated: {', '.join(changed_contact)}",
            strict=True,
        )

    before = audit.snapshot(locked, [field for field in LINK_FIELDS if field != "notes"])
    for field in LINK_FIELDS:
        setattr(locked, field, link_values.get(field, ""))
    locked.full_clean(validate_unique=False, validate_constraints=False)
    locked.save(update_fields=[*LINK_FIELDS, "updated_at"])
    audit.record(
        "update",
        actor=actor,
        subject=locked,
        before=before,
        after=audit.snapshot(locked, [field for field in LINK_FIELDS if field != "notes"]),
        note="guardian relationship settings updated",
        strict=True,
    )
    return locked


@transaction.atomic
def remove_guardian(*, link: GuardianLink, profile: StudentProfile, actor: Actor) -> None:
    require(actor, Action.PERSON_EDIT, profile, governance_model=_governance(profile))
    locked = (
        GuardianLink.objects.for_actor(actor)
        .select_for_update()
        .get(pk=link.pk, student=profile.person)
    )
    audit.record(
        "delete",
        actor=actor,
        subject=locked,
        note="guardian unlinked from student; Person retained",
        strict=True,
    )
    locked.delete()
