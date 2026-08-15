"""Note model — TODO 1.8.1 and 1.8.3, plan §4.7.

Tests cover: creation, subject types, visibility choices, pinned ordering,
custom queryset pinned_for, tenant isolation, and cross-org guard.
"""

from __future__ import annotations

import uuid

import pytest
from django.core.exceptions import ValidationError

from apps.core.notes import Note
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Dojo, Organization, Person

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def other_org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Other Org", slug="other-org")


@pytest.fixture
def dojo(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo A", slug="dojo-a")


@pytest.fixture
def person(org):
    with allow_unscoped("test setup"):
        return Person.objects.create(organization=org, given_name="Kenji", family_name="Sato")


@pytest.fixture
def note(org, person):
    with allow_unscoped("test setup"):
        return Note.objects.create(
            organization=org,
            author=person,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=uuid.uuid4(),
            body="Needs to work on kihon basics.",
        )


# -- basics -------------------------------------------------------------------


def test_note_creation(note):
    assert note.pk is not None
    assert note.body == "Needs to work on kihon basics."
    assert note.visibility == Note.Visibility.INSTRUCTORS
    assert note.pinned is False


def test_str_shows_preview(note):
    s = str(note)
    assert "student:" in s
    assert "kihon" in s


def test_str_truncates_long_body(note):
    note.body = "x" * 100
    note.save(update_fields=["body"])
    s = str(note)
    assert "…" in s


def test_str_replaces_newlines_in_preview(note):
    note.body = "Line one\nLine two"
    note.save(update_fields=["body"])
    s = str(note)
    assert "Line one Line two" in s


# -- visibility ----------------------------------------------------------------


def test_default_visibility_is_instructors(note):
    assert note.visibility == Note.Visibility.INSTRUCTORS


def test_all_visibility_choices_exist():
    assert len(Note.Visibility) == 4


# -- subject types -------------------------------------------------------------


def test_all_subject_types_exist():
    assert len(Note.SubjectType) == 4


# -- pinned ordering -----------------------------------------------------------


def test_pinned_notes_order_before_unpinned():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Org P", slug="org-p")
        subject = uuid.uuid4()
        older = Note.objects.create(
            organization=org,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=subject,
            body="Older note",
            pinned=False,
        )
        newer = Note.objects.create(
            organization=org,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=subject,
            body="Newer note",
            pinned=False,
        )
        pinned = Note.objects.create(
            organization=org,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=subject,
            body="Pinned note",
            pinned=True,
        )

    notes = list(Note.objects.for_organization(org.pk))
    assert notes[0].pk == pinned.pk
    # newer unpinned comes before older unpinned
    assert notes[1].pk == newer.pk
    assert notes[2].pk == older.pk


# -- pinned_for queryset helper ------------------------------------------------


def test_pinned_for_returns_only_pinned_for_subject():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Org PF", slug="org-pf")
        subject = uuid.uuid4()
        other_subject = uuid.uuid4()
        Note.objects.create(
            organization=org,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=subject,
            body="Pinned",
            pinned=True,
        )
        Note.objects.create(
            organization=org,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=subject,
            body="Not pinned",
            pinned=False,
        )
        Note.objects.create(
            organization=org,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=other_subject,
            body="Pinned on other",
            pinned=True,
        )

    result = Note.objects.for_organization(org.pk).pinned_for(Note.SubjectType.STUDENT, subject)
    assert result.count() == 1
    assert result.first().body == "Pinned"


def test_pinned_for_filters_by_subject_type():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Org PT", slug="org-pt")
        subject = uuid.uuid4()
        Note.objects.create(
            organization=org,
            subject_type=Note.SubjectType.STUDENT,
            subject_id=subject,
            body="Student pinned",
            pinned=True,
        )
        Note.objects.create(
            organization=org,
            subject_type=Note.SubjectType.INVOICE,
            subject_id=subject,
            body="Invoice pinned",
            pinned=True,
        )

    result = Note.objects.for_organization(org.pk).pinned_for(Note.SubjectType.STUDENT, subject)
    assert result.count() == 1
    assert result.first().body == "Student pinned"


# -- author nullable -----------------------------------------------------------


def test_note_without_author_is_allowed(note):
    with allow_unscoped("test setup"):
        no_author = Note.objects.create(
            organization=note.organization,
            subject_type=Note.SubjectType.SESSION,
            subject_id=uuid.uuid4(),
            body="System note",
            author=None,
        )
    assert no_author.author_id is None


# -- tenant isolation ----------------------------------------------------------


def test_cross_org_sees_nothing(note, other_org):
    outsider = Actor(user_id=None, person_id=None, organization_id=other_org.pk)
    assert Note.objects.for_actor(outsider).count() == 0


def test_owning_org_sees_it(note, org):
    actor = Actor(user_id=None, person_id=None, organization_id=org.pk)
    assert Note.objects.for_actor(actor).count() == 1


def test_for_organization_works(note, org):
    assert Note.objects.for_organization(org.pk).count() == 1


# -- cross-org guard -----------------------------------------------------------


def test_author_from_different_org_is_rejected(org, other_org):
    with allow_unscoped("test setup"):
        outsider = Person.objects.create(
            organization=other_org, given_name="Out", family_name="Sider"
        )
        with pytest.raises(ValidationError):
            Note.objects.create(
                organization=org,
                author=outsider,
                subject_type=Note.SubjectType.STUDENT,
                subject_id=uuid.uuid4(),
                body="Bad note",
            )
