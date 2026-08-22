"""The only unscoped reads and writes in the product, in one place — plan §3.

⚠ This file exists so that the tenant-scoping escape hatch has exactly one home
and that home is four lines long.

A public invitation is opened by somebody with no login, no organisation and
therefore no ``Actor`` — there is nothing to scope a query by. That is a real
hole in the model, so it is confined here rather than spread through the views:

* ``published_event_by_token`` matches on the secret token **and**
  ``is_published``. There is no listing, no lookup by id, and no other field it
  will match. Holding the token is the entire authorisation.
* ``save_public_rsvp`` writes one row whose parent is an event already resolved
  by the function above, so the organisation is decided by the token too.

⚠ If a third function ever seems to belong here, that is the moment to stop and
ask whether the public surface is still one page. It is listed by name in
tests/test_unscoped_guard.py, and adding unscoped access anywhere else in
apps/events/ makes that test fail — which is the point.
"""

from __future__ import annotations

from .models import Event, EventRsvp


def published_event_by_token(token: str) -> Event | None:
    """One published event, found by its secret token. Never anything else."""
    if not token:
        return None
    return (
        Event.objects.unscoped("public invitation: keyed on the secret token alone")
        .select_related("organization", "dojo")
        .filter(public_token=token, is_published=True)
        .first()
    )


def save_public_rsvp(rsvp: EventRsvp) -> EventRsvp:
    """Store one reply.

    ⚠ The organisation is not supplied by the caller and cannot be: it comes
    from ``rsvp.event``, which was resolved from the token.
    """
    rsvp.save()
    return rsvp
