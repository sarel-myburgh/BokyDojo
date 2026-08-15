"""Controlled student lifecycle transitions — TODO 1.1.12."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.core.encryption import looks_encrypted
from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.lifecycle import (
    BULK_TRANSITION_LIMIT,
    allowed_student_transitions,
    bulk_transition_student_status,
    transition_student_status,
)
from apps.identity.models import (
    Dojo,
    Enrollment,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    User,
)
from apps.identity.permissions import PermissionDenied

pytestmark = pytest.mark.django_db
PASSWORD = "correct-horse-battery"


@pytest.fixture
def world():
    with allow_unscoped("student lifecycle test setup"):
        org = Organization.objects.create(name="Lifecycle Org", slug="lifecycle-org")
        dojo = Dojo.objects.create(organization=org, name="Main", slug="lifecycle-main")
        other_dojo = Dojo.objects.create(organization=org, name="Other", slug="lifecycle-other")
        student = Person.objects.create(organization=org, given_name="Ari", family_name="Student")
        profile = StudentProfile.objects.create(
            person=student,
            home_dojo=dojo,
            status=StudentProfile.Status.ACTIVE,
        )

        admin_person = Person.objects.create(
            organization=org, given_name="Admin", family_name="User"
        )
        RoleAssignment.objects.create(
            organization=org,
            person=admin_person,
            role=Role.DOJO_ADMIN,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        admin = User.objects.create_user(
            email="admin@lifecycle.test", password=PASSWORD, person=admin_person
        )

        instructor_person = Person.objects.create(
            organization=org, given_name="Instructor", family_name="User"
        )
        RoleAssignment.objects.create(
            organization=org,
            person=instructor_person,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        instructor = User.objects.create_user(
            email="instructor@lifecycle.test",
            password=PASSWORD,
            person=instructor_person,
        )

        other_admin_person = Person.objects.create(
            organization=org, given_name="Other", family_name="Admin"
        )
        RoleAssignment.objects.create(
            organization=org,
            person=other_admin_person,
            role=Role.DOJO_ADMIN,
            scope_type=ScopeType.DOJO,
            dojo=other_dojo,
        )
        other_admin = User.objects.create_user(
            email="other-admin@lifecycle.test",
            password=PASSWORD,
            person=other_admin_person,
        )

    return locals()


def test_allowed_transition_graph_supports_real_join_hold_resume_and_return_paths():
    assert allowed_student_transitions(StudentProfile.Status.PROSPECT) == (
        StudentProfile.Status.TRIAL,
        StudentProfile.Status.ACTIVE,
        StudentProfile.Status.LAPSED,
    )
    assert StudentProfile.Status.ON_HOLD in allowed_student_transitions(
        StudentProfile.Status.ACTIVE
    )
    assert StudentProfile.Status.ACTIVE in allowed_student_transitions(
        StudentProfile.Status.ON_HOLD
    )
    assert StudentProfile.Status.ACTIVE in allowed_student_transitions(StudentProfile.Status.ALUMNI)
    assert allowed_student_transitions("invented") == ()


def test_hold_requires_reason_encrypts_it_and_never_audits_its_value(world):
    actor = actor_for_user(world["admin"])
    updated = transition_student_status(
        profile=world["profile"],
        to_status=StudentProfile.Status.ON_HOLD,
        hold_reason="Knee injury — no contact drills",
        actor=actor,
    )

    assert updated.status == StudentProfile.Status.ON_HOLD
    assert updated.hold_reason == "Knee injury — no contact drills"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT hold_reason FROM identity_studentprofile WHERE person_id = %s",
            [world["student"].pk.hex],
        )
        stored = cursor.fetchone()[0]
    assert looks_encrypted(stored)
    assert "Knee injury" not in stored

    entry = AuditLog.objects.get(action="update", subject_id=str(world["profile"].pk))
    assert entry.before == {"status": StudentProfile.Status.ACTIVE}
    assert entry.after == {"status": StudentProfile.Status.ON_HOLD}
    assert "Knee injury" not in str(entry.before)
    assert "Knee injury" not in str(entry.after)
    assert "Knee injury" not in entry.note


def test_resuming_clears_the_encrypted_hold_reason(world):
    actor = actor_for_user(world["admin"])
    held = transition_student_status(
        profile=world["profile"],
        to_status=StudentProfile.Status.ON_HOLD,
        hold_reason="Travel",
        actor=actor,
    )
    resumed = transition_student_status(
        profile=held,
        to_status=StudentProfile.Status.ACTIVE,
        actor=actor,
    )

    assert resumed.status == StudentProfile.Status.ACTIVE
    assert resumed.hold_reason == ""


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (StudentProfile.Status.TRIAL, StudentProfile.Status.ON_HOLD),
        (StudentProfile.Status.PROSPECT, StudentProfile.Status.ALUMNI),
        (StudentProfile.Status.ACTIVE, StudentProfile.Status.ACTIVE),
    ],
)
def test_invalid_direct_transitions_fail_without_mutation_or_audit(world, start, target):
    world["profile"].status = start
    world["profile"].save(update_fields=["status", "updated_at"])

    with pytest.raises(ValidationError):
        transition_student_status(
            profile=world["profile"],
            to_status=target,
            hold_reason="Not applicable",
            actor=actor_for_user(world["admin"]),
        )

    world["profile"].refresh_from_db()
    assert world["profile"].status == start
    assert not AuditLog.objects.filter(action="update").exists()


def test_hold_requires_nonblank_bounded_reason(world):
    actor = actor_for_user(world["admin"])

    with pytest.raises(ValidationError, match="administrative reason"):
        transition_student_status(
            profile=world["profile"],
            to_status=StudentProfile.Status.ON_HOLD,
            hold_reason="   ",
            actor=actor,
        )
    with pytest.raises(ValidationError, match="at most 200"):
        transition_student_status(
            profile=world["profile"],
            to_status=StudentProfile.Status.ON_HOLD,
            hold_reason="x" * 201,
            actor=actor,
        )


def test_instructor_and_other_dojo_admin_cannot_change_status(world):
    for user in (world["instructor"], world["other_admin"]):
        with pytest.raises(PermissionDenied):
            transition_student_status(
                profile=world["profile"],
                to_status=StudentProfile.Status.LAPSED,
                actor=actor_for_user(user),
            )

    world["profile"].refresh_from_db()
    assert world["profile"].status == StudentProfile.Status.ACTIVE


def test_strict_audit_failure_rolls_back_transition(world, monkeypatch):
    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("apps.identity.lifecycle.audit.record", fail_audit)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        transition_student_status(
            profile=world["profile"],
            to_status=StudentProfile.Status.LAPSED,
            actor=actor_for_user(world["admin"]),
        )

    world["profile"].refresh_from_db()
    assert world["profile"].status == StudentProfile.Status.ACTIVE


def test_enrollment_hold_reason_is_also_encrypted(world):
    enrollment = Enrollment.objects.create(
        student=world["student"],
        dojo=world["dojo"],
        started_on=world["profile"].created_at.date(),
        status=Enrollment.Status.ON_HOLD,
        hold_reason="Family travel",
    )
    assert enrollment.hold_reason == "Family travel"

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT hold_reason FROM identity_enrollment WHERE id = %s",
            [enrollment.pk.hex],
        )
        stored = cursor.fetchone()[0]
    assert looks_encrypted(stored)
    assert "Family travel" not in stored


@pytest.mark.django_db(transaction=True)
def test_hold_reason_migration_encrypts_existing_profile_and_enrollment_values():
    executor = MigrationExecutor(connection)
    executor.migrate([("identity", "0010_student_segment")])
    old_apps = executor.loader.project_state([("identity", "0010_student_segment")]).apps
    OrganizationV10 = old_apps.get_model("identity", "Organization")
    DojoV10 = old_apps.get_model("identity", "Dojo")
    PersonV10 = old_apps.get_model("identity", "Person")
    StudentProfileV10 = old_apps.get_model("identity", "StudentProfile")
    EnrollmentV10 = old_apps.get_model("identity", "Enrollment")

    org = OrganizationV10.objects.create(name="Migration Org", slug="migration-org")
    dojo = DojoV10.objects.create(organization=org, name="Migration Dojo", slug="migration-dojo")
    person = PersonV10.objects.create(organization=org, given_name="Legacy", family_name="Student")
    profile = StudentProfileV10.objects.create(
        person=person,
        home_dojo=dojo,
        status="on_hold",
        hold_reason="Legacy injury",
    )
    enrollment = EnrollmentV10.objects.create(
        student=person,
        dojo=dojo,
        started_on=profile.created_at.date(),
        status="on_hold",
        hold_reason="Legacy travel",
    )

    executor = MigrationExecutor(connection)
    executor.migrate([("identity", "0011_encrypt_hold_reasons")])

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT hold_reason FROM identity_studentprofile WHERE id = %s",
            [profile.pk.hex],
        )
        profile_value = cursor.fetchone()[0]
        cursor.execute(
            "SELECT hold_reason FROM identity_enrollment WHERE id = %s",
            [enrollment.pk.hex],
        )
        enrollment_value = cursor.fetchone()[0]

    assert looks_encrypted(profile_value)
    assert looks_encrypted(enrollment_value)
    assert "Legacy injury" not in profile_value
    assert "Legacy travel" not in enrollment_value
    executor = MigrationExecutor(connection)
    executor.migrate([("identity", "0010_student_segment")])
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT hold_reason FROM identity_studentprofile WHERE id = %s",
            [profile.pk.hex],
        )
        assert cursor.fetchone()[0] == "Legacy injury"
        cursor.execute(
            "SELECT hold_reason FROM identity_enrollment WHERE id = %s",
            [enrollment.pk.hex],
        )
        assert cursor.fetchone()[0] == "Legacy travel"

    # Leave the test database at the latest schema for any following tests.
    MigrationExecutor(connection).migrate([("identity", "0011_encrypt_hold_reasons")])


def test_generic_admin_does_not_expose_enrollment_hold_reason():
    from apps.identity.admin import EnrollmentAdmin

    assert EnrollmentAdmin.exclude == ("hold_reason",)


def _second_active_profile(world):
    person = Person.objects.create(
        organization=world["org"], given_name="Second", family_name="Student"
    )
    return StudentProfile.objects.create(
        person=person,
        home_dojo=world["dojo"],
        status=StudentProfile.Status.ACTIVE,
    )


def test_bulk_hold_and_resume_updates_every_selected_student(world):
    second = _second_active_profile(world)
    actor = actor_for_user(world["admin"])

    held = bulk_transition_student_status(
        profiles=[world["profile"], second],
        to_status=StudentProfile.Status.ON_HOLD,
        hold_reason="Seasonal closure",
        actor=actor,
    )

    assert len(held) == 2
    for profile in held:
        assert profile.status == StudentProfile.Status.ON_HOLD
        assert profile.hold_reason == "Seasonal closure"

    resumed = bulk_transition_student_status(
        profiles=held,
        to_status=StudentProfile.Status.ACTIVE,
        actor=actor,
    )
    assert len(resumed) == 2
    assert all(profile.status == StudentProfile.Status.ACTIVE for profile in resumed)
    assert all(profile.hold_reason == "" for profile in resumed)
    assert (
        AuditLog.objects.filter(action="update", note__startswith="student lifecycle:").count() == 4
    )


def test_bulk_hold_is_all_or_nothing_when_one_student_has_wrong_status(world):
    second = _second_active_profile(world)
    second.status = StudentProfile.Status.TRIAL
    second.save(update_fields=["status", "updated_at"])

    with pytest.raises(ValidationError, match="Every selected student"):
        bulk_transition_student_status(
            profiles=[world["profile"], second],
            to_status=StudentProfile.Status.ON_HOLD,
            hold_reason="Summer break",
            actor=actor_for_user(world["admin"]),
        )

    world["profile"].refresh_from_db()
    second.refresh_from_db()
    assert world["profile"].status == StudentProfile.Status.ACTIVE
    assert second.status == StudentProfile.Status.TRIAL
    assert not AuditLog.objects.filter(action="update").exists()


def test_bulk_resume_does_not_reactivate_lapsed_or_alumni_students(world):
    world["profile"].status = StudentProfile.Status.LAPSED
    world["profile"].save(update_fields=["status", "updated_at"])

    with pytest.raises(ValidationError, match="currently be On hold"):
        bulk_transition_student_status(
            profiles=[world["profile"]],
            to_status=StudentProfile.Status.ACTIVE,
            actor=actor_for_user(world["admin"]),
        )

    world["profile"].refresh_from_db()
    assert world["profile"].status == StudentProfile.Status.LAPSED


def test_bulk_transition_is_bounded_and_only_supports_hold_or_resume(world):
    actor = actor_for_user(world["admin"])

    with pytest.raises(ValidationError, match="at most"):
        bulk_transition_student_status(
            profiles=[world["profile"]] * (BULK_TRANSITION_LIMIT + 1),
            to_status=StudentProfile.Status.ON_HOLD,
            hold_reason="Summer",
            actor=actor,
        )
    with pytest.raises(ValidationError, match="only hold or resume"):
        bulk_transition_student_status(
            profiles=[world["profile"]],
            to_status=StudentProfile.Status.LAPSED,
            actor=actor,
        )


def test_bulk_transition_rolls_back_all_students_on_strict_audit_failure(world, monkeypatch):
    second = _second_active_profile(world)
    real_record = __import__("apps.core.audit", fromlist=["record"]).record
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second audit failed")
        return real_record(*args, **kwargs)

    monkeypatch.setattr("apps.identity.lifecycle.audit.record", fail_second)

    with pytest.raises(RuntimeError, match="second audit failed"):
        bulk_transition_student_status(
            profiles=[world["profile"], second],
            to_status=StudentProfile.Status.ON_HOLD,
            hold_reason="Summer",
            actor=actor_for_user(world["admin"]),
        )

    world["profile"].refresh_from_db()
    second.refresh_from_db()
    assert world["profile"].status == StudentProfile.Status.ACTIVE
    assert second.status == StudentProfile.Status.ACTIVE
    assert not AuditLog.objects.filter(action="update").exists()
