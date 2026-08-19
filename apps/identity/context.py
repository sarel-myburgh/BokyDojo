"""Template context shared by every signed-in page."""

from __future__ import annotations

from apps.core.version import display_version

from .mfa import should_encourage_mfa


def security_nudges(request) -> dict:
    """Whether to nudge this user towards a second factor.

    ⚠ A banner, not a redirect. Enrolment is optional by decision (see
    ``config/settings/base.py``): an organisation with no smartphone to hand
    would otherwise be locked out entirely, and with no SMTP there is no reset
    mail to rescue them.
    """
    user = getattr(request, "user", None)
    version = display_version()
    if user is None or not getattr(user, "is_authenticated", False):
        return {"encourage_mfa": False, "app_version": version}
    return {"encourage_mfa": should_encourage_mfa(user), "app_version": version}
