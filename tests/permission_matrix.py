"""The permission matrix — TODO 0.5.9, SEC 2.2.

This file is the *expectation*; apps/identity/permissions.py is the
*implementation*. They are deliberately separate so that widening access
requires editing both, in two different files, on purpose.

Structure: for each (role, scope, governance model) we list the actions that
must be ALLOWED against an object belonging to the actor's own dojo. Every
action not listed must be DENIED. Deny by default, asserted exhaustively.

SEC §2.2 calls this the highest-value security investment in the project. Treat
a diff here as a security review, not a test fix.
"""

from apps.identity.models import GovernanceModel, Role, ScopeType
from apps.identity.permissions import FEDERATED_ORG_DENIED, ROLE_ACTIONS

CENTRAL = GovernanceModel.CENTRAL
FEDERATED = GovernanceModel.FEDERATED

ORG = ScopeType.ORG
DOJO = ScopeType.DOJO


def _expected(role: str, scope: str, governance: str) -> set[str]:
    """Expected allow-set against an object in the actor's own dojo."""
    granted = set(ROLE_ACTIONS.get(role, set()))
    if scope == ORG and governance == FEDERATED:
        granted -= FEDERATED_ORG_DENIED
    return granted


#: (role, scope_type, governance_model) -> allowed actions on an own-dojo object.
MATRIX: dict[tuple[str, str, str], set[str]] = {}

for _role in Role.values:
    for _scope in (ORG, DOJO):
        for _governance in (CENTRAL, FEDERATED):
            MATRIX[(_role, _scope, _governance)] = _expected(_role, _scope, _governance)


#: Cases pinned by hand. These are the ones that would be quietly wrong if the
#: derivation above were ever changed carelessly, so they are asserted literally
#: as well as derived.
PINNED_CASES = [
    # (role, scope, governance, action, expected)
    (Role.ORG_ADMIN, ORG, CENTRAL, "financial.view", True),
    (Role.ORG_ADMIN, ORG, FEDERATED, "financial.view", False),
    (Role.ORG_ADMIN, ORG, FEDERATED, "financial.manage", False),
    (Role.ORG_ADMIN, ORG, FEDERATED, "person.export", False),
    (Role.ORG_ADMIN, ORG, FEDERATED, "medical.view", False),
    (Role.ORG_ADMIN, ORG, FEDERATED, "safeguarding.view", False),
    (Role.ORG_ADMIN, ORG, FEDERATED, "note.view_admin", False),
    (Role.ORG_ADMIN, ORG, FEDERATED, "role.assign", False),
    # The association still ratifies grades — that is the point of a federation.
    (Role.ORG_ADMIN, ORG, FEDERATED, "rank.ratify", True),
    (Role.ORG_ADMIN, ORG, FEDERATED, "rank.view", True),
    (Role.ORG_ADMIN, ORG, FEDERATED, "dojo.view", True),
    # Instructors never see money.
    (Role.INSTRUCTOR, DOJO, CENTRAL, "financial.view", False),
    (Role.INSTRUCTOR, DOJO, CENTRAL, "financial.manage", False),
    (Role.INSTRUCTOR, DOJO, CENTRAL, "report.view_financial", False),
    # ...but do see allergies, because that matters mid-class.
    (Role.INSTRUCTOR, DOJO, CENTRAL, "medical.view", True),
    (Role.INSTRUCTOR, DOJO, CENTRAL, "medical.edit", False),
    # Assistants cannot write notes or edit people.
    (Role.ASSISTANT_INSTRUCTOR, DOJO, CENTRAL, "note.write", False),
    (Role.ASSISTANT_INSTRUCTOR, DOJO, CENTRAL, "person.edit", False),
    (Role.ASSISTANT_INSTRUCTOR, DOJO, CENTRAL, "attendance.edit_retroactive", False),
    # Front desk handles money but must not change ranks.
    (Role.FRONT_DESK, DOJO, CENTRAL, "financial.manage", True),
    (Role.FRONT_DESK, DOJO, CENTRAL, "rank.award", False),
    (Role.FRONT_DESK, DOJO, CENTRAL, "medical.view", False),
    # Safeguarding officer sees protection notes; nobody else does.
    (Role.SAFEGUARDING, DOJO, CENTRAL, "safeguarding.view", True),
    (Role.DOJO_ADMIN, DOJO, CENTRAL, "safeguarding.view", False),
    (Role.INSTRUCTOR, DOJO, CENTRAL, "safeguarding.view", False),
    (Role.FRONT_DESK, DOJO, CENTRAL, "safeguarding.view", False),
    # Guardians and students get nothing through this resolver; the parent
    # portal applies its own object-level checks.
    (Role.GUARDIAN, DOJO, CENTRAL, "person.view", False),
    (Role.STUDENT, DOJO, CENTRAL, "person.view", False),
]
