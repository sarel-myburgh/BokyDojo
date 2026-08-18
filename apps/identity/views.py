"""Password, TOTP and recovery-code authentication flows."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.core import audit
from apps.core.models import AuditLog
from apps.core.sessions import stamp_session
from apps.core.throttle import (
    LOGIN_POLICY,
    MFA_POLICY,
    Throttled,
    enforce,
    register_failure,
    register_success,
)
from apps.identity.mfa import (
    confirm_credential,
    consume_recovery_code,
    consume_totp,
    ensure_credential,
    get_credential,
    provisioning_uri,
    user_requires_mfa,
)
from apps.identity.middleware import AUTH_FLOW_KEY, MFA_VERIFIED_AT_KEY
from apps.identity.models import Organization, User

logger = logging.getLogger(__name__)

LOGIN_SCOPE = "login"
MFA_SCOPE = "mfa"
PENDING_USER_KEY = "_bokydojo_pending_mfa_user"
PENDING_STARTED_KEY = "_bokydojo_pending_mfa_started"
PENDING_NEXT_KEY = "_bokydojo_pending_mfa_next"
RECOVERY_DISPLAY_KEY = "_bokydojo_mfa_recovery_display"
PENDING_MAX_AGE_SECONDS = 5 * 60
GENERIC_FAILURE = _("That email and password combination is not recognised.")
GENERIC_MFA_FAILURE = _("That verification code is not valid.")


def _safe_target(candidate: str) -> str:
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return reverse("today")


def _safe_next(request) -> str:
    return _safe_target(request.POST.get("next") or request.GET.get("next") or "")


def _begin_pending_mfa(request, user, next_url: str) -> None:
    request.session.cycle_key()
    request.session[PENDING_USER_KEY] = str(user.pk)
    request.session[PENDING_STARTED_KEY] = timezone.now().timestamp()
    request.session[PENDING_NEXT_KEY] = _safe_target(next_url)


def _clear_pending_mfa(request) -> None:
    for key in (PENDING_USER_KEY, PENDING_STARTED_KEY, PENDING_NEXT_KEY):
        request.session.pop(key, None)


def _pending_user(request):
    user_id = request.session.get(PENDING_USER_KEY)
    started = request.session.get(PENDING_STARTED_KEY)
    if not user_id or not started:
        return None
    if timezone.now().timestamp() - float(started) > PENDING_MAX_AGE_SECONDS:
        _clear_pending_mfa(request)
        return None
    return User.objects.select_related("person").filter(pk=user_id, is_active=True).first()


def _mfa_user(request):
    pending = _pending_user(request)
    if pending is not None:
        return pending, True
    if request.user.is_authenticated:
        return request.user, False
    return None, False


def _finish_login(request, user, *, mfa_verified: bool) -> HttpResponse:
    target = _safe_target(request.session.get(PENDING_NEXT_KEY, _safe_next(request)))
    source = audit.client_ip(request)

    login(request, user)
    stamp_session(request)
    request.session[AUTH_FLOW_KEY] = True
    if mfa_verified:
        request.session[MFA_VERIFIED_AT_KEY] = timezone.now().timestamp()
    _clear_pending_mfa(request)
    register_success(LOGIN_SCOPE, user.email, source=source)
    if mfa_verified:
        register_success(MFA_SCOPE, user.email, source=source)

    audit.record(
        "login",
        actor_label=user.email,
        subject=user.person,
        organization_id=user.person.organization_id if user.person else None,
        ip_address=source,
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        note="mfa" if mfa_verified else "password",
    )
    return redirect(target)


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def login_view(request) -> HttpResponse:
    if request.user.is_authenticated and request.method == "GET":
        return redirect(_safe_next(request))

    context = {
        "next": _safe_next(request),
        "setup_available": not Organization.objects.exists() and not User.objects.exists(),
    }
    if request.method == "GET":
        return render(request, "auth/login.html", context)

    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""
    source = audit.client_ip(request)

    try:
        enforce(LOGIN_SCOPE, email, LOGIN_POLICY, source=source)
    except Throttled as throttled:
        minutes = max(1, throttled.state.retry_after // 60)
        context["error"] = _("Too many attempts. Try again in about %(minutes)s minute(s).") % {
            "minutes": minutes
        }
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

    credential = get_credential(user)
    if user_requires_mfa(user) or (credential is not None and credential.is_confirmed):
        if credential is None:
            credential = ensure_credential(user)
        _begin_pending_mfa(request, user, context["next"])
        return redirect("mfa-challenge" if credential.is_confirmed else "mfa-setup")

    return _finish_login(request, user, mfa_verified=False)


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def mfa_setup_view(request) -> HttpResponse:
    user, is_pending = _mfa_user(request)
    if user is None:
        return redirect("login")

    source = audit.client_ip(request)
    credential = get_credential(user)
    if request.method == "POST" and request.POST.get("action") == "start":
        identifier = f"enrol:{user.email}"
        try:
            enforce(MFA_SCOPE, identifier, MFA_POLICY, source=source)
        except Throttled as throttled:
            minutes = max(1, throttled.state.retry_after // 60)
            error = _("Too many attempts. Try again in about %(minutes)s minute(s).") % {
                "minutes": minutes
            }
            return render(
                request,
                "auth/mfa_setup.html",
                {"can_start": True, "error": error},
                status=429,
            )
        if not user.check_password(request.POST.get("password") or ""):
            register_failure(MFA_SCOPE, identifier, MFA_POLICY, source=source)
            audit.record(
                "mfa_enrollment_failed",
                actor_label=user.email,
                organization_id=user.person.organization_id,
                ip_address=source,
            )
            return render(
                request,
                "auth/mfa_setup.html",
                {"can_start": True, "error": GENERIC_FAILURE},
                status=401,
            )
        register_success(MFA_SCOPE, identifier, source=source)
        credential = ensure_credential(user)
        return redirect("mfa-setup")

    if credential is None:
        return render(request, "auth/mfa_setup.html", {"can_start": True})

    if credential.is_confirmed:
        if is_pending:
            return redirect("mfa-challenge")
        return render(request, "auth/mfa_setup.html", {"is_confirmed": True})

    context = {
        "secret": credential.totp_secret,
        "provisioning_uri": provisioning_uri(credential),
        "next": _safe_next(request),
    }
    if request.method == "GET":
        return render(request, "auth/mfa_setup.html", context)

    try:
        enforce(MFA_SCOPE, user.email, MFA_POLICY, source=source)
    except Throttled as throttled:
        minutes = max(1, throttled.state.retry_after // 60)
        context["error"] = _("Too many attempts. Try again in about %(minutes)s minute(s).") % {
            "minutes": minutes
        }
        return render(request, "auth/mfa_setup.html", context, status=429)

    codes = confirm_credential(credential, request.POST.get("code") or "")
    if codes is None:
        register_failure(MFA_SCOPE, user.email, MFA_POLICY, source=source)
        context["error"] = GENERIC_MFA_FAILURE
        audit.record(
            "mfa_enrollment_failed",
            actor_label=user.email,
            organization_id=user.person.organization_id,
            ip_address=source,
        )
        return render(request, "auth/mfa_setup.html", context, status=400)

    register_success(MFA_SCOPE, user.email, source=source)
    audit.record(
        "mfa_enabled",
        actor_label=user.email,
        subject=user.person,
        organization_id=user.person.organization_id,
        ip_address=source,
    )
    if is_pending:
        _finish_login(request, user, mfa_verified=True)
    else:
        request.session.cycle_key()
        request.session[AUTH_FLOW_KEY] = True
        request.session[MFA_VERIFIED_AT_KEY] = timezone.now().timestamp()

    request.session[RECOVERY_DISPLAY_KEY] = codes
    return redirect("mfa-recovery-codes")


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def mfa_challenge_view(request) -> HttpResponse:
    user, is_pending = _mfa_user(request)
    if user is None:
        return redirect("login")

    credential = get_credential(user)
    if credential is None or not credential.is_confirmed:
        return redirect("mfa-setup")

    context = {
        "next": _safe_next(request),
        "setup_available": not Organization.objects.exists() and not User.objects.exists(),
    }
    if request.method == "GET":
        return render(request, "auth/mfa_challenge.html", context)

    source = audit.client_ip(request)
    try:
        enforce(MFA_SCOPE, user.email, MFA_POLICY, source=source)
    except Throttled as throttled:
        minutes = max(1, throttled.state.retry_after // 60)
        context["error"] = _("Too many attempts. Try again in about %(minutes)s minute(s).") % {
            "minutes": minutes
        }
        audit.record(
            "mfa_failed",
            actor_label=user.email,
            organization_id=user.person.organization_id,
            note="rejected by throttle",
            ip_address=source,
        )
        return render(request, "auth/mfa_challenge.html", context, status=429)

    code = request.POST.get("code") or ""
    verified = consume_totp(credential, code) or consume_recovery_code(credential, code)
    if not verified:
        register_failure(MFA_SCOPE, user.email, MFA_POLICY, source=source)
        context["error"] = GENERIC_MFA_FAILURE
        audit.record(
            "mfa_failed",
            actor_label=user.email,
            organization_id=user.person.organization_id,
            ip_address=source,
        )
        return render(request, "auth/mfa_challenge.html", context, status=401)

    audit.record(
        "mfa_verified",
        actor_label=user.email,
        subject=user.person,
        organization_id=user.person.organization_id,
        ip_address=source,
        note="pending login" if is_pending else "step-up",
    )
    if is_pending:
        return _finish_login(request, user, mfa_verified=True)

    request.session.cycle_key()
    request.session[AUTH_FLOW_KEY] = True
    request.session[MFA_VERIFIED_AT_KEY] = timezone.now().timestamp()
    register_success(MFA_SCOPE, user.email, source=source)
    return redirect(_safe_next(request))


@never_cache
@require_http_methods(["GET"])
def mfa_recovery_codes_view(request) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect("login")
    codes = request.session.pop(RECOVERY_DISPLAY_KEY, None)
    if not codes:
        return redirect("mfa-setup")
    return render(request, "auth/mfa_recovery_codes.html", {"codes": codes})


@never_cache
@csrf_protect
@require_http_methods(["POST"])
def logout_view(request) -> HttpResponse:
    if request.user.is_authenticated:
        audit.record(
            AuditLog.Action.LOGOUT,
            actor_label=request.user.email,
            ip_address=audit.client_ip(request),
        )
    logout(request)
    return redirect("login")


@never_cache
@csrf_protect
@login_required
@require_http_methods(["GET", "POST"])
def password_change_view(request) -> HttpResponse:
    """Choose your own password, replacing a temporary one — TODO 0.6.8.

    ⚠ Django's own form, which requires the current password. They have just
    typed it, so the friction is nil — and it means a session hijacked between
    sign-in and this screen cannot set a password of its own.
    """
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm

    from apps.identity.passwords import clear_must_change

    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        clear_must_change(user)
        # ⚠ Without this, changing the password rotates the session auth hash and
        # signs the person straight back out — having just proved who they are.
        update_session_auth_hash(request, user)
        audit.record(
            "password_change",
            actor=request.actor,
            subject=user.person,
            note="password chosen by the account holder",
            strict=True,
        )
        messages.success(request, _("Password changed."))
        # ⚠ Default to "", not None: _safe_target calls .startswith and a plain
        # visit to this screen carries no ?next=, which was a 500.
        return redirect(_safe_target(request.GET.get("next", "")) or "today")

    return render(
        request,
        "auth/password_change.html",
        {"form": form, "was_temporary": request.user.must_change_password},
    )
