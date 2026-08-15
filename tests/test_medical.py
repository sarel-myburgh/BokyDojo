"""Encrypted, permission-checked medical data — TODO 1.1.2 / SEC 2.3."""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.core import audit
from apps.core.encryption import looks_encrypted
from apps.core.models import AuditLog
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.medical import update_medical, view_do_not_spar, view_medical
from apps.identity.models import (
    Dojo,
    Enrollment,
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
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Medical Org", slug="medical-org")
        dojo = Dojo.objects.create(organization=org, name="Main Dojo", slug="main-dojo")
        student = Person.objects.create(organization=org, given_name="Mina", family_name="Student")
        profile = StudentProfile.objects.create(
            person=student,
            home_dojo=dojo,
            medical_notes="Carries an inhaler",
            allergies="Severe peanut allergy",
            conditions="Asthma",
            medications="Salbutamol",
            doctor_contact="Dr Dara +855 12 345 678",
            do_not_spar=True,
        )
        Enrollment.objects.create(
            student=student,
            dojo=dojo,
            started_on=datetime.date(2026, 1, 1),
            is_primary=True,
        )
        staff = Person.objects.create(organization=org, given_name="Head", family_name="Sensei")
    return {"org": org, "dojo": dojo, "student": student, "profile": profile, "staff": staff}


def actor(world, role, *, organization=None, dojo=None):
    organization = organization or world["org"]
    dojo = world["dojo"] if dojo is None else dojo
    return Actor(
        user_id=None,
        person_id=world["staff"].pk if organization == world["org"] else None,
        organization_id=organization.pk,
        dojo_ids=frozenset({dojo.pk}) if dojo else None,
        roles=frozenset(
            {
                (
                    role,
                    ScopeType.DOJO if dojo else ScopeType.ORG,
                    dojo.pk if dojo else None,
                )
            }
        ),
    )


def test_medical_text_is_encrypted_at_rest_and_decrypts_for_the_model(world):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT medical_notes, allergies, conditions, medications, doctor_contact, "
            "do_not_spar FROM identity_studentprofile"
        )
        stored = cursor.fetchone()

    assert all(looks_encrypted(value) for value in stored[:5])
    assert not any(
        secret in value
        for value in stored[:5]
        for secret in ("inhaler", "peanut", "Asthma", "Salbutamol", "Dara")
    )
    assert stored[5] is True or stored[5] == 1

    world["profile"].refresh_from_db()
    assert world["profile"].allergies == "Severe peanut allergy"
    assert world["profile"].doctor_contact == "Dr Dara +855 12 345 678"


def test_instructor_can_view_and_the_access_is_logged_without_values(world):
    details = view_medical(profile=world["profile"], actor=actor(world, Role.INSTRUCTOR))

    assert details.conditions == "Asthma"
    entry = AuditLog.objects.get(action="view_medical")
    assert entry.organization_id == world["org"].pk
    assert entry.actor_person_id == world["staff"].pk
    assert "Asthma" not in str(entry.before)
    assert "Asthma" not in str(entry.after)
    assert "Asthma" not in entry.note


def test_operational_do_not_spar_read_is_narrow_and_audited(world):
    assert view_do_not_spar(profile=world["profile"], actor=actor(world, Role.INSTRUCTOR)) is True

    entry = AuditLog.objects.get(action="view_medical")
    assert entry.note == "fields: do_not_spar"
    assert entry.before is None
    assert entry.after is None


def test_dojo_admin_can_update_and_only_field_names_enter_the_audit_log(world):
    details = update_medical(
        profile=world["profile"],
        changes={"conditions": "Controlled asthma", "do_not_spar": False},
        actor=actor(world, Role.DOJO_ADMIN),
    )

    assert details.conditions == "Controlled asthma"
    assert details.do_not_spar is False
    entry = AuditLog.objects.get(action="update_medical")
    assert "conditions" in entry.note
    assert "do_not_spar" in entry.note
    assert "Controlled asthma" not in str(entry.before)
    assert "Controlled asthma" not in str(entry.after)
    assert "Controlled asthma" not in entry.note

    with connection.cursor() as cursor:
        cursor.execute("SELECT conditions FROM identity_studentprofile")
        assert "Controlled asthma" not in cursor.fetchone()[0]


def test_front_desk_cannot_view_or_edit_medical_data(world):
    front_desk = actor(world, Role.FRONT_DESK)

    with pytest.raises(PermissionDenied):
        view_medical(profile=world["profile"], actor=front_desk)
    with pytest.raises(PermissionDenied):
        update_medical(
            profile=world["profile"],
            changes={"allergies": "None"},
            actor=front_desk,
        )

    assert not AuditLog.objects.filter(action__in=["view_medical", "update_medical"]).exists()


def test_cross_tenant_actor_cannot_access_medical_data(world):
    with allow_unscoped("test setup"):
        other = Organization.objects.create(name="Other", slug="other-medical")
    outsider = actor(world, Role.ORG_ADMIN, organization=other, dojo=None)

    with pytest.raises(PermissionDenied):
        view_medical(profile=world["profile"], actor=outsider)


def test_medical_update_rejects_unknown_wrong_type_and_oversized_values(world):
    admin_actor = actor(world, Role.DOJO_ADMIN)

    with pytest.raises(ValidationError, match="Unknown"):
        update_medical(profile=world["profile"], changes={"billing_notes": "no"}, actor=admin_actor)
    with pytest.raises(ValidationError, match="true or false"):
        update_medical(profile=world["profile"], changes={"do_not_spar": "yes"}, actor=admin_actor)
    with pytest.raises(ValidationError, match="10000"):
        update_medical(
            profile=world["profile"],
            changes={"medical_notes": "x" * 10_001},
            actor=admin_actor,
        )


def test_generic_audit_snapshot_excludes_every_encrypted_medical_value(world):
    snapshot = audit.snapshot(world["profile"])

    assert set(snapshot).isdisjoint(
        {"medical_notes", "allergies", "conditions", "medications", "doctor_contact", "do_not_spar"}
    )


def test_generic_admin_excludes_medical_fields():
    from apps.identity.admin import StudentProfileAdmin

    assert set(StudentProfileAdmin.exclude) == {
        "status",
        "hold_reason",
        "medical_notes",
        "allergies",
        "conditions",
        "medications",
        "doctor_contact",
        "do_not_spar",
    }
