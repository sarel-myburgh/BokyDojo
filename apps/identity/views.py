"""Sign-in and sign-out — TODO 0.6.4/0.6.5, SEC §2.1.

This is where the lockout policies written in 0.6.5 finally get wired to
something: ``apps/core/throttle.py`` shipped with the policies but no call site,
which made it decorative.

⚠ Not yet 2FA. TOTP for admin and finance roles is 0.6.2 and lands separately;
this view is the surface it will hook into (the credential check succeeds, then a
second factor is demanded before ``login()`` is called).

Enumeration: the failure message is identical for an unknown email, a wrong
password and an inactive user, and the throttle counter advances in all three
cases so response *timing* does not separate them either.
"""

from __future__ import annotations

import logging

from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.core import audit
from apps.core.models import AuditLog
from apps.core.sessions import stamp_session
from apps.core.throttle import LOGIN_POLICY, Throttled, enforce, register_failure, register_success

logger = logging.getLogger(__name__)

LOGIN_SCOPE = "login"
#: One message for every failure mode. See the module docstring.
GENERIC_FAILURE = _("That email and password combination is not recognised.")


def _safe_next(request) -> str:
    """Only ever redirect within this site.

    ``?next=`` is attacker-controlled; an absolute URL here would make the login
    page an open redirect and a convenient phishing hop.
    """
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return reverse("today")


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def login_view(request) -> HttpResponse:
    if request.user.is_authenticated and request.method == "GET":
        return redirect(_safe_next(request))

    context = {"next": _safe_next(request)}

    if request.method == "GET":
        return render(request, "auth/login.html", context)

    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""
    source = audit.client_ip(request)

    # Identifiers are canonicalised *before* they reach the throttle, so
    # "Sarel@x.com" and "sarel@x.com" share one counter rather than getting a
    # fresh allowance each — one of the unverified suspicions in the security
    # review of 2026-07-26.
    try:
        enforce(LOGIN_SCOPE, email, LOGIN_POLICY, source=source)
    except Throttled as throttled:
        minutes = max(1, throttled.state.retry_after // 60)
        context["error"] = _(
            "Too many attempts. Try again in about %(minutes)s minute(s)."
        ) % {"minutes": minutes}
        audit.record(
            "login_failed",
            actor_label=email,
            note="rejected by throttle",
            ip_address=source,
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return render(request, "auth/login.html", context, status=429)

    user = authenticate(request, username=email, password=password)

    if user is None or not user.is_active:
        register_failure(LOGIN_SCOPE, email, LOGIN_POLICY, source=source)
        context["error"] = GENERIC_FAILURE
        audit.record(
            "login_failed",
            actor_label=email,
            ip_address=source,
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return render(request, "auth/login.html", context, status=401)

    register_success(LOGIN_SCOPE, email, source=source)
    login(request, user)  # cycles the session key
    stamp_session(request)

    audit.record(
        "login",
        actor_label=user.email,
        subject=user.person,
        organization_id=user.person.organization_id if user.person else None,
        ip_address=source,
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    return redirect(_safe_next(request))


@never_cache
@csrf_protect
@require_http_methods(["POST"])
def logout_view(request) -> HttpResponse:
    """POST only: a GET logout can be triggered by any image tag on any page."""
    if request.user.is_authenticated:
        audit.record(
            AuditLog.Action.LOGOUT,
            actor_label=request.user.email,
            ip_address=audit.client_ip(request),
        )
    logout(request)
    return redirect("login")
