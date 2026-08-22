"""Events, and the invitation a stranger can open — plan §3."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.vary import vary_on_cookie

from apps.core import audit
from apps.core.throttle import RSVP_POLICY, Throttled, enforce, register_failure
from apps.identity.models import GovernanceModel, Organization
from apps.identity.permissions import Action, PermissionDenied, can

from .forms import EventForm, RsvpForm
from .models import Event, EventRsvp
from .public_access import published_event_by_token, save_public_rsvp


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
    form = EventForm(request.POST or None, actor=request.actor, organization=organization)

    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            event = form.save()
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
        request.POST or None, instance=event, actor=request.actor, organization=organization
    )

    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            event = form.save()
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
    form = RsvpForm()
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
                    "form": RsvpForm(),
                    "throttled": True,
                    "rsvp_open": event.rsvps_are_open,
                },
                status=429,
            )

        form = RsvpForm(request.POST)
        if not event.rsvps_are_open:
            form.add_error(None, _("Replies for this event are closed."))
        elif form.is_valid():
            rsvp = form.save(commit=False)
            rsvp.event = event
            try:
                rsvp.full_clean(validate_unique=False, validate_constraints=False)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                save_public_rsvp(rsvp)
                submitted = True
                form = RsvpForm()

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
