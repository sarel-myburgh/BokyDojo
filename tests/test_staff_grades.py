"""The rank a member of staff holds — plan §3.

⚠ The design decision under test throughout: a staff grade is **not** a
StudentStyleTrack. An instructor who is 5th dan is usually not enrolled as a
student anywhere, and giving them a student profile so they could hold a grade
would put them in class rosters, the check-in grid, and the active-students
count — all of which would be wrong.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.urls import reverse

from apps.core.scoping import allow_unscoped
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
from apps.ranks.models import Rank, RankLadder, StudentStyleTrack, Style
from apps.staffing.models import StaffGrade

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"  # pragma: allowlist secret


@pytest.fixture
def world():
    with allow_unscoped("staff grade test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        other_org = Organization.objects.create(name="Elsewhere", slug="elsewhere")
        dojo = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )

        boss = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org, person=boss, role=Role.ORG_ADMIN, scope_type=ScopeType.ORG
        )
        boss_user = User.objects.create_user("ops@example.com", PASSWORD, person=boss)

        teacher = Person.objects.create(organization=org, given_name="Mei", family_name="Kato")
        RoleAssignment.objects.create(
            organization=org,
            person=teacher,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        teacher_user = User.objects.create_user("mei@example.com", PASSWORD, person=teacher)

        karate = Style.objects.create(organization=org, name="Goju Ryu", is_ranked=True)
        boxing = Style.objects.create(organization=org, name="Boxing", is_ranked=False)
        ladder = RankLadder.objects.create(style=karate, name="Adult")
        shodan = Rank.objects.create(ladder=ladder, order=1, name="1st Dan")

        elsewhere_style = Style.objects.create(organization=other_org, name="Judo", is_ranked=True)
        elsewhere_ladder = RankLadder.objects.create(style=elsewhere_style, name="Adult")
        elsewhere_rank = Rank.objects.create(ladder=elsewhere_ladder, order=1, name="Green")

    return {
        "org": org,
        "other_org": other_org,
        "dojo": dojo,
        "boss": boss,
        "boss_user": boss_user,
        "teacher": teacher,
        "teacher_user": teacher_user,
        "karate": karate,
        "boxing": boxing,
        "ladder": ladder,
        "shodan": shodan,
        "elsewhere_style": elsewhere_style,
        "elsewhere_rank": elsewhere_rank,
    }


# -- a grade without being a student ------------------------------------------


def test_staff_can_hold_a_grade_without_a_student_record(world):
    """⚠ The whole point. No StudentProfile is created, so they stay out of
    rosters, the check-in grid, and the active-students count."""
    with allow_unscoped("test"):
        grade = StaffGrade.objects.create(
            person=world["teacher"], style=world["karate"], label="5th Dan"
        )

        assert grade.display_name == "5th Dan"
        assert not StudentProfile.objects.filter(person=world["teacher"]).exists()


def test_a_grade_can_come_off_a_configured_ladder(world):
    with allow_unscoped("test"):
        grade = StaffGrade.objects.create(
            person=world["teacher"], style=world["karate"], rank=world["shodan"]
        )

    assert grade.display_name == "1st Dan"


def test_a_typed_grade_is_allowed_because_dan_ranks_are_often_not_on_a_ladder(world):
    """⚠ Student ladders routinely stop at black belt. Refusing "5th Dan" until
    somebody extended one would mean the field simply went unused."""
    with allow_unscoped("test"):
        grade = StaffGrade(person=world["teacher"], style=world["karate"], label="5th Dan")
        grade.full_clean()
        grade.save()

    assert grade.rank_id is None


def test_a_grade_is_never_both_a_rank_and_a_label(world):
    """Two names for one grade can disagree, and nothing would say which wins."""
    grade = StaffGrade(
        person=world["teacher"],
        style=world["karate"],
        rank=world["shodan"],
        label="5th Dan",
    )

    # ⚠ clean() directly, not full_clean(). full_clean also evaluates the
    # database constraint, which would catch this too — and then this test would
    # pass whether or not clean() checked anything, while it is clean() that
    # produces the message the form shows against the field.
    with pytest.raises(ValidationError):
        grade.clean()


def test_a_grade_is_never_neither(world):
    grade = StaffGrade(person=world["teacher"], style=world["karate"])

    with pytest.raises(ValidationError):
        grade.clean()


def test_the_database_refuses_both_or_neither_even_without_full_clean(world):
    """⚠ A constraint, not only a form check — bulk loads and the import path
    never call full_clean."""
    with allow_unscoped("test"), pytest.raises(IntegrityError):
        StaffGrade.objects.create(person=world["teacher"], style=world["karate"], label="")


def test_a_rank_from_another_style_is_refused(world):
    """Otherwise a karate belt gets filed under boxing."""
    grade = StaffGrade(person=world["teacher"], style=world["boxing"], rank=world["shodan"])

    with pytest.raises(ValidationError):
        grade.clean()


def test_only_one_grade_per_style_per_person(world):
    """Two rows are two answers to "what dan is she" with nothing saying which."""
    with allow_unscoped("test"):
        StaffGrade.objects.create(person=world["teacher"], style=world["karate"], label="1st Dan")

        with pytest.raises(IntegrityError):
            StaffGrade.objects.create(
                person=world["teacher"], style=world["karate"], label="2nd Dan"
            )


def test_a_grade_is_optional(world):
    """⚠ Boxing has no belts. Nothing anywhere requires a grade to exist."""
    with allow_unscoped("test"):
        assert not StaffGrade.objects.filter(person=world["teacher"]).exists()

    # The person page renders perfectly happily without one — see
    # test_profiles.test_somebody_with_no_grade_is_offered_a_way_to_record_one.


# -- who may record one -------------------------------------------------------


def test_an_admin_can_record_a_grade(client, world):
    """⚠ Also the regression test for the scoping trap this hit first time out.

    Model.full_clean runs validate_unique, which evaluates a queryset with no
    tenant scope and raises UnscopedAccessError — so recording any grade at all
    failed. The duplicate check moved into the form, where it is scoped.
    """
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("staff-grade-add", args=[world["teacher"].pk]),
        {"style": str(world["karate"].pk), "rank": "", "label": "5th Dan", "awarded_on": ""},
    )

    assert response.status_code == 302
    with allow_unscoped("test"):
        assert StaffGrade.objects.get(person=world["teacher"]).label == "5th Dan"


def test_an_instructor_cannot_type_themselves_a_grade(client, world):
    """⚠ The reason administers_person exists apart from may_edit_person.

    Everybody may edit their own contact details — that is a way of reaching
    them. A rank is a claim about them, and self-service would make the field
    worthless.
    """
    client.force_login(world["teacher_user"])

    response = client.post(
        reverse("staff-grade-add", args=[world["teacher"].pk]),
        {"style": str(world["karate"].pk), "rank": "", "label": "10th Dan", "awarded_on": ""},
    )

    assert response.status_code == 403
    with allow_unscoped("test"):
        assert not StaffGrade.objects.filter(person=world["teacher"]).exists()


def test_an_instructor_cannot_grade_a_colleague_either(client, world):
    client.force_login(world["teacher_user"])

    response = client.post(
        reverse("staff-grade-add", args=[world["boss"].pk]),
        {"style": str(world["karate"].pk), "rank": "", "label": "1st Dan", "awarded_on": ""},
    )

    assert response.status_code == 403


def test_an_admin_can_remove_a_grade(client, world):
    with allow_unscoped("test"):
        grade = StaffGrade.objects.create(
            person=world["teacher"], style=world["karate"], label="1st Dan"
        )
    client.force_login(world["boss_user"])

    client.post(reverse("staff-grade-delete", args=[world["teacher"].pk, grade.pk]))

    with allow_unscoped("test"):
        assert not StaffGrade.objects.filter(pk=grade.pk).exists()


def test_an_instructor_cannot_remove_a_grade(client, world):
    with allow_unscoped("test"):
        grade = StaffGrade.objects.create(
            person=world["teacher"], style=world["karate"], label="1st Dan"
        )
    client.force_login(world["teacher_user"])

    response = client.post(reverse("staff-grade-delete", args=[world["teacher"].pk, grade.pk]))

    assert response.status_code == 403
    with allow_unscoped("test"):
        assert StaffGrade.objects.filter(pk=grade.pk).exists()


# -- not two answers for one style --------------------------------------------


def test_a_staff_grade_is_refused_where_a_student_track_already_exists(client, world):
    """⚠ The track is the live, promotable record. A staff grade beside it would
    be a second number for one style with nothing to say which is current."""
    with allow_unscoped("test"):
        StudentProfile.objects.create(
            person=world["teacher"],
            home_dojo=world["dojo"],
            status=StudentProfile.Status.ACTIVE,
        )
        StudentStyleTrack.objects.create(
            student=world["teacher"],
            style=world["karate"],
            ladder=world["ladder"],
            started_on=date(2024, 1, 1),
        )
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("staff-grade-add", args=[world["teacher"].pk]),
        {"style": str(world["karate"].pk), "rank": "", "label": "5th Dan", "awarded_on": ""},
    )

    assert response.status_code == 200
    assert "already graded in this style as a student" in response.content.decode()
    with allow_unscoped("test"):
        assert not StaffGrade.objects.filter(person=world["teacher"]).exists()


def test_a_staff_grade_is_allowed_in_a_style_they_have_no_track_in(client, world):
    """Being a student of karate does not stop them holding a boxing record."""
    with allow_unscoped("test"):
        StudentProfile.objects.create(
            person=world["teacher"],
            home_dojo=world["dojo"],
            status=StudentProfile.Status.ACTIVE,
        )
        StudentStyleTrack.objects.create(
            student=world["teacher"],
            style=world["karate"],
            ladder=world["ladder"],
            started_on=date(2024, 1, 1),
        )
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("staff-grade-add", args=[world["teacher"].pk]),
        {"style": str(world["boxing"].pk), "rank": "", "label": "Coach", "awarded_on": ""},
    )

    assert response.status_code == 302
    with allow_unscoped("test"):
        assert StaffGrade.objects.filter(person=world["teacher"], style=world["boxing"]).exists()


# -- tenancy ------------------------------------------------------------------


def test_a_style_from_another_organisation_cannot_be_chosen(client, world):
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("staff-grade-add", args=[world["teacher"].pk]),
        {
            "style": str(world["elsewhere_style"].pk),
            "rank": "",
            "label": "1st Dan",
            "awarded_on": "",
        },
    )

    assert response.status_code == 200
    with allow_unscoped("test"):
        assert not StaffGrade.objects.filter(person=world["teacher"]).exists()


# -- the page -----------------------------------------------------------------


def test_the_grade_shows_on_the_person_page(client, world):
    with allow_unscoped("test"):
        StaffGrade.objects.create(
            person=world["teacher"],
            style=world["karate"],
            label="5th Dan",
            awarded_on=date(2019, 6, 1),
        )
    client.force_login(world["boss_user"])

    body = client.get(reverse("person-detail", args=[world["teacher"].pk])).content.decode()

    assert "5th Dan" in body
    assert "Goju Ryu" in body


def test_the_form_is_hidden_from_somebody_who_may_not_record_grades(client, world):
    client.force_login(world["teacher_user"])

    body = client.get(reverse("account")).content.decode()

    assert reverse("staff-grade-add", args=[world["teacher"].pk]) not in body
