"""Seeding the standard Shotokan ladders — TODO 1.2.11.

Not a Django fixture: a fixture hardcodes the organisation foreign key, and in a
multi-tenant system every organisation teaching Shotokan needs its own rows.

⚠ Rank and style names are stored **data**, not interface labels, so they are
plain strings. Wrapping them in ``gettext_lazy`` would freeze whichever locale
happened to be active when an organisation was seeded, and — worse — would make
``get_or_create`` miss its own rows when called under a different locale,
silently duplicating the whole ladder. Translating rank names, if ever wanted,
is a display concern.
"""

from __future__ import annotations

from apps.ranks.models import Rank, RankLadder, Style

STYLE_NAME = "Shotokan Karate"
ADULT_LADDER_NAME = "Shotokan Adult Kyu/Dan"
JUNIOR_LADDER_NAME = "Shotokan Junior Mon"

#: (name, belt colour, stripes, min months at previous, min classes, min age)
ADULT_RANKS = [
    ("9th Kyu", "white", 0, 0, 0, 0),
    ("8th Kyu", "white", 1, 3, 40, 0),
    ("7th Kyu", "yellow", 0, 3, 40, 0),
    ("6th Kyu", "orange", 0, 3, 40, 0),
    ("5th Kyu", "green", 0, 4, 60, 0),
    ("4th Kyu", "blue", 0, 6, 80, 0),
    ("3rd Kyu", "purple", 0, 6, 80, 0),
    ("2nd Kyu", "brown", 0, 6, 80, 0),
    ("1st Kyu", "brown", 2, 9, 120, 14),
    ("1st Dan", "black", 0, 12, 200, 16),
]

#: Junior grades run down from 10th mon, each colour earning stripes before the
#: next colour. (mon number, colour, months between grades, classes between)
JUNIOR_GRADES = [
    (10, "white", 0, 0),
    (9, "yellow", 2, 20),
    (8, "orange", 2, 20),
    (7, "green", 3, 30),
    (6, "blue", 3, 30),
    (5, "purple", 4, 40),
    (4, "brown", 4, 40),
]


def _junior_ranks() -> list[tuple]:
    """Expand the junior grades into (name, colour, stripes, …) rows.

    White is a single entry — a beginner has nothing to stripe yet. Every other
    colour is awarded plain, then with one stripe, then two. Brown carries a
    third stripe as the last step before crossing to the adult ladder.
    """
    rows: list[tuple] = []
    for mon, colour, months, classes in JUNIOR_GRADES:
        stripe_counts = [0] if colour == "white" else [0, 1, 2]
        if colour == "brown":
            stripe_counts = [0, 1, 2, 3]
        for stripes in stripe_counts:
            if stripes == 0:
                name = f"{mon}th Mon"
            else:
                plural = "s" if stripes > 1 else ""
                name = f"{mon}th Mon ({stripes} stripe{plural})"
            rows.append((name, colour, stripes, months, classes, 0))
    return rows


def create_shotokan_ladders(organization) -> tuple[RankLadder, RankLadder]:
    """Create the standard Shotokan adult and junior ladders for one organisation.

    Idempotent: safe to call twice: returns the existing ladders unchanged.
    Returns ``(adult_ladder, junior_ladder)``.
    """
    org_id = organization.pk

    style, _created = Style.objects.for_organization(org_id).get_or_create(
        organization=organization,
        name=STYLE_NAME,
    )

    adult_ladder, _created = RankLadder.objects.for_organization(org_id).get_or_create(
        style=style,
        name=ADULT_LADDER_NAME,
        defaults={"applies_to": RankLadder.AppliesTo.ADULT},
    )
    junior_ladder, _created = RankLadder.objects.for_organization(org_id).get_or_create(
        style=style,
        name=JUNIOR_LADDER_NAME,
        defaults={"applies_to": RankLadder.AppliesTo.JUNIOR},
    )

    for ladder, rows in ((adult_ladder, ADULT_RANKS), (junior_ladder, _junior_ranks())):
        for order, (name, colour, stripes, months, classes, min_age) in enumerate(rows, start=1):
            Rank.objects.for_organization(org_id).get_or_create(
                ladder=ladder,
                order=order,
                defaults={
                    "name": name,
                    "belt_colour": colour,
                    "stripe_count": stripes,
                    "min_months_at_previous": months,
                    "min_classes_since_previous": classes,
                    "min_age": min_age,
                },
            )

    return adult_ladder, junior_ladder
