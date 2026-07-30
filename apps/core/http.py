"""HTTP glue — TODO 0.5.7 / SEC 2.2.

The permission resolver raises its own ``PermissionDenied`` rather than Django's,
so that service code has no dependency on the web layer. Something has to turn
that into a 403 at the boundary, and doing it in one middleware means no view can
forget to — a permission error that leaks as a 500 is both an information leak
and an outage.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

from apps.identity.permissions import PermissionDenied as ActionDenied

logger = logging.getLogger(__name__)


class PermissionDeniedMiddleware:
    """Translate a refused action into a 403."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, ActionDenied):
            actor = getattr(exception, "actor", None)
            logger.info(
                "PERMISSION DENIED action=%s person=%s path=%s",
                exception.action,
                getattr(actor, "person_id", None),
                request.path,
            )
            raise DjangoPermissionDenied(str(exception)) from exception
        return None
