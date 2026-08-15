"""Governance-model field visibility — TODO 0.5.8, plan §13.1."""

from __future__ import annotations

import uuid

import pytest

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import (
    Dojo,
    GovernanceModel,
    Organization,
    Person,
    Role,
    ScopeType,
)
from apps.identity.visibility import (
    DOJO_PRIVATE_PERSON_FIELDS,
    is_org_scoped_only,
    redact_person,
    visible_person_fields,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def federation():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(
            name="Cambodia Shotokan Association",
            slug="csa",
            governance_model=GovernanceModel.FEDERATED,
        )
        dojo = Dojo.objects.create(organization=org, name="Riverside Dojo", slug="riverside")
        student = Person.objects.create(
            organization=org,
            given_name="Sophea",
            family_name="Chan",
            email="parent@example.com",
            phone="+855 12 345 678",
            city="Phnom Penh",
        )
    return {"org": org, "dojo": dojo, "student": student}


def _actor(org_id, *, scope, dojo_id=None, role=Role.ORG_ADMIN):
    return Actor(
        user_id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        organization_id=org_id,
        dojo_ids=None if scope == ScopeType.ORG else frozenset({dojo_id}),
        roles=frozenset({(role, scope, dojo_id)}),
    )


def test_central_org_admin_sees_everything(federation):
    actor = _actor(federation["org"].pk, scope=ScopeType.ORG)
    fields = visible_person_fields(
        actor, federation["student"], governance_model=GovernanceModel.CENTRAL
    )
    assert DOJO_PRIVATE_PERSON_FIELDS <= fields


def test_federated_org_admin_cannot_see_contact_details(federation):
    """The association chairman does not get the member dojo's parent phone list."""
    actor = _actor(federation["org"].pk, scope=ScopeType.ORG)
    fields = visible_person_fields(
        actor, federation["student"], governance_model=GovernanceModel.FEDERATED
    )
    assert not (DOJO_PRIVATE_PERSON_FIELDS & fields)
    assert "given_name" in fields
    assert "family_name" in fields


def test_federated_dojo_role_still_sees_full_record(federation):
    """Withholding applies upward to the association, not within the dojo."""
    actor = _actor(
        federation["org"].pk,
        scope=ScopeType.DOJO,
        dojo_id=federation["dojo"].pk,
        role=Role.DOJO_ADMIN,
    )
    fields = visible_person_fields(
        actor, federation["student"], governance_model=GovernanceModel.FEDERATED
    )
    assert DOJO_PRIVATE_PERSON_FIELDS <= fields


def test_cross_organisation_sees_nothing(federation):
    outsider = Actor(user_id=None, person_id=uuid.uuid4(), organization_id=uuid.uuid4())
    assert (
        visible_person_fields(
            outsider, federation["student"], governance_model=GovernanceModel.FEDERATED
        )
        == frozenset()
    )


def test_redact_person_omits_withheld_fields(federation):
    actor = _actor(federation["org"].pk, scope=ScopeType.ORG)
    payload = redact_person(
        actor, federation["student"], governance_model=GovernanceModel.FEDERATED
    )
    assert "email" not in payload
    assert "phone" not in payload
    assert payload["given_name"] == "Sophea"


def test_redact_person_full_under_central(federation):
    actor = _actor(federation["org"].pk, scope=ScopeType.ORG)
    payload = redact_person(actor, federation["student"], governance_model=GovernanceModel.CENTRAL)
    assert payload["email"] == "parent@example.com"


def test_is_org_scoped_only_detects_mixed_roles(federation):
    mixed = Actor(
        user_id=None,
        person_id=uuid.uuid4(),
        organization_id=federation["org"].pk,
        roles=frozenset(
            {
                (Role.ORG_ADMIN, ScopeType.ORG, None),
                (Role.INSTRUCTOR, ScopeType.DOJO, federation["dojo"].pk),
            }
        ),
    )
    assert is_org_scoped_only(mixed) is False


def test_system_actor_sees_all_fields(federation):
    fields = visible_person_fields(
        Actor.system(), federation["student"], governance_model=GovernanceModel.FEDERATED
    )
    assert "email" in fields
