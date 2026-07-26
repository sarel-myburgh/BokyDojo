"""Governance-aware field visibility — TODO 0.5.8 / plan §13.1.

One resolver, consulted everywhere. The alternative — scattering
``if org.is_federated`` through views and serialisers — is exactly how a
federation's member dojo ends up leaking its parent contact list to the
association chairman.

Both governance models share one schema. Federated mode *withholds* fields; it
does not store them somewhere else.
"""

from __future__ import annotations

from apps.core.scoping import Actor

from .models import GovernanceModel, ScopeType
from .permissions import Action, can

#: Fields on Person that are the dojo's business, not the association's.
DOJO_PRIVATE_PERSON_FIELDS = frozenset(
    {
        "email",
        "phone",
        "address_line1",
        "address_line2",
        "city",
        "date_of_birth",
    }
)

#: What a federated organisation-scoped actor may always see about a member
#: dojo's student: enough to ratify a grading, nothing more.
FEDERATED_PERSON_FIELDS = frozenset(
    {
        "id",
        "given_name",
        "family_name",
        "preferred_name",
        "locale",
        "is_active",
    }
)


def is_org_scoped_only(actor: Actor) -> bool:
    """True if every role this actor holds is organisation-scoped."""
    if not actor.roles:
        return False
    return all(scope_type == ScopeType.ORG for _role, scope_type, _dojo in actor.roles)


def visible_person_fields(
    actor: Actor,
    person,
    *,
    governance_model: str,
) -> frozenset[str]:
    """The set of Person field names this actor may read."""
    all_fields = frozenset(f.name for f in person._meta.fields) | {"id"}

    if actor.is_system:
        return all_fields

    if person.organization_id != actor.organization_id:
        return frozenset()

    if governance_model != GovernanceModel.FEDERATED:
        return all_fields

    # Federated: an actor whose authority comes only from the organisation sees
    # the ranking-relevant subset. Anyone with a role at the person's own dojo
    # sees the full record, subject to the usual permission checks.
    if is_org_scoped_only(actor):
        return FEDERATED_PERSON_FIELDS & all_fields

    return all_fields


def redact_person(actor: Actor, person, *, governance_model: str) -> dict:
    """Serialise a Person to only what this actor may see."""
    allowed = visible_person_fields(actor, person, governance_model=governance_model)
    return {name: getattr(person, name) for name in sorted(allowed) if hasattr(person, name)}


def can_view_financials(actor: Actor, obj, *, governance_model: str) -> bool:
    """Financial visibility is its own bit — never implied by seniority (plan §3)."""
    return can(actor, Action.FINANCIAL_VIEW, obj, governance_model=governance_model)
