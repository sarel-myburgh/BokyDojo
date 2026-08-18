"""Session lifetime enforcement — TODO 0.6.4 / SEC 2.1.

Two independent limits, because they answer different questions:

* **Idle timeout** — a shared instructor tablet left on a bench in a dojo hall
  must not still be signed in an hour later. Measured from the last request.
* **Absolute cap** — a session may not live for ever no matter how much it is
  used. Bounds the damage from a stolen cookie, which idle timeout alone does
  not: an attacker who keeps using the cookie keeps it alive.

Both stamps live in the session itself, so nothing else has to be stored and a
flushed session is genuinely gone. ``django.contrib.auth.login`` cycles the
session key on sign-in, which covers the "rotate on privilege change" half of
0.6.4 for the login transition; ``rotate_session`` below is for role changes
made to an already-signed-in user.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import logout
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Set when the session was created, and never refreshed.
STARTED_AT_KEY = "_bokydojo_session_started"
#: Refreshed on every request.
LAST_SEEN_KEY = "_bokydojo_last_seen"


def _now_stamp() -> float:
    return timezone.now().timestamp()


def stamp_session(request) -> None:
    """Start the clocks. Call right after a successful sign-in."""
    request.session[STARTED_AT_KEY] = _now_stamp()
    request.session[LAST_SEEN_KEY] = _now_stamp()


def rotate_session(request) -> None:
    """Rotate the session key, keeping the contents — SEC §2.1.

    For privilege changes on a live session (a role granted or revoked): the
    identifier the browser holds should not survive a change in what it can do.
    """
    request.session.cycle_key()


class SessionTimeoutMiddleware:
    """Sign out sessions that are idle or simply too old."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.idle_seconds = getattr(settings, "SESSION_IDLE_TIMEOUT_SECONDS", 0)
        self.absolute_seconds = getattr(settings, "SESSION_ABSOLUTE_TIMEOUT_SECONDS", 0)

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            expired = self._expiry_reason(request)
            if expired:
                logger.info("SESSION EXPIRED reason=%s user=%s", expired, user.pk)
                logout(request)
            else:
                request.session[LAST_SEEN_KEY] = _now_stamp()
        return self.get_response(request)

    def _expiry_reason(self, request) -> str | None:
        now = _now_stamp()

        started = request.session.get(STARTED_AT_KEY)
        if started is None:
            # A session that predates this middleware, or one created by a path
            # that did not stamp it. Adopt it now rather than expiring it.
            request.session[STARTED_AT_KEY] = now
            started = now

        if self.absolute_seconds and now - started > self.absolute_seconds:
            return "absolute cap"

        last_seen = request.session.get(LAST_SEEN_KEY)
        if last_seen is not None and self.idle_seconds and now - last_seen > self.idle_seconds:
            return "idle timeout"

        return None
