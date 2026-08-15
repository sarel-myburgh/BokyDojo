"""HTTP flows for separate medical consent and versioned waiver signing."""

from __future__ import annotations

import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.documents import store
from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    ConsentPolicy,
    ConsentRecord,
    Dojo,
    GuardianLink,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    User,
)

pytestmark = pytest.mark.django_db
PASSWORD = "correct-horse-battery"


def _staff(org, dojo, role, *, email):
    person = Person.objects.create(
        organization=org,
        given_name=role.replace("_", " ").title(),
        family_name="Tester",
    )
    RoleAssignment.objects.create(
        organization=org,
        person=person,
        role=role,
        scope_type=ScopeType.DOJO,
        dojo=dojo,
    )
    return User.objects.create_user(email=email, password=PASSWORD, person=person)


@pytest.fixture
def world():
    today = timezone.localdate()
    with allow_unscoped("consent view test setup"):
        org = Organization.objects.create(name="Consent Screens", slug="consent-screens")
        dojo_a = Dojo.objects.create(organization=org, name="Dojo A", slug="consent-a")
        dojo_b = Dojo.objects.create(organization=org, name="Dojo B", slug="consent-b")
        child = Person.objects.create(
            organization=org,
            given_name="Soriya",
            family_name="Child",
            date_of_birth=today - datetime.timedelta(days=3652),
        )
        profile = StudentProfile.objects.create(person=child, home_dojo=dojo_a)
        parent = Person.objects.create(organization=org, given_name="Davy", family_name="Parent")
        GuardianLink.objects.create(
            student=child,
            guardian=parent,
            relationship=GuardianLink.Relationship.MOTHER,
            has_custody=True,
        )
        medical = ConsentPolicy.objects.create(
            organization=org,
            consent_type=ConsentRecord.Type.MEDICAL,
            version="medical-2026-01",
            title="Medical information consent",
            body="MEDICAL POLICY BODY ONLY",
        )
        waiver = ConsentPolicy.objects.create(
            organization=org,
            consent_type=ConsentRecord.Type.WAIVER,
            version="waiver-2026-02",
            title="Training waiver",
            body="WAIVER POLICY BODY ONLY",
        )
        admin = _staff(org, dojo_a, Role.DOJO_ADMIN, email="admin@consent.test")
        front_desk = _staff(org, dojo_a, Role.FRONT_DESK, email="desk@consent.test")
        other_dojo_admin = _staff(org, dojo_b, Role.DOJO_ADMIN, email="other-dojo@consent.test")
    return {
        "org": org,
        "dojo_a": dojo_a,
        "child": child,
        "profile": profile,
        "parent": parent,
        "medical": medical,
        "waiver": waiver,
        "admin": admin,
        "front_desk": front_desk,
        "other_dojo_admin": other_dojo_admin,
    }


def _url(world, kind):
    return reverse(f"{kind}-consent", args=[world["child"].pk])


def _decision(world, decision="grant", **changes):
    data = {
        "signer_id": str(world["parent"].pk),
        "signature_name": "Davy Parent",
        "confirm": "on",
        "decision": decision,
    }
    data.update(changes)
    return data


def test_medical_screen_is_explicitly_separate_and_never_cached(client, world):
    client.force_login(world["admin"])

    response = client.get(_url(world, "medical"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "MEDICAL POLICY BODY ONLY" in body
    assert "medical-2026-01" in body
    assert "separate, explicit medical-data decision" in body
    assert "WAIVER POLICY BODY ONLY" not in body
    assert "no-cache" in response.headers["Cache-Control"]


def test_waiver_screen_presents_the_exact_version_without_medical_terms(client, world):
    client.force_login(world["admin"])

    body = client.get(_url(world, "waiver")).content.decode()

    assert "WAIVER POLICY BODY ONLY" in body
    assert "waiver-2026-02" in body
    assert "MEDICAL POLICY BODY ONLY" not in body


def test_medical_grant_records_guardian_evidence_without_granting_waiver(client, world):
    client.force_login(world["admin"])

    response = client.post(
        _url(world, "medical"),
        _decision(world),
        REMOTE_ADDR="203.0.113.44",
        HTTP_USER_AGENT="Consent screen test",
    )

    assert response.status_code == 302
    record = ConsentRecord.objects.for_organization(world["org"].pk).get()
    assert record.consent_type == ConsentRecord.Type.MEDICAL
    assert record.policy_id == world["medical"].pk
    assert record.capacity == ConsentRecord.Capacity.PARENT
    assert record.ip_address == "203.0.113.44"
    assert record.user_agent == "Consent screen test"
    assert (
        not ConsentRecord.objects.for_organization(world["org"].pk)
        .filter(consent_type=ConsentRecord.Type.WAIVER)
        .exists()
    )


def test_waiver_grant_and_revocation_are_append_only(client, world):
    client.force_login(world["admin"])
    url = _url(world, "waiver")

    assert client.post(url, _decision(world)).status_code == 302
    assert client.post(url, _decision(world, "revoke")).status_code == 302

    records = list(
        ConsentRecord.objects.for_organization(world["org"].pk)
        .filter(consent_type=ConsentRecord.Type.WAIVER)
        .order_by("created_at")
    )
    assert len(records) == 2
    assert records[0].granted is True
    assert records[1].granted is False
    assert records[1].supersedes_id == records[0].pk
    assert all(record.policy_id == world["waiver"].pk for record in records)


def test_confirmation_and_authorised_signer_are_required(client, world):
    client.force_login(world["admin"])

    missing_confirmation = client.post(_url(world, "medical"), _decision(world, confirm=""))
    tampered_signer = client.post(
        _url(world, "medical"), _decision(world, signer_id=str(world["admin"].person_id))
    )

    assert missing_confirmation.status_code == 200
    assert tampered_signer.status_code == 200
    assert not ConsentRecord.objects.for_organization(world["org"].pk).exists()


def test_medical_requires_medical_permission_but_front_desk_may_capture_waiver(client, world):
    client.force_login(world["front_desk"])

    assert client.get(_url(world, "medical")).status_code == 403
    assert client.post(_url(world, "waiver"), _decision(world)).status_code == 302


def test_dojo_scope_hides_a_student_in_another_dojo(client, world):
    client.force_login(world["other_dojo_admin"])

    assert client.get(_url(world, "medical")).status_code == 404
    assert client.get(_url(world, "waiver")).status_code == 404


def test_missing_active_policy_fails_closed(client, world):
    world["medical"].is_active = False
    world["medical"].save(update_fields=["is_active", "updated_at"])
    client.force_login(world["admin"])

    assert client.get(_url(world, "medical")).status_code == 404


def test_invalid_decision_is_rejected_and_policy_html_is_escaped(client, world):
    ConsentPolicy.objects.for_organization(world["org"].pk).filter(pk=world["medical"].pk).update(
        is_active=False
    )
    with allow_unscoped("consent view test policy publication"):
        safe_policy = ConsentPolicy.objects.create(
            organization=world["org"],
            consent_type=ConsentRecord.Type.MEDICAL,
            version="medical-xss-test",
            title="Untrusted policy",
            body="<script>alert('unsafe')</script>",
        )
    client.force_login(world["admin"])

    response = client.get(_url(world, "medical"))
    invalid = client.post(_url(world, "medical"), _decision(world, decision="invalid"))

    body = response.content.decode()
    assert safe_policy.version in body
    assert "&lt;script&gt;" in body
    assert "<script>alert('unsafe')</script>" not in body
    assert invalid.status_code == 400


def test_post_requires_csrf(world):
    client = Client(enforce_csrf_checks=True)
    client.force_login(world["admin"])

    response = client.post(_url(world, "medical"), _decision(world))

    assert response.status_code == 403
    assert not ConsentRecord.objects.for_organization(world["org"].pk).exists()


def test_attached_waiver_download_is_permission_checked_and_forced_to_attachment(client, world):
    actor = actor_for_user(world["admin"])
    document = store(
        SimpleUploadedFile("waiver.pdf", b"%PDF-1.7\n" + b"0" * 200),
        organization=world["org"],
        kind="waiver",
        actor=actor,
        subject_person=world["child"],
    )
    world["waiver"].is_active = False
    world["waiver"].save(update_fields=["is_active", "updated_at"])
    with allow_unscoped("consent view attachment policy"):
        ConsentPolicy.objects.create(
            organization=world["org"],
            consent_type=ConsentRecord.Type.WAIVER,
            version="waiver-with-pdf",
            title="Attached waiver",
            document=document,
        )
    client.force_login(world["admin"])

    response = client.get(reverse("document-download", args=[document.pk]))

    assert response.status_code == 200
    assert b"".join(response.streaming_content).startswith(b"%PDF-1.7")
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert "private" in response.headers["Cache-Control"]
    assert "no-store" in response.headers["Cache-Control"]
