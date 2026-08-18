"""Tenant-scoped student directory and rich filtering — TODO 1.1.9/1.1.10."""

from __future__ import annotations

import datetime
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import BooleanField, DateTimeField, OuterRef, Prefetch, Q, Subquery, Value
from django.http import Http404, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.attendance.models import AttendanceRecord
from apps.core import audit, safeguarding
from apps.core.documents import may_read
from apps.core.models import Document
from apps.core.note_authoring import create_note, writable_visibilities
from apps.core.notes import Note
from apps.ranks.models import RankAward, StudentStyleTrack

from .consent import current_consent
from .forms import (
    StudentBulkStatusForm,
    StudentListFilterForm,
    StudentNoteForm,
    StudentSegmentCreateForm,
    StudentStatusTransitionForm,
)
from .lifecycle import bulk_transition_student_status, transition_student_status
from .medical import view_do_not_spar
from .models import (
    ConsentPolicy,
    ConsentRecord,
    EmergencyContact,
    Enrollment,
    GovernanceModel,
    GuardianLink,
    Organization,
    StudentProfile,
    StudentSegment,
)
from .permissions import ROLE_ACTIONS, Action, PermissionDenied, can
from .photos import active_photo_policy, current_photo_consent, current_student_photo
from .segments import create_student_segment, delete_student_segment
from .student_filters import STUDENT_FILTER_KEYS
from .visibility import is_org_scoped_only


def _holds_anywhere(actor, action: str) -> bool:
    return any(action in ROLE_ACTIONS.get(role, set()) for role, _scope, _dojo in actor.roles)


def _allow_private_person_fields(actor) -> bool:
    organization = Organization.objects.get(pk=actor.organization_id)
    return not (
        organization.governance_model == GovernanceModel.FEDERATED and is_org_scoped_only(actor)
    )


def _owned_segment(actor, raw_id: str) -> StudentSegment:
    try:
        segment_id = uuid.UUID(raw_id)
    except (TypeError, ValueError) as exc:
        raise Http404 from exc
    return get_object_or_404(
        StudentSegment.objects.for_organization(actor.organization_id),
        pk=segment_id,
        owner_id=actor.person_id,
    )


def _effective_filter_data(request, actor):
    raw_segment = request.GET.get("segment")
    if not raw_segment:
        return request.GET.copy(), None
    selected = _owned_segment(actor, raw_segment)
    data = QueryDict("", mutable=True)
    for key, value in selected.filters.items():
        data[key] = value
    for key in STUDENT_FILTER_KEYS:
        if key in request.GET:
            data.setlist(key, request.GET.getlist(key))
    return data, selected


def _validation_message(error: ValidationError) -> str:
    if hasattr(error, "message_dict"):
        return " ".join(str(item) for values in error.message_dict.values() for item in values)
    return " ".join(str(item) for item in error.messages)


def _years_ago(today: datetime.date, years: int) -> datetime.date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def _filtered_profiles(actor, form, waiver_policy, *, allow_private_person_fields):
    attendance = (
        AttendanceRecord.objects.for_actor(actor)
        .filter(student_id=OuterRef("person_id"), status__in=AttendanceRecord.ATTENDED_STATUSES)
        .order_by("-session__starts_at", "-created_at")
    )
    profiles = StudentProfile.objects.for_actor(actor).select_related(
        "person", "person__organization", "home_dojo"
    )
    profiles = profiles.annotate(
        last_attendance_at=Subquery(
            attendance.values("session__starts_at")[:1], output_field=DateTimeField()
        )
    )

    if waiver_policy is None:
        profiles = profiles.annotate(waiver_granted=Value(False, output_field=BooleanField()))
    else:
        decisions = (
            ConsentRecord.objects.for_organization(actor.organization_id)
            .filter(person_id=OuterRef("person_id"), policy=waiver_policy)
            .order_by("-granted_at", "-created_at")
        )
        profiles = profiles.annotate(
            waiver_granted=Subquery(decisions.values("granted")[:1], output_field=BooleanField())
        )

    if not form.is_valid():
        return profiles.none()
    values = form.cleaned_data
    query = values.get("q", "").strip()
    if query:
        search = (
            Q(person__given_name__icontains=query)
            | Q(person__family_name__icontains=query)
            | Q(person__preferred_name__icontains=query)
        )
        if allow_private_person_fields:
            search |= Q(person__email__icontains=query) | Q(person__phone__icontains=query)
        profiles = profiles.filter(search)
    if values.get("dojo"):
        profiles = profiles.filter(home_dojo=values["dojo"])
    if values.get("status"):
        profiles = profiles.filter(status=values["status"])
    if values.get("rank"):
        profiles = profiles.filter(
            person__style_tracks__status=StudentStyleTrack.Status.ACTIVE,
            person__style_tracks__current_rank=values["rank"],
        ).distinct()

    today = timezone.localdate()
    if allow_private_person_fields and values.get("age_min") is not None:
        profiles = profiles.filter(person__date_of_birth__lte=_years_ago(today, values["age_min"]))
    if allow_private_person_fields and values.get("age_max") is not None:
        profiles = profiles.filter(
            person__date_of_birth__gt=_years_ago(today, values["age_max"] + 1)
        )
    if values.get("attendance_gap"):
        cutoff = timezone.now() - datetime.timedelta(days=int(values["attendance_gap"]))
        profiles = profiles.filter(
            Q(last_attendance_at__lt=cutoff) | Q(last_attendance_at__isnull=True)
        )
    if values.get("unsigned_waiver"):
        profiles = profiles.filter(Q(waiver_granted=False) | Q(waiver_granted__isnull=True))
    if values.get("expired_licence"):
        profiles = profiles.filter(licence_expires_on__lt=today)
    return profiles


@login_required
@never_cache
@require_GET
def student_list_view(request):
    actor = request.actor
    if not _holds_anywhere(actor, Action.PERSON_VIEW):
        raise PermissionDenied(Action.PERSON_VIEW, actor)

    allow_private_person_fields = _allow_private_person_fields(actor)
    waiver_policy = (
        ConsentPolicy.objects.for_organization(actor.organization_id)
        .filter(consent_type=ConsentRecord.Type.WAIVER, is_active=True)
        .first()
    )
    medical_policy_exists = (
        ConsentPolicy.objects.for_organization(actor.organization_id)
        .filter(consent_type=ConsentRecord.Type.MEDICAL, is_active=True)
        .exists()
    )
    filter_data, selected_segment = _effective_filter_data(request, actor)
    form = StudentListFilterForm(
        filter_data,
        actor=actor,
        allow_private_person_fields=allow_private_person_fields,
    )
    profiles = _filtered_profiles(
        actor,
        form,
        waiver_policy,
        allow_private_person_fields=allow_private_person_fields,
    )
    active_tracks = (
        StudentStyleTrack.objects.for_organization(actor.organization_id)
        .filter(status=StudentStyleTrack.Status.ACTIVE)
        .select_related("style", "current_rank", "ladder")
        .order_by("style__name")
    )
    profiles = profiles.prefetch_related(
        Prefetch("person__style_tracks", queryset=active_tracks, to_attr="active_style_tracks")
    ).order_by("person__family_name", "person__given_name")

    page = Paginator(profiles, 50).get_page(request.GET.get("page"))
    for profile in page.object_list:
        governance = profile.person.organization.governance_model or GovernanceModel.CENTRAL
        profile.may_capture_waiver = waiver_policy is not None and can(
            actor, Action.PERSON_EDIT, profile, governance_model=governance
        )
        profile.may_capture_medical = medical_policy_exists and can(
            actor, Action.MEDICAL_EDIT, profile, governance_model=governance
        )
        profile.may_change_status = can(
            actor, Action.PERSON_EDIT, profile, governance_model=governance
        )

    query_params = request.GET.copy()
    query_params.pop("page", None)
    saved_filters = form.canonical_filters() if form.is_valid() else {}
    return render(
        request,
        "students/list.html",
        {
            "form": form,
            "page": page,
            "query_string": query_params.urlencode(),
            "waiver_policy": waiver_policy,
            "today": timezone.localdate(),
            "show_private_person_fields": allow_private_person_fields,
            "segments": StudentSegment.objects.for_organization(actor.organization_id).filter(
                owner_id=actor.person_id
            ),
            "selected_segment": selected_segment,
            "segment_form": StudentSegmentCreateForm(
                initial={"filter_query": filter_data.urlencode()}
            ),
            "can_save_segment": bool(saved_filters),
            "may_bulk_status": any(profile.may_change_status for profile in page.object_list),
            "may_bulk_promote": _holds_anywhere(actor, Action.RANK_AWARD),
            # ⚠ Menu visibility is not a control (SEC §2.2) — student_create_view
            # checks the same action itself. This only stops offering a button
            # that would refuse.
            "may_add_student": _holds_anywhere(actor, Action.PERSON_CREATE),
            "bulk_status_form": StudentBulkStatusForm(actor=actor),
        },
    )


@login_required
@require_POST
def student_segment_create_view(request):
    actor = request.actor
    if not _holds_anywhere(actor, Action.PERSON_VIEW):
        raise PermissionDenied(Action.PERSON_VIEW, actor)
    form = StudentSegmentCreateForm(request.POST)
    if form.is_valid():
        filter_data = QueryDict(form.cleaned_data["filter_query"])
        filter_form = StudentListFilterForm(
            filter_data,
            actor=actor,
            allow_private_person_fields=_allow_private_person_fields(actor),
        )
        if filter_form.is_valid() and filter_form.canonical_filters():
            try:
                create_student_segment(
                    name=form.cleaned_data["name"],
                    filters=filter_form.canonical_filters(),
                    actor=actor,
                )
            except ValidationError as exc:
                messages.error(request, _validation_message(exc))
            else:
                messages.success(request, _("Student segment saved."))
        else:
            messages.error(request, _("Choose at least one valid filter before saving."))
    else:
        messages.error(request, _("Enter a valid segment name and filters."))
    return redirect("student-list")


@login_required
@require_POST
def student_segment_delete_view(request, segment_id):
    actor = request.actor
    if not _holds_anywhere(actor, Action.PERSON_VIEW):
        raise PermissionDenied(Action.PERSON_VIEW, actor)
    segment = _owned_segment(actor, str(segment_id))
    delete_student_segment(segment=segment, actor=actor)
    messages.success(request, _("Student segment deleted."))
    return redirect("student-list")


@login_required
@require_POST
def student_bulk_status_view(request):
    actor = request.actor
    if not _holds_anywhere(actor, Action.PERSON_EDIT):
        raise PermissionDenied(Action.PERSON_EDIT, actor)

    form = StudentBulkStatusForm(request.POST, actor=actor)
    if form.is_valid():
        try:
            updated = bulk_transition_student_status(
                profiles=form.cleaned_data["student_ids"],
                to_status=form.target_status,
                hold_reason=form.cleaned_data["hold_reason"],
                actor=actor,
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(
                request,
                _("%(count)s student statuses updated.") % {"count": len(updated)},
            )
    else:
        messages.error(request, _validation_message(ValidationError(form.errors)))
    return redirect("student-list")


@login_required
@require_POST
def student_status_transition_view(request, person_id):
    actor = request.actor
    profile = get_object_or_404(
        StudentProfile.objects.for_actor(actor).select_related(
            "person", "person__organization", "home_dojo"
        ),
        person_id=person_id,
    )
    governance = profile.person.organization.governance_model or GovernanceModel.CENTRAL
    if not can(actor, Action.PERSON_EDIT, profile, governance_model=governance):
        raise PermissionDenied(Action.PERSON_EDIT, actor)

    form = StudentStatusTransitionForm(request.POST, current_status=profile.status)
    if form.is_valid():
        try:
            updated = transition_student_status(
                profile=profile,
                to_status=form.cleaned_data["to_status"],
                hold_reason=form.cleaned_data["hold_reason"],
                actor=actor,
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(
                request,
                _("Student status changed to %(status)s.")
                % {"status": updated.get_status_display()},
            )
    else:
        messages.error(request, _validation_message(ValidationError(form.errors)))
    return redirect("student-detail", person_id=person_id)


@login_required
@require_POST
def student_note_create_view(request, person_id):
    """Write a note about one student — TODO 1.8.x.

    The permission and the visibility level are both decided by ``create_note``.
    This view's only jobs are finding the tenant-scoped student, handing the form
    the actor so it offers the right levels, and reporting the outcome.
    """
    actor = request.actor
    profile = get_object_or_404(
        StudentProfile.objects.for_actor(actor).select_related(
            "person", "person__organization", "home_dojo"
        ),
        person_id=person_id,
    )

    form = StudentNoteForm(request.POST, actor=actor, subject=profile)
    if form.is_valid():
        try:
            note = create_note(
                subject=profile,
                body=form.cleaned_data["body"],
                visibility=form.cleaned_data["visibility"],
                pinned=form.cleaned_data["pinned"],
                actor=actor,
            )
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(
                request,
                _("Note saved: %(visibility)s.") % {"visibility": note.get_visibility_display()},
            )
    else:
        messages.error(request, _validation_message(ValidationError(form.errors)))
    return redirect(f"{reverse('student-detail', args=[person_id])}?tab=notes")


DETAIL_TABS = ("attendance", "rank", "notes", "billing", "documents", "family")


def _visible_student_notes(actor, profile, governance):
    """This student's notes, filtered to what this actor may read.

    The visibility rules themselves live on the queryset (TODO 1.8.2) rather
    than here — they used to be inline, which meant the next screen to read a
    note would have had to reimplement them or quietly go without.
    """
    return (
        Note.objects.for_actor(actor)
        .filter(subject_type=Note.SubjectType.STUDENT, subject_id=profile.person_id)
        .visible_to(actor, subject=profile, governance_model=governance)
        .select_related("author")
    )


@login_required
@never_cache
@require_GET
def student_detail_view(request, person_id):
    actor = request.actor
    profile = get_object_or_404(
        StudentProfile.objects.for_actor(actor).select_related(
            "person", "person__organization", "home_dojo"
        ),
        person_id=person_id,
    )
    governance = profile.person.organization.governance_model or GovernanceModel.CENTRAL
    if not can(actor, Action.PERSON_VIEW, profile, governance_model=governance):
        raise PermissionDenied(Action.PERSON_VIEW, actor)
    tab = request.GET.get("tab", "attendance")
    if tab not in DETAIL_TABS:
        raise Http404

    show_private = _allow_private_person_fields(actor)
    may_view_attendance = can(actor, Action.ATTENDANCE_VIEW, profile, governance_model=governance)
    attendance = []
    if may_view_attendance:
        attendance = list(
            AttendanceRecord.objects.for_actor(actor)
            .filter(student=profile.person)
            .select_related("session", "session__dojo", "session__template")
            .order_by("-session__starts_at")[:50]
        )

    tracks = []
    if can(actor, Action.RANK_VIEW, profile, governance_model=governance):
        awards = (
            RankAward.objects.for_organization(profile.organization_id)
            .select_related("rank", "awarded_by")
            .order_by("-awarded_on")
        )
        tracks = list(
            StudentStyleTrack.objects.for_organization(profile.organization_id)
            .filter(student=profile.person)
            .select_related("style", "ladder", "current_rank")
            .prefetch_related(Prefetch("awards", queryset=awards, to_attr="visible_awards"))
            .order_by("style__name", "-started_on")
        )

    notes = list(_visible_student_notes(actor, profile, governance))

    # ⚠ Safeguarding notes are fetched only when the tab is actually open, and
    # only through the service that writes the access log — TODO 1.8.4, SEC §4.
    # Loading them alongside the ordinary notes would log an access every time
    # anybody opened any tab of this page, which makes "who read this child's
    # safeguarding file" unanswerable by burying it in noise.
    may_view_safeguarding = safeguarding.may_view_safeguarding(actor, profile)
    safeguarding_notes = []
    if may_view_safeguarding and tab == "notes":
        safeguarding_notes = safeguarding.view_safeguarding_notes(subject=profile, actor=actor)

    # The form is built per actor, so the level dropdown only ever offers what
    # this person may author. `note_form` is None when they may write nothing,
    # which is how the template decides whether to render the composer at all.
    note_form = (
        StudentNoteForm(actor=actor, subject=profile)
        if writable_visibilities(actor, profile, governance_model=governance)
        else None
    )

    documents = []
    if show_private and tab == "documents":
        documents = [
            document
            for document in Document.objects.for_organization(profile.organization_id)
            .filter(subject_person=profile.person)
            .select_related("organization", "subject_person")
            if may_read(actor, document, governance_model=governance)
        ]
    guardians = []
    emergency_contacts = []
    enrollments = []
    if show_private and tab == "family":
        guardians = list(
            GuardianLink.objects.for_actor(actor)
            .filter(student=profile.person)
            .select_related("guardian")
        )
        emergency_contacts = list(
            EmergencyContact.objects.for_actor(actor).filter(person=profile.person)
        )
        enrollments = list(
            Enrollment.objects.for_actor(actor)
            .filter(student=profile.person)
            .select_related("dojo")
        )

    alerts = []
    if can(actor, Action.MEDICAL_VIEW, profile, governance_model=governance):
        if view_do_not_spar(profile=profile, actor=actor):
            alerts.append({"kind": "danger", "text": _("Do not spar")})
    if profile.status == StudentProfile.Status.ON_HOLD:
        alerts.append({"kind": "warning", "text": _("Membership is on hold")})
    today = timezone.localdate()
    if profile.licence_expires_on and profile.licence_expires_on < today:
        alerts.append({"kind": "warning", "text": _("Federation licence has expired")})
    if may_view_attendance and (
        not attendance
        or attendance[0].session.starts_at < timezone.now() - datetime.timedelta(days=21)
    ):
        alerts.append({"kind": "warning", "text": _("No attendance in the last 21 days")})

    waiver_policy = (
        ConsentPolicy.objects.for_organization(profile.organization_id)
        .filter(consent_type=ConsentRecord.Type.WAIVER, is_active=True)
        .first()
    )
    waiver_current = None
    if waiver_policy is not None:
        waiver_current = current_consent(
            person=profile.person,
            consent_type=ConsentRecord.Type.WAIVER,
            version=waiver_policy.version,
            actor=actor,
        )
    if waiver_policy is None or waiver_current is None or not waiver_current.granted:
        alerts.append({"kind": "warning", "text": _("Current waiver is unsigned")})
    for note in notes:
        if note.pinned:
            alerts.append({"kind": "note", "text": note.body})

    medical_policy_exists = (
        ConsentPolicy.objects.for_organization(profile.organization_id)
        .filter(consent_type=ConsentRecord.Type.MEDICAL, is_active=True)
        .exists()
    )
    photo_policy = active_photo_policy(profile)
    photo_consent = (
        current_photo_consent(profile=profile, actor=actor) if photo_policy is not None else None
    )
    profile_photo = current_student_photo(
        profile=profile,
        actor=actor,
        consent=photo_consent,
    )
    audit.record(
        "view_student",
        actor=actor,
        subject=profile,
        note=f"tab: {tab}",
        strict=True,
    )
    return render(
        request,
        "students/detail.html",
        {
            "profile": profile,
            "tab": tab,
            "tabs": DETAIL_TABS,
            "show_private_person_fields": show_private,
            "may_manage_guardians": show_private
            and can(actor, Action.PERSON_EDIT, profile, governance_model=governance),
            "may_change_status": can(
                actor, Action.PERSON_EDIT, profile, governance_model=governance
            ),
            "may_award_rank": can(actor, Action.RANK_AWARD, profile, governance_model=governance),
            "status_form": StudentStatusTransitionForm(current_status=profile.status),
            "attendance": attendance,
            "tracks": tracks,
            "notes": notes,
            "safeguarding_notes": safeguarding_notes,
            "note_form": note_form,
            "may_view_safeguarding": may_view_safeguarding,
            "documents": documents,
            "guardians": guardians,
            "emergency_contacts": emergency_contacts,
            "enrollments": enrollments,
            "alerts": alerts,
            "may_view_attendance": may_view_attendance,
            "may_view_billing": can(
                actor, Action.FINANCIAL_VIEW, profile, governance_model=governance
            ),
            "may_capture_waiver": waiver_policy is not None
            and can(actor, Action.PERSON_EDIT, profile, governance_model=governance),
            "may_capture_medical": medical_policy_exists
            and can(actor, Action.MEDICAL_EDIT, profile, governance_model=governance),
            "profile_photo": profile_photo,
            "may_capture_photo_consent": photo_policy is not None
            and can(actor, Action.PERSON_EDIT, profile, governance_model=governance),
            "may_upload_photo": photo_consent is not None
            and photo_consent.granted
            and can(actor, Action.PERSON_EDIT, profile, governance_model=governance),
        },
    )
