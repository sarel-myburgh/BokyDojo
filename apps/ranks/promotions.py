"""Transactional, audited manual rank promotion."""

from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.scoping import Actor
from apps.identity.models import GovernanceModel, StudentProfile
from apps.identity.permissions import Action, require
from apps.staffing.models import InstructorProfile

from .models import Rank, RankAward, StudentStyleTrack

BULK_PROMOTION_LIMIT = 30


def examiner_grading_ceiling(*, actor: Actor, organization_id):
    if actor.person_id is None:
        return None
    profile = (
        InstructorProfile.objects.for_organization(organization_id)
        .filter(person_id=actor.person_id)
        .select_related("max_grading_rank", "max_grading_rank__ladder")
        .first()
    )
    return profile.max_grading_rank if profile else None


def bulk_promotion_rank_choices(actor: Actor):
    """All organisation ranks the actor's optional examiner ceiling permits."""
    ranks = Rank.objects.for_organization(actor.organization_id).select_related(
        "ladder", "ladder__style"
    )
    ceiling = examiner_grading_ceiling(actor=actor, organization_id=actor.organization_id)
    if ceiling is not None:
        ranks = ranks.filter(ladder_id=ceiling.ladder_id, order__lte=ceiling.order)
    return ranks.order_by("ladder__style__name", "ladder__name", "order")


def promotion_rank_choices(track: StudentStyleTrack, *, actor: Actor | None = None):
    """Higher ranks on this ladder, bounded by the examiner's configured ceiling."""
    ranks = Rank.objects.for_organization(track.student.organization_id).filter(
        ladder_id=track.ladder_id
    )
    if track.current_rank_id:
        ranks = ranks.filter(order__gt=track.current_rank.order)
    if actor is not None:
        ceiling = examiner_grading_ceiling(
            actor=actor, organization_id=track.student.organization_id
        )
        if ceiling is not None:
            if ceiling.ladder_id != track.ladder_id:
                return ranks.none()
            ranks = ranks.filter(order__lte=ceiling.order)
    return ranks.order_by("order")


@transaction.atomic
def promote_student(
    *,
    profile: StudentProfile,
    track: StudentStyleTrack,
    rank: Rank,
    awarded_on: date,
    actor: Actor,
    certificate_number: str = "",
    notes: str = "",
) -> RankAward:
    """Record one internal promotion and update the denormalised current rank."""
    governance = profile.person.organization.governance_model or GovernanceModel.CENTRAL
    require(actor, Action.RANK_AWARD, profile, governance_model=governance)
    if profile.person_id != track.student_id:
        raise ValidationError({"track": _("This track belongs to a different student.")})
    if awarded_on > timezone.localdate():
        raise ValidationError({"awarded_on": _("A promotion date cannot be in the future.")})

    locked = (
        StudentStyleTrack.objects.for_organization(profile.organization_id)
        .select_for_update()
        .select_related("student", "style", "ladder", "current_rank")
        .get(pk=track.pk, student=profile.person)
    )
    if not locked.is_active:
        raise ValidationError({"track": _("Only an active style track can be promoted.")})
    if awarded_on < locked.started_on:
        raise ValidationError(
            {"awarded_on": _("The promotion date cannot be before the track started.")}
        )

    locked.recompute_current_rank()
    try:
        rank = (
            Rank.objects.for_organization(profile.organization_id)
            .select_related("ladder", "ladder__style")
            .get(pk=rank.pk, ladder=locked.ladder)
        )
    except Rank.DoesNotExist as exc:
        raise ValidationError({"rank": _("Choose a rank from this track's ladder.")}) from exc
    if locked.current_rank_id and rank.order <= locked.current_rank.order:
        raise ValidationError({"rank": _("Choose a rank above the student's current rank.")})
    ceiling = examiner_grading_ceiling(actor=actor, organization_id=profile.organization_id)
    if ceiling is not None and (ceiling.ladder_id != rank.ladder_id or rank.order > ceiling.order):
        raise ValidationError({"rank": _("This rank is above your grading ceiling.")})

    latest_date = (
        RankAward.objects.for_organization(profile.organization_id)
        .filter(track=locked, revoked_at__isnull=True)
        .order_by("-awarded_on")
        .values_list("awarded_on", flat=True)
        .first()
    )
    if latest_date and awarded_on < latest_date:
        raise ValidationError(
            {"awarded_on": _("The promotion date cannot predate the latest active award.")}
        )

    award = RankAward(
        track=locked,
        rank=rank,
        awarded_on=awarded_on,
        awarded_by_id=actor.person_id,
        recognition=RankAward.Recognition.INTERNAL,
        certificate_number=certificate_number.strip(),
        notes=notes.strip(),
        created_by_id=actor.person_id,
    )
    award.full_clean(validate_unique=False, validate_constraints=False)
    award.save()
    audit.record(
        "rank_promote",
        actor=actor,
        subject=award,
        after={
            "student_id": str(profile.person_id),
            "track_id": str(locked.pk),
            "rank_id": str(rank.pk),
            "awarded_on": awarded_on.isoformat(),
        },
        note="manual internal promotion",
        strict=True,
    )
    return award


@transaction.atomic
def bulk_promote_students(
    *,
    profiles,
    rank: Rank,
    awarded_on: date,
    actor: Actor,
    notes: str = "",
) -> list[RankAward]:
    """Promote a bounded student set atomically through the canonical service."""
    selected = sorted(list(profiles), key=lambda profile: str(profile.pk))
    if not selected:
        raise ValidationError({"student_ids": _("Select at least one student.")})
    if len(selected) > BULK_PROMOTION_LIMIT:
        raise ValidationError(
            {
                "student_ids": _("Select at most %(limit)s students at once.")
                % {"limit": BULK_PROMOTION_LIMIT}
            }
        )

    organization_id = selected[0].organization_id
    try:
        rank = (
            Rank.objects.for_organization(organization_id)
            .select_related("ladder", "ladder__style")
            .get(pk=rank.pk)
        )
    except Rank.DoesNotExist as exc:
        raise ValidationError({"rank": _("Choose a rank from this organisation.")}) from exc

    tracks = []
    for profile in selected:
        governance = profile.person.organization.governance_model or GovernanceModel.CENTRAL
        require(actor, Action.RANK_AWARD, profile, governance_model=governance)
        if profile.organization_id != organization_id:
            raise ValidationError(
                {"student_ids": _("All students must belong to one organisation.")}
            )
        try:
            track = (
                StudentStyleTrack.objects.for_organization(organization_id)
                .select_related("student", "style", "ladder", "current_rank")
                .get(
                    student=profile.person,
                    ladder=rank.ladder,
                    status=StudentStyleTrack.Status.ACTIVE,
                )
            )
        except StudentStyleTrack.DoesNotExist as exc:
            raise ValidationError(
                {
                    "student_ids": _(
                        "%(student)s has no active track on the selected rank ladder. "
                        "No promotions were recorded."
                    )
                    % {"student": profile.person.full_name}
                }
            ) from exc
        tracks.append(track)

    return [
        promote_student(
            profile=profile,
            track=track,
            rank=rank,
            awarded_on=awarded_on,
            actor=actor,
            notes=notes,
        )
        for profile, track in zip(selected, tracks, strict=True)
    ]
