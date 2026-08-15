"""Separate medical, waiver, and photograph consent screens."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.core import audit
from apps.core.documents import download_headers, open_document
from apps.core.models import Document
from apps.core.setting_registry import CONSENT_SELF_AGE
from apps.core.setting_resolver import ScopeChain, resolve

from .consent import current_consent, record_consent
from .forms import ConsentDecisionForm
from .models import ConsentPolicy, ConsentRecord, GovernanceModel, GuardianLink, StudentProfile
from .permissions import Action, require


def _profile_for_actor(actor, person_id) -> StudentProfile:
    return get_object_or_404(
        StudentProfile.objects.for_actor(actor).select_related(
            "person", "person__organization", "home_dojo"
        ),
        person_id=person_id,
    )


def _governance(profile: StudentProfile) -> str:
    return profile.person.organization.governance_model or GovernanceModel.CENTRAL


def _policy(profile: StudentProfile, consent_type: str) -> ConsentPolicy:
    return get_object_or_404(
        ConsentPolicy.objects.for_organization(profile.organization_id).select_related("document"),
        consent_type=consent_type,
        is_active=True,
    )


def _self_consent_age(profile: StudentProfile) -> int:
    return int(
        resolve(
            CONSENT_SELF_AGE.key,
            ScopeChain(organization_id=profile.organization_id),
        )
    )


def _signers(profile: StudentProfile, minimum_age: int):
    choices = []
    signers = {}
    person = profile.person
    if person.age is not None and person.age >= minimum_age:
        key = str(person.pk)
        choices.append((key, _("%(name)s — self") % {"name": person.full_name}))
        signers[key] = (person, ConsentRecord.Capacity.SELF)

    links = (
        GuardianLink.objects.for_organization(profile.organization_id)
        .filter(student=person, has_custody=True)
        .select_related("guardian")
        .order_by("guardian__family_name", "guardian__given_name")
    )
    for link in links:
        capacity = (
            ConsentRecord.Capacity.PARENT
            if link.relationship
            in (GuardianLink.Relationship.MOTHER, GuardianLink.Relationship.FATHER)
            else ConsentRecord.Capacity.GUARDIAN
        )
        key = str(link.guardian_id)
        choices.append(
            (
                key,
                _("%(name)s — %(capacity)s")
                % {"name": link.guardian.full_name, "capacity": link.get_relationship_display()},
            )
        )
        signers[key] = (link.guardian, capacity)
    return choices, signers


def _confirm_label(consent_type: str, granting: bool) -> str:
    if not granting:
        return _("I confirm that I am revoking this consent.")
    if consent_type == ConsentRecord.Type.MEDICAL:
        return _(
            "I explicitly consent to the collection and use of the medical information "
            "described above. This is separate from all other terms."
        )
    if consent_type == ConsentRecord.Type.PHOTO:
        return _(
            "I explicitly consent to photographs and video being used exactly as "
            "described above. This is separate from all other terms."
        )
    return _("I have read this exact waiver version and agree to it.")


def _capture(request, profile, policy, *, template_name: str) -> HttpResponse:
    actor = request.actor
    action = (
        Action.MEDICAL_EDIT
        if policy.consent_type == ConsentRecord.Type.MEDICAL
        else Action.PERSON_EDIT
    )
    require(actor, action, profile, governance_model=_governance(profile))
    minimum_age = _self_consent_age(profile)
    choices, signers = _signers(profile, minimum_age)
    current = current_consent(
        person=profile.person,
        consent_type=policy.consent_type,
        version=policy.version,
        actor=actor,
    )
    decision = request.POST.get("decision", "grant")
    if decision not in {"grant", "revoke"}:
        return HttpResponseBadRequest(_("Unknown consent decision."))
    granting = decision != "revoke"
    form = ConsentDecisionForm(
        request.POST or None,
        signer_choices=choices,
        confirm_label=_confirm_label(policy.consent_type, granting),
    )

    if request.method == "POST" and form.is_valid():
        signer = signers.get(form.cleaned_data["signer_id"])
        if signer is None:
            form.add_error("signer_id", _("Choose an authorised signer."))
        else:
            try:
                record_consent(
                    person=profile.person,
                    consent_type=policy.consent_type,
                    version=policy.version,
                    granted=granting,
                    granted_by=signer[0],
                    capacity=signer[1],
                    ip_address=audit.client_ip(request) or "",
                    actor=actor,
                    minimum_self_consent_age=minimum_age,
                    signature_name=form.cleaned_data["signature_name"],
                    policy=policy,
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    _("Consent recorded for %(name)s.") % {"name": profile.person.full_name},
                )
                return redirect(request.resolver_match.view_name, person_id=profile.person_id)

    return render(
        request,
        template_name,
        {
            "profile": profile,
            "policy": policy,
            "current": current,
            "form": form,
            "has_signers": bool(choices),
            "granting": granting,
            "minimum_age": minimum_age,
        },
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def medical_consent_view(request, person_id) -> HttpResponse:
    profile = _profile_for_actor(request.actor, person_id)
    policy = _policy(profile, ConsentRecord.Type.MEDICAL)
    return _capture(request, profile, policy, template_name="consent/medical.html")


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def photo_consent_view(request, person_id) -> HttpResponse:
    profile = _profile_for_actor(request.actor, person_id)
    policy = _policy(profile, ConsentRecord.Type.PHOTO)
    return _capture(request, profile, policy, template_name="consent/photo.html")


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def waiver_consent_view(request, person_id) -> HttpResponse:
    profile = _profile_for_actor(request.actor, person_id)
    policy = _policy(profile, ConsentRecord.Type.WAIVER)
    return _capture(request, profile, policy, template_name="consent/waiver.html")


@login_required
@never_cache
@require_http_methods(["GET"])
def document_download_view(request, document_id) -> FileResponse:
    if request.actor.organization_id is None:
        raise Http404
    document = get_object_or_404(
        Document.objects.for_organization(request.actor.organization_id), pk=document_id
    )
    if document.subject_person_id:
        try:
            _profile_for_actor(request.actor, document.subject_person_id)
        except Http404:
            raise
    payload = open_document(
        request.actor,
        document,
        governance_model=(document.organization.governance_model or GovernanceModel.CENTRAL),
    )
    response = FileResponse(iter([payload]))
    for name, value in download_headers(document).items():
        response[name] = value
    return response
