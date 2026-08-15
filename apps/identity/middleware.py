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
