"""Writing notes — TODO 1.8.x authoring.

Reading a level and writing one are separate questions. These tests pin the
answer to the second: you may write only at a level you could read back, plus
``private``, plus ``safeguarding`` if you hold the named role.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core.models import AuditLog
from apps.core.note_authoring import MAX_BODY_LENGTH, create_note, writable_visibilities
from apps.core.notes import Note
from apps.core.safeguarding import view_safeguarding_notes
from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    Dojo,
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


def _staff(org, role, email, dojo=None):
    person = Person.objects.create(
        organization=org, given_name=role.replace("_", " ").title(), family_name="Author"
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
    with allow_unscoped("note authoring test setup"):
        org = Organization.objects.create(name="Author Org", slug="author-org")
        dojo_a = Dojo.objects.create(organization=org, name="Dojo A", slug="author-a")
        dojo_b = Dojo.objects.create(organization=org, name="Dojo B", slug="author-b")

        student = Person.objects.create(organization=org, given_name="Sok", family_name="Pupil")
        profile = StudentProfile.objects.create(person=student, home_dojo=dojo_a)

        instructor = _staff(org, Role.INSTRUCTOR, "instructor@author.test", dojo_a)
        instructor_b = _staff(org, Role.INSTRUCTOR, "instructor-b@author.test", dojo_b)
        assistant = _staff(org, Role.ASSISTANT_INSTRUCTOR, "assistant@author.test", dojo_a)
        dojo_admin = _staff(org, Role.DOJO_ADMIN, "dojoadmin@author.test", dojo_a)
        officer = _staff(org, Role.SAFEGUARDING, "officer@author.test", dojo_a)
        guardian = _staff(org, Role.GUARDIAN, "parent@author.test", dojo_a)
    return locals()


# -- which levels each role may author -----------------------------------------


@pytest.mark.parametrize(
    ("role_key", "expected"),
    [
        ("instructor", ["private", "instructors", "parent_visible"]),
        ("dojo_admin", ["private", "instructors", "parent_visible", "admins"]),
        (
            "officer",
            ["private", "instructors", "parent_visible", "admins", "safeguarding"],
        ),
    ],
)
def test_writable_levels_per_role(world, role_key, expected):
    actor = actor_for_user(world[role_key])

    assert writable_visibilities(actor, world["profile"]) == expected


@pytest.mark.parametrize("role_key", ["assistant", "guardian"])
def test_roles_without_note_write_may_author_nothing(world, role_key):
    """⚠ An assistant instructor holds no NOTE_WRITE — §4 names them explicitly."""
    actor = actor_for_user(world[role_key])

    assert writable_visibilities(actor, world["profile"]) == []


def test_an_instructor_at_another_dojo_may_author_nothing(world):
    actor = actor_for_user(world["instructor_b"])

    assert writable_visibilities(actor, world["profile"]) == []


# -- the service enforces it, not just the form --------------------------------


def test_an_instructor_writes_an_instructor_note(world):
    note = create_note(
        subject=world["profile"],
        body="  Needs work on zenkutsu-dachi.  ",
        visibility=Note.Visibility.INSTRUCTORS,
        actor=actor_for_user(world["instructor"]),
    )

    assert note.body == "Needs work on zenkutsu-dachi.", "the body should be stripped"
    assert note.author_id == world["instructor"].person_id
    assert note.subject_id == world["student"].pk


def test_an_instructor_cannot_write_an_admin_note(world):
    """The documented trade-off: no write-only channel into a child's file."""
    with pytest.raises(PermissionDenied):
        create_note(
            subject=world["profile"],
            body="Quietly escalate this.",
            visibility=Note.Visibility.ADMINS,
            actor=actor_for_user(world["instructor"]),
        )


@pytest.mark.parametrize("role_key", ["instructor", "dojo_admin"])
def test_only_the_safeguarding_role_writes_a_safeguarding_note(world, role_key):
    with pytest.raises(PermissionDenied):
        create_note(
            subject=world["profile"],
            body="Father not authorised for pickup.",
            visibility=Note.Visibility.SAFEGUARDING,
            actor=actor_for_user(world[role_key]),
        )


def test_the_officer_writes_one_and_can_read_it_back(world):
    actor = actor_for_user(world["officer"])
    create_note(
        subject=world["profile"],
        body="Father not authorised for pickup.",
        visibility=Note.Visibility.SAFEGUARDING,
        actor=actor,
    )

    notes = view_safeguarding_notes(subject=world["profile"], actor=actor)
    assert [note.body for note in notes] == ["Father not authorised for pickup."]


@pytest.mark.parametrize("role_key", ["assistant", "guardian"])
def test_a_role_without_note_write_is_refused(world, role_key):
    with pytest.raises(PermissionDenied):
        create_note(
            subject=world["profile"],
            body="Should not be possible.",
            visibility=Note.Visibility.INSTRUCTORS,
            actor=actor_for_user(world[role_key]),
        )


def test_an_instructor_cannot_write_about_another_dojos_student(world):
    with pytest.raises(PermissionDenied):
        create_note(
            subject=world["profile"],
            body="Not my student.",
            visibility=Note.Visibility.INSTRUCTORS,
            actor=actor_for_user(world["instructor_b"]),
        )


def test_an_unknown_visibility_is_refused(world):
    """Not a ValidationError — an invented level is not a typo."""
    with pytest.raises(PermissionDenied):
        create_note(
            subject=world["profile"],
            body="x",
            visibility="everyone",
            actor=actor_for_user(world["officer"]),
        )


@pytest.mark.parametrize("body", ["", "   ", "\n\t "])
def test_an_empty_note_is_rejected(world, body):
    with pytest.raises(ValidationError):
        create_note(
            subject=world["profile"],
            body=body,
            visibility=Note.Visibility.INSTRUCTORS,
            actor=actor_for_user(world["instructor"]),
        )


def test_an_over_long_note_is_rejected(world):
    with pytest.raises(ValidationError):
        create_note(
            subject=world["profile"],
            body="x" * (MAX_BODY_LENGTH + 1),
            visibility=Note.Visibility.INSTRUCTORS,
            actor=actor_for_user(world["instructor"]),
        )


# -- audit ---------------------------------------------------------------------


def test_writing_is_audited_without_quoting_the_note(world):
    """⚠ Recording that a note was written is the audit's job; quoting it is not."""
    secret = "Bruising noted on both forearms."
    create_note(
        subject=world["profile"],
        body=secret,
        visibility=Note.Visibility.SAFEGUARDING,
        actor=actor_for_user(world["officer"]),
    )

    entry = AuditLog.objects.filter(action="create", subject_type="core.Note").get()
    assert entry.actor_person_id == world["officer"].person_id
    assert "safeguarding" in entry.note
    assert secret not in str(entry.after) + str(entry.before) + entry.note
    assert "body" not in (entry.after or {})


def test_a_refused_write_records_no_note_and_no_audit(world):
    with pytest.raises(PermissionDenied):
        create_note(
            subject=world["profile"],
            body="Should not persist.",
            visibility=Note.Visibility.SAFEGUARDING,
            actor=actor_for_user(world["instructor"]),
        )

    with allow_unscoped("note authoring test"):
        assert Note.objects.count() == 0
    assert AuditLog.objects.filter(subject_type="core.Note").count() == 0


# -- the page ------------------------------------------------------------------


def _post(client, world, user, **overrides):
    client.force_login(user)
    payload = {"body": "From the page.", "visibility": Note.Visibility.INSTRUCTORS}
    payload.update(overrides)
    return client.post(
        reverse("student-note-create", args=[world["student"].pk]), payload, follow=True
    )


def test_the_composer_is_offered_to_an_author(client, world):
    client.force_login(world["instructor"])

    body = client.get(
        reverse("student-detail", args=[world["student"].pk]), {"tab": "notes"}
    ).content.decode()

    # ⚠ Assert on the rendered path. {% url %} emits the URL, never the route
    # name, so checking for "student-note-create" would fail on a working page.
    assert reverse("student-note-create", args=[world["student"].pk]) in body
    assert "csrfmiddlewaretoken" in body


@pytest.mark.parametrize("role_key", ["assistant", "guardian"])
def test_the_composer_is_not_offered_to_a_role_that_cannot_write(client, world, role_key):
    client.force_login(world[role_key])

    response = client.get(reverse("student-detail", args=[world["student"].pk]), {"tab": "notes"})

    # Guardians are refused the page outright; an assistant may read it but must
    # not be shown a composer.
    if response.status_code == 200:
        action = reverse("student-note-create", args=[world["student"].pk])
        assert action not in response.content.decode()


def test_posting_from_the_page_saves_and_shows_the_note(client, world):
    body = _post(client, world, world["instructor"]).content.decode()

    assert "From the page." in body
    with allow_unscoped("note authoring test"):
        assert Note.objects.count() == 1


def test_posting_a_level_the_form_never_offered_is_refused(client, world):
    """An instructor posting `safeguarding` directly gets nowhere.

    ⚠ Two layers refuse this and the form's ChoiceField is the one that fires
    first, so this test does *not* exercise the service's re-check — deleting
    that re-check leaves this test green. The re-check is covered directly by
    test_only_the_safeguarding_role_writes_a_safeguarding_note; what this pins is
    that the HTTP path as a whole refuses, whichever layer catches it.
    """
    response = _post(client, world, world["instructor"], visibility=Note.Visibility.SAFEGUARDING)

    assert response.status_code in (200, 403)
    with allow_unscoped("note authoring test"):
        assert Note.objects.count() == 0, "a level the form never offered was accepted"


def test_a_pinned_note_reaches_the_student_header(client, world):
    _post(client, world, world["instructor"], body="Collect from the side door.", pinned="on")

    body = client.get(
        reverse("student-detail", args=[world["student"].pk]), {"tab": "attendance"}
    ).content.decode()

    assert "Collect from the side door." in body, "a pinned note should surface as an alert"


def test_posting_without_csrf_is_rejected(client, world):
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(world["instructor"])

    response = strict.post(
        reverse("student-note-create", args=[world["student"].pk]),
        {"body": "No token.", "visibility": Note.Visibility.INSTRUCTORS},
    )

    assert response.status_code == 403
    with allow_unscoped("note authoring test"):
        assert Note.objects.count() == 0
