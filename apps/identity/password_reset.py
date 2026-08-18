"""Single-use password reset with uniform request responses — TODO 0.6.6."""

from __future__ import annotations

import logging

from django.contrib.auth import password_validation
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.core import audit
from apps.core.throttle import RESET_POLICY, Throttled, enforce, register_failure
from apps.identity.models import User

logger = logging.getLogger(__name__)
RESET_SCOPE = "password-reset"
GENERIC_SENT_MESSAGE = _(
    "If an active account matches that email address, password reset instructions have been sent."
)
INVALID_LINK_MESSAGE = _("This password reset link is invalid or has expired.")


def _reset_user(uidb64: str):
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return User.objects.filter(pk=user_id, is_active=True).first()


def _send_reset_email(request, email: str, user) -> None:
    if user is None:
        body = _(
            "A password reset was requested for this email address, but no active "
            "BokyDojo account matches it. No action is needed."
        )
    else:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        path = reverse("password-reset-confirm", kwargs={"uidb64": uid, "token": token})
        reset_url = request.build_absolute_uri(path)
        body = _(
            "A password reset was requested for your BokyDojo account.\n\n"
            "Use this single-use link within 30 minutes:\n%(url)s\n\n"
            "If you did not request this, no action is needed."
        ) % {"url": reset_url}

    send_mail(
        subject=_("Reset your BokyDojo password"),
        message=body,
        from_email=None,
        recipient_list=[email],
        fail_silently=False,
    )


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def password_reset_request_view(request) -> HttpResponse:
    if request.method == "GET":
        return render(request, "auth/password_reset_request.html")

    email = (request.POST.get("email") or "").strip().lower()
    source = audit.client_ip(request)
    allowed = True
    try:
        enforce(RESET_SCOPE, email, RESET_POLICY, source=source)
    except Throttled:
        allowed = False

    if allowed and email:
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        try:
            _send_reset_email(request, email, user)
        except Exception:
            logger.exception("PASSWORD RESET EMAIL FAILED")
        register_failure(RESET_SCOPE, email, RESET_POLICY, source=source)

    audit.record(
        "password_reset_requested",
        actor_label="anonymous",
        subject_type="identity.User",
        ip_address=source,
        note="throttled" if not allowed else "accepted",
    )
    return render(
        request,
        "auth/password_reset_request.html",
        {"submitted": True, "message": GENERIC_SENT_MESSAGE},
    )


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def password_reset_confirm_view(request, uidb64: str, token: str) -> HttpResponse:
    user = _reset_user(uidb64)
    valid = user is not None and default_token_generator.check_token(user, token)
    context = {"valid_link": valid}

    if request.method == "GET":
        if not valid:
            context["error"] = INVALID_LINK_MESSAGE
        return render(request, "auth/password_reset_confirm.html", context)

    if not valid:
        context["error"] = INVALID_LINK_MESSAGE
        return render(request, "auth/password_reset_confirm.html", context, status=400)

    password = request.POST.get("password") or ""
    confirmation = request.POST.get("password_confirm") or ""
    if password != confirmation:
        context["error"] = _("The two passwords do not match.")
        return render(request, "auth/password_reset_confirm.html", context, status=400)

    try:
        password_validation.validate_password(password, user)
    except ValidationError as exc:
        context["errors"] = exc.messages
        return render(request, "auth/password_reset_confirm.html", context, status=400)

    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if not default_token_generator.check_token(locked, token):
            context["valid_link"] = False
            context["error"] = INVALID_LINK_MESSAGE
            return render(request, "auth/password_reset_confirm.html", context, status=400)
        locked.set_password(password)
        locked.last_password_change = timezone.now()
        locked.save(update_fields=["password", "last_password_change"])

    audit.record(
        "password_reset",
        actor_label=user.email,
        subject=user.person,
        organization_id=user.person.organization_id if user.person else None,
        ip_address=audit.client_ip(request),
    )
    return redirect("password-reset-complete")


@never_cache
@require_http_methods(["GET"])
def password_reset_complete_view(request) -> HttpResponse:
    return render(request, "auth/password_reset_complete.html")
