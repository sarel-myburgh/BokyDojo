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

from .models import Event, EventFormField, EventRsvp, RsvpAttachment


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


def questions_for(event: Event) -> list[EventFormField]:
    """The extra questions on one event's form.

    ⚠ Scoped by the event, which was itself resolved from the secret token — so
    the organisation is decided by the token here too. The reverse relation
    cannot be used directly: it refuses to evaluate without an actor, and a
    stranger has none.
    """
    return list(
        EventFormField.objects.unscoped("public invitation: questions of a token-resolved event")
        .filter(event=event)
        .order_by("order", "created_at")
    )


def save_public_rsvp(rsvp: EventRsvp) -> EventRsvp:
    """Store one reply.

    ⚠ The organisation is not supplied by the caller and cannot be: it comes
    from ``rsvp.event``, which was resolved from the token.
    """
    rsvp.save()
    return rsvp


def read_public_document(document) -> bytes:
    """Bytes of a poster or payment QR, for the invitation page.

    ⚠ Deliberately not ``open_document``: that authorises against an actor, and
    a stranger has none. The authorisation here is the event token — the caller
    has already resolved the event from it and taken the document off that
    event, so nothing else is reachable. No audit entry either: this is a poster
    an administrator published, not somebody's record.
    """
    from django.core.files.storage import default_storage

    with default_storage.open(document.storage_key, "rb") as handle:
        return handle.read()


def save_public_attachment(*, rsvp: EventRsvp, question: EventFormField, uploaded_file):
    """Store one file attached to a public reply.

    ⚠ The most exposed write in the product: a file, from somebody with no
    account, onto our disk. Four things stand between that and trouble, and all
    four are load-bearing:

    * ``validate_upload`` sniffs magic bytes rather than trusting the filename
      or the Content-Type, refuses SVG outright, and re-encodes images — which
      strips the GPS coordinates most phones bury in a photo.
    * The size cap here is a quarter of the authenticated one.
    * The caller is rate limited, so the cap is per file *and* per hour.
    * The stored document is kind EVENT_ATTACHMENT, which ``may_read`` only
      releases to somebody who can administer the organisation. It is never
      served back to the public, including to whoever uploaded it.
    """
    from django.core.exceptions import ValidationError
    from django.utils.translation import gettext as _

    from apps.core import uploads
    from apps.core.documents import store
    from apps.core.models import Document

    # ⚠ Read off the module at call time, not imported by value, so the limit
    # can be lowered in a test to exercise the check rather than the transport.
    limit = uploads.MAX_PUBLIC_UPLOAD_BYTES
    size = getattr(uploaded_file, "size", 0) or 0
    if size > limit:
        megabytes = max(1, limit // (1024 * 1024))
        raise ValidationError(
            _("That file is too big. The limit is %(mb)s MB.") % {"mb": megabytes}
        )

    document = store(
        uploaded_file,
        organization=rsvp.event.organization,
        kind=Document.Kind.EVENT_ATTACHMENT,
        actor=None,
    )
    attachment = RsvpAttachment(
        rsvp=rsvp,
        field_id=str(question.pk),
        label=question.label,
        document=document,
    )
    attachment.save()
    return attachment


def attachments_for(rsvp: EventRsvp) -> list[RsvpAttachment]:
    """Only used immediately after saving, to report what was stored."""
    return list(
        RsvpAttachment.objects.unscoped("public RSVP: files just written for this reply")
        .filter(rsvp=rsvp)
        .select_related("document")
    )
