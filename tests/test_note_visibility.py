"""Note visibility enforcement — TODO 1.8.2, plan §4.7.

The levels are only worth storing if reading them is filtered. These tests are
written against the four levels as the plan states them, not against the
implementation: private is the author's alone, instructors and admins are
separate grants, and parent_visible reaches a guardian of *that* child.
"""

from __future__ import annotations

import uuid

import pytest

from apps.core.notes import Note
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.models import (
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

pytestmark = pytest.mark.django_db
PASSWORD = "correct-horse-battery"


def _staff(org, role, email, dojo=None):
    person = Person.objects.create(
        organization=org,
        given_name=role.replace("_", " ").title(),
        family_name="NoteStaff",
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
    with allow_unscoped("note visibility test setup"):
        org = Organization.objects.create(name="Note Org", slug="note-org")
        dojo_a = Dojo.objects.create(organization=org, name="Dojo A", slug="note-a")
        dojo_b = Dojo.objects.create(organization=org, name="Dojo B", slug="note-b")

        student = Person.objects.create(organization=org, given_name="Sok", family_name="Pupil")
        profile = StudentProfile.objects.create(person=student, home_dojo=dojo_a)

        sibling = Person.objects.create(organization=org, given_name="Dara", family_name="Other")
        StudentProfile.objects.create(person=sibling, home_dojo=dojo_a)

        author = _staff(org, Role.INSTRUCTOR, "author@note.test", dojo_a)
        colleague = _staff(org, Role.INSTRUCTOR, "colleague@note.test", dojo_a)
        dojo_admin = _staff(org, Role.DOJO_ADMIN, "dojoadmin@note.test", dojo_a)
        instructor_b = _staff(org, Role.INSTRUCTOR, "instructor-b@note.test", dojo_b)

        guardian = _staff(org, Role.GUARDIAN, "guardian@note.test", dojo_a)
        stranger_guardian = _staff(org, Role.GUARDIAN, "stranger@note.test", dojo_a)
        GuardianLink.objects.create(
            guardian=guardian.person,
            student=student,
            relationship=GuardianLink.Relationship.MOTHER,
        )
        GuardianLink.objects.create(
            guardian=stranger_guardian.person,
            student=sibling,
            relationship=GuardianLink.Relationship.FATHER,
        )

        levels = {}
        for visibility in Note.Visibility:
            levels[visibility.value] = Note.objects.create(
                organization=org,
                author=author.person,
                subject_type=Note.SubjectType.STUDENT,
                subject_id=student.pk,
                body=f"body-{visibility.value}",
                visibility=visibility,
            )
    return locals()


def visible(world, user):
    """The note bodies this user may read about the subject student."""
    actor = actor_for_user(user)
    return {
        note.body
        for note in Note.objects.for_actor(actor)
        .filter(subject_type=Note.SubjectType.STUDENT, subject_id=world["student"].pk)
        .visible_to(actor, subject=world["profile"], governance_model=GovernanceModel.CENTRAL)
    }


# -- private -------------------------------------------------------------------


def test_author_reads_their_own_private_note(world):
    assert "body-private" in visible(world, world["author"])


def test_a_colleague_cannot_read_a_private_note(world):
    """Same role, same dojo, same student — and still not their note."""
    assert "body-private" not in visible(world, world["colleague"])


def test_an_admin_cannot_read_someone_elses_private_note(world):
    """Private is not "admins plus the author". Seniority does not open it."""
    assert "body-private" not in visible(world, world["dojo_admin"])


# -- instructors ---------------------------------------------------------------


def test_an_instructor_reads_instructor_notes(world):
    assert "body-instructors" in visible(world, world["colleague"])


def test_an_instructor_cannot_read_admin_notes(world):
    """ "Escalate this to the office" is the entire point of the admin level."""
    assert "body-admins" not in visible(world, world["colleague"])


def test_an_admin_reads_admin_notes(world):
    assert "body-admins" in visible(world, world["dojo_admin"])


def test_an_instructor_at_another_dojo_reads_nothing(world):
    """The subject carries the dojo, so a dojo-scoped grant must not reach here."""
    assert visible(world, world["instructor_b"]) == set()


# -- parent_visible ------------------------------------------------------------


def test_a_guardian_reads_a_parent_visible_note_about_their_child(world):
    assert "body-parent_visible" in visible(world, world["guardian"])


def test_a_guardian_reads_nothing_else_about_their_child(world):
    """Parent-visible is the only level a guardian is entitled to."""
    assert visible(world, world["guardian"]) == {"body-parent_visible"}


def test_a_guardian_of_another_child_reads_nothing(world):
    """Being *a* parent is not being *this* child's parent."""
    assert visible(world, world["stranger_guardian"]) == set()


def test_instructors_also_read_parent_visible_notes(world):
    """Marking a note parent-safe must not hide it from the staff room."""
    assert "body-parent_visible" in visible(world, world["colleague"])


# -- actors with no standing ---------------------------------------------------


def test_an_anonymous_actor_reads_nothing(world):
    anonymous = Actor(user_id=None, person_id=None, organization_id=world["org"].pk)

    assert Note.objects.for_organization(world["org"].pk).visible_to(anonymous).count() == 0


def test_a_none_actor_reads_nothing(world):
    assert Note.objects.for_organization(world["org"].pk).visible_to(None).count() == 0


def test_a_person_less_actor_does_not_inherit_authorless_notes(world):
    """⚠ `Q(author_id=actor.person_id)` with no person is `author_id IS NULL`.

    That is not "no author clause" — it matches every system-written note. Such
    an actor is already anonymous, so this is defence in depth rather than a
    live hole, but the clause must never be built from a None.
    """
    with allow_unscoped("note visibility test setup"):
        Note.objects.create(
            organization=world["org"],
            author=None,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=world["student"].pk,
            body="body-system-written",
            visibility=Note.Visibility.PRIVATE,
        )
    person_less = Actor(user_id=uuid.uuid4(), person_id=None, organization_id=world["org"].pk)

    bodies = {
        note.body for note in Note.objects.for_organization(world["org"].pk).visible_to(person_less)
    }
    assert "body-system-written" not in bodies


def test_a_system_actor_reads_everything(world):
    """Background jobs and management commands are deliberately exempt."""
    count = Note.objects.for_organization(world["org"].pk).visible_to(Actor.system()).count()

    assert count == len(Note.Visibility)


# -- the level is enforced on the page, not just in the queryset ---------------


def test_the_student_page_shows_only_permitted_notes(client, world):
    client.force_login(world["colleague"])

    body = client.get(f"/students/{world['student'].pk}/?tab=notes").content.decode()

    assert "body-instructors" in body
    assert "body-parent_visible" in body
    assert "body-private" not in body, "a colleague's private note reached the page"
    assert "body-admins" not in body, "an admin-level note reached an instructor"
