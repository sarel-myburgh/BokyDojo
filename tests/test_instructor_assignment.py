"""InstructorAssignment — TODO 1.3.5, plan §4.3."""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Dojo, InstructorAssignment, Organization, Person

pytestmark = pytest.mark.django_db

JAN = datetime.date(2024, 1, 1)
JUN = datetime.date(2024, 6, 1)
MAY_END = datetime.date(2024, 5, 31)


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def dojo(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo A", slug="dojo-a")


@pytest.fixture
def dojo_b(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo B", slug="dojo-b")


@pytest.fixture
def person(org):
    with allow_unscoped("test setup"):
        return Person.objects.create(organization=org, given_name="Takeshi", family_name="Yamada")


@pytest.fixture
def assignment(dojo, person):
    with allow_unscoped("test setup"):
        return InstructorAssignment.objects.create(dojo=dojo, person=person, started_on=JAN)


# -- basics -------------------------------------------------------------------


def test_creation(assignment):
    assert assignment.started_on == JAN
    assert assignment.ended_on is None
    assert assignment.is_head_instructor is False


def test_str_identifies_person_and_dojo(assignment):
    rendered = str(assignment)
    assert "Takeshi" in rendered
    assert "Dojo A" in rendered


def test_is_active_reflects_ended_on(assignment):
    assert assignment.is_active is True
    assignment.ended_on = datetime.date(2024, 12, 31)
    assignment.save(update_fields=["ended_on"])
    assert assignment.is_active is False


def test_head_instructor_flag(dojo, person):
    with allow_unscoped("test setup"):
        head = InstructorAssignment.objects.create(
            dojo=dojo, person=person, started_on=JAN, is_head_instructor=True
        )
    assert head.is_head_instructor is True
    assert "Head instructor" in str(head)


# -- one active assignment per dojo -------------------------------------------


def test_second_active_assignment_at_same_dojo_is_rejected(dojo, person):
    with allow_unscoped("test setup"):
        InstructorAssignment.objects.create(dojo=dojo, person=person, started_on=JAN)
        with pytest.raises(IntegrityError):
            InstructorAssignment.objects.create(dojo=dojo, person=person, started_on=JUN)


def test_reassignment_allowed_once_the_first_has_ended(dojo, person):
    """An instructor who leaves and returns gets a second row, not an edited one —
    the teaching history is a record."""
    with allow_unscoped("test setup"):
        first = InstructorAssignment.objects.create(
            dojo=dojo, person=person, started_on=JAN, ended_on=MAY_END
        )
        second = InstructorAssignment.objects.create(dojo=dojo, person=person, started_on=JUN)

    assert first.pk != second.pk
    with allow_unscoped("verifying history survives"):
        assert InstructorAssignment.objects.filter(person=person, dojo=dojo).count() == 2


def test_one_person_may_teach_at_several_dojos(dojo, dojo_b, person):
    with allow_unscoped("test setup"):
        InstructorAssignment.objects.create(dojo=dojo, person=person, started_on=JAN)
        InstructorAssignment.objects.create(dojo=dojo_b, person=person, started_on=JAN)

    actor = Actor(user_id=None, person_id=None, organization_id=dojo.organization_id)
    assert InstructorAssignment.objects.for_actor(actor).count() == 2


# -- tenancy ------------------------------------------------------------------


def test_another_organisation_sees_nothing(org, assignment):
    with allow_unscoped("test setup"):
        other = Organization.objects.create(name="Other Org", slug="other-org")
    outsider = Actor(user_id=None, person_id=None, organization_id=other.pk)
    assert InstructorAssignment.objects.for_actor(outsider).count() == 0


def test_owning_organisation_sees_it(org, assignment):
    actor = Actor(user_id=None, person_id=None, organization_id=org.pk)
    assert InstructorAssignment.objects.for_actor(actor).count() == 1


def test_dojo_scoped_actor_sees_only_their_own_dojo(org, dojo, dojo_b, person):
    """The case that matters: an instructor list must not leak across dojos
    within the same organisation. This is what tenant_dojo_path is for."""
    with allow_unscoped("test setup"):
        other_person = Person.objects.create(
            organization=org, given_name="Sokha", family_name="Chan"
        )
        InstructorAssignment.objects.create(dojo=dojo, person=person, started_on=JAN)
        InstructorAssignment.objects.create(dojo=dojo_b, person=other_person, started_on=JAN)

    scoped_to_a = Actor(
        user_id=None,
        person_id=None,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo.pk}),
    )
    visible = list(InstructorAssignment.objects.for_actor(scoped_to_a))
    assert len(visible) == 1
    assert visible[0].dojo_id == dojo.pk


def test_for_organization_works_without_an_actor(org, assignment):
    assert InstructorAssignment.objects.for_organization(org.pk).count() == 1
