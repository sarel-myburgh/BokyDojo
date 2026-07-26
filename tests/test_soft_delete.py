"""Soft delete — TODO 0.3.2, plan §2.

Person is the concrete case: student records are attached to attendance,
rank awards and invoices, all of which are evidence. They are never removed.
"""

from __future__ import annotations

import pytest

from apps.core.scoping import Actor, UnscopedAccessError, allow_unscoped
from apps.identity.models import Organization, Person

pytestmark = pytest.mark.django_db


@pytest.fixture
def org_with_people():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Soft Org", slug="soft-org")
        keep = Person.objects.create(organization=org, given_name="Keep", family_name="Me")
        gone = Person.objects.create(organization=org, given_name="Gone", family_name="Away")
    actor = Actor(user_id=None, person_id=keep.pk, organization_id=org.pk)
    return org, keep, gone, actor


def test_soft_deleted_rows_are_hidden_from_for_actor(org_with_people):
    _org, _keep, gone, actor = org_with_people
    gone.soft_delete(actor)

    names = {p.given_name for p in Person.objects.for_actor(actor)}
    assert names == {"Keep"}


def test_soft_deleted_row_still_exists_in_the_table(org_with_people):
    _org, _keep, gone, actor = org_with_people
    gone.soft_delete(actor)

    with allow_unscoped("verifying the row survives"):
        assert Person.objects.filter(pk=gone.pk).exists()


def test_soft_delete_records_who_and_when(org_with_people):
    _org, keep, gone, actor = org_with_people
    gone.soft_delete(actor)
    gone.refresh_from_db()

    assert gone.deleted_at is not None
    assert gone.deleted_by_id == keep.pk
    assert gone.is_deleted


def test_including_deleted_can_be_asked_for_explicitly(org_with_people):
    _org, _keep, gone, actor = org_with_people
    gone.soft_delete(actor)

    names = {p.given_name for p in Person.objects.for_actor_including_deleted(actor)}
    assert names == {"Keep", "Gone"}


def test_restore_brings_a_row_back(org_with_people):
    _org, _keep, gone, actor = org_with_people
    gone.soft_delete(actor)
    gone.restore()

    names = {p.given_name for p in Person.objects.for_actor(actor)}
    assert names == {"Keep", "Gone"}


def test_queryset_hard_delete_is_refused(org_with_people):
    _org, _keep, _gone, actor = org_with_people
    with pytest.raises(NotImplementedError):
        Person.objects.for_actor(actor).delete()


def test_soft_delete_queryset_still_enforces_tenant_scoping(org_with_people):
    """Soft-delete composes on top of scoping — it must not weaken it."""
    with pytest.raises(UnscopedAccessError):
        list(Person.objects.all())


def test_alive_and_dead_helpers(org_with_people):
    _org, _keep, gone, actor = org_with_people
    gone.soft_delete(actor)

    with allow_unscoped("checking both partitions"):
        assert Person.objects.alive().count() == 1
        assert Person.objects.dead().count() == 1
