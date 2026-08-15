"""Template edit semantics — TODO 1.4.5, plan §4.5.

*"Editing a template offers 'this occurrence / this and future' semantics —
decide this early, it's the classic calendar-app trap."*

The trap is that both obvious implementations lose data: editing in place makes
past sessions lie about what happened, and regenerating the future deletes rows
that attendance and cancellations point at. These tests are mostly about what
must *survive* an edit.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.attendance.models import AttendanceRecord
from apps.core.scoping import allow_unscoped
from apps.core.timezones import dojo_zone
from apps.identity.actors import actor_for_user
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    User,
)
from apps.identity.permissions import PermissionDenied
from apps.scheduling.edits import edit_this_and_future, move_occurrence
from apps.scheduling.materialise import materialise_template
from apps.scheduling.models import ClassSession, ClassTemplate

pytestmark = pytest.mark.django_db
PASSWORD = "correct-horse-battery"


def _staff(org, role, email, dojo=None):
    person = Person.objects.create(
        organization=org, given_name=role.replace("_", " ").title(), family_name="Sched"
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
    with allow_unscoped("template edit test setup"):
        org = Organization.objects.create(name="Sched Org", slug="sched-org")
        dojo = Dojo.objects.create(organization=org, name="Dojo A", slug="sched-a")
        tz = dojo_zone(dojo)
        today = timezone.now().astimezone(tz).date()
        template = ClassTemplate.objects.create(
            dojo=dojo,
            name="Adults",
            rrule="FREQ=DAILY",
            start_time=datetime.time(18, 30),
            duration_minutes=90,
            room="Main",
            active_from=today - datetime.timedelta(days=30),
        )
        admin = _staff(org, Role.DOJO_ADMIN, "admin@sched.test", dojo)
        instructor = _staff(org, Role.INSTRUCTOR, "instructor@sched.test", dojo)
        student = Person.objects.create(organization=org, given_name="Sok", family_name="P")
        StudentProfile.objects.create(person=student, home_dojo=dojo)

    materialise_template(
        template,
        from_date=today - datetime.timedelta(days=30),
        to_date=today + datetime.timedelta(days=30),
    )
    return locals()


def _sessions(world, template=None):
    with allow_unscoped("template edit test"):
        qs = ClassSession.objects.all()
        if template is not None:
            qs = qs.filter(template=template)
        return list(qs.order_by("starts_at"))


def _future_session(world, days=3):
    """The session ``days`` days from today, in the dojo's own timezone."""
    tz = world["tz"]
    target = world["today"] + datetime.timedelta(days=days)
    return next(s for s in _sessions(world) if s.starts_at.astimezone(tz).date() == target)


# -- this occurrence -----------------------------------------------------------


def test_moving_one_occurrence_leaves_every_other_alone(world):
    session = _future_session(world)
    before = len(_sessions(world))

    moved = move_occurrence(
        session=session,
        actor=actor_for_user(world["admin"]),
        starts_at=session.starts_at + datetime.timedelta(hours=1),
    )

    assert moved.starts_at.astimezone(world["tz"]).hour == 19
    assert len(_sessions(world)) == before, "moving one class must not add or remove any"


def test_moving_one_occurrence_does_not_change_the_template(world):
    session = _future_session(world)

    move_occurrence(
        session=session,
        actor=actor_for_user(world["admin"]),
        starts_at=session.starts_at + datetime.timedelta(hours=1),
    )

    with allow_unscoped("template edit test"):
        template = ClassTemplate.objects.get(pk=world["template"].pk)
    assert template.start_time == datetime.time(18, 30), "the rule itself must be untouched"


def test_the_vacated_slot_is_not_refilled_on_the_next_run(world):
    """⚠ The bug this exists to prevent: a move silently becoming a duplicate.

    Materialisation keys on (template, starts_at) and never deletes, so without
    recording the vacated slot the next run sees 18:30 standing empty and
    helpfully recreates the class — leaving the dojo with two.
    """
    session = _future_session(world)
    target_date = session.starts_at.astimezone(world["tz"]).date()

    move_occurrence(
        session=session,
        actor=actor_for_user(world["admin"]),
        starts_at=session.starts_at + datetime.timedelta(hours=1),
    )
    materialise_template(
        world["template"],
        from_date=world["today"] - datetime.timedelta(days=30),
        to_date=world["today"] + datetime.timedelta(days=30),
    )

    on_that_day = [
        s for s in _sessions(world) if s.starts_at.astimezone(world["tz"]).date() == target_date
    ]
    assert len(on_that_day) == 1, f"the class was duplicated: {on_that_day}"
    assert on_that_day[0].starts_at.astimezone(world["tz"]).hour == 19


def test_moving_twice_still_protects_the_original_slot(world):
    """⚠ moved_from records the generator's slot, not the intermediate time."""
    session = _future_session(world)
    actor = actor_for_user(world["admin"])
    original = session.starts_at

    once = move_occurrence(
        session=session, actor=actor, starts_at=original + datetime.timedelta(hours=1)
    )
    twice = move_occurrence(
        session=once, actor=actor, starts_at=original + datetime.timedelta(hours=2)
    )

    assert twice.moved_from == original, "the second move overwrote the original slot"


def test_a_class_that_has_already_started_cannot_be_moved(world):
    past = next(s for s in _sessions(world) if s.starts_at < timezone.now())

    with pytest.raises(ValidationError):
        move_occurrence(
            session=past,
            actor=actor_for_user(world["admin"]),
            starts_at=timezone.now() + datetime.timedelta(days=1),
        )


def test_a_class_cannot_be_moved_into_the_past(world):
    session = _future_session(world)

    with pytest.raises(ValidationError):
        move_occurrence(
            session=session,
            actor=actor_for_user(world["admin"]),
            starts_at=timezone.now() - datetime.timedelta(days=1),
        )


def test_an_instructor_cannot_reschedule(world):
    session = _future_session(world)

    with pytest.raises(PermissionDenied):
        move_occurrence(
            session=session,
            actor=actor_for_user(world["instructor"]),
            starts_at=session.starts_at + datetime.timedelta(hours=1),
        )


# -- this and future -----------------------------------------------------------


def _split(world, **changes):
    return edit_this_and_future(
        template=world["template"],
        from_date=world["today"] + datetime.timedelta(days=7),
        changes=changes or {"start_time": datetime.time(19, 30)},
        actor=actor_for_user(world["admin"]),
    )


def test_the_split_closes_the_original_the_day_before(world):
    successor = _split(world)

    with allow_unscoped("template edit test"):
        original = ClassTemplate.objects.get(pk=world["template"].pk)
    assert original.active_to == world["today"] + datetime.timedelta(days=6)
    assert successor.active_from == world["today"] + datetime.timedelta(days=7)
    assert successor.start_time == datetime.time(19, 30)


def test_past_sessions_keep_their_original_time(world):
    """⚠ The whole point. What happened on the 3rd must not change."""
    past = [s for s in _sessions(world) if s.starts_at < timezone.now()]
    before = {s.pk: s.starts_at for s in past}

    _split(world)

    with allow_unscoped("template edit test"):
        for pk, starts_at in before.items():
            assert ClassSession.objects.get(pk=pk).starts_at == starts_at


def test_past_sessions_still_point_at_the_template_that_made_them(world):
    _split(world)

    past = [s for s in _sessions(world) if s.starts_at < timezone.now()]
    assert past, "precondition: there is history"
    assert all(s.template_id == world["template"].pk for s in past)


def test_sessions_before_the_split_date_are_untouched(world):
    """Only "and future" — a session three days out is before a split at seven."""
    session = _future_session(world, days=3)

    _split(world)

    with allow_unscoped("template edit test"):
        assert ClassSession.objects.get(pk=session.pk).template_id == world["template"].pk


def test_future_sessions_are_regenerated_at_the_new_time(world):
    successor = _split(world)

    tz = world["tz"]
    after_split = [
        s
        for s in _sessions(world, template=successor)
        if s.starts_at.astimezone(tz).date() >= world["today"] + datetime.timedelta(days=7)
    ]
    assert after_split, "the successor produced no sessions"
    assert all(s.starts_at.astimezone(tz).hour == 19 for s in after_split)
    assert all(s.starts_at.astimezone(tz).minute == 30 for s in after_split)


def test_exactly_one_class_per_day_after_the_split(world):
    """⚠ The other half of the trap: a split that leaves both rules running.

    The nightly generator is re-run here on purpose. Failing to close the old
    template leaves the schedule *looking* correct until the next run, when the
    old rule quietly refills every day it still claims — so a test that only
    checks the state immediately after the split would miss it entirely.
    """
    successor = _split(world)
    # ⚠ Re-read the templates first. The split closed the original in the
    # database; the fixture's in-memory copy still believes active_to is None,
    # and materialising *that* would regenerate the old rule from a stale object.
    # The real job loads templates fresh, so this reproduces what it does.
    with allow_unscoped("template edit test"):
        templates = list(ClassTemplate.objects.filter(dojo=world["dojo"]))
    for template in templates:
        materialise_template(
            template,
            from_date=world["today"] - datetime.timedelta(days=30),
            to_date=world["today"] + datetime.timedelta(days=30),
        )

    tz = world["tz"]
    per_day = {}
    for session in _sessions(world):
        day = session.starts_at.astimezone(tz).date()
        per_day.setdefault(day, []).append(session)
    doubled = {day: rows for day, rows in per_day.items() if len(rows) > 1}
    assert not doubled, f"more than one class on a day: {doubled}"
    assert successor.pk is not None


def test_a_cancelled_future_class_survives_the_split(world):
    """A cancellation was communicated to parents. Regenerating must not undo it."""
    session = _future_session(world, days=10)
    with allow_unscoped("template edit test"):
        session.status = ClassSession.Status.CANCELLED
        session.cancellation_reason = "Instructor away"
        session.save(update_fields=["status", "cancellation_reason"])

    _split(world)

    with allow_unscoped("template edit test"):
        kept = ClassSession.objects.get(pk=session.pk)
    assert kept.status == ClassSession.Status.CANCELLED
    assert kept.cancellation_reason == "Instructor away"


def test_a_cancelled_day_does_not_gain_a_second_class(world):
    session = _future_session(world, days=10)
    target = session.starts_at.astimezone(world["tz"]).date()
    with allow_unscoped("template edit test"):
        session.status = ClassSession.Status.CANCELLED
        session.save(update_fields=["status"])

    _split(world)

    on_that_day = [
        s for s in _sessions(world) if s.starts_at.astimezone(world["tz"]).date() == target
    ]
    assert len(on_that_day) == 1, "the cancelled day was given a fresh scheduled class"
    assert on_that_day[0].status == ClassSession.Status.CANCELLED


def test_a_future_session_with_attendance_is_never_deleted(world):
    """Attendance is evidence. A scheduling edit must not be able to destroy it."""
    session = _future_session(world, days=10)
    with allow_unscoped("template edit test"):
        AttendanceRecord.objects.create(
            session=session,
            student=world["student"],
            status=AttendanceRecord.Status.PRESENT,
        )

    _split(world)

    with allow_unscoped("template edit test"):
        assert ClassSession.objects.filter(pk=session.pk).exists()
        assert AttendanceRecord.objects.filter(session_id=session.pk).exists()


def test_a_moved_occurrence_survives_the_split(world):
    """Somebody deliberately moved that class; a later rule change is not a reason
    to move it back."""
    session = _future_session(world, days=10)
    moved = move_occurrence(
        session=session,
        actor=actor_for_user(world["admin"]),
        starts_at=session.starts_at + datetime.timedelta(hours=3),
    )

    _split(world)

    with allow_unscoped("template edit test"):
        kept = ClassSession.objects.get(pk=moved.pk)
    assert kept.starts_at == moved.starts_at


# -- refusals ------------------------------------------------------------------


def test_splitting_from_today_is_refused(world):
    """Today may already have been taught — that is a per-occurrence edit."""
    with pytest.raises(ValidationError):
        edit_this_and_future(
            template=world["template"],
            from_date=world["today"],
            changes={"start_time": datetime.time(19, 30)},
            actor=actor_for_user(world["admin"]),
        )


def test_splitting_from_the_past_is_refused(world):
    with pytest.raises(ValidationError):
        edit_this_and_future(
            template=world["template"],
            from_date=world["today"] - datetime.timedelta(days=3),
            changes={"start_time": datetime.time(19, 30)},
            actor=actor_for_user(world["admin"]),
        )


def test_an_unknown_field_is_refused(world):
    with pytest.raises(ValidationError):
        _split(world, dojo="somewhere else")


def test_an_empty_change_is_refused(world):
    with pytest.raises(ValidationError):
        edit_this_and_future(
            template=world["template"],
            from_date=world["today"] + datetime.timedelta(days=7),
            changes={},
            actor=actor_for_user(world["admin"]),
        )


def test_an_instructor_cannot_split_a_template(world):
    with pytest.raises(PermissionDenied):
        edit_this_and_future(
            template=world["template"],
            from_date=world["today"] + datetime.timedelta(days=7),
            changes={"start_time": datetime.time(19, 30)},
            actor=actor_for_user(world["instructor"]),
        )


def test_both_edits_are_audited(world):
    from apps.core.models import AuditLog

    session = _future_session(world)
    move_occurrence(
        session=session,
        actor=actor_for_user(world["admin"]),
        starts_at=session.starts_at + datetime.timedelta(hours=1),
    )
    _split(world)

    notes = " ".join(AuditLog.objects.filter(action="update").values_list("note", flat=True))
    assert "this occurrence" in notes
    assert "split from" in notes
