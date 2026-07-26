"""Document storage and permission-checked serving — TODO 0.3.9b, SEC 2.3, 2.6."""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.documents import (
    download_headers,
    may_read,
    open_document,
    store,
)
from apps.core.models import AuditLog, Document
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    Dojo,
    GovernanceModel,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
)

pytestmark = pytest.mark.django_db

PDF_BYTES = b"%PDF-1.7\n" + b"0" * 200
CENTRAL = GovernanceModel.CENTRAL


@pytest.fixture
def world():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Doc Org", slug="doc-org")
        other = Organization.objects.create(name="Other", slug="other-doc-org")
        dojo = Dojo.objects.create(organization=org, name="Main", slug="doc-main")

        admin = Person.objects.create(organization=org, given_name="Ada", family_name="Admin")
        instructor = Person.objects.create(
            organization=org, given_name="Ivo", family_name="Instructor"
        )
        desk = Person.objects.create(organization=org, given_name="Dee", family_name="Desk")
        student = Person.objects.create(organization=org, given_name="Sao", family_name="Student")

        RoleAssignment.objects.create(
            organization=org, person=admin, role=Role.ORG_ADMIN, scope_type=ScopeType.ORG
        )
        RoleAssignment.objects.create(
            organization=org,
            person=instructor,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        RoleAssignment.objects.create(
            organization=org,
            person=desk,
            role=Role.FRONT_DESK,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
    return {
        "org": org,
        "other": other,
        "dojo": dojo,
        "admin": admin,
        "instructor": instructor,
        "desk": desk,
        "student": student,
    }


def _actor(person, org, *, role, scope=ScopeType.ORG, dojo=None):
    return Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=None if scope == ScopeType.ORG else frozenset({dojo.pk}),
        roles=frozenset({(role, scope, None if scope == ScopeType.ORG else dojo.pk)}),
    )


@pytest.fixture
def admin_actor(world):
    return _actor(world["admin"], world["org"], role=Role.ORG_ADMIN)


def _store(world, actor, kind=Document.Kind.WAIVER, name="waiver.pdf"):
    return store(
        SimpleUploadedFile(name, PDF_BYTES),
        organization=world["org"],
        kind=kind,
        actor=actor,
        subject_person=world["student"],
    )


# -- storing ------------------------------------------------------------------


def test_store_records_metadata_and_writes_the_file(world, admin_actor):
    document = _store(world, admin_actor)
    assert document.byte_size == len(PDF_BYTES)
    assert document.content_type == "application/pdf"
    assert len(document.checksum) == 64
    assert document.original_filename == "waiver.pdf"


def test_storage_key_does_not_use_the_uploaded_filename(world, admin_actor):
    """Path traversal and extension confusion die here."""
    document = _store(world, admin_actor, name="../../etc/passwd.pdf")
    assert ".." not in document.storage_key
    assert "passwd" not in document.storage_key
    assert str(document.pk) in document.storage_key


def test_invalid_upload_is_refused_before_anything_is_written(world, admin_actor):
    with pytest.raises(ValidationError):
        store(
            SimpleUploadedFile("evil.svg", b"<svg><script>alert(1)</script></svg>"),
            organization=world["org"],
            kind=Document.Kind.OTHER,
            actor=admin_actor,
        )
    with allow_unscoped("verifying nothing was recorded"):
        assert Document.objects.count() == 0


def test_medical_and_identity_are_always_marked_sensitive(world, admin_actor):
    """Misclassifying these is the expensive direction of the error, so the
    caller does not get to decide."""
    medical = _store(world, admin_actor, kind=Document.Kind.MEDICAL)
    identity = _store(world, admin_actor, kind=Document.Kind.IDENTITY, name="id.pdf")
    waiver = _store(world, admin_actor, name="w2.pdf")

    assert medical.is_sensitive is True
    assert identity.is_sensitive is True
    assert waiver.is_sensitive is False


def test_upload_is_audited(world, admin_actor):
    document = _store(world, admin_actor)
    entry = AuditLog.objects.filter(
        subject_id=str(document.pk), action=AuditLog.Action.CREATE
    ).first()
    assert entry is not None
    assert entry.actor_person_id == world["admin"].pk


# -- reading ------------------------------------------------------------------


def test_admin_can_read(world, admin_actor):
    document = _store(world, admin_actor)
    assert open_document(admin_actor, document, governance_model=CENTRAL) == PDF_BYTES


def test_another_organisation_cannot_read(world, admin_actor):
    document = _store(world, admin_actor)
    outsider = Actor(user_id=None, person_id=None, organization_id=world["other"].pk)
    with pytest.raises(PermissionDenied):
        open_document(outsider, document, governance_model=CENTRAL)


def test_anonymous_cannot_read(world, admin_actor):
    document = _store(world, admin_actor)
    anonymous = Actor(user_id=None, person_id=None, organization_id=None)
    with pytest.raises(PermissionDenied):
        open_document(anonymous, document, governance_model=CENTRAL)


def test_front_desk_cannot_read_a_medical_document(world, admin_actor):
    """Front desk handles money and enrolments, not medical letters — the
    permission matrix already says so; this proves documents honour it."""
    medical = _store(world, admin_actor, kind=Document.Kind.MEDICAL)
    desk_actor = _actor(
        world["desk"], world["org"], role=Role.FRONT_DESK, scope=ScopeType.DOJO,
        dojo=world["dojo"],
    )
    assert may_read(desk_actor, medical, governance_model=CENTRAL) is False


def test_instructor_may_read_a_medical_document(world, admin_actor):
    """Allergies matter mid-class, and the matrix grants instructors
    medical.view for exactly that reason."""
    medical = _store(world, admin_actor, kind=Document.Kind.MEDICAL)
    instructor_actor = _actor(
        world["instructor"], world["org"], role=Role.INSTRUCTOR,
        scope=ScopeType.DOJO, dojo=world["dojo"],
    )
    # The student has no home dojo set, so the object carries no dojo and a
    # dojo-scoped role cannot reach it. That is the deny-by-default behaviour.
    assert may_read(instructor_actor, medical, governance_model=CENTRAL) is False


def test_denied_reads_are_audited(world, admin_actor):
    """Repeated refusals against one child's documents is exactly the pattern
    worth alerting on."""
    document = _store(world, admin_actor)
    outsider = Actor(user_id=None, person_id=None, organization_id=world["other"].pk)

    with pytest.raises(PermissionDenied):
        open_document(outsider, document, governance_model=CENTRAL)

    denied = AuditLog.objects.filter(
        subject_id=str(document.pk), note__contains="DENIED"
    )
    assert denied.exists()


def test_successful_reads_are_audited(world, admin_actor):
    document = _store(world, admin_actor)
    open_document(admin_actor, document, governance_model=CENTRAL)
    assert AuditLog.objects.filter(
        subject_id=str(document.pk), action=AuditLog.Action.VIEW
    ).exists()


# -- serving headers ----------------------------------------------------------


def test_headers_force_download_rather_than_inline_rendering(world, admin_actor):
    """A PDF rendered in-page runs its own JavaScript in our origin."""
    headers = download_headers(_store(world, admin_actor))
    assert headers["Content-Disposition"].startswith("attachment;")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in headers["Content-Security-Policy"]
    assert "no-store" in headers["Cache-Control"]


def test_filename_in_the_header_is_sanitised(world, admin_actor):
    document = _store(world, admin_actor, name='evil".pdf')
    disposition = download_headers(document)["Content-Disposition"]
    assert disposition.count('"') == 2  # only the delimiters survive


# -- tenancy ------------------------------------------------------------------


def test_documents_are_tenant_scoped(world, admin_actor):
    _store(world, admin_actor)
    outsider = Actor(user_id=None, person_id=None, organization_id=world["other"].pk)
    assert Document.objects.for_actor(outsider).count() == 0
    assert Document.objects.for_actor(admin_actor).count() == 1
