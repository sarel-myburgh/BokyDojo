"""Consent-gated, metadata-stripped student photographs — TODO 1.1.14."""

from __future__ import annotations

import io

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from apps.core.models import AuditLog, Document
from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.consent import record_consent
from apps.identity.models import (
    ConsentPolicy,
    ConsentRecord,
    Dojo,
    GovernanceModel,
    GuardianLink,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    User,
)
from apps.identity.photos import current_student_photo, upload_student_photo

pytestmark = pytest.mark.django_db
PASSWORD = "correct-horse-battery"


def _staff(org, role, email, dojo=None):
    person = Person.objects.create(organization=org, given_name=role.title(), family_name="Staff")
    RoleAssignment.objects.create(
        organization=org,
        person=person,
        role=role,
        scope_type=ScopeType.DOJO if dojo else ScopeType.ORG,
        dojo=dojo,
    )
    return User.objects.create_user(email=email, password=PASSWORD, person=person)


def _jpeg(name="student.jpg", *, with_metadata=True):
    buffer = io.BytesIO()
    image = Image.new("RGB", (24, 24), (80, 120, 160))
    if with_metadata:
        exif = image.getexif()
        exif[0x010F] = "SecretCamera"
        exif[0x9C9B] = "child-location-title"
        image.save(buffer, format="JPEG", exif=exif)
    else:
        image.save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


@pytest.fixture
def world(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    with allow_unscoped("student photo test setup"):
        org = Organization.objects.create(name="Photo Org", slug="photo-org")
        dojo_a = Dojo.objects.create(organization=org, name="A", slug="photo-a")
        dojo_b = Dojo.objects.create(organization=org, name="B", slug="photo-b")
        child = Person.objects.create(
            organization=org,
            given_name="Mika",
            family_name="Student",
            date_of_birth=__import__("datetime").date(2015, 1, 1),
        )
        profile = StudentProfile.objects.create(
            person=child,
            home_dojo=dojo_a,
            status=StudentProfile.Status.ACTIVE,
        )
        parent = Person.objects.create(organization=org, given_name="Pat", family_name="Parent")
        GuardianLink.objects.create(
            student=child,
            guardian=parent,
            relationship=GuardianLink.Relationship.MOTHER,
            has_custody=True,
        )
        policy = ConsentPolicy.objects.create(
            organization=org,
            consent_type=ConsentRecord.Type.PHOTO,
            version="photo-2026-01",
            title="Photo policy",
            body="PHOTO USE TERMS ONLY",
        )
        admin = _staff(org, Role.DOJO_ADMIN, "admin@photo.test", dojo_a)
        instructor = _staff(org, Role.INSTRUCTOR, "instructor@photo.test", dojo_a)
        other_admin = _staff(org, Role.DOJO_ADMIN, "other@photo.test", dojo_b)
        org_admin = _staff(org, Role.ORG_ADMIN, "org-admin@photo.test")
    return locals()


def _consent(world, *, granted=True):
    return record_consent(
        person=world["child"],
        consent_type=ConsentRecord.Type.PHOTO,
        version=world["policy"].version,
        granted=granted,
        granted_by=world["parent"],
        capacity=ConsentRecord.Capacity.PARENT,
        ip_address="203.0.113.80",
        actor=actor_for_user(world["admin"]),
        minimum_self_consent_age=18,
        signature_name="Pat Parent",
        policy=world["policy"],
    )


def test_photo_consent_is_a_separate_exact_version_flow(client, world):
    client.force_login(world["admin"])
    url = reverse("photo-consent", args=[world["child"].pk])

    page = client.get(url)
    response = client.post(
        url,
        {
            "decision": "grant",
            "signer_id": str(world["parent"].pk),
            "signature_name": "Pat Parent",
            "confirm": "on",
        },
    )

    assert page.status_code == 200
    assert "PHOTO USE TERMS ONLY" in page.content.decode()
    assert "separate, explicit decision" in page.content.decode()
    assert response.status_code == 302
    record = ConsentRecord.objects.for_organization(world["org"].pk).get(
        consent_type=ConsentRecord.Type.PHOTO
    )
    assert record.policy_id == world["policy"].pk
    assert record.granted is True


def test_upload_fails_closed_without_current_granted_consent(client, world):
    client.force_login(world["admin"])
    url = reverse("student-photo-upload", args=[world["child"].pk])

    page = client.get(url)
    response = client.post(url, {"photo": _jpeg()})

    assert page.status_code == 200
    assert "Current explicit photo consent has not been granted" in page.content.decode()
    assert response.status_code == 200
    assert (
        not Document.objects.for_organization(world["org"].pk)
        .filter(kind=Document.Kind.PHOTO)
        .exists()
    )


def test_photo_is_reencoded_stored_privately_and_rendered_with_safe_headers(client, world):
    _consent(world)
    client.force_login(world["admin"])
    response = client.post(
        reverse("student-photo-upload", args=[world["child"].pk]),
        {"photo": _jpeg()},
    )

    assert response.status_code == 302
    photo = Document.objects.for_organization(world["org"].pk).get(kind=Document.Kind.PHOTO)
    assert photo.subject_person_id == world["child"].pk
    assert photo.content_type == "image/jpeg"
    with default_storage.open(photo.storage_key, "rb") as handle:
        stored = handle.read()
    assert b"SecretCamera" not in stored
    assert b"child-location-title" not in stored
    with Image.open(io.BytesIO(stored)) as image:
        assert dict(image.getexif()) == {}

    image_response = client.get(reverse("student-photo", args=[world["child"].pk]))
    detail = client.get(reverse("student-detail", args=[world["child"].pk]))
    assert image_response.status_code == 200
    assert image_response["Content-Type"] == "image/jpeg"
    assert image_response["Content-Disposition"] == "inline"
    assert "private" in image_response["Cache-Control"]
    assert "no-store" in image_response["Cache-Control"]
    assert image_response["X-Content-Type-Options"] == "nosniff"
    assert image_response["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert reverse("student-photo", args=[world["child"].pk]) in detail.content.decode()
    assert AuditLog.objects.filter(
        action="view", subject_id=str(photo.pk), note="document read"
    ).exists()


def test_revocation_immediately_hides_photo_without_deleting_evidence(client, world):
    _consent(world)
    photo = upload_student_photo(
        profile=world["profile"],
        uploaded_file=_jpeg(),
        actor=actor_for_user(world["admin"]),
    )
    _consent(world, granted=False)
    client.force_login(world["admin"])

    assert client.get(reverse("student-photo", args=[world["child"].pk])).status_code == 404
    assert client.get(reverse("document-download", args=[photo.pk])).status_code == 403
    detail = client.get(reverse("student-detail", args=[world["child"].pk]))
    assert reverse("student-photo", args=[world["child"].pk]) not in detail.content.decode()
    assert Document.objects.for_organization(world["org"].pk).filter(pk=photo.pk).exists()
    assert (
        current_student_photo(profile=world["profile"], actor=actor_for_user(world["admin"]))
        is None
    )


@pytest.mark.parametrize(
    "upload",
    [
        SimpleUploadedFile("fake.jpg", b"%PDF-1.7\n" + b"0" * 100),
        SimpleUploadedFile(
            "attack.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        ),
    ],
)
def test_non_image_and_active_content_uploads_are_rejected(client, world, upload):
    _consent(world)
    client.force_login(world["admin"])

    response = client.post(
        reverse("student-photo-upload", args=[world["child"].pk]),
        {"photo": upload},
    )

    assert response.status_code == 200
    assert (
        not Document.objects.for_organization(world["org"].pk)
        .filter(kind=Document.Kind.PHOTO)
        .exists()
    )


def test_photo_upload_and_display_enforce_dojo_scope_and_edit_permission(client, world):
    _consent(world)
    upload_url = reverse("student-photo-upload", args=[world["child"].pk])

    client.force_login(world["instructor"])
    assert client.post(upload_url, {"photo": _jpeg()}).status_code == 403

    client.force_login(world["other_admin"])
    assert client.get(upload_url).status_code == 404
    assert client.post(upload_url, {"photo": _jpeg()}).status_code == 404


def test_federation_org_actor_cannot_see_photo_or_download_document(client, world):
    _consent(world)
    photo = upload_student_photo(
        profile=world["profile"],
        uploaded_file=_jpeg(),
        actor=actor_for_user(world["admin"]),
    )
    world["org"].governance_model = GovernanceModel.FEDERATED
    world["org"].save(update_fields=["governance_model", "updated_at"])
    client.force_login(world["org_admin"])

    detail = client.get(reverse("student-detail", args=[world["child"].pk]))
    assert detail.status_code == 200
    assert reverse("student-photo", args=[world["child"].pk]) not in detail.content.decode()
    assert client.get(reverse("student-photo", args=[world["child"].pk])).status_code == 404
    assert client.get(reverse("document-download", args=[photo.pk])).status_code == 403
    assert client.get(reverse("document-download", args=[photo.pk])).status_code == 403


def test_latest_upload_replaces_display_but_preserves_prior_document(world):
    _consent(world)
    actor = actor_for_user(world["admin"])
    first = upload_student_photo(
        profile=world["profile"], uploaded_file=_jpeg("first.jpg"), actor=actor
    )
    second = upload_student_photo(
        profile=world["profile"], uploaded_file=_jpeg("second.jpg"), actor=actor
    )

    assert first.pk != second.pk
    assert current_student_photo(profile=world["profile"], actor=actor).pk == second.pk
    assert (
        Document.objects.for_organization(world["org"].pk).filter(kind=Document.Kind.PHOTO).count()
        == 2
    )


def test_photo_upload_requires_csrf(world):
    _consent(world)
    client = Client(enforce_csrf_checks=True)
    client.force_login(world["admin"])

    response = client.post(
        reverse("student-photo-upload", args=[world["child"].pk]),
        {"photo": _jpeg()},
    )

    assert response.status_code == 403
    assert (
        not Document.objects.for_organization(world["org"].pk)
        .filter(kind=Document.Kind.PHOTO)
        .exists()
    )
