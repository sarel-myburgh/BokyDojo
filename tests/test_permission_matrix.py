"""Generated permission tests — TODO 0.5.10, SEC 2.2.

Every (role, scope, governance, action) combination is asserted. Nothing is
sampled. If you add an Action, this suite fails until the matrix says what it
should do — which is the intent.
"""

from __future__ import annotations

import uuid

import pytest

from apps.core.scoping import Actor
from apps.identity.models import GovernanceModel, ScopeType
from apps.identity.permissions import Action, can

from .permission_matrix import MATRIX, PINNED_CASES

ORG_ID = uuid.UUID("00000000-0000-7000-8000-00000000da01")
DOJO_A = uuid.UUID("00000000-0000-7000-8000-00000000d0a1")
DOJO_B = uuid.UUID("00000000-0000-7000-8000-00000000d0b2")
OTHER_ORG_ID = uuid.UUID("00000000-0000-7000-8000-0000000000ff")


class FakeObject:
    """Stand-in for any tenant-scoped record."""

    def __init__(self, organization_id=ORG_ID, dojo_id=DOJO_A):
        self.organization_id = organization_id
        self.dojo_id = dojo_id


def build_actor(role: str, scope: str, dojo_id=DOJO_A, org_id=ORG_ID) -> Actor:
    scope_dojo = None if scope == ScopeType.ORG else dojo_id
    return Actor(
        user_id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        organization_id=org_id,
        dojo_ids=None if scope == ScopeType.ORG else frozenset({dojo_id}),
        roles=frozenset({(role, scope, scope_dojo)}),
    )


@pytest.mark.parametrize(("key", "expected_allowed"), sorted(MATRIX.items()))
def test_matrix_exhaustive(key, expected_allowed):
    """Every action is either in the expected allow-set or denied. No gaps."""
    role, scope, governance = key
    actor = build_actor(role, scope)
    target = FakeObject()

    for action in Action.all():
        result = can(actor, action, target, governance_model=governance)
        should_allow = action in expected_allowed
        assert result is should_allow, (
            f"{role} @ {scope} under {governance}: action {action!r} "
            f"returned {result}, matrix expects {should_allow}"
        )


@pytest.mark.parametrize(("role", "scope", "governance", "action", "expected"), PINNED_CASES)
def test_pinned_cases(role, scope, governance, action, expected):
    actor = build_actor(role, scope)
    assert can(actor, action, FakeObject(), governance_model=governance) is expected


# -- cross-boundary denial ----------------------------------------------------


@pytest.mark.parametrize("governance", [GovernanceModel.CENTRAL, GovernanceModel.FEDERATED])
def test_dojo_scoped_role_cannot_reach_another_dojo(governance):
    """A dojo admin at Dojo A has no authority whatsoever at Dojo B."""
    actor = build_actor("dojo_admin", ScopeType.DOJO, dojo_id=DOJO_A)
    other_dojo_object = FakeObject(dojo_id=DOJO_B)

    for action in Action.all():
        assert (
            can(actor, action, other_dojo_object, governance_model=governance) is False
        ), f"dojo_admin at Dojo A was allowed {action!r} at Dojo B"


@pytest.mark.parametrize("governance", [GovernanceModel.CENTRAL, GovernanceModel.FEDERATED])
def test_no_role_reaches_another_organisation(governance):
    """Cross-tenant access is refused before any role is even consulted."""
    actor = build_actor("org_admin", ScopeType.ORG)
    foreign = FakeObject(organization_id=OTHER_ORG_ID)

    for action in Action.all():
        assert can(actor, action, foreign, governance_model=governance) is False


def test_anonymous_actor_is_denied_everything():
    anonymous = Actor(user_id=None, person_id=None, organization_id=None)
    for action in Action.all():
        assert can(anonymous, action, FakeObject(), governance_model=GovernanceModel.CENTRAL) is False


def test_none_actor_is_denied():
    assert can(None, Action.PERSON_VIEW, FakeObject(), governance_model=GovernanceModel.CENTRAL) is False


def test_dojo_scoped_role_cannot_act_on_org_level_objects():
    """An object with no dojo belongs to the organisation; dojo roles can't touch it."""
    actor = build_actor("dojo_admin", ScopeType.DOJO)
    org_level = FakeObject(dojo_id=None)
    for action in Action.all():
        assert can(actor, action, org_level, governance_model=GovernanceModel.CENTRAL) is False


def test_federated_denials_do_not_apply_to_org_level_objects():
    """The federation withholding applies to *dojo-owned* records specifically."""
    actor = build_actor("org_admin", ScopeType.ORG)
    org_level = FakeObject(dojo_id=None)
    assert can(
        actor, Action.FINANCIAL_VIEW, org_level, governance_model=GovernanceModel.FEDERATED
    ) is True


def test_every_action_appears_in_at_least_one_role():
    """Catches an Action added to the enum but never granted to anyone."""
    from apps.identity.permissions import ROLE_ACTIONS

    granted = set().union(*ROLE_ACTIONS.values())
    ungranted = set(Action.all()) - granted
    assert not ungranted, f"Actions defined but granted to no role: {sorted(ungranted)}"
