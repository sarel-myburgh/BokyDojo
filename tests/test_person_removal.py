"""Archiving students and removing people — plan §3.

⚠ The most destructive controls in the product, so most of what follows is
about what they refuse to do: erase history, lock the organisation out, delete
the person using them, or let an instructor remove a colleague.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.core.scoping import allow_unscoped
from apps.identity.actors import actor_for_user
from apps.identity.lifecycle import (
    ARCHIVE_STATUS,
    archive_student,
    delete_person,
    restore_person,
    unarchive_student,
)
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

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"  # pragma: allowlist secret


@pytest.fixture
def world():
    with allow_unscoped("removal test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        dojo = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )

        def staff(given, role, email, scope=ScopeType.ORG, at=None):
            person = Person.objects.create(
                organization=org, given_name=given, family_name="Person", email=email
            )
            RoleAssignment.objects.create(
                organization=org,
                person=person,
                role=role,
                scope_type=scope,
                dojo=at,
            )
            return person, User.objects.create_user(email, PASSWORD, person=person)

        boss, boss_user = staff("Ops", Role.ORG_ADMIN, "ops@example.com")
        second_boss, _ = staff("Deputy", Role.ORG_ADMIN, "deputy@example.com")
        dojo_admin, dojo_admin_user = staff(
            "Dee", Role.DOJO_ADMIN, "dee@example.com", ScopeType.DOJO, dojo
        )
        teacher, teacher_user = staff(
            "Mei", Role.INSTRUCTOR, "mei@example.com", ScopeType.DOJO, dojo
        )

        child = Person.objects.create(
            organization=org,
            given_name="Mika",
            family_name="Student",
            date_of_birth=datetime.date(2014, 5, 1),
        )
        profile = StudentProfile.objects.create(
            person=child, home_dojo=dojo, status=StudentProfile.Status.ACTIVE
        )
    return {
        "org": org,
        "dojo": dojo,
        "boss": boss,
        "boss_user": boss_user,
        "second_boss": second_boss,
        "dojo_admin": dojo_admin,
        "dojo_admin_user": dojo_admin_user,
        "teacher": teacher,
        "teacher_user": teacher_user,
        "child": child,
        "profile": profile,
    }


def actor(user):
    return actor_for_user(user)


# -- archiving a student ------------------------------------------------------


def test_an_instructor_can_archive_a_student(world):
    """⚠ The point of a separate STUDENT_ARCHIVE action. Instructors do not hold
    PERSON_EDIT and must not gain it just to take somebody off the roll."""
    archive_student(profile=world["profile"], actor=actor(world["teacher_user"]))

    with allow_unscoped("test"):
        world["profile"].refresh_from_db()
    assert world["profile"].status == ARCHIVE_STATUS


def test_a_dojo_admin_can_archive_a_student(world):
    archive_student(profile=world["profile"], actor=actor(world["dojo_admin_user"]))

    with allow_unscoped("test"):
        world["profile"].refresh_from_db()
    assert world["profile"].status == ARCHIVE_STATUS


def test_archiving_keeps_everything_they_did(world):
    """⚠ Not a deletion. "Was this child in class that evening" is a
    safeguarding question asked months later and it must still have an answer."""
    from apps.attendance.models import AttendanceRecord
    from apps.scheduling.models import ClassSession

    with allow_unscoped("test"):
        session = ClassSession.objects.create(
            dojo=world["dojo"],
            starts_at=timezone.now() - datetime.timedelta(days=7),
            ends_at=timezone.now() - datetime.timedelta(days=7, hours=-1),
        )
        AttendanceRecord.objects.create(
            session=session,
            student=world["child"],
            status=AttendanceRecord.Status.PRESENT,
            method=AttendanceRecord.Method.ROSTER,
        )

    archive_student(profile=world["profile"], actor=actor(world["teacher_user"]))

    with allow_unscoped("test"):
        assert AttendanceRecord.objects.filter(student=world["child"]).count() == 1
        assert Person.objects.filter(pk=world["child"].pk).exists()


def test_archiving_is_reversible(world):
    archive_student(profile=world["profile"], actor=actor(world["teacher_user"]))
    unarchive_student(profile=world["profile"], actor=actor(world["teacher_user"]))

    with allow_unscoped("test"):
        world["profile"].refresh_from_db()
    assert world["profile"].status == StudentProfile.Status.ACTIVE


def test_archiving_is_audited(world):
    from apps.core.models import AuditLog

    with allow_unscoped("test"):
        before = AuditLog.objects.count()

    archive_student(profile=world["profile"], actor=actor(world["teacher_user"]))

    with allow_unscoped("test"):
        assert AuditLog.objects.count() > before


def test_front_desk_cannot_archive(world):
    """⚠ Only the roles asked for. Front desk edits contact details; taking
    somebody off the roll is a different decision."""
    with allow_unscoped("test"):
        person = Person.objects.create(
            organization=world["org"], given_name="Fran", family_name="Desk"
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=person,
            role=Role.FRONT_DESK,
            scope_type=ScopeType.ORG,
        )
        user = User.objects.create_user("fran@example.com", PASSWORD, person=person)

    with pytest.raises(PermissionDenied):
        archive_student(profile=world["profile"], actor=actor(user))


# -- removing a person --------------------------------------------------------


def test_an_org_admin_can_remove_staff(world):
    delete_person(person=world["teacher"], actor=actor(world["boss_user"]))

    with allow_unscoped("test"):
        world["teacher"].refresh_from_db()
    assert world["teacher"].is_deleted


def test_an_org_admin_can_remove_a_student(world):
    delete_person(person=world["child"], actor=actor(world["boss_user"]))

    with allow_unscoped("test"):
        world["child"].refresh_from_db()
    assert world["child"].is_deleted


def test_a_removed_person_disappears_from_every_scoped_query(world):
    delete_person(person=world["teacher"], actor=actor(world["boss_user"]))

    visible = Person.objects.for_actor(actor(world["boss_user"])).filter(pk=world["teacher"].pk)

    assert not visible.exists()


def test_a_removed_person_cannot_sign_in(world):
    """⚠ Two independent stops, because "removed" that leaves a live session is
    not removed: the login is deactivated, and actor_for_user refuses to build a
    scope for a deleted person even if it were not."""
    delete_person(person=world["teacher"], actor=actor(world["boss_user"]))

    with allow_unscoped("test"):
        login = User.objects.get(pk=world["teacher_user"].pk)
    assert login.is_active is False

    login.is_active = True  # even if the flag were somehow flipped back
    rebuilt = actor_for_user(login)
    assert rebuilt.organization_id is None
    assert rebuilt.person_id is None


def test_removing_keeps_the_history(world):
    """⚠ A soft delete. The row stays so attendance, audit and financial records
    keep pointing at a real person."""
    delete_person(person=world["child"], actor=actor(world["boss_user"]))

    with allow_unscoped("test"):
        assert Person.objects.filter(pk=world["child"].pk).exists()


def test_removal_is_reversible(world):
    delete_person(person=world["teacher"], actor=actor(world["boss_user"]))
    restore_person(person=world["teacher"], actor=actor(world["boss_user"]))

    with allow_unscoped("test"):
        world["teacher"].refresh_from_db()
        login = User.objects.get(pk=world["teacher_user"].pk)
    assert not world["teacher"].is_deleted
    assert login.is_active is True
    assert (
        Person.objects.for_actor(actor(world["boss_user"])).filter(pk=world["teacher"].pk).exists()
    )


def test_you_cannot_remove_yourself(world):
    """⚠ It revokes your own access mid-request, and if you were the last
    administrator nobody is left who can undo it."""
    with pytest.raises(ValidationError):
        delete_person(person=world["boss"], actor=actor(world["boss_user"]))


def test_one_org_admin_can_remove_another(world):
    delete_person(person=world["second_boss"], actor=actor(world["boss_user"]))

    with allow_unscoped("test"):
        world["second_boss"].refresh_from_db()
    assert world["second_boss"].is_deleted


def test_a_sole_org_admin_cannot_be_removed(world):
    """⚠ In practice the self-removal guard is what stops this.

    Only an organisation administrator holds PERSON_DELETE, so once there is one
    left the only person who could remove them is themselves — and that is
    refused. The explicit last-administrator check in delete_person is therefore
    a backstop rather than the thing doing the work today; it would start
    mattering the moment PERSON_DELETE were granted to any other role, which is
    exactly when nobody would remember to add it.
    """
    delete_person(person=world["second_boss"], actor=actor(world["boss_user"]))

    with pytest.raises(ValidationError):
        delete_person(person=world["boss"], actor=actor(world["boss_user"]))

    with allow_unscoped("test"):
        world["boss"].refresh_from_db()
    assert not world["boss"].is_deleted


def test_the_last_admin_backstop_fires_on_its_own_terms(world):
    """⚠ Exercises the branch the test above cannot reach, by asking somebody
    who is not the target to remove the only administrator left."""
    delete_person(person=world["second_boss"], actor=actor(world["boss_user"]))

    with allow_unscoped("test"):
        deputy = Person.objects.create(
            organization=world["org"], given_name="Late", family_name="Admin"
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=deputy,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
        deputy_user = User.objects.create_user("late@example.com", PASSWORD, person=deputy)
        # Revoke the new admin's own role *after* building their actor, so they
        # still hold the permission while no longer counting towards the total.
        deputy_actor = actor(deputy_user)
        RoleAssignment.objects.filter(person=deputy).update(revoked_at=timezone.now())

    with pytest.raises(ValidationError):
        delete_person(person=world["boss"], actor=deputy_actor)


def test_an_instructor_cannot_remove_anybody(world):
    with pytest.raises(PermissionDenied):
        delete_person(person=world["child"], actor=actor(world["teacher_user"]))


def test_a_dojo_admin_cannot_remove_anybody(world):
    """⚠ Archiving is theirs; removal reaches across every dojo and stays with
    an organisation administrator."""
    with pytest.raises(PermissionDenied):
        delete_person(person=world["child"], actor=actor(world["dojo_admin_user"]))


def test_removal_is_audited(world):
    from apps.core.models import AuditLog

    delete_person(person=world["teacher"], actor=actor(world["boss_user"]))

    with allow_unscoped("test"):
        assert AuditLog.objects.filter(action="delete").exists()


# -- the screens --------------------------------------------------------------


def test_the_confirmation_page_says_what_will_happen(client, world):
    client.force_login(world["boss_user"])

    body = client.get(reverse("person-delete", args=[world["teacher"].pk])).content.decode()

    assert "cannot sign in" in body
    assert "history is kept" in body.lower() or "History is kept" in body
    assert "undo" in body.lower()


def test_a_get_on_the_confirmation_page_removes_nobody(client, world):
    """⚠ The page is a confirmation, not the action."""
    client.force_login(world["boss_user"])

    client.get(reverse("person-delete", args=[world["teacher"].pk]))

    with allow_unscoped("test"):
        world["teacher"].refresh_from_db()
    assert not world["teacher"].is_deleted


def test_an_instructor_cannot_reach_the_confirmation_page(client, world):
    client.force_login(world["teacher_user"])

    response = client.get(reverse("person-delete", args=[world["child"].pk]))

    assert response.status_code == 403


def test_removed_people_are_listed_and_restorable(client, world):
    delete_person(person=world["teacher"], actor=actor(world["boss_user"]))
    client.force_login(world["boss_user"])

    body = client.get(reverse("removed-people")).content.decode()
    assert "Mei" in body

    client.post(reverse("person-restore", args=[world["teacher"].pk]))
    with allow_unscoped("test"):
        world["teacher"].refresh_from_db()
    assert not world["teacher"].is_deleted


def test_the_removed_list_is_closed_to_others(client, world):
    """⚠ Checked on roles, not on a row: the list may be empty, and there would
    then be nothing to check against."""
    client.force_login(world["dojo_admin_user"])

    assert client.get(reverse("removed-people")).status_code == 403


def test_the_student_page_offers_archiving_to_an_instructor(client, world):
    client.force_login(world["teacher_user"])

    body = client.get(reverse("student-detail", args=[world["child"].pk])).content.decode()

    assert reverse("student-archive", args=[world["child"].pk]) in body


def test_the_student_page_offers_removal_to_an_org_admin(client, world):
    """⚠ Students never appear in the staff list, so this was the only page an
    administrator could reach one from — and it offered archiving and no way to
    remove. The control existed and was unreachable, which is indistinguishable
    from broken."""
    client.force_login(world["boss_user"])

    body = client.get(reverse("student-detail", args=[world["child"].pk])).content.decode()

    assert reverse("person-delete", args=[world["child"].pk]) in body


def test_the_student_page_hides_removal_from_an_instructor(client, world):
    client.force_login(world["teacher_user"])

    body = client.get(reverse("student-detail", args=[world["child"].pk])).content.decode()

    assert reverse("person-delete", args=[world["child"].pk]) not in body
    assert reverse("student-archive", args=[world["child"].pk]) in body


def test_the_person_page_offers_removal_only_to_those_who_may(client, world):
    client.force_login(world["boss_user"])
    allowed = client.get(reverse("person-detail", args=[world["teacher"].pk])).content.decode()
    assert reverse("person-delete", args=[world["teacher"].pk]) in allowed

    client.force_login(world["dojo_admin_user"])
    denied = client.get(reverse("person-detail", args=[world["teacher"].pk])).content.decode()
    assert reverse("person-delete", args=[world["teacher"].pk]) not in denied


def test_the_page_never_offers_to_remove_yourself(client, world):
    client.force_login(world["boss_user"])

    body = client.get(reverse("person-detail", args=[world["boss"].pk])).content.decode()

    assert reverse("person-delete", args=[world["boss"].pk]) not in body


# -- the two ways a person could go missing from the staff list ---------------


def test_a_removed_person_is_gone_from_the_staff_list(client, world):
    """⚠ The reported bug. RoleAssignment is not soft-deletable, so every query
    joining to Person had to remember to exclude removed ones by itself. The
    staff list did not, so removed people stayed listed — and 404ed on the
    click, because Person's own manager had already stopped returning them."""
    delete_person(person=world["teacher"], actor=actor(world["boss_user"]))
    client.force_login(world["boss_user"])

    body = client.get(reverse("org-settings")).content.decode()

    assert "Mei" not in body
    assert reverse("person-detail", args=[world["teacher"].pk]) not in body


def test_no_scoped_role_query_can_surface_a_removed_person(world):
    """⚠ Fixed at the manager rather than at the call site, because there were a
    dozen call sites and no way to tell which had remembered."""
    from apps.identity.models import RoleAssignment

    delete_person(person=world["teacher"], actor=actor(world["boss_user"]))

    for_actor = RoleAssignment.objects.for_actor(actor(world["boss_user"])).filter(
        person=world["teacher"]
    )
    for_org = RoleAssignment.objects.for_organization(world["org"].pk).filter(
        person=world["teacher"]
    )

    assert not for_actor.exists()
    assert not for_org.exists()


def test_actor_construction_still_sees_its_own_assignments(world):
    """⚠ The one path that must keep reading them unfiltered: it uses unscoped()
    and guards on the person's deleted_at itself. Filtering there would be
    harmless today and wrong the moment the guard moved."""
    rebuilt = actor(world["boss_user"])

    assert rebuilt.organization_id == world["org"].pk
    assert rebuilt.roles


def test_revoking_every_role_does_not_hide_somebody(client, world):
    """⚠ The second thing reported, and it is not the same as removal.

    Taking the last role away used to drop somebody off this page entirely:
    still in the database, still reachable by anyone who knew the URL, but with
    no route back to grant them another role or remove them properly.
    """
    from apps.identity.models import RoleAssignment

    with allow_unscoped("test"):
        RoleAssignment.objects.filter(person=world["teacher"]).update(revoked_at=timezone.now())
    client.force_login(world["boss_user"])

    body = client.get(reverse("org-settings")).content.decode()

    assert "Mei" in body, "somebody with no roles fell off the staff list"
    assert reverse("person-detail", args=[world["teacher"].pk]) in body
    assert "no roles" in body


def test_somebody_with_no_roles_can_be_given_one_again(client, world):
    """⚠ The point of keeping them listed."""
    from apps.identity.models import RoleAssignment

    with allow_unscoped("test"):
        RoleAssignment.objects.filter(person=world["teacher"]).update(revoked_at=timezone.now())
    client.force_login(world["boss_user"])

    client.post(
        reverse("role-grant", args=[world["teacher"].pk]),
        {"role": Role.INSTRUCTOR, "scope": "dojo", "dojo": str(world["dojo"].pk)},
    )

    with allow_unscoped("test"):
        assert RoleAssignment.objects.filter(
            person=world["teacher"], revoked_at__isnull=True
        ).exists()


def test_revoking_a_role_leaves_the_person_alone(world):
    """⚠ Revoking a role is not deleting a person, and nothing should blur it."""
    from apps.identity.models import RoleAssignment

    with allow_unscoped("test"):
        RoleAssignment.objects.filter(person=world["teacher"]).update(revoked_at=timezone.now())
        world["teacher"].refresh_from_db()
        login = User.objects.get(pk=world["teacher_user"].pk)

    assert not world["teacher"].is_deleted
    assert login.is_active is True
