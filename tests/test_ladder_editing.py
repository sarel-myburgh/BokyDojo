"""Defining belts, and dojos that disagree about them.

The case that prompted this: two clubs teaching the same art run different
syllabuses — eight kyu grades at one, ten at another — and the model used to
forbid it outright with ``unique(style, applies_to)``.

⚠ The load-bearing tests are ``test_a_dojos_own_belts_win_over_the_organisations``
and ``test_an_awarded_belt_cannot_be_removed``. The first is the whole point of
the feature; the second is the line between a setting and a record.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.enrolment import enrol_student
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
from apps.ranks.enrolment_tracks import choose_ladder
from apps.ranks.models import Rank, RankLadder, StudentStyleTrack, Style

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def world():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        goju = Style.objects.create(organization=org, name="Goju Ryu", is_ranked=True)
        sen_sok = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )
        central = Dojo.objects.create(
            organization=org, name="Central", slug="central", timezone="Asia/Phnom_Penh"
        )
        sen_sok.styles.set([goju])
        central.styles.set([goju])
        person = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org, person=person, role=Role.ORG_ADMIN, scope_type=ScopeType.ORG
        )
        admin = User.objects.create_user(email="ops@example.com", password=PASSWORD, person=person)
    actor = Actor(
        user_id=admin.pk,
        person_id=person.pk,
        organization_id=org.pk,
        dojo_ids=None,
        roles=frozenset({(Role.ORG_ADMIN, ScopeType.ORG, None)}),
    )
    return {
        "org": org,
        "goju": goju,
        "sen_sok": sen_sok,
        "central": central,
        "admin": admin,
        "actor": actor,
    }


def make_ladder(style, *, name, dojo=None, applies_to=RankLadder.AppliesTo.ADULT, belts=()):
    with allow_unscoped("test setup"):
        ladder = RankLadder.objects.create(style=style, name=name, dojo=dojo, applies_to=applies_to)
        for order, belt in enumerate(belts, start=1):
            Rank.objects.create(ladder=ladder, name=belt, order=order)
        return ladder


def make_student(org, given="Emma", dob=None):
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=org, given_name=given, family_name="Roux", date_of_birth=dob
        )
        StudentProfile.objects.create(person=person, status=StudentProfile.Status.ACTIVE)
        return person


def track_of(person):
    with allow_unscoped("test read"):
        return (
            StudentStyleTrack.objects.filter(student=person)
            .select_related("ladder", "ladder__dojo")
            .first()
        )


# -- dojos that disagree ------------------------------------------------------


def test_two_dojos_can_run_different_belts_for_one_style(world):
    """⚠ Used to be impossible: unique(style, applies_to) allowed exactly one."""
    make_ladder(world["goju"], name="Standard", belts=["10th Kyu", "9th Kyu"])
    make_ladder(world["goju"], name="Sen Sok belts", dojo=world["sen_sok"], belts=["8th Kyu"])

    with allow_unscoped("test read"):
        assert RankLadder.objects.filter(style=world["goju"]).count() == 2


def test_a_dojos_own_belts_win_over_the_organisations(world):
    """The whole point: enrolling at Sen Sok puts you on Sen Sok's belts."""
    make_ladder(world["goju"], name="Standard", belts=["10th Kyu"])
    sen_sok_ladder = make_ladder(
        world["goju"], name="Sen Sok belts", dojo=world["sen_sok"], belts=["8th Kyu"]
    )
    emma = make_student(world["org"])

    enrol_student(
        student=emma,
        dojo=world["sen_sok"],
        started_on=datetime.date(2026, 1, 5),
        actor=world["actor"],
    )

    assert track_of(emma).ladder == sen_sok_ladder


def test_a_dojo_without_its_own_belts_uses_the_organisations(world):
    org_ladder = make_ladder(world["goju"], name="Standard", belts=["10th Kyu"])
    make_ladder(world["goju"], name="Sen Sok belts", dojo=world["sen_sok"], belts=["8th Kyu"])
    emma = make_student(world["org"])

    enrol_student(
        student=emma,
        dojo=world["central"],
        started_on=datetime.date(2026, 1, 5),
        actor=world["actor"],
    )

    assert track_of(emma).ladder == org_ladder


def test_age_still_splits_junior_and_adult_within_one_dojo(world):
    make_ladder(
        world["goju"],
        name="Sen Sok adults",
        dojo=world["sen_sok"],
        applies_to=RankLadder.AppliesTo.ADULT,
    )
    make_ladder(
        world["goju"],
        name="Sen Sok juniors",
        dojo=world["sen_sok"],
        applies_to=RankLadder.AppliesTo.JUNIOR,
    )
    child = make_student(world["org"], dob=datetime.date.today() - datetime.timedelta(days=9 * 365))

    ladder = choose_ladder(
        world["goju"],
        student=child,
        organization_id=world["org"].pk,
        dojo=world["sen_sok"],
    )

    assert ladder.applies_to == RankLadder.AppliesTo.JUNIOR
    assert ladder.dojo == world["sen_sok"]


def test_two_organisation_wide_adult_ladders_are_refused(world):
    """⚠ A partial constraint, because SQL does not treat NULLs as equal — one
    plain unique(style, dojo, applies_to) would allow this."""
    from django.db.utils import IntegrityError

    make_ladder(world["goju"], name="First", belts=["10th Kyu"])

    with pytest.raises(IntegrityError):
        make_ladder(world["goju"], name="Second")


# -- editing belts ------------------------------------------------------------


def test_a_ladder_can_be_added_through_the_screen(client, world):
    client.force_login(world["admin"])

    client.post(
        reverse("style-detail", args=[world["goju"].pk]),
        {"name": "Sen Sok belts", "applies_to": "adult", "dojo": str(world["sen_sok"].pk)},
    )

    with allow_unscoped("test read"):
        ladder = RankLadder.objects.get(name="Sen Sok belts")
    assert ladder.dojo == world["sen_sok"]


def test_belts_are_appended_in_order(client, world):
    ladder = make_ladder(world["goju"], name="Standard")
    client.force_login(world["admin"])

    for belt in ("10th Kyu", "9th Kyu", "8th Kyu"):
        client.post(
            reverse("ladder-detail", args=[ladder.pk]),
            {
                "name": belt,
                "belt_colour": "white",
                "stripe_count": 0,
                "min_months_at_previous": 3,
                "min_classes_since_previous": 0,
                "min_age": 0,
            },
        )

    with allow_unscoped("test read"):
        names = list(
            Rank.objects.filter(ladder=ladder).order_by("order").values_list("name", flat=True)
        )
    assert names == ["10th Kyu", "9th Kyu", "8th Kyu"]


def test_belts_can_be_reordered(client, world):
    """⚠ Two passes, or unique(ladder, order) collides the moment two swap."""
    ladder = make_ladder(world["goju"], name="Standard", belts=["A", "B", "C"])
    with allow_unscoped("test read"):
        ranks = {r.name: r for r in Rank.objects.filter(ladder=ladder)}
    client.force_login(world["admin"])

    client.post(
        reverse("rank-reorder", args=[ladder.pk]),
        {
            f"order:{ranks['A'].pk}": "3",
            f"order:{ranks['B'].pk}": "1",
            f"order:{ranks['C'].pk}": "2",
        },
    )

    with allow_unscoped("test read"):
        names = list(
            Rank.objects.filter(ladder=ladder).order_by("order").values_list("name", flat=True)
        )
    assert names == ["B", "C", "A"]


def test_an_unawarded_belt_can_be_removed(client, world):
    ladder = make_ladder(world["goju"], name="Standard", belts=["A", "B"])
    with allow_unscoped("test read"):
        rank = Rank.objects.get(ladder=ladder, name="B")
    client.force_login(world["admin"])

    client.post(reverse("rank-delete", args=[ladder.pk, rank.pk]))

    with allow_unscoped("test read"):
        assert not Rank.objects.filter(pk=rank.pk).exists()


def test_an_awarded_belt_cannot_be_removed(client, world):
    """⚠ A grade somebody holds is a record, not a setting. RankAward.rank is
    PROTECT, so without the check this is an IntegrityError 500."""
    ladder = make_ladder(world["goju"], name="Standard", belts=["10th Kyu"])
    emma = make_student(world["org"])
    enrol_student(
        student=emma,
        dojo=world["central"],
        started_on=datetime.date(2026, 1, 5),
        actor=world["actor"],
    )
    with allow_unscoped("test read"):
        profile = StudentProfile.objects.get(person=emma)
        rank = Rank.objects.get(ladder=ladder)
    from apps.ranks.promotions import promote_student

    promote_student(
        profile=profile,
        track=track_of(emma),
        rank=rank,
        awarded_on=datetime.date(2026, 2, 1),
        actor=world["actor"],
    )
    client.force_login(world["admin"])

    response = client.post(reverse("rank-delete", args=[ladder.pk, rank.pk]), follow=True)

    with allow_unscoped("test read"):
        assert Rank.objects.filter(pk=rank.pk).exists()
    assert "cannot be removed" in response.content.decode()


def test_the_ladder_screen_flags_a_style_with_no_belts(client, world):
    client.force_login(world["admin"])

    body = client.get(reverse("style-detail", args=[world["goju"].pk])).content.decode()

    assert "nobody can be graded" in body


def test_an_unranked_style_offers_no_belts(client, world):
    with allow_unscoped("test setup"):
        boxing = Style.objects.create(organization=world["org"], name="Boxing", is_ranked=False)
    client.force_login(world["admin"])

    body = client.get(reverse("style-detail", args=[boxing.pk])).content.decode()

    assert "awards no grades" in body
    assert "Add a set of belts" not in body


def test_an_instructor_cannot_edit_belts(client, world):
    with allow_unscoped("test setup"):
        person = Person.objects.create(
            organization=world["org"], given_name="Sen", family_name="Sei"
        )
        RoleAssignment.objects.create(
            organization=world["org"],
            person=person,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=world["sen_sok"],
        )
        user = User.objects.create_user(
            email="sensei@example.com", password=PASSWORD, person=person
        )
    ladder = make_ladder(world["goju"], name="Standard", belts=["A"])
    client.force_login(user)

    assert client.get(reverse("style-detail", args=[world["goju"].pk])).status_code == 403
    assert client.get(reverse("ladder-detail", args=[ladder.pk])).status_code == 403


def test_another_tenants_ladder_is_a_404(client, world):
    other = Organization.objects.create(name="Other", slug="other-org")
    with allow_unscoped("test setup"):
        foreign_style = Style.objects.create(organization=other, name="Foreign")
        foreign = RankLadder.objects.create(
            style=foreign_style, name="Theirs", applies_to=RankLadder.AppliesTo.ADULT
        )
    client.force_login(world["admin"])

    assert client.get(reverse("ladder-detail", args=[foreign.pk])).status_code == 404


def test_a_ladder_cannot_pair_two_organisations(world):
    """⚠ same_organization_fields, added when `dojo` arrived. Scoping decides who
    may read a row, never what may be written."""
    from django.core.exceptions import ValidationError

    other = Organization.objects.create(name="Other", slug="other-org")
    with allow_unscoped("test setup"):
        foreign_dojo = Dojo.objects.create(
            organization=other, name="Theirs", slug="theirs", timezone="UTC"
        )
        with pytest.raises(ValidationError):
            RankLadder.objects.create(
                style=world["goju"],
                dojo=foreign_dojo,
                name="Sneaky",
                applies_to=RankLadder.AppliesTo.ADULT,
            )
