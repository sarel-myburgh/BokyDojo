"""Style tracks follow enrolment — plan §4.4.

A dojo teaches particular arts. Enrol somebody there and they are, by that fact,
training those arts — so the style track is derived from the enrolment rather
than being a separate thing an admin must remember to create.

Emma joins Sen Sok, which teaches Goju Ryu, and Urban Village, which teaches
boxing. She gets two tracks: a Goju Ryu one carrying its own grade history, and a
boxing one carrying none, because boxing is marked unranked. Neither was typed in
by anybody.

⚠ **Tracks are created, never removed.** Ending an enrolment does not end the
track: a rank is not un-earned by leaving a dojo, and somebody who trains the
same art at two dojos would lose their grade history the moment one enrolment
closed. Ending a track is a deliberate act (`StudentStyleTrack.Status.ENDED`),
and it stays that way.

⚠ **A track with no ladder is a real, honest state.** Unranked styles never get
one. Ranked styles get one only when it can be *known* — a style with a single
ladder, or an age that decides between the junior and adult ladders. Guessing
would put an eight-year-old on the adult ladder and nobody would notice until a
grading.
"""

from __future__ import annotations

import datetime

from django.db import transaction

from apps.core.scoping import Actor
from apps.core.setting_registry import JUNIOR_LADDER_MAX_AGE
from apps.core.setting_resolver import ScopeChain, resolve

from .models import RankLadder, StudentStyleTrack, Style


def junior_age_limit(organization_id) -> int:
    return int(resolve(JUNIOR_LADDER_MAX_AGE.key, ScopeChain(organization_id=organization_id)))


def choose_ladder(style: Style, *, student, organization_id) -> RankLadder | None:
    """The ladder this student belongs on for this style, or None if unknowable.

    ⚠ Returns None rather than picking. A style with both an adult and a junior
    ladder and a student whose birthday nobody recorded has no right answer, and
    the wrong one is invisible until the day it refuses or permits a grading.
    """
    if not style.is_ranked:
        return None

    ladders = list(RankLadder.objects.for_organization(organization_id).filter(style=style))
    if not ladders:
        return None
    if len(ladders) == 1:
        return ladders[0]

    age = getattr(student, "age", None)
    if age is None:
        return None

    wanted = (
        RankLadder.AppliesTo.JUNIOR
        if age < junior_age_limit(organization_id)
        else RankLadder.AppliesTo.ADULT
    )
    return next((ladder for ladder in ladders if ladder.applies_to == wanted), None)


@transaction.atomic
def ensure_track(
    *,
    student,
    style: Style,
    actor: Actor,
    organization_id,
    started_on: datetime.date | None = None,
) -> tuple[StudentStyleTrack, bool]:
    """Give this student a track for this style if they have not got one.

    Returns ``(track, created)``. Idempotent, and it never rewrites an existing
    track — somebody may have moved a student onto a different ladder by hand,
    and re-enrolling them must not undo that.
    """
    existing = (
        StudentStyleTrack.objects.for_organization(organization_id)
        .filter(student=student, style=style)
        .first()
    )
    if existing is not None:
        # ⚠ One exception: fill in a ladder that could not be chosen before. A
        # student whose birthday is added later should stop being stuck without
        # one, and that is a gap being closed rather than a decision reversed.
        if existing.ladder_id is None and style.is_ranked:
            ladder = choose_ladder(style, student=student, organization_id=organization_id)
            if ladder is not None:
                existing.ladder = ladder
                existing.save(update_fields=["ladder", "updated_at"])
        return existing, False

    track = StudentStyleTrack(
        student=student,
        style=style,
        ladder=choose_ladder(style, student=student, organization_id=organization_id),
        started_on=started_on or datetime.date.today(),
    )
    track.save()
    return track, True


def sync_tracks_for_enrolment(enrollment, *, actor: Actor) -> list[StudentStyleTrack]:
    """Give the student a track for everything their new dojo teaches.

    Returns the tracks created. A dojo with no styles set yet creates nothing,
    which is why the organisation settings screen exists.
    """
    dojo = enrollment.dojo
    organization_id = dojo.organization_id
    created: list[StudentStyleTrack] = []

    # ⚠ Not ``dojo.styles.all()``. A reverse/M2M manager on a tenant-scoped model
    # hands back a scoped queryset, which refuses to evaluate without an actor —
    # the same trap as ``session.attendance_records``. Asking Style directly both
    # works and applies the tenant filter, which also makes it impossible for a
    # mis-set M2M row to hand a student another organisation's ladder (M2M is
    # beyond the reach of ``same_organization_fields``).
    styles = Style.objects.for_organization(organization_id).filter(dojos=dojo)

    for style in styles:
        track, was_created = ensure_track(
            student=enrollment.student,
            style=style,
            actor=actor,
            organization_id=organization_id,
            started_on=enrollment.started_on,
        )
        if was_created:
            created.append(track)
    return created
