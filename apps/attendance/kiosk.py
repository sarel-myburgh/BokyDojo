"""Hand-around check-in — TODO 1.7, plan §12.8/§13.2, decision D1.

⚠ **This is not the kiosk the plan describes, and the difference is deliberate.**
§13.2 designs an unattended tablet bolted up by the door: its own device token
rather than a user session, its own roster scope, PINs, lockout, revocation. That
shape follows from the device being *shared and unwatched*.

The decided scenario (D1, 2026-08-17) is different: **the instructor's own phone
or tablet, carried, with students queuing to tap their face while the instructor
watches.** The device never lives at the door. Attendance is supervised.

Two things fall out of that, and they make the whole feature smaller:

* **No device token.** The instructor is already signed in and standing there;
  their own session is the authentication, and it is strictly safer than a
  long-lived token sitting on a shared tablet. If a permanently-mounted kiosk is
  ever wanted, `1.7.1` comes back — it is not dead, it is not needed *here*.
* **No PINs.** §13.2 itself calls them "a convenience control, not a security
  boundary". Supervision is the control instead, and it is a better one: the
  person who knows whether Sokha is in the room is watching the queue.

⚠ **But handing over an unlocked phone is a real risk, and it is the one this
module exists for.** While check-in is running, the session is *locked* to the
kiosk: every other view refuses. Without that, a curious nine-year-old with the
instructor's phone is two taps from the student list, a medical alert or a
safeguarding note. Leaving requires the instructor's password, because the whole
point is that the person holding the phone might not be them.
"""

from __future__ import annotations

from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone

#: Session key holding the id of the session being checked in.
LOCK_KEY = "kiosk_session_id"
LOCK_STARTED_KEY = "kiosk_started_at"

#: URL names reachable while locked. Everything else 302s back to the grid.
#:
#: ⚠ Kept as names, not path prefixes, so a new URL cannot accidentally match a
#: prefix and become reachable from a handed-over phone.
ALLOWED_ROUTES = frozenset(
    {
        "kiosk",
        "kiosk-mark",
        "kiosk-exit",
        "attendance-sync",  # the offline queue flushing the taps
        "student-photo",  # the faces already on the grid
        "logout",
        "service-worker",
        "offline",
        "healthz",
    }
)


def start(request, session_id) -> None:
    request.session[LOCK_KEY] = str(session_id)
    request.session[LOCK_STARTED_KEY] = timezone.now().isoformat()


def stop(request) -> None:
    request.session.pop(LOCK_KEY, None)
    request.session.pop(LOCK_STARTED_KEY, None)


def locked_session_id(request):
    return request.session.get(LOCK_KEY)


class KioskLockMiddleware:
    """While check-in is running, this session may only reach the kiosk.

    ⚠ A redirect, not a 403. The person holding the phone is a student who tapped
    something; putting them back on the grid is the useful answer, and an error
    page is a dead end with a browser chrome they should not be exploring.

    ⚠ Deliberately not a permission check. The instructor genuinely holds every
    right this blocks — the lock is about *who is holding the device*, which no
    permission system models. That is also why it cannot be bypassed by role.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session_id = request.session.get(LOCK_KEY)
        if not session_id:
            return self.get_response(request)

        match = getattr(request, "resolver_match", None)
        route = match.url_name if match else None
        if route is None:
            # resolver_match is only populated after URL resolution, which has
            # not happened for middleware running before the view. Resolve here
            # rather than guessing from the path.
            from django.urls import Resolver404, resolve

            try:
                route = resolve(request.path_info).url_name
            except Resolver404:
                route = None

        if route in ALLOWED_ROUTES:
            return self.get_response(request)

        return HttpResponseRedirect(reverse("kiosk", args=[session_id]))
