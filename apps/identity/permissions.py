"""Permission resolver — TODO 0.5.7 / SEC 2.2.

Deny by default. Every answer is `(actor, action, object) -> bool`, and the
mapping below is the single source of truth: it is also the fixture the
permission test suite is generated from (TODO 0.5.9/0.5.10), so a change here
that widens access will fail tests until the expectations are updated
deliberately.

Object-level checks are not optional. Hiding a menu item is not a control.
"""

from __future__ import annotations

from uuid import UUID

from apps.core.scoping import Actor

from .models import GovernanceModel, Role, ScopeType


class Action:
    """Named permissions. Strings, so they survive in fixtures and audit logs."""

    ORG_EDIT = "org.edit"
    DOJO_VIEW = "dojo.view"
    DOJO_EDIT = "dojo.edit"
    DOJO_CREATE = "dojo.create"

    PERSON_VIEW = "person.view"
    PERSON_EDIT = "person.edit"
    PERSON_CREATE = "person.create"
    PERSON_EXPORT = "person.export"
    #: ⚠ Removing somebody from the organisation entirely. Its own action rather
    #: than part of PERSON_EDIT, because editing a phone number and erasing a
    #: colleague are not the same power — front desk holds the first.
    PERSON_DELETE = "person.delete"
    #: ⚠ Taking a student off the active roll. Narrower than PERSON_EDIT on
    #: purpose: instructors need this and must not thereby gain the right to
    #: edit medical records, which PERSON_EDIT carries.
    STUDENT_ARCHIVE = "student.archive"

    MEDICAL_VIEW = "medical.view"
    MEDICAL_EDIT = "medical.edit"
    SAFEGUARDING_VIEW = "safeguarding.view"

    ROLE_ASSIGN = "role.assign"

    ATTENDANCE_VIEW = "attendance.view"
    ATTENDANCE_RECORD = "attendance.record"
    ATTENDANCE_EDIT_RETROACTIVE = "attendance.edit_retroactive"

    RANK_VIEW = "rank.view"
    RANK_AWARD = "rank.award"
    RANK_RATIFY = "rank.ratify"

    NOTE_VIEW_INSTRUCTOR = "note.view_instructor"
    NOTE_VIEW_ADMIN = "note.view_admin"
    NOTE_WRITE = "note.write"

    FINANCIAL_VIEW = "financial.view"
    FINANCIAL_MANAGE = "financial.manage"

    TIMEENTRY_VIEW_OWN = "timeentry.view_own"
    TIMEENTRY_APPROVE = "timeentry.approve"

    REPORT_VIEW = "report.view"
    REPORT_VIEW_FINANCIAL = "report.view_financial"

    @classmethod
    def all(cls) -> list[str]:
        return sorted(
            value
            for key, value in vars(cls).items()
            if not key.startswith("_") and isinstance(value, str)
        )


#: Base grants per role, before scope and governance are applied.
ROLE_ACTIONS: dict[str, set[str]] = {
    Role.ORG_ADMIN: {
        Action.ORG_EDIT,
        Action.PERSON_DELETE,
        Action.STUDENT_ARCHIVE,
        Action.DOJO_VIEW,
        Action.DOJO_EDIT,
        Action.DOJO_CREATE,
        Action.PERSON_VIEW,
        Action.PERSON_EDIT,
        Action.PERSON_CREATE,
        Action.PERSON_EXPORT,
        Action.MEDICAL_VIEW,
        Action.MEDICAL_EDIT,
        Action.ROLE_ASSIGN,
        Action.ATTENDANCE_VIEW,
        Action.ATTENDANCE_RECORD,
        Action.ATTENDANCE_EDIT_RETROACTIVE,
        Action.RANK_VIEW,
        Action.RANK_AWARD,
        Action.RANK_RATIFY,
        Action.NOTE_VIEW_INSTRUCTOR,
        Action.NOTE_VIEW_ADMIN,
        Action.NOTE_WRITE,
        Action.FINANCIAL_VIEW,
        Action.FINANCIAL_MANAGE,
        Action.TIMEENTRY_VIEW_OWN,
        Action.TIMEENTRY_APPROVE,
        Action.REPORT_VIEW,
        Action.REPORT_VIEW_FINANCIAL,
    },
    Role.DOJO_ADMIN: {
        # ⚠ Archives students but cannot delete a person — that stays with an
        # organisation administrator, because it reaches across every dojo.
        Action.STUDENT_ARCHIVE,
        Action.DOJO_VIEW,
        Action.DOJO_EDIT,
        Action.PERSON_VIEW,
        Action.PERSON_EDIT,
        Action.PERSON_CREATE,
        Action.PERSON_EXPORT,
        Action.MEDICAL_VIEW,
        Action.MEDICAL_EDIT,
        Action.ROLE_ASSIGN,
        Action.ATTENDANCE_VIEW,
        Action.ATTENDANCE_RECORD,
        Action.ATTENDANCE_EDIT_RETROACTIVE,
        Action.RANK_VIEW,
        Action.RANK_AWARD,
        Action.NOTE_VIEW_INSTRUCTOR,
        Action.NOTE_VIEW_ADMIN,
        Action.NOTE_WRITE,
        Action.FINANCIAL_VIEW,
        Action.FINANCIAL_MANAGE,
        Action.TIMEENTRY_VIEW_OWN,
        Action.TIMEENTRY_APPROVE,
        Action.REPORT_VIEW,
        Action.REPORT_VIEW_FINANCIAL,
    },
    Role.INSTRUCTOR: {
        Action.STUDENT_ARCHIVE,
        Action.DOJO_VIEW,
        Action.PERSON_VIEW,
        Action.MEDICAL_VIEW,  # allergies matter mid-class
        Action.ATTENDANCE_VIEW,
        Action.ATTENDANCE_RECORD,
        Action.RANK_VIEW,
        Action.NOTE_VIEW_INSTRUCTOR,
        Action.NOTE_WRITE,
        Action.TIMEENTRY_VIEW_OWN,
        Action.REPORT_VIEW,
    },
    Role.ASSISTANT_INSTRUCTOR: {
        Action.DOJO_VIEW,
        Action.PERSON_VIEW,
        Action.MEDICAL_VIEW,
        Action.ATTENDANCE_VIEW,
        Action.ATTENDANCE_RECORD,
        Action.RANK_VIEW,
        Action.TIMEENTRY_VIEW_OWN,
    },
    Role.FRONT_DESK: {
        Action.DOJO_VIEW,
        Action.PERSON_VIEW,
        Action.PERSON_EDIT,
        Action.PERSON_CREATE,
        Action.ATTENDANCE_VIEW,
        Action.ATTENDANCE_RECORD,
        Action.RANK_VIEW,
        Action.FINANCIAL_VIEW,
        Action.FINANCIAL_MANAGE,
        Action.REPORT_VIEW,
    },
    Role.SAFEGUARDING: {
        Action.DOJO_VIEW,
        Action.PERSON_VIEW,
        Action.MEDICAL_VIEW,
        Action.SAFEGUARDING_VIEW,
        Action.NOTE_VIEW_INSTRUCTOR,
        Action.NOTE_VIEW_ADMIN,
        Action.NOTE_WRITE,
    },
    Role.GUARDIAN: set(),  # handled by the parent portal's own object checks
    Role.STUDENT: set(),
}

#: Actions an *organisation-scoped* actor may never perform on a dojo-owned
#: object when the organisation is a federation (plan §13.1). Member dojos are
#: independent businesses: the association ratifies ranks, it does not read the
#: books or the parents' phone numbers.
FEDERATED_ORG_DENIED: set[str] = {
    Action.FINANCIAL_VIEW,
    Action.FINANCIAL_MANAGE,
    Action.REPORT_VIEW_FINANCIAL,
    Action.PERSON_EXPORT,
    Action.PERSON_EDIT,
    Action.MEDICAL_VIEW,
    Action.MEDICAL_EDIT,
    Action.SAFEGUARDING_VIEW,
    Action.NOTE_VIEW_ADMIN,
    Action.NOTE_VIEW_INSTRUCTOR,
    Action.ATTENDANCE_EDIT_RETROACTIVE,
    Action.TIMEENTRY_APPROVE,
    Action.ROLE_ASSIGN,
}


def object_dojo_id(obj) -> UUID | None:
    """Best-effort extraction of the dojo an object belongs to."""
    if obj is None:
        return None
    for attribute in ("dojo_id", "home_dojo_id"):
        value = getattr(obj, attribute, None)
        if value is not None:
            return value
    # A Dojo is its own dojo.
    if obj.__class__.__name__ == "Dojo":
        return obj.pk
    return None


def object_org_id(obj) -> UUID | None:
    if obj is None:
        return None
    value = getattr(obj, "organization_id", None)
    if value is not None:
        return value
    dojo = getattr(obj, "dojo", None)
    if dojo is not None:
        return dojo.organization_id
    return None


def can(
    actor: Actor,
    action: str,
    obj=None,
    *,
    governance_model: str = GovernanceModel.CENTRAL,
) -> bool:
    """The only sanctioned way to ask whether an actor may do something."""
    if actor is None:
        return False
    if actor.is_system:
        return True
    if actor.is_anonymous:
        return False

    target_org = object_org_id(obj)
    if target_org is not None and target_org != actor.organization_id:
        return False

    target_dojo = object_dojo_id(obj)

    for role, scope_type, scope_dojo_id in actor.roles:
        granted = ROLE_ACTIONS.get(role, set())
        if action not in granted:
            continue

        if scope_type == ScopeType.DOJO:
            # A dojo-scoped role only applies to that dojo's objects. Objects with
            # no dojo (organisation-level records) are out of reach.
            if target_dojo is None or scope_dojo_id != target_dojo:
                continue
            return True

        if scope_type == ScopeType.ORG:
            if (
                governance_model == GovernanceModel.FEDERATED
                and target_dojo is not None
                and action in FEDERATED_ORG_DENIED
            ):
                continue
            return True

    return False


def require(actor: Actor, action: str, obj=None, *, governance_model: str) -> None:
    """Raise unless permitted. Use in views and services."""
    if not can(actor, action, obj, governance_model=governance_model):
        raise PermissionDenied(action=action, actor=actor)


class PermissionDenied(Exception):
    def __init__(self, action: str, actor: Actor | None = None):
        self.action = action
        self.actor = actor
        super().__init__(f"Permission denied for action {action!r}")
