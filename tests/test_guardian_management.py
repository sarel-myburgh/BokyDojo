"""Audited, dojo-scoped guardian management - TODO 1.1.4."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from apps.core.encryption import looks_encrypted
from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.guardians import add_guardian, remove_guardian, update_guardian
from apps.identity.models import (
    Dojo,
    EmergencyContact,
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


def _staff(org, role, email, dojo=None):
    person = Person.objects.create(
        organization=org,
        given_name=role.title(),
        family_name="GuardianStaff",
    )
    RoleAssignment.objects.create(
        organization=org,
        person=person,
        role=role,
        scope_type=ScopeType.DOJO if dojo else ScopeType.ORG,
        dojo=dojo,
    )
    return User.objects.create_user(email=email, password=PASSWORD, person=person)


def _student(org, dojo, given):
    person = Person.objects.create(
        organization=org,
        given_name=given,
        family_name="Student",
    )
    return StudentProfile.objects.create(person=person, home_dojo=dojo)


@pytest.fixture
def world():
    with allow_unscoped("guardian management test setup"):
        org = Organization.objects.create(name="Guardian Org", slug="guardian-management")
        dojo_a = Dojo.objects.create(organization=org, name="Dojo A", slug="guardian-a")
        dojo_b = Dojo.objects.create(organization=org, name="Dojo B", slug="guardian-b")
        child_a = _student(org, dojo_a, "Ari")
        sibling_a = _student(org, dojo_a, "Bora")
        child_b = _student(org, dojo_b, "Cara")
        guardian = Person.objects.create(
            organization=org,
            given_name="Pat",
            family_name="Parent",
            email="pat@example.test",
        )
        link = GuardianLink.objects.create(
            guardian=guardian,
            student=child_a.person,
            relationship=GuardianLink.Relationship.MOTHER,
            is_primary_contact=True,
            notes="Sensitive family context",
        )
        contact_a = EmergencyContact.objects.create(
            person=child_a.person,
            name="Trusted A",
            phone="111",
        )
        contact_b = EmergencyContact.objects.create(
            person=child_b.person,
            name="Trusted B",
            phone="222",
        )
        admin_a = _staff(org, Role.DOJO_ADMIN, "admin-a@guardian.test", dojo_a)
        admin_b = _staff(org, Role.DOJO_ADMIN, "admin-b@guardian.test", dojo_b)
        org_admin = _staff(org, Role.ORG_ADMIN, "org-admin@guardian.test")
        instructor = _staff(org, Role.INSTRUCTOR, "instructor@guardian.test", dojo_a)
    return locals()


def _link_values(**overrides):
    values = {
        "relationship": GuardianLink.Relationship.FATHER,
        "is_primary_contact": False,
        "is_emergency_contact": True,
        "is_financially_responsible": False,
        "has_custody": False,
        "notes": "Call after school",
    }
    values.update(overrides)
    return values


def _contact_values(**overrides):
    values = {
        "given_name": "Sam",
        "family_name": "Guardian",
        "email": "sam@example.test",
        "phone": "",
    }
    values.update(overrides)
    return values


def test_dojo_scope_applies_to_guardian_links_and_emergency_contacts(world):
    actor_a = actor_for_user(world["admin_a"])
    actor_b = actor_for_user(world["admin_b"])
    org_actor = actor_for_user(world["org_admin"])

    assert list(GuardianLink.objects.for_actor(actor_a)) == [world["link"]]
    assert not GuardianLink.objects.for_actor(actor_b).exists()
    assert list(EmergencyContact.objects.for_actor(actor_a)) == [world["contact_a"]]
    assert list(EmergencyContact.objects.for_actor(actor_b)) == [world["contact_b"]]
    assert EmergencyContact.objects.for_actor(org_actor).count() == 2


def test_adds_multiple_guardians_and_reuses_one_person_for_siblings(world):
    actor = actor_for_user(world["admin_a"])
    second = add_guardian(
        profile=world["child_a"],
        actor=actor,
        contact_values=_contact_values(),
        link_values=_link_values(is_financially_responsible=True),
    )
    sibling_link = add_guardian(
        profile=world["sibling_a"],
        actor=actor,
        existing_guardian=world["guardian"],
        link_values=_link_values(
            relationship=GuardianLink.Relationship.MOTHER,
            is_emergency_contact=False,
            has_custody=True,
        ),
    )

    assert (
        GuardianLink.objects.for_actor(actor).filter(student=world["child_a"].person).count() == 2
    )
    assert second.is_emergency_contact is True
    assert second.is_financially_responsible is True
    assert sibling_link.guardian_id == world["guardian"].pk
    assert sibling_link.has_custody is True
    assert sibling_link.is_primary_contact is False


def test_new_guardian_requires_contact_channel_and_rejects_duplicate(world):
    actor = actor_for_user(world["admin_a"])
    with pytest.raises(ValidationError):
        add_guardian(
            profile=world["child_a"],
            actor=actor,
            contact_values=_contact_values(email="", phone=""),
            link_values=_link_values(),
        )
    with pytest.raises(ValidationError):
        add_guardian(
            profile=world["child_a"],
            actor=actor,
            existing_guardian=world["guardian"],
            link_values=_link_values(),
        )


def test_notes_are_encrypted_and_never_copied_into_audit(world):
    actor = actor_for_user(world["admin_a"])
    link = add_guardian(
        profile=world["sibling_a"],
        actor=actor,
        contact_values=_contact_values(),
        link_values=_link_values(notes="Private safeguarding phrase"),
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT notes FROM identity_guardianlink WHERE id = %s", [link.pk.hex])
        stored = cursor.fetchone()[0]

    assert looks_encrypted(stored)
    assert "Private safeguarding phrase" not in stored
    audit_text = " ".join(
        str(value)
        for row in AuditLog.objects.filter(subject_id=str(link.pk))
        for value in (row.before, row.after, row.note)
    )
    assert "Private safeguarding phrase" not in audit_text


def test_shared_guardian_contact_needs_rights_over_every_child(world):
    actor_a = actor_for_user(world["admin_a"])
    actor_org = actor_for_user(world["org_admin"])
    GuardianLink.objects.create(
        guardian=world["guardian"],
        student=world["child_b"].person,
        relationship=GuardianLink.Relationship.MOTHER,
    )

    with pytest.raises(PermissionDenied):
        update_guardian(
            link=world["link"],
            profile=world["child_a"],
            actor=actor_a,
            contact_values=_contact_values(given_name="Changed"),
            link_values=_link_values(),
        )

    updated = update_guardian(
        link=world["link"],
        profile=world["child_a"],
        actor=actor_a,
        contact_values={
            "given_name": world["guardian"].given_name,
            "family_name": world["guardian"].family_name,
            "email": world["guardian"].email,
            "phone": world["guardian"].phone,
        },
        link_values=_link_values(is_financially_responsible=True),
    )
    assert updated.is_financially_responsible is True

    update_guardian(
        link=world["link"],
        profile=world["child_a"],
        actor=actor_org,
        contact_values=_contact_values(given_name="Changed"),
        link_values=_link_values(),
    )
    world["guardian"].refresh_from_db()
    assert world["guardian"].given_name == "Changed"


def test_strict_audit_failure_rolls_back_add_and_remove(world):
    actor = actor_for_user(world["admin_a"])
    before_people = Person.objects.for_organization(world["org"].pk).count()
    with patch("apps.identity.guardians.audit.record", side_effect=RuntimeError("audit down")):
        with pytest.raises(RuntimeError, match="audit down"):
            add_guardian(
                profile=world["sibling_a"],
                actor=actor,
                contact_values=_contact_values(),
                link_values=_link_values(),
            )
    assert Person.objects.for_organization(world["org"].pk).count() == before_people

    with patch("apps.identity.guardians.audit.record", side_effect=RuntimeError("audit down")):
        with pytest.raises(RuntimeError, match="audit down"):
            remove_guardian(link=world["link"], profile=world["child_a"], actor=actor)
    assert GuardianLink.objects.for_actor(actor).filter(pk=world["link"].pk).exists()


def test_remove_unlinks_but_retains_guardian_person(world):
    actor = actor_for_user(world["admin_a"])
    remove_guardian(link=world["link"], profile=world["child_a"], actor=actor)
    assert not GuardianLink.objects.for_actor(actor).filter(pk=world["link"].pk).exists()
    assert Person.objects.for_organization(world["org"].pk).filter(pk=world["guardian"].pk).exists()


def test_family_screen_and_crud_flow(client, world):
    client.force_login(world["admin_a"])
    detail_url = reverse("student-detail", args=[world["child_a"].person_id])
    detail = client.get(detail_url, {"tab": "family"})
    body = detail.content.decode()
    assert detail.status_code == 200
    assert "Pat Parent" in body
    assert reverse("guardian-add", args=[world["child_a"].person_id]) in body
    assert reverse("guardian-edit", args=[world["child_a"].person_id, world["link"].pk]) in body

    response = client.post(
        reverse("guardian-add", args=[world["child_a"].person_id]),
        {
            **_contact_values(given_name="New"),
            **_link_values(is_emergency_contact="on"),
        },
    )
    assert response.status_code == 302
    created = GuardianLink.objects.for_actor(actor_for_user(world["admin_a"])).get(
        guardian__given_name="New"
    )
    assert created.is_emergency_contact is True

    response = client.post(
        reverse("guardian-remove", args=[world["child_a"].person_id, created.pk])
    )
    assert response.status_code == 302
    assert Person.objects.for_organization(world["org"].pk).filter(pk=created.guardian_id).exists()


@pytest.mark.parametrize("user_key", ["admin_b", "instructor"])
def test_guardian_routes_enforce_scope_and_edit_permission(client, world, user_key):
    client.force_login(world[user_key])
    response = client.get(reverse("guardian-add", args=[world["child_a"].person_id]))
    assert response.status_code == (404 if user_key == "admin_b" else 403)


def test_guardian_writes_require_csrf(world):
    client = Client(enforce_csrf_checks=True)
    client.force_login(world["admin_a"])
    response = client.post(
        reverse("guardian-remove", args=[world["child_a"].person_id, world["link"].pk])
    )
    assert response.status_code == 403
    assert (
        GuardianLink.objects.for_actor(actor_for_user(world["admin_a"]))
        .filter(pk=world["link"].pk)
        .exists()
    )


def test_generic_admin_does_not_expose_guardian_notes():
    from apps.identity.admin import GuardianLinkAdmin

    assert GuardianLinkAdmin.exclude == ("notes",)


@pytest.mark.django_db(transaction=True)
def test_guardian_notes_migration_encrypts_existing_plaintext():
    executor = MigrationExecutor(connection)
    executor.migrate([("identity", "0011_encrypt_hold_reasons")])
    old_apps = executor.loader.project_state([("identity", "0011_encrypt_hold_reasons")]).apps
    OrganizationV11 = old_apps.get_model("identity", "Organization")
    PersonV11 = old_apps.get_model("identity", "Person")
    GuardianLinkV11 = old_apps.get_model("identity", "GuardianLink")
    org = OrganizationV11.objects.create(name="Guardian Migration", slug="guardian-migration")
    guardian = PersonV11.objects.create(organization=org, given_name="Legacy", family_name="Parent")
    student = PersonV11.objects.create(organization=org, given_name="Legacy", family_name="Child")
    link = GuardianLinkV11.objects.create(
        guardian=guardian,
        student=student,
        relationship="guardian",
        notes="Legacy private note",
    )

    MigrationExecutor(connection).migrate([("identity", "0012_scope_and_encrypt_guardian_records")])
    with connection.cursor() as cursor:
        cursor.execute("SELECT notes FROM identity_guardianlink WHERE id = %s", [link.pk.hex])
        value = cursor.fetchone()[0]
    assert looks_encrypted(value)
    assert "Legacy private note" not in value

    MigrationExecutor(connection).migrate([("identity", "0011_encrypt_hold_reasons")])
    with connection.cursor() as cursor:
        cursor.execute("SELECT notes FROM identity_guardianlink WHERE id = %s", [link.pk.hex])
        assert cursor.fetchone()[0] == "Legacy private note"
    MigrationExecutor(connection).migrate([("identity", "0012_scope_and_encrypt_guardian_records")])
