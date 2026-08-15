"""Audited writes for personal reusable student-filter segments."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from apps.core import audit
from apps.core.models import AuditLog
from apps.core.scoping import Actor

from .models import StudentSegment
from .permissions import ROLE_ACTIONS, Action, PermissionDenied
from .student_filters import validate_saved_student_filters

MAX_SEGMENTS_PER_PERSON = 50


def _require_access(actor: Actor) -> None:
    if actor.organization_id is None or actor.person_id is None:
        raise PermissionDenied(Action.PERSON_VIEW, actor)
    if not any(
        Action.PERSON_VIEW in ROLE_ACTIONS.get(role, set()) for role, _scope, _dojo in actor.roles
    ):
        raise PermissionDenied(Action.PERSON_VIEW, actor)


def create_student_segment(*, name: str, filters: dict[str, str], actor: Actor) -> StudentSegment:
    _require_access(actor)
    name = name.strip()
    if not name:
        raise ValidationError({"name": _("Enter a segment name.")})
    validate_saved_student_filters(filters)
    owned = StudentSegment.objects.for_organization(actor.organization_id).filter(
        owner_id=actor.person_id
    )
    if owned.count() >= MAX_SEGMENTS_PER_PERSON:
        raise ValidationError({"name": _("You already have the maximum of 50 saved segments.")})
    if owned.filter(name__iexact=name).exists():
        raise ValidationError({"name": _("You already have a segment with this name.")})

    segment = StudentSegment(
        organization_id=actor.organization_id,
        owner_id=actor.person_id,
        name=name,
        filters=filters,
        created_by_id=actor.person_id,
    )
    segment.full_clean(validate_unique=False, validate_constraints=False)
    try:
        with transaction.atomic():
            segment.save()
            audit.record(
                AuditLog.Action.CREATE,
                actor=actor,
                subject=segment,
                after={"name": segment.name, "filter_keys": sorted(filters)},
                note="student segment created",
                strict=True,
            )
    except IntegrityError as exc:
        raise ValidationError({"name": _("You already have a segment with this name.")}) from exc
    return segment


def delete_student_segment(*, segment: StudentSegment, actor: Actor) -> None:
    _require_access(actor)
    if segment.organization_id != actor.organization_id or segment.owner_id != actor.person_id:
        raise PermissionDenied(Action.PERSON_VIEW, actor)
    with transaction.atomic():
        locked = (
            StudentSegment.objects.for_organization(actor.organization_id)
            .select_for_update()
            .get(
                pk=segment.pk,
                owner_id=actor.person_id,
            )
        )
        safe_before = {"name": locked.name, "filter_keys": sorted(locked.filters)}
        segment_id = str(locked.pk)
        locked.delete()
        audit.record(
            AuditLog.Action.DELETE,
            actor=actor,
            subject_type=StudentSegment._meta.label,
            subject_id=segment_id,
            organization_id=actor.organization_id,
            before=safe_before,
            note="student segment deleted",
            strict=True,
        )
