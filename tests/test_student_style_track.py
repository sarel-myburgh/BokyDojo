"""StudentStyleTrack — TODO 1.2.4, plan §4.2."""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Organization, Person
from apps.ranks.models import Rank, RankLadder, StudentStyleTrack, Style
from apps.ranks.seeding import create_shotokan_ladders

pytestmark = pytest.mark.django_db

START = datetime.date(2020, 1, 1)
LATER = datetime.date(2024, 6, 1)


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Track Org", slug="track-org")


@pytest.fixture
def ladders(org):
    with allow_unscoped("test setup"):
        adult, junior = create_shotokan_ladders(org)
    return {"adult": adult, "junior": junior}


@pytest.fixture
def student(org):
    with allow_unscoped("test setup"):
        return Person.objects.create(
            organization=org, given_name="Mealea", family_name="Kim"
        )


@pytest.fixture
def actor(org):
    return Actor(user_id=None, person_id=None, organization_id=org.pk)


def _track(student, ladder, **kwargs):
    with allow_unscoped("test setup"):
        return StudentStyleTrack.objects.create(
            student=student,
            style=ladder.style,
            ladder=ladder,
            started_on=kwargs.pop("started_on", START),
            **kwargs,
        )


# -- rank is per style --------------------------------------------------------


def test_a_student_can_progress_in_two_styles_independently(org, student, ladders):
    """The whole reason this model exists: 3rd kyu in karate and a blue belt in
    BJJ at the same time, on separate ladders."""
    with allow_unscoped("test setup"):
        bjj = Style.objects.create(organization=org, name="Brazilian Jiu-Jitsu")
        bjj_ladder = RankLadder.objects.create(
            style=bjj, name="BJJ Adult", applies_to=RankLadder.AppliesTo.ADULT
        )
        blue = Rank.objects.create(ladder=bjj_ladder, order=3, name="Blue", belt_colour="blue")
        karate_rank = Rank.objects.filter(ladder=ladders["adult"], order=8).first()

    _track(student, ladders["adult"], current_rank=karate_rank)
    _track(student, bjj_ladder, current_rank=blue)

    tracks = StudentStyleTrack.objects.for_organization(org.pk).filter(student=student)
    assert tracks.count() == 2
    assert {t.current_rank.belt_colour for t in tracks} == {"brown", "blue"}


def test_only_one_active_track_per_style(student, ladders):
    _track(student, ladders["adult"])
    with allow_unscoped("test setup"), pytest.raises(IntegrityError):
        StudentStyleTrack.objects.create(
            student=student,
            style=ladders["adult"].style,
            ladder=ladders["adult"],
            started_on=LATER,
        )


def test_a_closed_track_frees_the_slot(student, ladders):
    first = _track(student, ladders["adult"])
    first.close(status=StudentStyleTrack.Status.ENDED, on_date=LATER)

    second = _track(student, ladders["adult"], started_on=LATER)
    assert second.pk is not None


# -- junior to adult crossing -------------------------------------------------


def test_transfer_keeps_the_old_track_and_its_rank(student, ladders):
    """A child who turns sixteen does not have their history rewritten."""
    with allow_unscoped("test setup"):
        junior_rank = Rank.objects.filter(ladder=ladders["junior"]).order_by("-order").first()

    junior_track = _track(student, ladders["junior"], current_rank=junior_rank)
    adult_track = junior_track.transfer_to_ladder(ladders["adult"], on_date=LATER)

    junior_track.refresh_from_db()
    assert junior_track.status == StudentStyleTrack.Status.TRANSFERRED
    assert junior_track.ended_on == LATER
    assert junior_track.current_rank_id == junior_rank.pk  # history preserved

    assert adult_track.is_active
    assert adult_track.ladder_id == ladders["adult"].pk
    assert adult_track.current_rank_id is None  # regrades from scratch


def test_transfer_must_stay_within_the_same_style(org, student, ladders):
    with allow_unscoped("test setup"):
        judo = Style.objects.create(organization=org, name="Judo")
        judo_ladder = RankLadder.objects.create(
            style=judo, name="Judo Adult", applies_to=RankLadder.AppliesTo.ADULT
        )

    track = _track(student, ladders["junior"])
    with pytest.raises(ValidationError):
        track.transfer_to_ladder(judo_ladder, on_date=LATER)


# -- rank must match the ladder -----------------------------------------------


def test_rank_from_another_ladder_is_rejected(student, ladders):
    """A junior track holding an adult dan grade would make every downstream
    eligibility calculation run against the wrong progression."""
    with allow_unscoped("test setup"):
        dan = Rank.objects.filter(ladder=ladders["adult"]).order_by("-order").first()

    with pytest.raises(ValidationError, match="different ladder"):
        _track(student, ladders["junior"], current_rank=dan)


def test_matching_rank_is_accepted(student, ladders):
    with allow_unscoped("test setup"):
        junior_rank = Rank.objects.filter(ladder=ladders["junior"]).first()
    track = _track(student, ladders["junior"], current_rank=junior_rank)
    assert track.current_rank_id == junior_rank.pk


def test_ungraded_track_is_allowed(student, ladders):
    """A beginner enrolled but not yet graded."""
    track = _track(student, ladders["adult"])
    assert track.current_rank_id is None
    assert "ungraded" in str(track)


# -- lifecycle ----------------------------------------------------------------


def test_close_requires_a_terminal_status(student, ladders):
    track = _track(student, ladders["adult"])
    with pytest.raises(ValueError):
        track.close(status=StudentStyleTrack.Status.ACTIVE, on_date=LATER)


def test_active_track_may_not_carry_an_end_date(student, ladders):
    with allow_unscoped("test setup"), pytest.raises(IntegrityError):
        StudentStyleTrack.objects.create(
            student=student,
            style=ladders["adult"].style,
            ladder=ladders["adult"],
            started_on=START,
            ended_on=LATER,
            status=StudentStyleTrack.Status.ACTIVE,
        )


def test_end_date_may_not_precede_the_start(student, ladders):
    track = _track(student, ladders["adult"])
    with allow_unscoped("test setup"), pytest.raises(IntegrityError):
        track.close(status=StudentStyleTrack.Status.ENDED, on_date=datetime.date(2019, 1, 1))


# -- tenancy ------------------------------------------------------------------


def test_cross_organisation_style_is_rejected(org, student, ladders):
    with allow_unscoped("test setup"):
        other = Organization.objects.create(name="Other", slug="other-track-org")
        other_style = Style.objects.create(organization=other, name="Shotokan Karate")
        other_ladder = RankLadder.objects.create(
            style=other_style, name="Theirs", applies_to=RankLadder.AppliesTo.ADULT
        )
        with pytest.raises(ValidationError):
            StudentStyleTrack.objects.create(
                student=student,
                style=other_style,
                ladder=other_ladder,
                started_on=START,
            )


def test_another_organisation_cannot_see_the_track(student, ladders):
    _track(student, ladders["adult"])
    with allow_unscoped("test setup"):
        other = Organization.objects.create(name="Outsider", slug="outsider-track-org")
    outsider = Actor(user_id=None, person_id=None, organization_id=other.pk)
    assert StudentStyleTrack.objects.for_actor(outsider).count() == 0


def test_owning_organisation_sees_the_track(student, ladders, actor):
    _track(student, ladders["adult"])
    assert StudentStyleTrack.objects.for_actor(actor).count() == 1
