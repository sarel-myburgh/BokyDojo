"""Ranking models — TODO 1.2.1-1.2.3, 1.2.11."""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Organization
from apps.ranks.models import Rank, RankLadder, Style
from apps.ranks.seeding import create_shotokan_ladders

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def style(org):
    with allow_unscoped("test setup"):
        return Style.objects.create(organization=org, name="Shotokan Karate")


@pytest.fixture
def adult_ladder(style):
    with allow_unscoped("test setup"):
        return RankLadder.objects.create(style=style, name="Adult Kyu/Dan", applies_to="adult")


@pytest.fixture
def junior_ladder(style):
    with allow_unscoped("test setup"):
        return RankLadder.objects.create(style=style, name="Junior Mon", applies_to="junior")


# ---- Style tests ----


def test_style_creation(style):
    assert Style.objects.for_actor(Actor.system()).count() == 1


def test_style_str(style):
    assert str(style) == "Shotokan Karate"


def test_style_unique_per_org(org):
    with allow_unscoped("test setup"):
        Style.objects.create(organization=org, name="BJJ")
        with pytest.raises(IntegrityError):
            Style.objects.create(organization=org, name="BJJ")


def test_style_different_orgs_same_name():
    with allow_unscoped("test setup"):
        org1 = Organization.objects.create(name="Org 1", slug="org1")
        org2 = Organization.objects.create(name="Org 2", slug="org2")
        Style.objects.create(organization=org1, name="Karate")
        Style.objects.create(organization=org2, name="Karate")
        assert Style.objects.for_actor(Actor.system()).count() == 2


# ---- RankLadder tests ----


def test_ladder_creation(adult_ladder, junior_ladder):
    assert adult_ladder.applies_to == "adult"
    assert junior_ladder.applies_to == "junior"


def test_ladder_str(adult_ladder):
    assert "Adult" in str(adult_ladder)


def test_ladder_unique_name_per_style(style):
    with allow_unscoped("test setup"):
        RankLadder.objects.create(style=style, name="Ladder A", applies_to="adult")
        with pytest.raises(IntegrityError):
            RankLadder.objects.create(style=style, name="Ladder A", applies_to="junior")


def test_ladder_unique_applies_to_per_style(style):
    with allow_unscoped("test setup"):
        RankLadder.objects.create(style=style, name="Adult Ladder", applies_to="adult")
        with pytest.raises(IntegrityError):
            RankLadder.objects.create(style=style, name="Adult Ladder 2", applies_to="adult")


def test_ladder_tenant_org_path(style, adult_ladder):
    assert RankLadder.tenant_org_path == "style__organization_id"


# ---- Rank tests ----


@pytest.fixture
def white_rank(adult_ladder):
    with allow_unscoped("test setup"):
        return Rank.objects.create(
            ladder=adult_ladder,
            order=1,
            name="9th Kyu",
            belt_colour="white",
            stripe_count=0,
            min_months_at_previous=0,
            min_classes_since_previous=0,
            min_age=0,
        )


def test_rank_creation(white_rank):
    assert white_rank.name == "9th Kyu"
    assert white_rank.belt_colour == "white"
    assert white_rank.order == 1
    assert white_rank.stripe_count == 0


def test_rank_str(white_rank):
    s = str(white_rank)
    assert "9th Kyu" in s
    assert "Adult" in s


def test_rank_unique_order_per_ladder(adult_ladder, white_rank):
    with allow_unscoped("test setup"):
        with pytest.raises(IntegrityError):
            Rank.objects.create(
                ladder=adult_ladder,
                order=1,
                name="Dup",
                belt_colour="white",
                stripe_count=0,
                min_months_at_previous=0,
                min_classes_since_previous=0,
                min_age=0,
            )


def test_same_order_allowed_in_different_ladders(adult_ladder, junior_ladder):
    with allow_unscoped("test setup"):
        Rank.objects.create(
            ladder=adult_ladder,
            order=1,
            name="9th Kyu",
            belt_colour="white",
            stripe_count=0,
            min_months_at_previous=0,
            min_classes_since_previous=0,
            min_age=0,
        )
        Rank.objects.create(
            ladder=junior_ladder,
            order=1,
            name="White",
            belt_colour="white",
            stripe_count=0,
            min_months_at_previous=0,
            min_classes_since_previous=0,
            min_age=0,
        )
        assert Rank.objects.for_actor(Actor.system()).count() == 2


def test_rank_progression_fields(adult_ladder):
    with allow_unscoped("test setup"):
        rank = Rank.objects.create(
            ladder=adult_ladder,
            order=10,
            name="1st Dan",
            belt_colour="black",
            stripe_count=0,
            min_months_at_previous=12,
            min_classes_since_previous=100,
            min_age=16,
        )
        assert rank.min_months_at_previous == 12
        assert rank.min_classes_since_previous == 100
        assert rank.min_age == 16
        assert rank.belt_colour == "black"


def test_rank_tenant_org_path(adult_ladder):
    assert Rank.tenant_org_path == "ladder__style__organization_id"


def test_rank_ordering(adult_ladder):
    with allow_unscoped("test setup"):
        Rank.objects.create(
            ladder=adult_ladder,
            order=10,
            name="1st Dan",
            belt_colour="black",
            stripe_count=0,
            min_months_at_previous=0,
            min_classes_since_previous=0,
            min_age=0,
        )
        Rank.objects.create(
            ladder=adult_ladder,
            order=1,
            name="9th Kyu",
            belt_colour="white",
            stripe_count=0,
            min_months_at_previous=0,
            min_classes_since_previous=0,
            min_age=0,
        )
        ranks = list(Rank.objects.for_actor(Actor.system()))
        assert ranks[0].order == 1
        assert ranks[1].order == 10


# ---- Seeding tests ----


@pytest.fixture
def seed_org():
    with allow_unscoped("test setup"):
        return Organization.objects.create(name="Shotokan Dojo", slug="shotokan-dojo")


def test_shotokan_adult_ladder_rank_count(seed_org):
    with allow_unscoped("test setup"):
        adult, _ = create_shotokan_ladders(seed_org)
    ranks = list(Rank.objects.for_actor(Actor.system()).filter(ladder=adult))
    assert len(ranks) == 10
    assert ranks[0].name == "9th Kyu"
    assert ranks[-1].name == "1st Dan"


def test_shotokan_junior_ladder_rank_count(seed_org):
    with allow_unscoped("test setup"):
        _, junior = create_shotokan_ladders(seed_org)
    ranks = list(Rank.objects.for_actor(Actor.system()).filter(ladder=junior))
    assert len(ranks) == 20
    assert ranks[0].name == "10th Mon"
    assert ranks[-1].name == "4th Mon (3 stripes)"


def test_order_contiguous_and_unique(seed_org):
    with allow_unscoped("test setup"):
        adult, junior = create_shotokan_ladders(seed_org)
    for ladder in (adult, junior):
        orders = list(
            Rank.objects.for_actor(Actor.system())
            .filter(ladder=ladder)
            .values_list("order", flat=True)
        )
        assert orders == sorted(orders)
        assert len(orders) == len(set(orders))


def test_seeding_idempotent(seed_org):
    with allow_unscoped("test setup"):
        adult1, junior1 = create_shotokan_ladders(seed_org)
        adult2, junior2 = create_shotokan_ladders(seed_org)
    assert adult1.pk == adult2.pk
    assert junior1.pk == junior2.pk
    assert Rank.objects.for_actor(Actor.system()).filter(ladder=adult1).count() == 10
    assert Rank.objects.for_actor(Actor.system()).filter(ladder=junior1).count() == 20


def test_ladders_belong_to_org(seed_org):
    with allow_unscoped("test setup"):
        adult, junior = create_shotokan_ladders(seed_org)
    assert adult.style.organization_id == seed_org.pk
    assert junior.style.organization_id == seed_org.pk


def test_second_org_gets_separate_rows(seed_org):
    with allow_unscoped("test setup"):
        create_shotokan_ladders(seed_org)
        org2 = Organization.objects.create(name="Org 2", slug="org-2")
        adult2, junior2 = create_shotokan_ladders(org2)

    assert adult2.style.organization_id == org2.pk
    assert junior2.style.organization_id == org2.pk

    all_adult_ranks = Rank.objects.for_actor(Actor.system()).filter(
        ladder__applies_to="adult",
    )
    assert all_adult_ranks.count() == 20


# -- regression tests for issues found in review ------------------------------


def test_seeding_needs_no_unscoped_escape_hatch(seed_org):
    """The seeder knows its organisation, so it must scope itself.

    Requiring allow_unscoped() to seed would mean production seeding code had to
    reach for the tenant escape hatch, which is exactly what that hatch exists to
    make rare.
    """
    adult, junior = create_shotokan_ladders(seed_org)
    assert adult.style.organization_id == seed_org.pk
    assert junior.style.organization_id == seed_org.pk


def test_seeding_is_idempotent_across_locales(seed_org):
    """Rank names are data, not interface labels.

    If they were wrapped in gettext_lazy, get_or_create would look them up in
    whatever locale is active and miss its own rows under a different one,
    silently duplicating the entire ladder. Seed under two locales and assert
    the row count does not move.
    """
    from django.utils import translation

    with translation.override("en"):
        create_shotokan_ladders(seed_org)
    counts_after_english = (
        Style.objects.for_organization(seed_org.pk).count(),
        RankLadder.objects.for_organization(seed_org.pk).count(),
        Rank.objects.for_organization(seed_org.pk).count(),
    )

    for locale in ("km", "zh-hans"):
        with translation.override(locale):
            create_shotokan_ladders(seed_org)

    counts_after_others = (
        Style.objects.for_organization(seed_org.pk).count(),
        RankLadder.objects.for_organization(seed_org.pk).count(),
        Rank.objects.for_organization(seed_org.pk).count(),
    )
    assert counts_after_english == counts_after_others


def test_rank_names_are_stored_as_plain_strings(seed_org):
    """A lazy translation object stored in the database is a latent duplicate."""
    create_shotokan_ladders(seed_org)
    for rank in Rank.objects.for_organization(seed_org.pk):
        assert type(rank.name) is str


def test_junior_ladder_covers_white_through_brown(seed_org):
    _adult, junior = create_shotokan_ladders(seed_org)
    colours = list(
        Rank.objects.for_organization(seed_org.pk)
        .filter(ladder=junior)
        .order_by("order")
        .values_list("belt_colour", flat=True)
    )
    assert colours[0] == "white"
    assert colours[-1] == "brown"
    assert "green" in colours


def test_junior_orders_are_contiguous_from_one(seed_org):
    _adult, junior = create_shotokan_ladders(seed_org)
    orders = list(
        Rank.objects.for_organization(seed_org.pk)
        .filter(ladder=junior)
        .order_by("order")
        .values_list("order", flat=True)
    )
    assert orders == list(range(1, len(orders) + 1))
