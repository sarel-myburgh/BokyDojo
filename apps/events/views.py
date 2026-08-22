"""Events, and the invitation a stranger can open — plan §3."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.vary import vary_on_cookie

from apps.core import audit
from apps.core.models import Document
from apps.core.throttle import RSVP_POLICY, Throttled, enforce, register_failure
from apps.identity.models import GovernanceModel, Organization
from apps.identity.permissions import Action, PermissionDenied, can

from .forms import EventForm, EventFormFieldForm, RsvpForm
from .models import Event, EventFormField, EventRsvp, RsvpAttachment
from .public_access import (
    published_event_by_token,
    questions_for,
    read_public_document,
    save_public_attachment,
    save_public_rsvp,
)


def _organization(actor) -> Organization:
    # Organization is the tenant root and carries no scoping of its own; the id
    # comes from the actor, never from the request.
    return get_object_or_404(Organization, pk=actor.organization_id)


def _require_manage(actor) -> Organization:
    organization = _organization(actor)
    if not can(actor, Action.ORG_EDIT, organization, governance_model=GovernanceModel.CENTRAL):
        raise PermissionDenied(action=Action.ORG_EDIT, actor=actor)
    return organization


# -- staff screens ------------------------------------------------------------


@login_required
def event_list_view(request):
    events = list(
        Event.objects.for_actor(request.actor).select_related("dojo").order_by("-starts_at")[:100]
    )
    counts = _rsvp_counts(request.actor, events)
    now = timezone.now()
    return render(
        request,
        "events/list.html",
        {
            "upcoming": [
                {"event": e, "rsvps": counts.get(e.pk, 0)}
                for e in events
                if (e.ends_at or e.starts_at) >= now
            ][::-1],
            "past": [
                {"event": e, "rsvps": counts.get(e.pk, 0)}
                for e in events
                if (e.ends_at or e.starts_at) < now
            ],
            "may_manage": can(
                request.actor,
                Action.ORG_EDIT,
                _organization(request.actor),
                governance_model=GovernanceModel.CENTRAL,
            ),
        },
    )


def _rsvp_counts(actor, events) -> dict:
    from django.db.models import Count

    rows = (
        EventRsvp.objects.for_actor(actor)
        .filter(event__in=events, status=EventRsvp.Status.COMING)
        .values("event_id")
        .annotate(total=Count("id"))
    )
    return {row["event_id"]: row["total"] for row in rows}


@login_required
@require_http_methods(["GET", "POST"])
def event_create_view(request):
    organization = _require_manage(request.actor)
    form = EventForm(
        request.POST or None, request.FILES or None, actor=request.actor, organization=organization
    )

    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            event = form.save()
            _store_event_images(event, form, actor=request.actor, organization=organization)
            audit.record_change("create", event, actor=request.actor)
        messages.success(request, _("Event created. Publish it when you are ready to share it."))
        return redirect("event-detail", event_id=event.pk)

    return render(request, "events/form.html", {"form": form, "is_new": True})


@login_required
@require_http_methods(["GET", "POST"])
def event_edit_view(request, event_id):
    organization = _require_manage(request.actor)
    event = get_object_or_404(Event.objects.for_actor(request.actor), pk=event_id)
    form = EventForm(
        request.POST or None,
        request.FILES or None,
        instance=event,
        actor=request.actor,
        organization=organization,
    )

    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            event = form.save()
            _store_event_images(event, form, actor=request.actor, organization=organization)
            audit.record_change("update", event, actor=request.actor)
        messages.success(request, _("Event updated."))
        return redirect("event-detail", event_id=event.pk)

    return render(request, "events/form.html", {"form": form, "event": event, "is_new": False})


@login_required
def event_detail_view(request, event_id):
    event = get_object_or_404(
        Event.objects.for_actor(request.actor).select_related("dojo"), pk=event_id
    )
    may_manage = can(
        request.actor,
        Action.ORG_EDIT,
        _organization(request.actor),
        governance_model=GovernanceModel.CENTRAL,
    )
    rsvps = list(
        EventRsvp.objects.for_actor(request.actor).filter(event=event).order_by("created_at")
    )
    coming = [r for r in rsvps if r.status == EventRsvp.Status.COMING]
    return render(
        request,
        "events/detail.html",
        {
            "event": event,
            "rsvps": rsvps,
            "coming_count": sum(r.party_size for r in coming),
            "may_manage": may_manage,
            "public_url": request.build_absolute_uri(_public_path(event)),
        },
    )


def _store_event_images(event: Event, form, *, actor, organization) -> None:
    """Put the poster and the payment QR through the ordinary document path.

    ⚠ Same validation as every other upload: magic bytes rather than the
    filename, SVG refused, images re-encoded so EXIF — and the GPS in it — is
    stripped. An administrator photographing a QR code on their own desk should
    not be publishing their office coordinates with it.
    """
    from apps.core.documents import store

    changed = []
    for field_name in ("image", "payment_qr"):
        uploaded = form.cleaned_data.get(field_name)
        if not uploaded:
            continue
        document = store(
            uploaded,
            organization=organization,
            kind=Document.Kind.EVENT_IMAGE,
            actor=actor,
        )
        setattr(event, field_name, document)
        changed.append(field_name)
    if changed:
        event.save(update_fields=[*changed, "updated_at"])


def _public_path(event: Event) -> str:
    """⚠ One address for both visibilities, always keyed on the token.

    A public event could have a readable URL, but then flipping it back to
    private would leave the readable one working and cached. Visibility controls
    whether search engines are invited in (see the template's robots tag), not
    how hard the address is to guess.
    """
    return reverse("event-public", args=[event.public_token])


@login_required
@require_POST
def event_publish_view(request, event_id):
    _require_manage(request.actor)
    event = get_object_or_404(Event.objects.for_actor(request.actor), pk=event_id)

    event.is_published = not event.is_published
    event.save(update_fields=["is_published", "updated_at"])
    audit.record_change("update", event, actor=request.actor)
    messages.success(
        request,
        _("Event published — the link works now.")
        if event.is_published
        else _("Event unpublished — the link no longer works."),
    )
    return redirect("event-detail", event_id=event.pk)


@login_required
@require_POST
def event_new_link_view(request, event_id):
    """Issue a fresh secret link, killing the old one.

    ⚠ The reason this exists: a link shared with the wrong group cannot be
    unshared. Rotating the token is the only way to actually revoke it.
    """
    from .models import new_public_token

    _require_manage(request.actor)
    event = get_object_or_404(Event.objects.for_actor(request.actor), pk=event_id)
    event.public_token = new_public_token()
    event.save(update_fields=["public_token", "updated_at"])
    audit.record_change("update", event, actor=request.actor)
    messages.success(request, _("New link issued. The previous one has stopped working."))
    return redirect("event-detail", event_id=event.pk)


@login_required
@require_POST
def rsvp_delete_view(request, event_id, rsvp_id):
    """⚠ A real delete, not a soft one. This is somebody outside the
    organisation asking us to forget them, and honouring that means the row
    goes."""
    _require_manage(request.actor)
    event = get_object_or_404(Event.objects.for_actor(request.actor), pk=event_id)
    rsvp = get_object_or_404(
        EventRsvp.objects.for_actor(request.actor).filter(event=event), pk=rsvp_id
    )
    rsvp.delete()
    messages.success(request, _("Reply deleted."))
    return redirect("event-detail", event_id=event.pk)


# -- the public page ----------------------------------------------------------


def _published_event(token: str) -> Event:
    """Find one published event by its token, and nothing else.

    ⚠ The unscoped lookup lives in public_access.py, alone, so the escape hatch
    has one auditable home — see the note at the top of that module.
    """
    event = published_event_by_token(token)
    if event is None:
        raise Http404("No such event.")
    return event


def _questions(event: Event) -> list:
    """This event's own questions — see public_access.questions_for."""
    return questions_for(event)


@csrf_protect
@vary_on_cookie
@require_http_methods(["GET", "POST"])
def event_public_view(request, token):
    """The invitation. Openable by anybody holding the link.

    ⚠ Every value rendered here comes from the Event row itself. Nothing on this
    page walks a relation to a person, a student, or an attendance record, and
    that is what keeps it contained: no query behind this page touches a table
    with personal data in it.
    """
    event = _published_event(token)
    questions = _questions(event)
    form = RsvpForm(questions=questions)
    submitted = False

    if request.method == "POST":
        source = audit.client_ip(request)
        identifier = f"rsvp:{event.pk}"
        try:
            enforce("rsvp", identifier, RSVP_POLICY, source=source)
        except Throttled:
            return render(
                request,
                "events/public.html",
                {
                    "event": event,
                    "form": RsvpForm(questions=questions),
                    "throttled": True,
                    "rsvp_open": event.rsvps_are_open,
                },
                status=429,
            )

        form = RsvpForm(request.POST, request.FILES, questions=questions)
        if not event.rsvps_are_open:
            form.add_error(None, _("Replies for this event are closed."))
        elif form.is_valid():
            rsvp = form.save(commit=False)
            rsvp.event = event
            rsvp.answers = form.answers()
            try:
                rsvp.full_clean(validate_unique=False, validate_constraints=False)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                try:
                    with transaction.atomic():
                        save_public_rsvp(rsvp)
                        # ⚠ Inside the transaction and only after the reply
                        # itself validated: a rejected form must not leave
                        # files on disk with no row pointing at them.
                        for field, uploaded in form.attachments():
                            save_public_attachment(
                                rsvp=rsvp, question=field, uploaded_file=uploaded
                            )
                except ValidationError as exc:
                    form.add_error(None, exc)
                else:
                    submitted = True
                    form = RsvpForm(questions=questions)

        # ⚠ Every POST counts, successful or not. This is a rate limit on a form
        # anybody can reach, not a failure counter: a valid submission is
        # precisely the thing being capped, because the abuse is volume.
        register_failure("rsvp", identifier, RSVP_POLICY, source=source)

    return render(
        request,
        "events/public.html",
        {
            "event": event,
            "form": form,
            "submitted": submitted,
            "rsvp_open": event.rsvps_are_open,
        },
    )


# -- who is coming ------------------------------------------------------------


def _answer_columns(event: Event, *, actor) -> list:
    """The questions to show as columns, live ones first.

    ⚠ Includes questions that have since been deleted, recovered from the
    answers themselves. Somebody who replied to a question that was later
    removed still answered it, and dropping the column would quietly discard
    what they said.
    """
    live = list(
        EventFormField.objects.for_actor(actor).filter(event=event).order_by("order", "created_at")
    )
    columns = [{"key": str(q.pk), "label": q.label, "is_live": True} for q in live]
    seen = {c["key"] for c in columns}

    for rsvp in EventRsvp.objects.for_actor(actor).filter(event=event):
        for key, answer in (rsvp.answers or {}).items():
            if key in seen:
                continue
            seen.add(key)
            columns.append(
                {
                    "key": key,
                    "label": (answer or {}).get("label") or _("(deleted question)"),
                    "is_live": False,
                }
            )
    return columns


def _cell(rsvp: EventRsvp, key: str):
    answer = (rsvp.answers or {}).get(key)
    if not isinstance(answer, dict):
        return ""
    value = answer.get("value")
    if isinstance(value, bool):
        return _("Yes") if value else _("No")
    return "" if value is None else value


@login_required
def event_attendees_view(request, event_id):
    """Who is coming — the list an organiser works from on the day."""
    event = get_object_or_404(
        Event.objects.for_actor(request.actor).select_related("dojo"), pk=event_id
    )
    rsvps = list(
        EventRsvp.objects.for_actor(request.actor).filter(event=event).order_by("created_at")
    )
    columns = _answer_columns(event, actor=request.actor)
    coming = [r for r in rsvps if r.status == EventRsvp.Status.COMING]

    # ⚠ One query, attached in Python. The reverse relation is scoped and
    # refuses to evaluate from a template without an actor.
    by_rsvp: dict = {}
    for attachment in (
        RsvpAttachment.objects.for_actor(request.actor)
        .filter(rsvp__event=event)
        .select_related("document")
    ):
        by_rsvp.setdefault(attachment.rsvp_id, []).append(attachment)

    return render(
        request,
        "events/attendees.html",
        {
            "event": event,
            "columns": columns,
            "rows": [
                {
                    "rsvp": rsvp,
                    "cells": [_cell(rsvp, column["key"]) for column in columns],
                    "attachments": by_rsvp.get(rsvp.pk, []),
                }
                for rsvp in rsvps
            ],
            "reply_count": len(coming),
            "head_count": sum(r.party_size for r in coming),
            "may_manage": can(
                request.actor,
                Action.ORG_EDIT,
                _organization(request.actor),
                governance_model=GovernanceModel.CENTRAL,
            ),
        },
    )


@login_required
def event_attendees_export_view(request, event_id):
    """The same list as a spreadsheet.

    ⚠ Goes through csv_report_response, which audits the export before releasing
    the file and neutralises leading =, +, - and @ in every cell. That matters
    more here than anywhere else in the product: these values were typed by
    anonymous members of the public, and a cell beginning with "=" is a formula
    that runs when an administrator opens the file in Excel.
    """
    from apps.core.reports import csv_report_response

    event = get_object_or_404(Event.objects.for_actor(request.actor), pk=event_id)
    rsvps = list(
        EventRsvp.objects.for_actor(request.actor).filter(event=event).order_by("created_at")
    )
    columns = _answer_columns(event, actor=request.actor)

    header = [
        str(_("Name")),
        str(_("Email")),
        str(_("Phone")),
        str(_("People")),
        str(_("Status")),
        str(_("Replied")),
        str(_("Notes")),
    ] + [column["label"] for column in columns]

    rows = [
        [
            rsvp.name,
            rsvp.email,
            rsvp.phone,
            rsvp.party_size,
            rsvp.get_status_display(),
            timezone.localtime(rsvp.created_at).strftime("%Y-%m-%d %H:%M"),
            rsvp.note,
        ]
        + [_cell(rsvp, column["key"]) for column in columns]
        for rsvp in rsvps
    ]

    slug = "".join(c if c.isalnum() else "-" for c in event.name).strip("-").lower() or "event"
    return csv_report_response(
        filename=f"{slug}-replies.csv",
        header=header,
        rows=rows,
        actor=request.actor,
    )


# -- building the form --------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def event_form_builder_view(request, event_id):
    """Add and remove the questions on this event's reply form."""
    _require_manage(request.actor)
    event = get_object_or_404(Event.objects.for_actor(request.actor), pk=event_id)
    form = EventFormFieldForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        question = form.save(commit=False)
        question.event = event
        last = (
            EventFormField.objects.for_actor(request.actor)
            .filter(event=event)
            .order_by("-order")
            .first()
        )
        question.order = (last.order + 1) if last else 0
        question.save()
        audit.record_change("create", question, actor=request.actor)
        messages.success(request, _("Question added to the form."))
        return redirect("event-form-builder", event_id=event.pk)

    return render(
        request,
        "events/form_builder.html",
        {
            "event": event,
            "form": form,
            "questions": list(
                EventFormField.objects.for_actor(request.actor)
                .filter(event=event)
                .order_by("order", "created_at")
            ),
            "public_url": request.build_absolute_uri(_public_path(event)),
        },
    )


@login_required
@require_POST
def event_form_field_delete_view(request, event_id, field_id):
    """⚠ Removes the question, never the answers.

    Replies already given keep what they said — the attendee list recovers the
    column from the answers themselves. Deleting a question is a change to what
    is asked next, not permission to rewrite what people already told you.
    """
    _require_manage(request.actor)
    event = get_object_or_404(Event.objects.for_actor(request.actor), pk=event_id)
    question = get_object_or_404(
        EventFormField.objects.for_actor(request.actor).filter(event=event), pk=field_id
    )
    question.delete()
    messages.success(request, _("Question removed from the form."))
    return redirect("event-form-builder", event_id=event.pk)


# -- serving the pictures -----------------------------------------------------


def _image_response(document, payload: bytes):
    """⚠ inline, because it is an <img> — safe only because validate_upload
    re-encoded it, so what is stored is genuinely the image type it claims. The
    sandbox CSP below is the belt to that braces."""
    response = HttpResponse(payload, content_type=document.content_type)
    response["Content-Disposition"] = "inline"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    return response


def event_public_image_view(request, token, which):
    """The poster and the payment QR, for whoever holds the invitation link.

    ⚠ The only documents in the product served without a session, and they are
    reachable only through the event's own secret token — the same key that
    opens the invitation itself.
    """
    # ⚠ An explicit allowlist, not an if/else. Falling through to the QR for any
    # unrecognised value would make the route answer to names it never had.
    if which not in ("image", "payment_qr"):
        raise Http404("No such image.")

    event = _published_event(token)
    document = event.image if which == "image" else event.payment_qr
    # ⚠ The kind check is what stops this becoming a way to read any document at
    # all: attaching a student photograph to an event must not make it publicly
    # fetchable.
    if document is None or document.kind != Document.Kind.EVENT_IMAGE:
        raise Http404("No such image.")

    return _image_response(document, read_public_document(document))


@login_required
def rsvp_attachment_view(request, event_id, attachment_id):
    """A file somebody attached to their reply. Staff only.

    ⚠ Goes through open_document, which authorises against ``may_read`` and
    audits the read — including a refused one. For EVENT_ATTACHMENT that means
    somebody who can administer the organisation, and nobody else: not the
    person who uploaded it, and not anybody else holding the invitation link.
    """
    from apps.core.documents import open_document

    event = get_object_or_404(Event.objects.for_actor(request.actor), pk=event_id)
    attachment = get_object_or_404(
        RsvpAttachment.objects.for_actor(request.actor)
        .filter(rsvp__event=event)
        .select_related("document"),
        pk=attachment_id,
    )
    payload = open_document(
        request.actor,
        attachment.document,
        governance_model=event.organization.governance_model or GovernanceModel.CENTRAL,
    )
    response = _image_response(attachment.document, payload)
    # ⚠ attachment, not inline: this one is a stranger's file. A PDF rendered
    # in-page would run its own JavaScript in our origin.
    response["Content-Disposition"] = "attachment"
    response["Cache-Control"] = "private, no-store"
    return response
