"""A dojo's timetable, built from its settings page — plan §1.4."""

from __future__ import annotations

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.core import audit
from apps.identity.models import Dojo, GovernanceModel
from apps.identity.permissions import Action, require

from .materialise import materialise_sessions, materialise_template
from .models import ClassSession, ClassTemplate
from .schedule_forms import ClassTemplateForm, days_from_rrule

#: How far ahead a newly saved class is written into the calendar. Matches the
#: scheduled job's horizon so the two do not disagree about what exists.
HORIZON_DAYS = 60


def _governance(dojo: Dojo) -> str:
    return dojo.organization.governance_model or GovernanceModel.CENTRAL


def _dojo_for_edit(request, dojo_id) -> Dojo:
    dojo = get_object_or_404(
        Dojo.objects.for_actor(request.actor).select_related("organization"), pk=dojo_id
    )
    require(request.actor, Action.DOJO_EDIT, dojo, governance_model=_governance(dojo))
    return dojo


def schedule_rows(*, dojo: Dojo, actor) -> list[dict]:
    """Every class on this dojo's timetable, with its days spelled out."""
    from .schedule_forms import WEEKDAYS

    labels = dict(WEEKDAYS)
    templates = (
        ClassTemplate.objects.for_actor(actor)
        .filter(dojo=dojo)
        .select_related("style")
        .order_by("start_time", "name")
    )
    today = timezone.localdate()
    rows = []
    for template in templates:
        codes = days_from_rrule(template.rrule)
        rows.append(
            {
                "template": template,
                "days": [labels[code] for code in codes],
                # ⚠ Shown because a template with an end date in the past stops
                # producing classes silently. "Why has Tuesday vanished" is
                # otherwise unanswerable from this screen.
                "is_finished": bool(template.active_to and template.active_to < today),
                "is_pending": template.active_from > today,
            }
        )
    return rows


@login_required
@require_http_methods(["GET", "POST"])
def class_template_create_view(request, dojo_id):
    dojo = _dojo_for_edit(request, dojo_id)
    form = ClassTemplateForm(request.POST or None, actor=request.actor, dojo=dojo)

    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            template = form.save()
            audit.record_change("create", template, actor=request.actor)
            _fill_calendar(template)
        messages.success(request, _("Class added to the timetable."))
        return redirect("dojo-edit", dojo_id=dojo.pk)

    return render(
        request,
        "scheduling/class_template_form.html",
        {"form": form, "dojo": dojo, "is_new": True},
    )


@login_required
@require_http_methods(["GET", "POST"])
def class_template_edit_view(request, dojo_id, template_id):
    dojo = _dojo_for_edit(request, dojo_id)
    template = get_object_or_404(
        ClassTemplate.objects.for_actor(request.actor).filter(dojo=dojo), pk=template_id
    )
    form = ClassTemplateForm(
        request.POST or None, instance=template, actor=request.actor, dojo=dojo
    )

    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            template = form.save()
            audit.record_change("update", template, actor=request.actor)
            _drop_untouched_future_sessions(template, actor=request.actor)
            _fill_calendar(template)
        messages.success(request, _("Timetable updated."))
        return redirect("dojo-edit", dojo_id=dojo.pk)

    return render(
        request,
        "scheduling/class_template_form.html",
        {"form": form, "dojo": dojo, "template": template, "is_new": False},
    )


@login_required
@require_POST
def class_template_end_view(request, dojo_id, template_id):
    """Stop a class running, without deleting what already happened.

    ⚠ Ended, not deleted. Sessions in the past carry attendance, and those
    records are the answer to "was this child in class that evening" — a
    question that gets asked in a safeguarding review. Deleting the template
    would cascade them away.
    """
    dojo = _dojo_for_edit(request, dojo_id)
    template = get_object_or_404(
        ClassTemplate.objects.for_actor(request.actor).filter(dojo=dojo), pk=template_id
    )

    with transaction.atomic():
        template.active_to = timezone.localdate()
        template.save(update_fields=["active_to", "updated_at"])
        audit.record_change("update", template, actor=request.actor)
        _drop_untouched_future_sessions(template, actor=request.actor)

    messages.success(request, _("Class removed from the timetable from today."))
    return redirect("dojo-edit", dojo_id=dojo.pk)


def _fill_calendar(template: ClassTemplate) -> None:
    today = timezone.localdate()
    materialise_template(
        template,
        from_date=today,
        to_date=today + datetime.timedelta(days=HORIZON_DAYS),
    )


def _drop_untouched_future_sessions(template: ClassTemplate, *, actor) -> None:
    """Clear future sessions so the new pattern can be written in their place.

    ⚠ Only future ones, only from this template, and only those nobody has
    touched. A session with attendance marked against it, or one somebody moved
    or cancelled by hand, is a decision somebody made — rewriting the timetable
    is not licence to erase it. Those are left where they are, and the calendar
    will show them beside the new pattern until they pass.
    """
    from apps.attendance.models import AttendanceRecord

    now = timezone.now()
    candidates = ClassSession.objects.for_actor(actor).filter(
        template=template,
        starts_at__gt=now,
        status=ClassSession.Status.SCHEDULED,
        moved_from__isnull=True,
    )
    marked = set(
        AttendanceRecord.objects.for_actor(actor)
        .filter(session__in=candidates)
        .values_list("session_id", flat=True)
    )
    candidates.exclude(pk__in=marked).delete()


@login_required
@require_POST
def dojo_schedule_refresh_view(request, dojo_id):
    """Top the calendar up by hand, for when somebody wants to see it now."""
    dojo = _dojo_for_edit(request, dojo_id)
    result = materialise_sessions(actor=request.actor, dojo=dojo, horizon_days=HORIZON_DAYS)
    messages.success(
        request,
        _("Calendar filled in — %(count)s class(es) added.") % {"count": result.created},
    )
    return redirect("dojo-edit", dojo_id=dojo.pk)
