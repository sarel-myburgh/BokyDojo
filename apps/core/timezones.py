"""Rendering times in the right timezone — plan §4.5, CONTRIBUTING conventions.

The rule is "store UTC, render in the dojo's timezone". Storage was already
right; rendering was not — with ``TIME_ZONE = "UTC"`` and nothing activating a
timezone, an 18:30 class in Phnom Penh appeared on the Today screen as 11:30,
which is the same instant and completely useless to the instructor reading it.

Two mechanisms, because one is not enough:

* This middleware activates the *actor's own* timezone for the request. That
  fixes date rendering and — less obviously — ``__date`` lookups in report
  filters, which Django evaluates in the active timezone. A report boundary that
  moves depending on the server's timezone is a subtle, permanent source of
  "these numbers are wrong".
* Templates showing a specific dojo's class times wrap them in
  ``{% timezone session.dojo.timezone %}``, which overrides this for that block.
  An organisation admin can see dojos in two countries at once, and no single
  active timezone is correct for that page.
"""

from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

logger = logging.getLogger(__name__)


def zone(name: str | None) -> datetime.tzinfo:
    """Resolve a timezone name, falling back to UTC rather than raising.

    Names arrive from the database, where a typo or a renamed IANA zone is
    possible. Without this, every date calculation for that one tenant raises
    ``ZoneInfoNotFoundError`` at request time — a data problem escalated into an
    outage.
    """
    if not name:
        return datetime.UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("UNKNOWN TIMEZONE %r — falling back to UTC", name)
        return datetime.UTC


def dojo_zone(dojo) -> datetime.tzinfo:
    """The timezone a dojo's classes are scheduled in."""
    return zone(getattr(dojo, "timezone", None))


def actor_timezone(actor) -> str | None:
    """The timezone to render this actor's dates in.

    Their single dojo if they have exactly one, otherwise their organisation's
    default. Deliberately not "the first of several dojos" — that would be a
    coin toss the user cannot see.
    """
    if actor is None or actor.organization_id is None:
        return None

    from apps.identity.models import Dojo, Organization

    # Scoped reads, not an escape hatch: the dojo is one this actor is already
    # restricted to, so for_actor() is guaranteed to find it. (The first draft
    # of this used allow_unscoped and tests/test_unscoped_guard.py rejected it,
    # correctly — nothing in a request path needs that.)
    if actor.dojo_ids is not None and len(actor.dojo_ids) == 1:
        dojo = Dojo.objects.for_actor(actor).filter(pk=next(iter(actor.dojo_ids))).first()
        if dojo is not None and dojo.timezone:
            return dojo.timezone

    # Organization is not tenant-scoped — it *is* the tenant root — so it has no
    # scoping guard to satisfy. The id comes from the actor, never from input.
    organization = Organization.objects.filter(pk=actor.organization_id).first()
    if organization is not None:
        return organization.default_timezone

    return None


class ActiveTimezoneMiddleware:
    """Activate the actor's timezone for the duration of the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        name = actor_timezone(getattr(request, "actor", None))
        if not name:
            return self.get_response(request)

        timezone.activate(zone(name))
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
