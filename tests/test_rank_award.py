"""RankAward — TODO 1.2.5, 1.2.9, 1.2.10, plan §4.4."""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Organization, Person
from apps.ranks.models import Rank, RankAward, StudentStyleTrack
from apps.ranks.seeding import create_shotokan_ladders

pytestmark = pytest.mark.django_db

DAY = datetime.date(2024, 3, 1)
LATER = datetime.date(2024, 9, 1)


@pytest.fixture
def world():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Award Org", slug="award-org")
        adult, junior = create_shotokan_ladders(org)
        student = Person.objects.create(organization=org, given_name="Rithy", family_name="Sok")
        sensei = Person.objects.create(organization=org, given_name="Kenji", family_name="Sensei")
        track = StudentStyleTrack.objects.create(
            student=student, style=adult.style, ladder=adult, started_on=DAY
        )
        ranks = list(Rank.objects.filter(ladder=adult).order_by("order"))
    return {
        "org": org,
        "adult": adult,
        "junior": junior,
        "student": student,
        "sensei": sensei,
        "track": track,
        "ranks": ranks,
    }


def _award(world, index, **kwargs):
    with allow_unscoped("test setup"):
        return RankAward.objects.create(
            track=world["track"],
            rank=world["ranks"][index],
            awarded_on=kwargs.pop("awarded_on", DAY),
            **kwargs,
        )


# -- derived current rank -----------------------------------------------------


def test_awarding_updates_the_tracks_current_rank(world):
    _award(world, 0)
    world["track"].refresh_from_db()
    assert world["track"].current_rank_id == world["ranks"][0].pk


def test_current_rank_is_the_highest_held_not_the_most_recent(world):
    """Order of entry must not decide rank — a backdated award of a lower grade
    should not demote someone."""
    _award(world, 3, awarded_on=DAY)
    _award(world, 1, awarded_on=LATER)

    world["track"].refresh_from_db()
    assert world["track"].current_rank_id == world["ranks"][3].pk


def test_a_track_with_no_awards_has_no_rank(world):
    assert world["track"].current_rank_id is None


# -- revocation, not deletion -------------------------------------------------


def test_revoking_falls_back_to_the_previous_grade(world):
    _award(world, 0)
    top = _award(world, 2)

    top.revoke(by=world["sensei"], reason="graded in error")

    world["track"].refresh_from_db()
    assert world["track"].current_rank_id == world["ranks"][0].pk


def test_revocation_keeps_the_record(world):
    award = _award(world, 1)
    award.revoke(by=world["sensei"], reason="administrative correction")

    award.refresh_from_db()
    assert award.is_revoked
    assert award.revocation_reason == "administrative correction"
    assert award.revoked_by_id == world["sensei"].pk
    with allow_unscoped("verifying the row survives"):
        assert RankAward.objects.filter(pk=award.pk).exists()


def test_revocation_requires_a_reason(world):
    award = _award(world, 1)
    with pytest.raises(ValidationError):
        award.revoke(by=world["sensei"], reason="   ")


def test_revoking_twice_is_a_no_op(world):
    award = _award(world, 1)
    award.revoke(by=world["sensei"], reason="first")
    award.revoke(by=world["sensei"], reason="second")
    award.refresh_from_db()
    assert award.revocation_reason == "first"


def test_a_revoked_grade_may_be_awarded_again(world):
    """The unique constraint only covers live awards."""
    first = _award(world, 1)
    first.revoke(by=world["sensei"], reason="clerical error")
    again = _award(world, 1, awarded_on=LATER)
    assert again.pk != first.pk


def test_the_same_live_grade_cannot_be_awarded_twice(world):
    _award(world, 1)
    with allow_unscoped("test setup"), pytest.raises(IntegrityError):
        RankAward.objects.create(track=world["track"], rank=world["ranks"][1], awarded_on=LATER)


# -- external recognition (1.2.9) ---------------------------------------------


def test_recognised_grade_must_name_its_source(world):
    """ "Recognised, source unknown" is how unverifiable black belts enter a
    register and never leave."""
    with pytest.raises(ValidationError, match="organisation"):
        _award(world, 5, recognition=RankAward.Recognition.RECOGNISED)


def test_recognised_grade_with_a_source_is_accepted(world):
    award = _award(
        world,
        5,
        recognition=RankAward.Recognition.RECOGNISED,
        awarded_by_external_org="Japan Karate Association",
    )
    assert award.is_external


def test_provisional_recognition_also_requires_a_source(world):
    with pytest.raises(ValidationError):
        _award(world, 5, recognition=RankAward.Recognition.PROVISIONAL)


def test_internal_and_honorary_need_no_external_source(world):
    assert _award(world, 2).is_external is False
    assert _award(world, 3, recognition=RankAward.Recognition.HONORARY).is_external is False


# -- ladder integrity ---------------------------------------------------------


def test_a_rank_from_another_ladder_cannot_be_awarded(world):
    with allow_unscoped("test setup"):
        junior_rank = Rank.objects.filter(ladder=world["junior"]).first()
    with pytest.raises(ValidationError, match="ladder"):
        RankAward.objects.create(track=world["track"], rank=junior_rank, awarded_on=DAY)


# -- tenancy ------------------------------------------------------------------


def test_another_organisation_cannot_see_awards(world):
    _award(world, 0)
    with allow_unscoped("test setup"):
        other = Organization.objects.create(name="Outside", slug="outside-award-org")
    outsider = Actor(user_id=None, person_id=None, organization_id=other.pk)
    assert RankAward.objects.for_actor(outsider).count() == 0


def test_owning_organisation_sees_awards(world):
    _award(world, 0)
    actor = Actor(user_id=None, person_id=None, organization_id=world["org"].pk)
    assert RankAward.objects.for_actor(actor).count() == 1
