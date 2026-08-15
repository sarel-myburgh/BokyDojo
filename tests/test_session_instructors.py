"""Who is teaching, and who is covering — TODO 1.4.8, plan §4.5.

The distinction under test is between "who normally teaches Tuesday" and "who
actually taught last Tuesday". Pay and safeguarding both read the second, so a
substitution must land on one class and never on the rule behind it.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.core.timezones import dojo_zone
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    Dojo,
    InstructorAssignment,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)
from apps.identity.permissions import PermissionDenied
from apps.scheduling.instructors import (
    assign_instructor,
    assign_substitute,
    instructors_for,
    remove_instructor,
)
from apps.scheduling.materialise import materialise_template
from apps.scheduling.models import (
    ClassSession,
    ClassTemplate,
    SessionInstructor,
    TemplateInstructor,
)

pytestmark = pytest.mark.django_db
PASSWORD = "correct-horse-battery"


def _person(org, given, dojo=None, *, teaches=True):
    person = Person.objects.create(organization=org, given_name=given, family_name="Sensei")
    if teaches and dojo is not None:
        InstructorAssignment.objects.create(
            dojo=dojo, person=person, started_on=timezone.now().date()
        )
    return person


def _staff(org, role, email, dojo=None):
    person = Person.objects.create(
        organization=org, given_name=role.replace("_", " ").title(), family_name="Staff"
    )
    RoleAssignment.objects.create(
        organization=org,
        person=person,
        role=role,
        scope_type=ScopeType.DOJO if dojo else ScopeType.ORG,
        dojo=dojo,
    )
    return User.objects.create_user(email=email, password=PASSWORD, person=person)


@pytest.fixture
def world():
    with allow_unscoped("session instructor test setup"):
        org = Organization.objects.create(name="Teach Org", slug="teach-org")
        other_org = Organization.objects.create(name="Other", slug="teach-other")
        dojo = Dojo.objects.create(organization=org, name="Central", slug="teach-central")
        dojo_b = Dojo.objects.create(organization=org, name="Sen Sok", slug="teach-sensok")
        tz = dojo_zone(dojo)
        today = timezone.now().astimezone(tz).date()

        regular = _person(org, "Mei", dojo)
        cover = _person(org, "Dara", dojo_b)  # teaches at the *other* dojo
        outsider = _person(other_org, "Kenji", None, teaches=False)
        not_an_instructor = _person(org, "Sok", dojo, teaches=False)

        template = ClassTemplate.objects.create(
            dojo=dojo,
            name="Adults",
            rrule="FREQ=DAILY",
            start_time=datetime.time(18, 30),
            duration_minutes=90,
            active_from=today - datetime.timedelta(days=10),
        )
        TemplateInstructor.objects.create(template=template, person=regular)
        admin = _staff(org, Role.DOJO_ADMIN, "admin@teach.test", dojo)
        instructor_user = _staff(org, Role.INSTRUCTOR, "inst@teach.test", dojo)

    materialise_template(
        template,
        from_date=today - datetime.timedelta(days=10),
        to_date=today + datetime.timedelta(days=10),
    )
    return locals()


def _future_session(world, days=2):
    tz = world["tz"]
    target = world["today"] + datetime.timedelta(days=days)
    with allow_unscoped("session instructor test"):
        return next(
            s
            for s in ClassSession.objects.order_by("starts_at")
            if s.starts_at.astimezone(tz).date() == target
        )


def _names(world, session):
    return sorted(
        row.person.given_name for row in instructors_for(session, actor_for_user(world["admin"]))
    )


# -- the template seeds sessions ------------------------------------------------


def test_materialising_copies_the_default_instructor_onto_every_session(world):
    with allow_unscoped("session instructor test"):
        sessions = list(ClassSession.objects.all())
        assert sessions
        for session in sessions:
            people = list(SessionInstructor.objects.filter(session=session))
            assert [row.person_id for row in people] == [world["regular"].pk]


def test_a_template_with_no_default_instructor_creates_none(world):
    with allow_unscoped("session instructor test"):
        bare = ClassTemplate.objects.create(
            dojo=world["dojo"],
            name="Unstaffed",
            rrule="FREQ=DAILY",
            start_time=datetime.time(7, 0),
            duration_minutes=60,
            active_from=world["today"],
        )
    materialise_template(bare, from_date=world["today"], to_date=world["today"])

    with allow_unscoped("session instructor test"):
        assert not SessionInstructor.objects.filter(session__template=bare).exists()


# -- substitution ---------------------------------------------------------------


def test_a_substitute_replaces_the_regular_on_that_class_only(world):
    session = _future_session(world)
    other = _future_session(world, days=3)

    assign_substitute(
        session=session,
        replacing=world["regular"],
        substitute=world["cover"],
        actor=actor_for_user(world["admin"]),
    )

    assert _names(world, session) == ["Dara"]
    assert _names(world, other) == ["Mei"], "next week must revert on its own"


def test_the_substitution_records_who_was_covered_for(world):
    """⚠ "Dara covered for Mei" is a different fact from "Dara taught"."""
    session = _future_session(world)

    _, added = assign_substitute(
        session=session,
        replacing=world["regular"],
        substitute=world["cover"],
        actor=actor_for_user(world["admin"]),
    )

    assert added.is_substitute is True
    assert added.replaces_id == world["regular"].pk


def test_substituting_never_touches_the_template(world):
    session = _future_session(world)

    assign_substitute(
        session=session,
        replacing=world["regular"],
        substitute=world["cover"],
        actor=actor_for_user(world["admin"]),
    )

    with allow_unscoped("session instructor test"):
        defaults = list(
            TemplateInstructor.objects.filter(template=world["template"]).values_list(
                "person_id", flat=True
            )
        )
    assert defaults == [world["regular"].pk]


def test_a_substitution_survives_the_next_generator_run(world):
    """⚠ Re-seeding existing sessions would silently undo every substitution."""
    session = _future_session(world)
    assign_substitute(
        session=session,
        replacing=world["regular"],
        substitute=world["cover"],
        actor=actor_for_user(world["admin"]),
    )

    materialise_template(
        world["template"],
        from_date=world["today"] - datetime.timedelta(days=10),
        to_date=world["today"] + datetime.timedelta(days=10),
    )

    assert _names(world, session) == ["Dara"]


def test_a_substitute_may_come_from_another_dojo(world):
    """The usual case: tonight's cover normally teaches across town."""
    session = _future_session(world)

    assign_substitute(
        session=session,
        replacing=world["regular"],
        substitute=world["cover"],
        actor=actor_for_user(world["admin"]),
    )

    assert _names(world, session) == ["Dara"]


def test_substituting_for_somebody_not_teaching_is_refused(world):
    session = _future_session(world)

    with pytest.raises(ValidationError):
        assign_substitute(
            session=session,
            replacing=world["cover"],  # not on this class
            substitute=world["not_an_instructor"],
            actor=actor_for_user(world["admin"]),
        )


# -- who may be assigned --------------------------------------------------------


def test_somebody_from_another_organisation_is_refused(world):
    session = _future_session(world)

    with pytest.raises(ValidationError):
        assign_instructor(
            session=session, person=world["outsider"], actor=actor_for_user(world["admin"])
        )


def test_a_non_instructor_is_refused(world):
    session = _future_session(world)

    with pytest.raises(ValidationError):
        assign_instructor(
            session=session,
            person=world["not_an_instructor"],
            actor=actor_for_user(world["admin"]),
        )


def test_the_same_person_cannot_be_added_twice(world):
    session = _future_session(world)

    with pytest.raises(ValidationError):
        assign_instructor(
            session=session, person=world["regular"], actor=actor_for_user(world["admin"])
        )


def test_nobody_can_cover_for_themselves(world):
    session = _future_session(world)

    with pytest.raises(ValidationError):
        assign_instructor(
            session=session,
            person=world["regular"],
            actor=actor_for_user(world["admin"]),
            is_substitute=True,
            replaces=world["regular"],
        )


def test_an_instructor_cannot_reassign_teaching(world):
    session = _future_session(world)

    with pytest.raises(PermissionDenied):
        assign_substitute(
            session=session,
            replacing=world["regular"],
            substitute=world["cover"],
            actor=actor_for_user(world["instructor_user"]),
        )


# -- past sessions ---------------------------------------------------------------


def test_a_past_class_can_still_be_corrected(world):
    """⚠ Deliberately unlike moving a class: pay depends on this being right.

    "Dara actually took last Tuesday" is a correction of fact, not a rewrite of
    the schedule, and refusing it would leave the record permanently wrong.
    """
    with allow_unscoped("session instructor test"):
        past = next(
            s for s in ClassSession.objects.order_by("starts_at") if s.starts_at < timezone.now()
        )

    assign_substitute(
        session=past,
        replacing=world["regular"],
        substitute=world["cover"],
        actor=actor_for_user(world["admin"]),
    )

    assert _names(world, past) == ["Dara"]


# -- removal and audit ------------------------------------------------------------


def test_removing_an_instructor_leaves_the_class_unstaffed(world):
    session = _future_session(world)

    remove_instructor(
        session=session, person=world["regular"], actor=actor_for_user(world["admin"])
    )

    assert _names(world, session) == []


def test_removing_somebody_not_on_the_class_is_refused(world):
    session = _future_session(world)

    with pytest.raises(ValidationError):
        remove_instructor(
            session=session, person=world["cover"], actor=actor_for_user(world["admin"])
        )


def test_every_change_is_audited(world):
    session = _future_session(world)
    actor = actor_for_user(world["admin"])

    assign_substitute(
        session=session, replacing=world["regular"], substitute=world["cover"], actor=actor
    )
    remove_instructor(session=session, person=world["cover"], actor=actor)

    notes = " ".join(AuditLog.objects.values_list("note", flat=True))
    assert "covering" in notes
    assert "removed" in notes
