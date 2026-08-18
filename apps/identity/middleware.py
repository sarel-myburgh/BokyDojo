"""Enforce MFA when a signed-in user's privilege level requires it."""

from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect
from django.urls import resolve, reverse

from apps.identity.mfa import get_credential, user_requires_mfa

MFA_VERIFIED_AT_KEY = "_dojomaster_mfa_verified"
AUTH_FLOW_KEY = "_dojomaster_auth_flow"
MFA_ALLOWED_URL_NAMES = frozenset(
    {"login", "logout", "mfa-challenge", "mfa-setup", "mfa-recovery-codes"}
)


class MfaEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "MFA_ENFORCEMENT_ENABLED", True):
            return self.get_response(request)
        user = getattr(request, "user", None)
        url_name = resolve(request.path_info).url_name
        if (
            user is not None
            and user.is_authenticated
            and url_name not in MFA_ALLOWED_URL_NAMES
            and user_requires_mfa(user)
            and MFA_VERIFIED_AT_KEY not in request.session
        ):
            credential = get_credential(user)
            target = "mfa-challenge" if credential and credential.is_confirmed else "mfa-setup"
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"{reverse(target)}?{query}")
        return self.get_response(request)


#: Reachable while a forced password change is outstanding. ⚠ The MFA screens are
#: here because the two enforcements stack: somebody signing in with a temporary
#: password who also holds a TOTP credential must be able to finish the second
#: factor before being sent to choose a password.
PASSWORD_CHANGE_ALLOWED_URL_NAMES = frozenset(
    {
        "login",
        "logout",
        "password-change",
        "mfa-challenge",
        "mfa-setup",
        "mfa-recovery-codes",
        "healthz",
        "service-worker",
    }
)


class PasswordChangeRequiredMiddleware:
    """Hold a session at the password screen until a temporary one is replaced.

    ⚠ Enforced on every request rather than checked once at login. A temporary
    password is known to the administrator who issued it, so it has to stop being
    a way in the moment the person it was given to has used it — checking only at
    the login view would leave the account usable by two people for as long as
    the session lasted.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and resolve(request.path_info).url_name not in PASSWORD_CHANGE_ALLOWED_URL_NAMES
        ):
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"{reverse('password-change')}?{query}")
        return self.get_response(request)
