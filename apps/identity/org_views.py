"""Organisation settings, and the screens for adding what a dojo is made of.

Until now everything could be *read* and almost nothing *created* — the roll, the
timetable and the reports all assumed somebody else had put the data there, which
in practice meant the Django admin or the seed. These are the four gaps that
stopped a real dojo setting itself up: styles, dojos, students, instructors.

⚠ Styles are the spine. A dojo teaches styles; enrolling a student there gives
them a track per style; a track is what carries a rank. Get the styles wrong and
every grade in the organisation hangs off the wrong thing, which is why the
settings screen is the first stop and why an unranked style is a first-class
choice rather than an afterthought.
"""

from __future__ import annotations

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.core import audit
from apps.core.relations import scoped_m2m_ids, set_scoped_m2m
from apps.identity.enrolment import enrol_student
from apps.identity.models import (
    Dojo,
    Enrollment,
    GovernanceModel,
    InstructorAssignment,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    StudentProfile,
    User,
)
from apps.identity.org_forms import DojoForm, StudentForm, StyleForm
from apps.identity.permissions import ROLE_ACTIONS, Action, PermissionDenied, require
from apps.ranks.models import RankLadder, Style
from apps.staffing.models import InstructorProfile


def _holds_anywhere(actor, action: str) -> bool:
    return any(action in ROLE_ACTIONS.get(role, set()) for role, _scope, _dojo in actor.roles)


def _organization(actor) -> Organization:
    # Organization is the tenant root and carries no scoping of its own; the id
    # comes from the actor, never from the request.
    return get_object_or_404(Organization, pk=actor.organization_id)


def _require_org_edit(actor, organization) -> None:
    require(actor, Action.ORG_EDIT, organization, governance_model=GovernanceModel.CENTRAL)


def _governance(dojo) -> str:
    return dojo.organization.governance_model or GovernanceModel.CENTRAL


# -- organisation settings ----------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def organization_settings_view(request) -> HttpResponse:
    """Styles and dojos in one place."""
    actor = request.actor
    organization = _organization(actor)
    _require_org_edit(actor, organization)

    style_form = StyleForm(organization=organization)

    if request.method == "POST" and request.POST.get("action") == "add-style":
        style_form = StyleForm(request.POST, organization=organization)
        if style_form.is_valid():
            style = style_form.save(commit=False)
            style.organization = organization
            style.save()
            audit.record_change("create", style, actor=actor)
            messages.success(
                request,
                _("Added %(name)s.") % {"name": style.name},
            )
            return redirect("org-settings")

    styles = list(Style.objects.for_actor(actor).order_by("name"))
    ladders = RankLadder.objects.for_actor(actor).select_related("style")
    ladders_by_style: dict = {}
    for ladder in ladders:
        ladders_by_style.setdefault(ladder.style_id, []).append(ladder)

    dojos = list(Dojo.objects.for_actor(actor).order_by("name"))
    by_id = {style.pk: style for style in styles}
    dojo_styles: dict = {}
    for dojo in dojos:
        dojo_styles[dojo.pk] = [
            by_id[style_id] for style_id in scoped_m2m_ids(dojo, "styles") if style_id in by_id
        ]

    return render(
        request,
        "identity/org_settings.html",
        {
            "organization": organization,
            "style_form": style_form,
            "styles": [
                {
                    "style": style,
                    "ladders": ladders_by_style.get(style.pk, []),
                    "dojo_count": sum(1 for entries in dojo_styles.values() if style in entries),
                }
                for style in styles
            ],
            "dojos": [{"dojo": dojo, "styles": dojo_styles.get(dojo.pk, [])} for dojo in dojos],
        },
    )


@login_required
@require_http_methods(["POST"])
def style_toggle_ranked_view(request, style_id) -> HttpResponse:
    """Flip a style between ranked and unranked.

    ⚠ Refused once the style has ladders. Turning a graded art unranked would
    orphan every ladder and leave existing rank awards pointing at a style that
    claims not to grade — a contradiction nothing downstream could resolve.
    """
    actor = request.actor
    organization = _organization(actor)
    _require_org_edit(actor, organization)
    style = get_object_or_404(Style.objects.for_actor(actor), pk=style_id)

    has_ladders = RankLadder.objects.for_actor(actor).filter(style=style).exists()
    if style.is_ranked and has_ladders:
        messages.error(
            request,
            _(
                "%(name)s has rank ladders, so it cannot be marked unranked. "
                "Remove the ladders first if it really does not grade."
            )
            % {"name": style.name},
        )
        return redirect("org-settings")

    style.is_ranked = not style.is_ranked
    style.save(update_fields=["is_ranked", "updated_at"])
    audit.record_change("update", style, actor=actor)
    messages.success(
        request,
        _("%(name)s now %(state)s ranks.")
        % {"name": style.name, "state": _("uses") if style.is_ranked else _("does not use")},
    )
    return redirect("org-settings")


# -- dojos --------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def dojo_create_view(request) -> HttpResponse:
    actor = request.actor
    organization = _organization(actor)
    if not _holds_anywhere(actor, Action.DOJO_CREATE):
        raise PermissionDenied(action=Action.DOJO_CREATE, actor=actor)

    form = DojoForm(request.POST or None, actor=actor, organization=organization)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            dojo = form.save(commit=False)
            dojo.organization = organization
            dojo.save()
            # ⚠ Not form.save_m2m(): it calls .set(), which reads through the
            # target's scoped manager and refuses without an actor. See
            # apps/core/relations.py.
            set_scoped_m2m(
                dojo, "styles", form.cleaned_data["styles"], organization_id=organization.pk
            )
            audit.record_change("create", dojo, actor=actor)
        messages.success(request, _("Created %(name)s.") % {"name": dojo.name})
        return redirect("dojo-edit", dojo_id=dojo.pk)

    return render(
        request,
        "identity/dojo_form.html",
        {"form": form, "is_new": True},
    )


@login_required
@require_http_methods(["GET", "POST"])
def dojo_edit_view(request, dojo_id) -> HttpResponse:
    actor = request.actor
    dojo = get_object_or_404(
        Dojo.objects.for_actor(actor).select_related("organization"), pk=dojo_id
    )
    require(actor, Action.DOJO_EDIT, dojo, governance_model=_governance(dojo))

    form = DojoForm(
        request.POST or None,
        instance=dojo,
        actor=actor,
        organization=dojo.organization,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save(commit=False)
            dojo.save()
            set_scoped_m2m(
                dojo,
                "styles",
                form.cleaned_data["styles"],
                organization_id=dojo.organization_id,
            )
            audit.record_change("update", dojo, actor=actor)
            # ⚠ Styles may have been added, so everybody already enrolled here
            # needs the tracks they would have got had the style been set first.
            # Without this, adding boxing to a dojo silently applies only to
            # members who join afterwards.
            from apps.ranks.enrolment_tracks import sync_tracks_for_enrolment

            added = 0
            for enrollment in Enrollment.objects.for_actor(actor).filter(
                dojo=dojo, ended_on__isnull=True
            ):
                added += len(sync_tracks_for_enrolment(enrollment, actor=actor))
        messages.success(
            request,
            _("Saved. %(count)s existing member(s) gained a style track.") % {"count": added}
            if added
            else _("Saved."),
        )
        return redirect("dojo-edit", dojo_id=dojo.pk)

    return render(
        request,
        "identity/dojo_form.html",
        {"form": form, "dojo": dojo, "is_new": False},
    )


# -- people -------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def student_create_view(request) -> HttpResponse:
    """Add somebody to the roll — the gap that made the app read-only."""
    actor = request.actor
    if not _holds_anywhere(actor, Action.PERSON_CREATE):
        raise PermissionDenied(action=Action.PERSON_CREATE, actor=actor)

    form = StudentForm(request.POST or None, actor=actor)
    if request.method == "POST" and form.is_valid():
        dojo = form.cleaned_data["dojo"]
        require(actor, Action.PERSON_CREATE, dojo, governance_model=_governance(dojo))
        with transaction.atomic():
            person = Person(
                organization_id=actor.organization_id,
                given_name=form.cleaned_data["given_name"],
                family_name=form.cleaned_data["family_name"],
                date_of_birth=form.cleaned_data["date_of_birth"],
                email=form.cleaned_data["email"],
                phone=form.cleaned_data["phone"],
            )
            person.save()
            StudentProfile.objects.for_actor(actor).create(
                person=person,
                status=StudentProfile.Status.ACTIVE,
                home_dojo=dojo,
                joined_on=form.cleaned_data["started_on"],
            )
            audit.record_change("create", person, actor=actor)
            # Goes through the canonical service, so the style tracks for this
            # dojo's arts are created by the same code path as every other
            # enrolment.
            enrol_student(
                student=person,
                dojo=dojo,
                started_on=form.cleaned_data["started_on"],
                actor=actor,
            )
        messages.success(request, _("Added %(name)s.") % {"name": person.full_name})
        return redirect("student-detail", person_id=person.pk)

    return render(request, "identity/student_form.html", {"form": form})


def _role_rows(actor):
    """Everybody holding a live staff role, with all of their roles."""
    from apps.identity.org_forms import STAFF_ROLES

    assignments = (
        RoleAssignment.objects.for_actor(actor)
        .filter(revoked_at__isnull=True, role__in=STAFF_ROLES)
        .select_related("person", "dojo")
        .order_by("person__family_name", "person__given_name")
    )
    people: dict = {}
    for assignment in assignments:
        entry = people.setdefault(assignment.person_id, {"person": assignment.person, "roles": []})
        entry["roles"].append(assignment)
    return list(people.values())


@login_required
@require_http_methods(["GET"])
def staff_list_view(request) -> HttpResponse:
    """Who holds what — TODO plan §3."""
    actor = request.actor
    if not _holds_anywhere(actor, Action.ROLE_ASSIGN):
        raise PermissionDenied(action=Action.ROLE_ASSIGN, actor=actor)

    return render(
        request,
        "identity/staff_list.html",
        {"rows": _role_rows(actor)},
    )


@login_required
@require_http_methods(["GET", "POST"])
def staff_create_view(request) -> HttpResponse:
    """Add a staff member with one or more roles.

    ⚠ Replaces the old instructor-only screen. A person holds a *set* of roles —
    an organisation administrator who also teaches Tuesdays is ordinary — and the
    permission layer always modelled it that way; only this form did not.
    """
    from apps.identity.org_forms import TEACHING_ROLES, StaffForm

    actor = request.actor
    if not _holds_anywhere(actor, Action.ROLE_ASSIGN):
        raise PermissionDenied(action=Action.ROLE_ASSIGN, actor=actor)

    form = StaffForm(request.POST or None, actor=actor)
    if request.method == "POST" and form.is_valid():
        roles = form.cleaned_data["roles"]
        scope_is_org = form.cleaned_data["scope"] == "org"
        dojo = form.cleaned_data["dojo"]
        if dojo is not None:
            require(actor, Action.ROLE_ASSIGN, dojo, governance_model=_governance(dojo))

        with transaction.atomic():
            person = Person(
                organization_id=actor.organization_id,
                given_name=form.cleaned_data["given_name"],
                family_name=form.cleaned_data["family_name"],
                email=form.cleaned_data["email"],
            )
            person.save()

            for role in roles:
                # ⚠ ORG_ADMIN is always organisation-scoped; a dojo-scoped one
                # would hold none of the powers the role implies, because can()
                # only grants a dojo-scoped role over that dojo's own objects.
                as_org = scope_is_org or role == Role.ORG_ADMIN
                RoleAssignment.objects.for_actor(actor).create(
                    organization_id=actor.organization_id,
                    person=person,
                    role=role,
                    scope_type=ScopeType.ORG if as_org else ScopeType.DOJO,
                    dojo=None if as_org else dojo,
                )

            if any(role in TEACHING_ROLES for role in roles):
                # ⚠ Both records, as before. RoleAssignment is what the
                # permission layer reads; InstructorAssignment is what the
                # substitution check reads, and without it every attempt to put
                # them on a class is refused.
                InstructorAssignment.objects.for_actor(actor).create(
                    dojo=dojo,
                    person=person,
                    is_head_instructor=form.cleaned_data["is_head_instructor"],
                    started_on=datetime.date.today(),
                )
                profile = InstructorProfile.objects.for_actor(actor).create(
                    person=person,
                    pay_type=form.cleaned_data["pay_type"],
                    pay_rate_minor_units=int((form.cleaned_data["pay_rate"] or 0) * 100),
                    pay_currency=dojo.currency,
                )
                set_scoped_m2m(
                    profile,
                    "styles",
                    form.cleaned_data["styles"],
                    organization_id=actor.organization_id,
                )

            # ⚠ No password is set. The creator must not choose one on somebody
            # else's behalf — they would know it, and the first thing the new
            # person does is not change it. An unusable password plus the
            # existing single-use reset flow (0.6.6) is the safe route in.
            user = User.objects.create_user(
                email=form.cleaned_data["email"], password=None, person=person
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            audit.record_change("create", person, actor=actor)

        messages.success(
            request,
            _(
                "Added %(name)s with %(count)s role(s). They cannot sign in until "
                "they set a password — send them the 'forgot password' link."
            )
            % {"name": person.full_name, "count": len(roles)},
        )
        return redirect("staff-list")

    return render(request, "identity/staff_form.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def staff_roles_view(request, person_id) -> HttpResponse:
    """Grant another role to somebody who already exists.

    ⚠ This is the screen that makes it RBAC rather than a job title. Without it
    an existing instructor could never become an administrator without being
    created again as a second person.
    """
    from apps.identity.org_forms import RoleGrantForm

    actor = request.actor
    if not _holds_anywhere(actor, Action.ROLE_ASSIGN):
        raise PermissionDenied(action=Action.ROLE_ASSIGN, actor=actor)
    person = get_object_or_404(Person.objects.for_actor(actor), pk=person_id)

    form = RoleGrantForm(request.POST or None, actor=actor)
    if request.method == "POST" and form.is_valid():
        role = form.cleaned_data["role"]
        as_org = form.cleaned_data["scope"] == "org" or role == Role.ORG_ADMIN
        dojo = form.cleaned_data["dojo"]
        if dojo is not None:
            require(actor, Action.ROLE_ASSIGN, dojo, governance_model=_governance(dojo))

        existing = (
            RoleAssignment.objects.for_actor(actor)
            .filter(
                person=person,
                role=role,
                scope_type=ScopeType.ORG if as_org else ScopeType.DOJO,
                dojo=None if as_org else dojo,
            )
            .first()
        )
        if existing is not None and existing.revoked_at is None:
            messages.error(request, _("They already hold that role."))
        elif existing is not None:
            # ⚠ Un-revoke rather than create a second row: the table is unique on
            # (person, role, scope, dojo), so a second insert would fail.
            existing.revoked_at = None
            existing.save(update_fields=["revoked_at", "updated_at"])
            audit.record_change("update", existing, actor=actor)
            messages.success(request, _("Restored that role."))
        else:
            assignment = RoleAssignment.objects.for_actor(actor).create(
                organization_id=actor.organization_id,
                person=person,
                role=role,
                scope_type=ScopeType.ORG if as_org else ScopeType.DOJO,
                dojo=None if as_org else dojo,
            )
            audit.record_change("create", assignment, actor=actor)
            messages.success(request, _("Role added."))
        return redirect("staff-roles", person_id=person.pk)

    assignments = list(
        RoleAssignment.objects.for_actor(actor)
        .filter(person=person, revoked_at__isnull=True)
        .select_related("dojo")
        .order_by("role")
    )
    return render(
        request,
        "identity/staff_roles.html",
        {"person": person, "assignments": assignments, "form": form},
    )


@login_required
@require_POST
def role_revoke_view(request, person_id, assignment_id) -> HttpResponse:
    """Take a role away.

    ⚠ Revoked, not deleted. Everything that reads roles filters on
    ``revoked_at__isnull=True``, and who held what and until when is exactly the
    question an investigation asks months later.
    """
    actor = request.actor
    if not _holds_anywhere(actor, Action.ROLE_ASSIGN):
        raise PermissionDenied(action=Action.ROLE_ASSIGN, actor=actor)
    person = get_object_or_404(Person.objects.for_actor(actor), pk=person_id)
    assignment = get_object_or_404(
        RoleAssignment.objects.for_actor(actor).filter(person=person, revoked_at__isnull=True),
        pk=assignment_id,
    )

    # ⚠ Nobody may remove their own last administrator role, and no organisation
    # may be left without one. Either is a locked-out tenant needing database
    # access to recover.
    if assignment.role == Role.ORG_ADMIN:
        remaining = (
            RoleAssignment.objects.for_actor(actor)
            .filter(role=Role.ORG_ADMIN, revoked_at__isnull=True)
            .exclude(pk=assignment.pk)
            .count()
        )
        if remaining == 0:
            messages.error(
                request,
                _("This is the last organisation administrator. Add another first."),
            )
            return redirect("staff-roles", person_id=person.pk)

    assignment.revoked_at = timezone.now()
    assignment.save(update_fields=["revoked_at", "updated_at"])
    audit.record_change("update", assignment, actor=actor)
    messages.success(request, _("Role revoked."))
    return redirect("staff-roles", person_id=person.pk)
