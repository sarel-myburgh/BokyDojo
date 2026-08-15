"""One-time installation wizard — TODO 0.7.4."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.core import audit
from apps.identity.forms import FirstRunForm
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)

SETUP_LOCK_KEY = "dojomaster:first-run-setup"
SETUP_LOCK_SECONDS = 60


def setup_available() -> bool:
    return not Organization.objects.exists() and not User.objects.exists()


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def first_run_view(request) -> HttpResponse:
    if not setup_available():
        raise Http404

    form = FirstRunForm(request.POST or None)
    if request.method == "GET":
        return render(
            request,
            "auth/first_run.html",
            {"form": form, "token_required": bool(settings.FIRST_RUN_SETUP_TOKEN)},
        )

    if not form.is_valid():
        return render(
            request,
            "auth/first_run.html",
            {"form": form, "token_required": bool(settings.FIRST_RUN_SETUP_TOKEN)},
            status=400,
        )

    expected_token = settings.FIRST_RUN_SETUP_TOKEN
    supplied_token = form.cleaned_data["setup_token"]
    if expected_token and not secrets.compare_digest(supplied_token, expected_token):
        audit.record(
            "first_run_setup_failed",
            actor_label="anonymous",
            ip_address=audit.client_ip(request),
            note="invalid setup token",
        )
        return render(
            request,
            "auth/first_run.html",
            {"form": form, "token_required": True, "setup_error": "Invalid setup token."},
            status=403,
        )

    if not cache.add(SETUP_LOCK_KEY, "locked", timeout=SETUP_LOCK_SECONDS):
        return render(
            request,
            "auth/first_run.html",
            {
                "form": form,
                "token_required": bool(expected_token),
                "setup_error": "Setup is already in progress.",
            },
            status=409,
        )

    try:
        with transaction.atomic():
            if not setup_available():
                raise Http404
            data = form.cleaned_data
            organization = Organization.objects.create(
                name=data["organization_name"],
                slug=data["organization_slug"],
                governance_model=data["governance_model"],
                country=data["country"],
                default_timezone=data["timezone"],
                default_currency=data["currency"],
            )
            dojo = Dojo.objects.for_organization(organization.pk).create(
                organization=organization,
                name=data["dojo_name"],
                slug="main",
                city=data["dojo_city"],
                country=data["country"],
                timezone=data["timezone"],
                currency=data["currency"],
            )
            person = Person.objects.for_organization(organization.pk).create(
                organization=organization,
                given_name=data["admin_given_name"],
                family_name=data["admin_family_name"],
                email=data["admin_email"],
            )
            user = User.objects.create_user(
                email=data["admin_email"],
                password=data["admin_password"],
                person=person,
            )
            RoleAssignment.objects.for_organization(organization.pk).create(
                organization=organization,
                person=person,
                role=Role.ORG_ADMIN,
                scope_type=ScopeType.ORG,
                can_view_financials=True,
                can_export_pii=True,
            )
            audit.record(
                "first_run_setup",
                actor_label=user.email,
                subject=organization,
                organization_id=organization.pk,
                ip_address=audit.client_ip(request),
                note=f"first dojo {dojo.pk}",
                strict=True,
            )
    finally:
        cache.delete(SETUP_LOCK_KEY)

    return redirect("login")
