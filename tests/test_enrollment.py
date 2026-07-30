"""Enrolment, multi-dojo membership and transfers — TODO 1.3.1 – 1.3.4, plan §4.3."""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.enrolment import enrol_student, set_primary_dojo, transfer_student
from apps.identity.models import (
    Dojo,
    Enrollment,
    GovernanceModel,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    TransferRecord,
)
from apps.identity.permissions import PermissionDenied

pytestmark = pytest.mark.django_db

JAN = datetime.date(2026, 1, 1)
MAR = datetime.date(2026, 3, 1)
JUN = datetime.date(2026, 6, 1)


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def other_org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Other Org", slug="other-org")


@pytest.fixture
def dojo_a(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo A", slug="dojo-a")


@pytest.fixture
def dojo_b(org):
    with allow_unscoped("test setup"):
        return Dojo.objects.create(organization=org, name="Dojo B", slug="dojo-b")


@pytest.fixture
def student(org):
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=org, given_name="Sokha", family_name="Chhorn"
        )
        StudentProfile.objects.create(person=person, status=StudentProfile.Status.ACTIVE)
        return person


@pytest.fixture
def admin_person(org):
    with allow_unscoped("test setup"):
        return Person.objects.create(organization=org, given_name="Admin", family_name="User")


@pytest.fixture
def admin(org, admin_person):
    """An org admin — sees every dojo, may edit people."""
    with allow_unscoped("test setup"):
        RoleAssignment.objects.create(
            organization=org,
            person=admin_person,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
    return Actor(
        user_id=None,
        person_id=admin_person.pk,
        organization_id=org.pk,
        roles=frozenset({(Role.ORG_ADMIN, ScopeType.ORG, None)}),
    )


@pytest.fixture
def instructor_at_a(org, dojo_a):
    """Dojo-scoped instructor: may record attendance, may not move students."""
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=org, given_name="Takeshi", family_name="Yamada"
        )
    return Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo_a.pk}),
        roles=frozenset({(Role.INSTRUCTOR, ScopeType.DOJO, dojo_a.pk)}),
    )


# -- enrolment basics ---------------------------------------------------------


def test_first_enrolment_becomes_primary(student, dojo_a, admin):
    enrollment = enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)

    assert enrollment.is_primary is True
    assert enrollment.status == Enrollment.Status.ACTIVE
    assert enrollment.is_live is True


def test_second_enrolment_is_not_primary_by_default(student, dojo_a, dojo_b, admin):
    enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)
    second = enrol_student(student=student, dojo=dojo_b, started_on=MAR, actor=admin)

    assert second.is_primary is False
    live = Enrollment.objects.for_actor(admin).filter(student=student, ended_on__isnull=True)
    assert live.count() == 2, "multi-dojo enrolment must be possible — plan §4.3"


def test_primary_enrolment_populates_home_dojo(student, dojo_a, admin):
    enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)

    profile = StudentProfile.objects.for_actor(admin).get(person=student)
    assert profile.home_dojo_id == dojo_a.pk


def test_promoting_a_second_dojo_moves_primary_and_home_dojo(student, dojo_a, dojo_b, admin):
    first = enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)
    enrol_student(student=student, dojo=dojo_b, started_on=MAR, actor=admin)

    set_primary_dojo(student=student, dojo=dojo_b, actor=admin)

    first.refresh_from_db()
    assert first.is_primary is False
    profile = StudentProfile.objects.for_actor(admin).get(person=student)
    assert profile.home_dojo_id == dojo_b.pk


def test_instructor_may_not_enrol(student, dojo_a, instructor_at_a):
    with pytest.raises(PermissionDenied):
        enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=instructor_at_a)


# -- database invariants ------------------------------------------------------


def test_two_live_enrolments_at_the_same_dojo_are_rejected(student, dojo_a):
    with allow_unscoped("test setup"):
        Enrollment.objects.create(student=student, dojo=dojo_a, started_on=JAN)
        with pytest.raises(IntegrityError), transaction.atomic():
            Enrollment.objects.create(student=student, dojo=dojo_a, started_on=MAR)


def test_rejoining_after_leaving_is_allowed(student, dojo_a):
    with allow_unscoped("test setup"):
        Enrollment.objects.create(
            student=student,
            dojo=dojo_a,
            started_on=JAN,
            status=Enrollment.Status.ENDED,
            ended_on=MAR,
        )
        again = Enrollment.objects.create(student=student, dojo=dojo_a, started_on=JUN)
    assert again.is_live is True


def test_two_primary_enrolments_are_rejected(student, dojo_a, dojo_b):
    with allow_unscoped("test setup"):
        Enrollment.objects.create(
            student=student, dojo=dojo_a, started_on=JAN, is_primary=True
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            Enrollment.objects.create(
                student=student, dojo=dojo_b, started_on=MAR, is_primary=True
            )


def test_ended_status_requires_an_end_date(student, dojo_a):
    with allow_unscoped("test setup"), pytest.raises(IntegrityError), transaction.atomic():
        Enrollment.objects.create(
            student=student,
            dojo=dojo_a,
            started_on=JAN,
            status=Enrollment.Status.ENDED,
        )


def test_enrolment_may_not_span_organisations(student, other_org):
    """The same_organization_fields invariant — SEC §2.2."""
    with allow_unscoped("test setup"):
        foreign_dojo = Dojo.objects.create(
            organization=other_org, name="Foreign", slug="foreign"
        )
        with pytest.raises(ValidationError):
            Enrollment.objects.create(student=student, dojo=foreign_dojo, started_on=JAN)


# -- transfers ----------------------------------------------------------------


def test_transfer_ends_the_old_enrolment_and_opens_a_new_one(student, dojo_a, dojo_b, admin):
    old = enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)

    record = transfer_student(
        student=student,
        to_dojo=dojo_b,
        effective_on=JUN,
        actor=admin,
        reason="family moved",
    )

    old.refresh_from_db()
    assert old.status == Enrollment.Status.ENDED
    assert old.ended_on == JUN
    assert old.dojo_id == dojo_a.pk, "the old row must still point at the old dojo"
    assert old.is_primary is False

    new = Enrollment.objects.for_actor(admin).get(student=student, ended_on__isnull=True)
    assert new.dojo_id == dojo_b.pk
    assert new.is_primary is True
    assert new.started_on == JUN

    assert record.from_dojo_id == dojo_a.pk
    assert record.to_dojo_id == dojo_b.pk
    assert record.effective_on == JUN
    assert "family moved" in record.reason


def test_transfer_moves_the_home_dojo(student, dojo_a, dojo_b, admin):
    enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)
    transfer_student(student=student, to_dojo=dojo_b, effective_on=JUN, actor=admin)

    profile = StudentProfile.objects.for_actor(admin).get(person=student)
    assert profile.home_dojo_id == dojo_b.pk


def test_transfer_requires_rights_at_both_dojos(student, dojo_a, dojo_b, org, admin):
    """A dojo admin at A cannot push a student into B — TODO 1.3.3."""
    enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)

    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Head", family_name="A")
    admin_of_a_only = Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo_a.pk}),
        roles=frozenset({(Role.DOJO_ADMIN, ScopeType.DOJO, dojo_a.pk)}),
    )

    with pytest.raises(PermissionDenied):
        transfer_student(
            student=student, to_dojo=dojo_b, effective_on=JUN, actor=admin_of_a_only
        )


def test_transfer_without_a_live_enrolment_is_refused(student, dojo_b, admin):
    with pytest.raises(ValueError, match="no primary enrolment"):
        transfer_student(student=student, to_dojo=dojo_b, effective_on=JUN, actor=admin)


def test_transfer_record_is_visible_to_the_receiving_dojo(student, dojo_a, dojo_b, org, admin):
    """Arrivals must be visible at the destination — TransferRecord.tenant_scope_q."""
    enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)
    transfer_student(student=student, to_dojo=dojo_b, effective_on=JUN, actor=admin)

    with allow_unscoped("test setup"):
        person = Person.objects.create(organization=org, given_name="Head", family_name="B")
    admin_of_b = Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo_b.pk}),
        roles=frozenset({(Role.DOJO_ADMIN, ScopeType.DOJO, dojo_b.pk)}),
    )

    assert TransferRecord.objects.for_actor(admin_of_b).count() == 1


def test_transfer_record_is_hidden_from_an_unrelated_dojo(student, dojo_a, dojo_b, org, admin):
    enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)
    transfer_student(student=student, to_dojo=dojo_b, effective_on=JUN, actor=admin)

    with allow_unscoped("test setup"):
        dojo_c = Dojo.objects.create(organization=org, name="Dojo C", slug="dojo-c")
        person = Person.objects.create(organization=org, given_name="Head", family_name="C")
    admin_of_c = Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=frozenset({dojo_c.pk}),
        roles=frozenset({(Role.DOJO_ADMIN, ScopeType.DOJO, dojo_c.pk)}),
    )

    assert TransferRecord.objects.for_actor(admin_of_c).count() == 0


def test_transfer_between_the_same_dojo_is_refused(student, dojo_a, admin):
    enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)
    with pytest.raises(ValueError, match="two different dojos"):
        transfer_student(student=student, to_dojo=dojo_a, effective_on=JUN, actor=admin)


def test_attendance_history_survives_a_transfer(student, dojo_a, dojo_b, admin):
    """TODO 1.3.4 — the reason a transfer is two rows and not an UPDATE.

    Attendance at the old dojo must still be attendance *at the old dojo* after
    the student has moved, or every historical report silently reattributes
    itself to wherever the student happens to train now.
    """
    from apps.attendance.models import AttendanceRecord
    from apps.attendance.services import mark_attendance
    from apps.scheduling.models import ClassSession

    enrol_student(student=student, dojo=dojo_a, started_on=JAN, actor=admin)

    with allow_unscoped("test setup"):
        session_at_a = ClassSession.objects.create(
            dojo=dojo_a,
            starts_at=timezone.now() - datetime.timedelta(hours=2),
            ends_at=timezone.now() - datetime.timedelta(hours=1),
        )
    mark_attendance(
        session=session_at_a,
        student=student,
        status=AttendanceRecord.Status.PRESENT,
        actor=admin,
    )

    transfer_student(student=student, to_dojo=dojo_b, effective_on=JUN, actor=admin)

    record = AttendanceRecord.objects.for_actor(admin).get(student=student)
    assert record.session.dojo_id == dojo_a.pk
    assert record.status == AttendanceRecord.Status.PRESENT

    # And the ended enrolment still says where they trained, with its own dates.
    old = Enrollment.objects.for_actor(admin).get(student=student, ended_on__isnull=False)
    assert old.dojo_id == dojo_a.pk
    assert old.started_on == JAN
    assert old.ended_on == JUN


def test_federated_org_admin_may_not_transfer_between_member_dojos(
    other_org, admin_person
):
    """A federation ratifies grades; it does not move members' students — plan §13.1."""
    with allow_unscoped("test setup"):
        other_org.governance_model = GovernanceModel.FEDERATED
        other_org.save(update_fields=["governance_model"])
        member_a = Dojo.objects.create(organization=other_org, name="M A", slug="m-a")
        member_b = Dojo.objects.create(organization=other_org, name="M B", slug="m-b")
        person = Person.objects.create(
            organization=other_org, given_name="Fed", family_name="Admin"
        )
        pupil = Person.objects.create(
            organization=other_org, given_name="Pupil", family_name="One"
        )
        Enrollment.objects.create(
            student=pupil, dojo=member_a, started_on=JAN, is_primary=True
        )

    fed_admin = Actor(
        user_id=None,
        person_id=person.pk,
        organization_id=other_org.pk,
        roles=frozenset({(Role.ORG_ADMIN, ScopeType.ORG, None)}),
    )

    with pytest.raises(PermissionDenied):
        transfer_student(
            student=pupil, to_dojo=member_b, effective_on=JUN, actor=fed_admin
        )
