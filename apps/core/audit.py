"""Audit trail helpers and middleware — TODO 0.3.5 / SEC 2.6.

Every state change is recorded with actor, subject and before/after state. The
log is append-only (see AuditLog.delete) and is the evidence base for:

  - safeguarding questions ("who looked at this child's record?")
  - billing disputes ("who wrote this invoice off?")
  - your own admin access on the managed tier (SEC §6.6)

Writes must never break the request they are recording. A failure to audit is
logged loudly but does not raise — an unavailable audit table should not stop a
parent paying an invoice. Actions where that trade-off is wrong (permission
changes, exports) should call ``record()`` with ``strict=True``.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import models

from .scoping import Actor, get_current_actor, reset_current_actor, set_current_actor

logger = logging.getLogger(__name__)

#: Never copy these into before/after snapshots, whatever model they appear on.
SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "pin",
        "pin_hash",
        "api_key",
        "secret",
        "token",
        "recovery_codes",
        "recovery_code_hashes",
        "totp_secret",
        "medical_notes",
        "allergies",
        "conditions",
        "medications",
        "doctor_contact",
        "do_not_spar",
        "signature_name",
        "filters",
        "hold_reason",
        # Note bodies are encrypted at rest (SEC §4). Copying one into a
        # before/after snapshot would write it back out in plaintext, so the
        # audit trail would become the easiest place to read the safeguarding
        # note you are not allowed to see.
        "body",
    }
)

MAX_VALUE_LENGTH = 2000


def _serialise_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    text = str(value)
    if len(text) > MAX_VALUE_LENGTH:
        return text[:MAX_VALUE_LENGTH] + "…"
    return text


def snapshot(instance: models.Model | None, fields: list[str] | None = None) -> dict | None:
    """Capture a model's field values, minus anything sensitive."""
    if instance is None:
        return None

    names = fields or [f.attname for f in instance._meta.fields]
    return {
        name: _serialise_value(getattr(instance, name, None))
        for name in names
        if name not in SENSITIVE_FIELDS and not name.endswith("_password")
    }


def diff(before: dict | None, after: dict | None) -> tuple[dict | None, dict | None]:
    """Reduce a before/after pair to only the keys that actually changed."""
    if before is None or after is None:
        return before, after
    changed = [key for key in after if before.get(key) != after.get(key)]
    if not changed:
        return None, None
    return (
        {key: before.get(key) for key in changed},
        {key: after.get(key) for key in changed},
    )


def record(
    action: str,
    *,
    actor: Actor | None = None,
    subject: models.Model | None = None,
    subject_type: str = "",
    subject_id: str = "",
    before: dict | None = None,
    after: dict | None = None,
    organization_id=None,
    ip_address: str | None = None,
    user_agent: str = "",
    note: str = "",
    actor_label: str = "",
    strict: bool = False,
):
    """Write one audit entry. Returns the entry, or None if writing failed."""
    from .models import AuditLog  # local import: avoids an app-loading cycle

    actor = actor or get_current_actor()

    if subject is not None:
        subject_type = subject_type or subject._meta.label
        subject_id = subject_id or str(subject.pk)
        if organization_id is None:
            organization_id = getattr(subject, "organization_id", None)

    if organization_id is None and actor is not None:
        organization_id = actor.organization_id

    try:
        return AuditLog.objects.create(
            action=action,
            organization_id=organization_id,
            actor_person_id=actor.person_id if actor else None,
            actor_label=actor_label or ("system" if actor and actor.is_system else ""),
            subject_type=subject_type,
            subject_id=subject_id,
            before=before,
            after=after,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:512],
            note=note[:512],
        )
    except Exception:
        # A broken audit table must not take the application down with it, but it
        # must be impossible to miss in the logs.
        logger.exception(
            "AUDIT WRITE FAILED action=%s subject=%s:%s", action, subject_type, subject_id
        )
        if strict:
            raise
        return None


def record_change(action: str, instance: models.Model, before: dict | None = None, **kwargs):
    """Convenience wrapper that diffs before/after for you."""
    after = snapshot(instance)
    before_changed, after_changed = diff(before, after)
    return record(
        action,
        subject=instance,
        before=before_changed,
        after=after_changed if before is not None else after,
        **kwargs,
    )


def client_ip(request) -> str | None:
    """Best-effort client IP.

    ``X-Forwarded-For`` is only trusted because the managed deployment always
    sits behind our own reverse proxy (Caddy). If that ever stops being true,
    this must be revisited — a spoofable IP in an audit log is worse than none.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class AuditContextMiddleware:
    """Bind the request's actor so audit writes and services can find it.

    The actor is still passed explicitly into service functions; this is a
    convenience for the audit layer and for templates, not an alternative to
    scoping (see apps/core/scoping.py).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from apps.identity.actors import actor_for_user

        actor = actor_for_user(getattr(request, "user", None))
        request.actor = actor
        request.audit_ip = client_ip(request)
        request.audit_user_agent = request.META.get("HTTP_USER_AGENT", "")[:512]

        token = set_current_actor(actor)
        try:
            return self.get_response(request)
        finally:
            reset_current_actor(token)
