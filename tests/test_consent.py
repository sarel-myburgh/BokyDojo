"""Versioned, append-only consent evidence — TODO 1.1.6 / SEC 6.5."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from apps.core.encryption import looks_encrypted
from apps.core.models import AuditLog, Document
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.consent import current_consent, record_consent
from apps.identity.models import (
    ConsentPolicy,
    ConsentRecord,
    Dojo,
    GuardianLink,
    Organization,
    Person,
    Role,
    ScopeType,
    StudentProfile,
)
from apps.identity.permissions import PermissionDenied

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    today = timezone.localdate()
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Consent Org", slug="consent-org")
        dojo = Dojo.objects.create(organization=org, name="Consent Dojo", slug="consent-dojo")
        child = Person.objects.create(
            organization=org,
            given_name="Soriya",
            family_name="Child",
            date_of_birth=today.replace(year=today.year - 10),
        )
        StudentProfile.objects.create(person=child, home_dojo=dojo)
        adult = Person.objects.create(
            organization=org,
            given_name="Maly",
            family_name="Adult",
            date_of_birth=today.replace(year=today.year - 25),
        )
        StudentProfile.objects.create(person=adult, home_dojo=dojo)
        parent = Person.objects.create(organization=org, given_name="Davy", family_name="Parent")
        GuardianLink.objects.create(
            student=child,
            guardian=parent,
            relationship=GuardianLink.Relationship.MOTHER,
            has_custody=True,
        )
        staff = Person.objects.create(organization=org, given_name="Head", family_name="Instructor")
        waiver = Document.objects.create(
            organization=org,
            subject_person=child,
            uploaded_by=staff,
            kind=Document.Kind.WAIVER,
            original_filename="waiver-v3.pdf",
            storage_key="documents/waiver-v3.pdf",
            content_type="application/pdf",
            byte_size=100,
            checksum="a" * 64,
        )
        waiver_policy = ConsentPolicy.objects.create(
            organization=org,
            consent_type=ConsentRecord.Type.WAIVER,
            version="waiver-v3",
            title="Training waiver",
            body="Exact waiver version three.",
            document=waiver,
        )
    return {
        "org": org,
        "dojo": dojo,
        "child": child,
        "adult": adult,
        "parent": parent,
        "staff": staff,
        "waiver": waiver,
        "waiver_policy": waiver_policy,
    }


def staff_actor(world, role=Role.DOJO_ADMIN):
    return Actor(
        user_id=None,
        person_id=world["staff"].pk,
        organization_id=world["org"].pk,
        dojo_ids=frozenset({world["dojo"].pk}),
        roles=frozenset({(role, ScopeType.DOJO, world["dojo"].pk)}),
    )


def parent_decision(world, *, consent_type=ConsentRecord.Type.PHOTO, granted=True, **kwargs):
    values = {
        "person": world["child"],
        "consent_type": consent_type,
        "version": "v1",
        "granted": granted,
        "granted_by": world["parent"],
        "capacity": ConsentRecord.Capacity.PARENT,
        "ip_address": "203.0.113.10",
        "actor": staff_actor(world),
        "minimum_self_consent_age": 16,
        "signature_name": "Davy Parent",
        "user_agent": "Consent test browser",
    }
    values.update(kwargs)
    return record_consent(**values)


def test_parental_consent_records_version_capacity_timestamp_and_encrypted_signature(world):
    record = parent_decision(world)

    assert record.granted is True
    assert record.version == "v1"
    assert record.capacity == ConsentRecord.Capacity.PARENT
    assert record.granted_at is not None
    assert record.ip_address == "203.0.113.10"
    assert record.signature_name == "Davy Parent"

    with connection.cursor() as cursor:
        cursor.execute("SELECT signature_name FROM identity_consentrecord")
        stored = cursor.fetchone()[0]
    assert looks_encrypted(stored)
    assert "Davy" not in stored


def test_revocation_is_a_new_record_linked_to_the_prior_grant(world):
    granted = parent_decision(world)
    revoked = parent_decision(world, granted=False)

    assert revoked.pk != granted.pk
    assert revoked.supersedes_id == granted.pk
    assert revoked.granted is False
    latest = current_consent(
        person=world["child"],
        consent_type=ConsentRecord.Type.PHOTO,
        version="v1",
        actor=staff_actor(world),
    )
    assert latest.pk == revoked.pk
    assert ConsentRecord.objects.for_organization(world["org"].pk).count() == 2
    assert AuditLog.objects.filter(action="record_consent").count() == 2
    assert AuditLog.objects.filter(action="view_consent").count() == 1


def test_consent_records_cannot_be_rewritten_deleted_or_bulk_bypassed(world):
    record = parent_decision(world)
    record.granted = False

    with pytest.raises(NotImplementedError, match="append-only"):
        record.save()
    with pytest.raises(NotImplementedError, match="append-only"):
        record.delete()
    queryset = ConsentRecord.objects.for_organization(world["org"].pk).filter(pk=record.pk)
    with pytest.raises(NotImplementedError, match="append-only"):
        queryset.update(granted=False)
    with pytest.raises(NotImplementedError, match="cannot be deleted"):
        queryset.delete()
    with pytest.raises(NotImplementedError, match="consent service"):
        ConsentRecord.objects.for_organization(world["org"].pk).bulk_create([])


def test_minor_cannot_self_consent_and_non_custodial_adult_cannot_sign(world):
    with pytest.raises(ValidationError, match="too young"):
        record_consent(
            person=world["child"],
            consent_type=ConsentRecord.Type.PHOTO,
            version="v1",
            granted=True,
            granted_by=world["child"],
            capacity=ConsentRecord.Capacity.SELF,
            ip_address="203.0.113.10",
            actor=staff_actor(world),
            minimum_self_consent_age=16,
            signature_name="Soriya Child",
        )

    with allow_unscoped("test setup"):
        stranger = Person.objects.create(
            organization=world["org"], given_name="Not", family_name="Guardian"
        )
    with pytest.raises(ValidationError, match="custodial guardian"):
        parent_decision(world, granted_by=stranger, signature_name="Not Guardian")


def test_adult_can_sign_for_themselves_at_the_explicit_configured_age(world):
    record = record_consent(
        person=world["adult"],
        consent_type=ConsentRecord.Type.DATA_PROCESSING,
        version="privacy-2",
        granted=True,
        granted_by=world["adult"],
        capacity=ConsentRecord.Capacity.SELF,
        ip_address="2001:db8::10",
        actor=staff_actor(world),
        minimum_self_consent_age=16,
        signature_name="Maly Adult",
    )

    assert record.granted_by_id == world["adult"].pk
    assert record.capacity == ConsentRecord.Capacity.SELF


def test_medical_consent_requires_medical_permission_and_is_separate(world):
    front_desk = staff_actor(world, Role.FRONT_DESK)
    with pytest.raises(PermissionDenied):
        parent_decision(world, consent_type=ConsentRecord.Type.MEDICAL, actor=front_desk)

    medical = parent_decision(world, consent_type=ConsentRecord.Type.MEDICAL)
    data = parent_decision(world, consent_type=ConsentRecord.Type.DATA_PROCESSING)
    assert medical.pk != data.pk
    with pytest.raises(PermissionDenied):
        current_consent(
            person=world["child"],
            consent_type=ConsentRecord.Type.MEDICAL,
            version="v1",
            actor=front_desk,
        )


def test_waiver_requires_its_versioned_document_and_signature(world):
    with pytest.raises(ValidationError, match="waiver document"):
        parent_decision(world, consent_type=ConsentRecord.Type.WAIVER)
    with pytest.raises(ValidationError, match="enter their name"):
        parent_decision(world, signature_name="")

    waiver = parent_decision(
        world,
        consent_type=ConsentRecord.Type.WAIVER,
        version="waiver-v3",
        policy=world["waiver_policy"],
    )
    assert waiver.document_id == world["waiver"].pk
    assert waiver.version == "waiver-v3"
    assert waiver.policy_id == world["waiver_policy"].pk


def test_revoking_absent_or_already_revoked_consent_is_rejected(world):
    with pytest.raises(ValidationError, match="no prior consent"):
        parent_decision(world, granted=False)
    parent_decision(world)
    parent_decision(world, granted=False)
    with pytest.raises(ValidationError, match="no prior consent"):
        parent_decision(world, granted=False)


def test_cross_tenant_signer_is_rejected_by_model_integrity(world):
    with allow_unscoped("test setup"):
        other = Organization.objects.create(name="Other Consent", slug="other-consent")
        outsider = Person.objects.create(
            organization=other, given_name="Outside", family_name="Signer"
        )
    record = ConsentRecord(
        person=world["child"],
        consent_type=ConsentRecord.Type.PHOTO,
        version="v1",
        granted=True,
        granted_by=outsider,
        capacity=ConsentRecord.Capacity.GUARDIAN,
        ip_address="203.0.113.10",
        signature_name="Outside Signer",
    )

    with pytest.raises(ValidationError, match="different organisation"):
        record.save()


def test_invalid_age_policy_and_ip_are_rejected(world):
    with pytest.raises(ValidationError, match="between 13 and 18"):
        parent_decision(world, minimum_self_consent_age=12)
    with pytest.raises(ValidationError, match="IP address"):
        parent_decision(world, ip_address="")


def test_published_policy_content_is_immutable_but_may_be_retired(world):
    policy = world["waiver_policy"]
    policy.body = "Replacement wording"

    with pytest.raises(NotImplementedError, match="immutable"):
        policy.save()
    with pytest.raises(NotImplementedError, match="immutable"):
        ConsentPolicy.objects.for_organization(world["org"].pk).filter(pk=policy.pk).update(
            body="Replacement wording"
        )
    with pytest.raises(NotImplementedError, match="cannot be deleted"):
        policy.delete()
    with pytest.raises(NotImplementedError, match="cannot be deleted"):
        ConsentPolicy.objects.for_organization(world["org"].pk).filter(pk=policy.pk).delete()

    policy.refresh_from_db()
    policy.is_active = False
    policy.save(update_fields=["is_active", "updated_at"])
    policy.refresh_from_db()
    assert policy.is_active is False
    assert policy.body == "Exact waiver version three."


def test_consent_policy_rejects_a_document_from_another_organisation(world):
    with allow_unscoped("test setup"):
        other = Organization.objects.create(name="Other Policy", slug="other-policy")
        foreign_document = Document.objects.create(
            organization=other,
            kind=Document.Kind.WAIVER,
            original_filename="foreign.pdf",
            storage_key="documents/foreign.pdf",
            content_type="application/pdf",
            byte_size=10,
            checksum="b" * 64,
        )
        policy = ConsentPolicy(
            organization=world["org"],
            consent_type=ConsentRecord.Type.WAIVER,
            version="foreign-document",
            title="Foreign document",
            document=foreign_document,
        )

    with pytest.raises(ValidationError, match="different organisation"):
        policy.save()
