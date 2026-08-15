"""Safeguarding notes — TODO 1.8.4, SEC §4.

§4 states three obligations, and each gets its own tests here: safeguarding notes
are *encrypted*, *access-logged*, and *restricted to a named safeguarding role* —
explicitly "not visible to every assistant instructor".

The canonical example in the spec is "father not authorised for pickup", so that
is the note under test. If it leaks, a child goes home with the wrong adult.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.models import AuditLog
from apps.core.notes import Note
from apps.core.safeguarding import may_view_safeguarding, view_safeguarding_notes
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    Dojo,
    GovernanceModel,
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
SECRET = "Father is not authorised for pickup."


def _staff(org, role, email, dojo=None):
    person = Person.objects.create(
        organization=org,
        given_name=role.replace("_", " ").title(),
        family_name="Staff",
    )
    RoleAssignment.objects.create(
        organization=org,
        person=person,
        role=role,
        scope_type=ScopeType.DOJO if dojo else ScopeType.ORG,
        dojo=dojo,
    )
    return User.objects.create_user(email=email, password=PASSWORD, person=person)


@pytest.fixture
def world():
    with allow_unscoped("safeguarding test setup"):
        org = Organization.objects.create(name="SG Org", slug="sg-org")
        dojo_a = Dojo.objects.create(organization=org, name="Dojo A", slug="sg-a")
        dojo_b = Dojo.objects.create(organization=org, name="Dojo B", slug="sg-b")

        student = Person.objects.create(organization=org, given_name="Sok", family_name="Pupil")
        profile = StudentProfile.objects.create(person=student, home_dojo=dojo_a)

        officer = _staff(org, Role.SAFEGUARDING, "officer@sg.test", dojo_a)
        officer_b = _staff(org, Role.SAFEGUARDING, "officer-b@sg.test", dojo_b)
        instructor = _staff(org, Role.INSTRUCTOR, "instructor@sg.test", dojo_a)
        assistant = _staff(org, Role.ASSISTANT_INSTRUCTOR, "assistant@sg.test", dojo_a)
        dojo_admin = _staff(org, Role.DOJO_ADMIN, "dojoadmin@sg.test", dojo_a)
        org_admin = _staff(org, Role.ORG_ADMIN, "orgadmin@sg.test")

        note = Note.objects.create(
            organization=org,
            author=officer.person,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=student.pk,
            body=SECRET,
            visibility=Note.Visibility.SAFEGUARDING,
        )
        ordinary = Note.objects.create(
            organization=org,
            author=instructor.person,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=student.pk,
            body="Works hard on kata.",
            visibility=Note.Visibility.INSTRUCTORS,
        )
    return locals()


# -- encrypted at rest ---------------------------------------------------------


def _stored_bodies() -> str:
    """Every note body exactly as the database holds it.

    ⚠ Read the raw column, not the model: the field decrypts transparently, so
    asserting through the ORM would pass just as happily against a plaintext
    column — precisely the bug this is meant to catch. Selecting every row rather
    than one by id keeps it backend-agnostic; SQLite stores UUIDs undashed.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT body FROM core_note")  # nosec B608
        return "\n".join(row[0] for row in cursor.fetchall())


def test_the_body_is_ciphertext_in_the_database(world):
    stored = _stored_bodies()

    assert SECRET not in stored, "the safeguarding note is sitting in the database in plaintext"
    assert "authorised" not in stored


def test_it_still_reads_back_correctly(world):
    with allow_unscoped("safeguarding test"):
        assert Note.objects.get(pk=world["note"].pk).body == SECRET


def test_ordinary_note_bodies_are_encrypted_too(world):
    """The column is shared, so encryption is all-or-nothing. It is 'all'."""
    assert "Works hard" not in _stored_bodies()


# -- restricted to a named role ------------------------------------------------


def _visible_bodies(world, user):
    actor = actor_for_user(user)
    return {
        note.body
        for note in Note.objects.for_actor(actor)
        .filter(subject_type=Note.SubjectType.STUDENT, subject_id=world["student"].pk)
        .visible_to(actor, subject=world["profile"], governance_model=GovernanceModel.CENTRAL)
    }


@pytest.mark.parametrize(
    "role_key", ["instructor", "assistant", "dojo_admin", "org_admin", "officer"]
)
def test_no_role_reaches_a_safeguarding_note_through_the_ordinary_path(world, role_key):
    """⚠ Not even the safeguarding officer, and not even its own author.

    visible_to is what every ordinary screen uses. Excluding safeguarding there
    unconditionally means a new screen cannot leak one by forgetting a filter —
    it has to go and ask for it deliberately.
    """
    assert SECRET not in _visible_bodies(world, world[role_key])


def test_the_officer_reads_it_through_the_service(world):
    notes = view_safeguarding_notes(
        subject=world["profile"], actor=actor_for_user(world["officer"])
    )

    assert [note.body for note in notes] == [SECRET]


@pytest.mark.parametrize("role_key", ["instructor", "assistant", "dojo_admin", "org_admin"])
def test_the_service_refuses_every_role_but_safeguarding(world, role_key):
    """§4: "not visible to every assistant instructor" — nor to the org admin."""
    with pytest.raises(PermissionDenied):
        view_safeguarding_notes(subject=world["profile"], actor=actor_for_user(world[role_key]))


def test_a_safeguarding_officer_at_another_dojo_is_refused(world):
    """The role is scoped to a dojo; it is not a master key to the organisation."""
    with pytest.raises(PermissionDenied):
        view_safeguarding_notes(subject=world["profile"], actor=actor_for_user(world["officer_b"]))


def test_an_anonymous_actor_is_refused(world):
    anonymous = Actor(user_id=None, person_id=None, organization_id=world["org"].pk)

    with pytest.raises(PermissionDenied):
        view_safeguarding_notes(subject=world["profile"], actor=anonymous)


def test_may_view_safeguarding_agrees_with_the_service(world):
    assert may_view_safeguarding(actor_for_user(world["officer"]), world["profile"]) is True
    assert may_view_safeguarding(actor_for_user(world["dojo_admin"]), world["profile"]) is False


# -- access-logged -------------------------------------------------------------


def _access_logs():
    return AuditLog.objects.filter(action="view_safeguarding")


def test_reading_writes_an_access_log(world):
    view_safeguarding_notes(subject=world["profile"], actor=actor_for_user(world["officer"]))

    entry = _access_logs().get()
    assert entry.actor_person_id == world["officer"].person_id
    assert entry.subject_id == str(world["profile"].pk)
    assert "StudentProfile" in entry.subject_type


def test_the_log_still_names_the_reader_after_the_person_is_gone(world):
    """⚠ actor_person is SET_NULL, and this log is read years later.

    Staff leave and records get redacted. If the entry's only link to a human is
    a foreign key, "who read this child's file" becomes unanswerable exactly when
    somebody finally asks it.
    """
    officer = world["officer"]
    view_safeguarding_notes(subject=world["profile"], actor=actor_for_user(officer))

    entry = _access_logs().get()
    assert officer.email in entry.actor_label

    # The deliberate erasure path. Person is a soft-delete model, so an ordinary
    # .delete() is refused; this is what a real right-to-erasure request runs.
    with allow_unscoped("safeguarding test"):
        Person.objects.filter(pk=officer.person_id).hard_delete()

    entry.refresh_from_db()
    assert entry.actor_person_id is None, "precondition: the FK is cleared"
    assert officer.email in entry.actor_label, "the log forgot who looked"


def test_an_empty_result_is_still_logged(world):
    """ "Who went looking" is the question §4 exists to answer.

    A search that found nothing is still somebody going through a child's
    safeguarding file, and it is the one an abuser would rely on not being kept.
    """
    with allow_unscoped("safeguarding test"):
        other = Person.objects.create(
            organization=world["org"], given_name="No", family_name="Notes"
        )
        empty_profile = StudentProfile.objects.create(person=other, home_dojo=world["dojo_a"])

    notes = view_safeguarding_notes(subject=empty_profile, actor=actor_for_user(world["officer"]))

    assert notes == []
    assert _access_logs().count() == 1


def test_a_refused_read_writes_no_access_log(world):
    """A denial is not an access. It must not dilute the log it is not part of."""
    with pytest.raises(PermissionDenied):
        view_safeguarding_notes(subject=world["profile"], actor=actor_for_user(world["dojo_admin"]))

    assert _access_logs().count() == 0


def test_reading_does_not_copy_the_body_into_the_access_log(world):
    view_safeguarding_notes(subject=world["profile"], actor=actor_for_user(world["officer"]))

    for entry in AuditLog.objects.all():
        assert SECRET not in str(entry.before) + str(entry.after) + entry.note


def test_snapshotting_a_note_never_captures_its_body(world):
    """⚠ Otherwise the audit trail is the easiest place to read the secret.

    A snapshot is taken of whatever a caller passes, so this asserts on the
    mechanism — `body` being in audit.SENSITIVE_FIELDS — rather than on the one
    code path that happens not to snapshot a Note today. The next writer of a
    note-editing screen gets the protection without having to know it exists.
    """
    from apps.core import audit

    audit.record(
        "update",
        actor=actor_for_user(world["officer"]),
        subject=world["note"],
        before=audit.snapshot(world["note"]),
        after=audit.snapshot(world["note"]),
    )

    entry = AuditLog.objects.filter(action="update").get()
    assert entry.before is not None, "the snapshot itself must not be empty"
    assert "body" not in entry.before, "the note body was copied into the audit trail"
    assert SECRET not in str(entry.before) + str(entry.after)


def test_str_does_not_preview_a_safeguarding_body(world):
    """__str__ ends up in admin lists, tracebacks and stray log lines."""
    assert SECRET not in str(world["note"])
    assert "Works hard" in str(world["ordinary"]), "ordinary notes still preview"


# -- on the page ---------------------------------------------------------------


def _detail(client, world, user, tab="notes"):
    client.force_login(user)
    return client.get(
        reverse("student-detail", args=[world["student"].pk]), {"tab": tab}
    ).content.decode()


def test_the_officer_sees_it_on_the_notes_tab(client, world):
    assert SECRET in _detail(client, world, world["officer"])


@pytest.mark.parametrize("role_key", ["instructor", "assistant", "dojo_admin", "org_admin"])
def test_the_page_does_not_leak_it_to_anybody_else(client, world, role_key):
    body = _detail(client, world, world[role_key])

    assert SECRET not in body
    assert "Safeguarding" not in body, "the section itself should not be offered"


def test_opening_another_tab_does_not_log_a_safeguarding_access(client, world):
    """Otherwise every page view logs one and the trail becomes unreadable."""
    _detail(client, world, world["officer"], tab="attendance")

    assert _access_logs().count() == 0


# -- the data migration --------------------------------------------------------


def test_the_backfill_encrypts_existing_plaintext_and_reverses(world):
    """⚠ The one operation here that can silently destroy data.

    A fresh test database runs the migration over zero rows, so the backfill is
    never actually exercised by the suite booting. This drives it directly: write
    plaintext straight into the column the way a pre-0008 row looked, run the
    forward function, and prove it is both encrypted at rest and readable back.
    Then run the reverse and prove a downgrade does not strand ciphertext in a
    column that can no longer decrypt it.
    """
    import importlib

    from django.db import connection

    # A module whose name starts with a digit cannot be imported by statement.
    migration = importlib.import_module("apps.core.migrations.0008_encrypt_note_bodies")
    encrypt_existing_bodies = migration.encrypt_existing_bodies
    decrypt_existing_bodies = migration.decrypt_existing_bodies

    plain = "Pre-migration plaintext about a child."
    with connection.cursor() as cursor:
        cursor.execute("UPDATE core_note SET body = %s", [plain])

    encrypt_existing_bodies(None, connection.schema_editor())
    assert plain not in _stored_bodies(), "the backfill left plaintext in the column"
    with allow_unscoped("safeguarding test"):
        assert Note.objects.get(pk=world["note"].pk).body == plain

    decrypt_existing_bodies(None, connection.schema_editor())
    assert plain in _stored_bodies(), "the reverse migration did not restore plaintext"
