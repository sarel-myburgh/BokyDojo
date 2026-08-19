"""Profile pictures and staff detail editing — plan §3.

⚠ **Staff pictures are not student photographs and do not share their rules.**
A student photograph is consent-gated because it is a child's face on a check-in
grid; the consent record is the lawful basis and the evidence. A staff profile
picture is employment data, and an administrator adding one for a colleague
cannot consent on that colleague's behalf without inventing the very evidence the
trail exists to provide. They use ``Document.Kind.PROFILE_PHOTO``, which nothing
reading student photographs will ever pick up.

⚠ **Who may edit whom.** ``Person`` carries no dojo, so ``can(actor, PERSON_EDIT,
person)`` grants only to organisation-scoped roles — a dojo administrator would
be refused on the object alone, however obviously they run that dojo. So the rule
is written out: an organisation administrator may edit anybody, and a dojo
administrator may edit somebody who holds a role at a dojo they administer.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.core.documents import may_read, store
from apps.core.models import Document
from apps.core.scoping import Actor
from apps.core.uploads import validate_upload

from .models import GovernanceModel, Person, Role, RoleAssignment
from .permissions import ROLE_ACTIONS, Action


def _governance(person: Person) -> str:
    return person.organization.governance_model or GovernanceModel.CENTRAL


def is_self(actor: Actor, person: Person) -> bool:
    return actor.person_id is not None and actor.person_id == person.pk


def may_edit_person(actor: Actor, person: Person) -> bool:
    """Whether ``actor`` may change ``person``'s own details.

    Everybody may edit themselves. Beyond that, see the module docstring: the
    object-level check cannot express "the dojo they administer", because a
    Person has no dojo to match against.
    """
    if actor is None or actor.is_anonymous:
        return False
    if is_self(actor, person):
        return True
    if person.organization_id != actor.organization_id:
        return False

    # An organisation-scoped administrator: anybody in the organisation.
    for role, scope_type, _dojo_id in actor.roles:
        if scope_type == "org" and Action.ROLE_ASSIGN in ROLE_ACTIONS.get(role, set()):
            return True

    # A dojo-scoped administrator: somebody who holds a role at one of theirs.
    administered = {
        dojo_id
        for role, scope_type, dojo_id in actor.roles
        if scope_type == "dojo"
        and dojo_id is not None
        and Action.ROLE_ASSIGN in ROLE_ACTIONS.get(role, set())
    }
    if not administered:
        return False
    return (
        RoleAssignment.objects.for_organization(actor.organization_id)
        .filter(person=person, revoked_at__isnull=True, dojo_id__in=administered)
        .exists()
    )


def require_edit(actor: Actor, person: Person) -> None:
    from .permissions import PermissionDenied

    if not may_edit_person(actor, person):
        raise PermissionDenied(action=Action.PERSON_EDIT, actor=actor)


def current_profile_photo(*, person: Person, actor: Actor) -> Document | None:
    """The latest profile picture this actor may see, or None."""
    photo = (
        Document.objects.for_organization(person.organization_id)
        .filter(subject_person=person, kind=Document.Kind.PROFILE_PHOTO)
        .order_by("-created_at")
        .first()
    )
    if photo is None:
        return None
    if not may_read(actor, photo, governance_model=_governance(person)):
        return None
    return photo


def upload_profile_photo(*, person: Person, uploaded_file, actor: Actor) -> Document:
    """Store a profile picture for ``person``.

    ⚠ Allowed for the person themselves, or for an administrator over them. No
    consent record, and that is the decision — see the module docstring.

    The image is re-encoded on the way in by ``store``, which strips EXIF and
    therefore the GPS coordinates of wherever the photograph was taken.
    """
    require_edit(actor, person)

    file_kind = validate_upload(uploaded_file)
    if not file_kind.is_image:
        raise ValidationError({"photo": _("Upload a JPEG, PNG, GIF, or WebP image.")})

    return store(
        uploaded_file,
        organization=person.organization,
        kind=Document.Kind.PROFILE_PHOTO,
        actor=actor,
        subject_person=person,
    )


def dojos_and_belts(*, person: Person, actor: Actor) -> dict:
    """What to show somebody about themselves — read-only.

    ⚠ Read-only on purpose. Which dojos somebody belongs to and what grade they
    hold are decisions an administrator makes; a profile screen that let people
    edit either would be a self-service promotion.
    """
    from apps.ranks.models import StudentStyleTrack

    from .models import Dojo, Enrollment

    role_dojos = set(
        RoleAssignment.objects.for_actor(actor)
        .filter(person=person, revoked_at__isnull=True, dojo__isnull=False)
        .values_list("dojo_id", flat=True)
    )
    enrolled_dojos = set(
        Enrollment.objects.for_actor(actor)
        .filter(student=person, ended_on__isnull=True)
        .values_list("dojo_id", flat=True)
    )
    dojos = list(
        Dojo.objects.for_actor(actor).filter(pk__in=role_dojos | enrolled_dojos).order_by("name")
    )
    tracks = list(
        StudentStyleTrack.objects.for_actor(actor)
        .filter(student=person, status=StudentStyleTrack.Status.ACTIVE)
        .select_related("style", "ladder", "current_rank")
        .order_by("style__name")
    )
    return {"dojos": dojos, "tracks": tracks}


def person_page_context(*, person: Person, actor: Actor) -> dict:
    """Everything the one staff page shows.

    ⚠ One builder, used by the page itself and by every action that redirects
    back to it or re-renders it with a bound form. There used to be a separate
    roles screen with its own copy of half of this, and the two drifted — you
    could reach a person from two places and be offered different things.
    """
    from apps.identity.org_forms import RoleGrantForm
    from apps.identity.permissions import Action, can

    from .models import GovernanceModel, RoleAssignment, StudentProfile

    may_assign = _holds_anywhere(actor, Action.ROLE_ASSIGN)
    assignments = (
        list(
            RoleAssignment.objects.for_actor(actor)
            .filter(person=person, revoked_at__isnull=True)
            .select_related("dojo")
            .order_by("role")
        )
        if may_assign
        else []
    )

    # ⚠ Grades hang off StudentProfile, not Person. A member of staff who is not
    # also enrolled has no profile and therefore no grade to award — which is a
    # real state to report, not an empty list to render silently.
    profile = StudentProfile.objects.for_actor(actor).filter(person=person).first()
    may_award = profile is not None and can(
        actor,
        Action.RANK_AWARD,
        profile,
        governance_model=_governance(person),
    )

    return {
        "person": person,
        "photo": current_profile_photo(person=person, actor=actor),
        "is_self": is_self(actor, person),
        "may_edit": may_edit_person(actor, person),
        "may_assign_roles": may_assign,
        "assignments": assignments,
        "role_form": RoleGrantForm(actor=actor) if may_assign else None,
        "student_profile": profile,
        "may_award_rank": may_award,
        # ⚠ Presentation only — temporary_password_view checks ORG_EDIT itself.
        # Menu visibility is not a control (SEC §2.2).
        "may_issue_password": can(
            actor,
            Action.ORG_EDIT,
            person.organization,
            governance_model=GovernanceModel.CENTRAL,
        ),
        **dojos_and_belts(person=person, actor=actor),
    }


def _holds_anywhere(actor: Actor, action: str) -> bool:
    from .permissions import ROLE_ACTIONS

    return any(action in ROLE_ACTIONS.get(role, set()) for role, _s, _d in actor.roles)


def is_org_admin(actor: Actor) -> bool:
    return any(role == Role.ORG_ADMIN and scope == "org" for role, scope, _d in actor.roles)
