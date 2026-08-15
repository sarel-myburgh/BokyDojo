"""Tenant scoping enforcement — TODO 0.3.3, 0.3.4, SEC 2.2."""

from __future__ import annotations

import pytest

from apps.core.scoping import Actor, UnscopedAccessError, allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    Dojo,
    GovernanceModel,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_orgs():
    with allow_unscoped("test setup"):
        org_a = Organization.objects.create(name="Alpha Karate", slug="alpha")
        org_b = Organization.objects.create(
            name="Beta Kai", slug="beta", governance_model=GovernanceModel.FEDERATED
        )
        dojo_a1 = Dojo.objects.create(organization=org_a, name="Alpha Central", slug="a1")
        dojo_a2 = Dojo.objects.create(organization=org_a, name="Alpha North", slug="a2")
        dojo_b1 = Dojo.objects.create(organization=org_b, name="Beta Riverside", slug="b1")

        alice = Person.objects.create(organization=org_a, given_name="Alice", family_name="Admin")
        ian = Person.objects.create(organization=org_a, given_name="Ian", family_name="Instructor")
        bob = Person.objects.create(organization=org_b, given_name="Bob", family_name="Beta")

        RoleAssignment.objects.create(
            organization=org_a,
            person=alice,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
            can_view_financials=True,
        )
        RoleAssignment.objects.create(
            organization=org_a,
            person=ian,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo_a1,
        )
        RoleAssignment.objects.create(
            organization=org_b,
            person=bob,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )

        alice_user = User.objects.create_user("alice@example.com", "pw", person=alice)
        ian_user = User.objects.create_user("ian@example.com", "pw", person=ian)

    return {
        "org_a": org_a,
        "org_b": org_b,
        "dojo_a1": dojo_a1,
        "dojo_a2": dojo_a2,
        "dojo_b1": dojo_b1,
        "alice": alice,
        "ian": ian,
        "bob": bob,
        "alice_user": alice_user,
        "ian_user": ian_user,
    }


def test_unscoped_evaluation_raises(two_orgs):
    """The default is refusal, not a full table scan."""
    with pytest.raises(UnscopedAccessError):
        list(Person.objects.all())


def test_unscoped_count_raises(two_orgs):
    with pytest.raises(UnscopedAccessError):
        Person.objects.count()


def test_unscoped_exists_raises(two_orgs):
    with pytest.raises(UnscopedAccessError):
        Person.objects.filter(given_name="Alice").exists()


def test_unscoped_update_raises(two_orgs):
    with pytest.raises(UnscopedAccessError):
        Person.objects.filter(given_name="Alice").update(city="Phnom Penh")


def test_allow_unscoped_permits_deliberate_access(two_orgs):
    with allow_unscoped("test asserting the escape hatch works"):
        assert Person.objects.count() == 3


def test_unscoped_requires_a_reason(two_orgs):
    with pytest.raises(ValueError):
        Person.objects.unscoped("")


def test_for_actor_limits_to_own_organisation(two_orgs):
    actor = actor_for_user(two_orgs["alice_user"])
    names = {p.given_name for p in Person.objects.for_actor(actor)}
    assert names == {"Alice", "Ian"}
    assert "Bob" not in names


def test_actor_from_other_org_sees_nothing_of_ours(two_orgs):
    foreign_actor = Actor(
        user_id=None,
        person_id=two_orgs["bob"].pk,
        organization_id=two_orgs["org_b"].pk,
    )
    people = list(Person.objects.for_actor(foreign_actor))
    assert [p.given_name for p in people] == ["Bob"]


def test_dojo_scoped_actor_sees_only_their_dojo(two_orgs):
    actor = actor_for_user(two_orgs["ian_user"])
    assert actor.dojo_ids == frozenset({two_orgs["dojo_a1"].pk})

    dojos = list(Dojo.objects.for_actor(actor))
    assert [d.slug for d in dojos] == ["a1"]


def test_org_scoped_actor_sees_all_dojos_in_org(two_orgs):
    actor = actor_for_user(two_orgs["alice_user"])
    assert actor.is_org_wide
    slugs = sorted(d.slug for d in Dojo.objects.for_actor(actor))
    assert slugs == ["a1", "a2"]


def test_actor_with_no_organisation_sees_nothing(two_orgs):
    stateless = Actor(user_id=None, person_id=None, organization_id=None)
    assert list(Person.objects.for_actor(stateless)) == []


def test_system_actor_bypasses_scoping(two_orgs):
    assert Person.objects.for_actor(Actor.system()).count() == 3


def test_for_actor_rejects_none():
    with pytest.raises(UnscopedAccessError):
        Person.objects.for_actor(None)


def test_scope_survives_further_filtering(two_orgs):
    """Chaining after for_actor() must not drop the scope flag or the filter."""
    actor = actor_for_user(two_orgs["alice_user"])
    qs = Person.objects.for_actor(actor).filter(given_name__startswith="A")
    assert [p.given_name for p in qs] == ["Alice"]


def test_for_organization_scopes_without_an_actor(two_orgs):
    """The actorless entry point still applies the tenant filter — it is a
    scoping method, not an escape hatch."""
    names = {p.given_name for p in Person.objects.for_organization(two_orgs["org_a"].pk)}
    assert names == {"Alice", "Ian"}


def test_for_organization_does_not_leak_across_tenants(two_orgs):
    names = {p.given_name for p in Person.objects.for_organization(two_orgs["org_b"].pk)}
    assert names == {"Bob"}


def test_for_organization_rejects_none(two_orgs):
    """A None organisation id would otherwise filter on nothing at all."""
    with pytest.raises(UnscopedAccessError):
        Person.objects.for_organization(None)


def test_for_organization_result_is_readable(two_orgs):
    assert Person.objects.for_organization(two_orgs["org_a"].pk).count() == 2


def test_unauthenticated_user_yields_anonymous_actor():
    actor = actor_for_user(None)
    assert actor.is_anonymous
    assert actor.organization_id is None
