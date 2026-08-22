"""Gradings, competitions, seminars — and the form people reply on.

⚠ The RSVP page is the only part of BokyDojo a stranger can open. Everything
here is shaped by that:

* An event is **private by default** and has to be published deliberately.
* A private event is reached by an unguessable token, never by its id, and
  carries ``noindex``. A public one gets a readable slug because being findable
  is the point of a public competition.
* The page renders **only fields typed into this model**. Nothing on it walks a
  relation to a student, a member, or an attendance record. That is the whole
  containment story: no query on that page can reach personal data because no
  query on that page touches those tables.
* An RSVP is somebody outside the system handing us their name. It is personal
  data we solicited, so it is minimal, it is deletable, and it is never shown to
  anyone but staff.
"""

from __future__ import annotations

import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


def new_public_token() -> str:
    """⚠ 32 URL-safe characters from secrets — this is the only thing standing
    between a private event and the internet, so it is a real secret and not a
    slug with a number on the end."""
    return secrets.token_urlsafe(24)


class Event(TenantScopedModel):
    tenant_org_path = "organization_id"
    same_organization_fields = ("organization", "dojo")

    class Kind(models.TextChoices):
        GRADING = "grading", _("Grading")
        COMPETITION = "competition", _("Competition")
        SEMINAR = "seminar", _("Seminar or course")
        SOCIAL = "social", _("Social")
        OTHER = "other", _("Other")

    class Visibility(models.TextChoices):
        #: Reachable only with the token, and asks search engines to stay away.
        PRIVATE = "private", _("Anyone with the link")
        #: Reachable at a readable address and indexable.
        PUBLIC = "public", _("Public — anyone can find it")

    organization = models.ForeignKey(
        "identity.Organization", on_delete=models.PROTECT, related_name="events"
    )
    #: Optional: an organisation-wide event belongs to no single dojo.
    dojo = models.ForeignKey(
        "identity.Dojo",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="events",
    )

    name = models.CharField(_("name"), max_length=200)
    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices, default=Kind.GRADING)
    summary = models.CharField(_("summary"), max_length=300, blank=True)
    details = models.TextField(_("details"), blank=True)

    starts_at = models.DateTimeField(_("starts at"))
    ends_at = models.DateTimeField(_("ends at"), null=True, blank=True)

    location_name = models.CharField(_("place"), max_length=200, blank=True)
    address = models.TextField(_("address"), blank=True)
    #: ⚠ Coordinates, not an embedded map. The page links out to Google Maps
    #: rather than framing it: an embed would load Google's script into a page
    #: our CSP deliberately keeps closed, and would report every visitor —
    #: including every parent opening a grading invitation — to Google.
    latitude = models.DecimalField(
        _("latitude"), max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        _("longitude"), max_digits=9, decimal_places=6, null=True, blank=True
    )

    #: ⚠ Displayed, never charged. No card details are taken anywhere in this
    #: app; how to pay is free text so an organisation can say "at the door" or
    #: give bank details.
    price_minor_units = models.PositiveIntegerField(_("price"), default=0)
    price_currency = models.CharField(_("currency"), max_length=3, default="USD")
    payment_note = models.CharField(_("how to pay"), max_length=200, blank=True)

    capacity = models.PositiveSmallIntegerField(
        _("places"), default=0, help_text=_("0 means no limit.")
    )
    rsvp_closes_at = models.DateTimeField(_("replies close"), null=True, blank=True)

    is_published = models.BooleanField(_("published"), default=False)
    visibility = models.CharField(
        _("who can see it"),
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    public_token = models.CharField(
        max_length=64, unique=True, default=new_public_token, editable=False
    )

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        ordering = ("-starts_at",)
        indexes = [
            models.Index(fields=["organization", "starts_at"]),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        if self.ends_at and self.starts_at and self.ends_at < self.starts_at:
            raise ValidationError({"ends_at": _("The end cannot be before the start.")})
        # ⚠ Both or neither. One coordinate alone points at the equator or the
        # prime meridian, which is a confidently wrong pin rather than none.
        if (self.latitude is None) != (self.longitude is None):
            raise ValidationError(
                {"longitude": _("Enter both latitude and longitude, or neither.")}
            )

    @property
    def is_free(self) -> bool:
        return self.price_minor_units == 0

    @property
    def price_display(self) -> str:
        if self.is_free:
            return str(_("Free"))
        return f"{self.price_currency} {self.price_minor_units / 100:.2f}"

    @property
    def map_url(self) -> str:
        """A link out to Google Maps, or "" when there is nothing to point at."""
        if self.latitude is not None and self.longitude is not None:
            return (
                f"https://www.google.com/maps/search/?api=1&query={self.latitude},{self.longitude}"
            )
        target = self.address or self.location_name
        if not target:
            return ""
        from urllib.parse import quote

        return f"https://www.google.com/maps/search/?api=1&query={quote(target)}"

    @property
    def rsvps_are_open(self) -> bool:
        if not self.is_published:
            return False
        if self.rsvp_closes_at and timezone.now() > self.rsvp_closes_at:
            return False
        return timezone.now() < (self.ends_at or self.starts_at)


class EventRsvp(TenantScopedModel):
    """Somebody replying to an invitation.

    ⚠ Personal data volunteered by a member of the public, so: as few fields as
    a reply needs, visible to staff only, and deletable on request. No IP
    address is stored — the throttle needs one for a few minutes and the cache
    holds it there; writing it into the database would mean keeping a record of
    where every parent was when they replied, for ever, to stop duplicate form
    posts.
    """

    tenant_org_path = "event__organization_id"
    same_organization_fields = ("event",)

    class Status(models.TextChoices):
        COMING = "coming", _("Coming")
        CANCELLED = "cancelled", _("Cancelled")

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="rsvps")
    name = models.CharField(_("name"), max_length=200)
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("phone"), max_length=40, blank=True)
    party_size = models.PositiveSmallIntegerField(_("how many people"), default=1)
    note = models.CharField(_("anything we should know"), max_length=500, blank=True)
    status = models.CharField(
        _("status"), max_length=12, choices=Status.choices, default=Status.COMING
    )

    class Meta:
        verbose_name = _("RSVP")
        verbose_name_plural = _("RSVPs")
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"{self.name} — {self.event}"

    def clean(self):
        super().clean()
        if not (self.email or "").strip() and not (self.phone or "").strip():
            raise ValidationError(
                {"email": _("Give an email address or a phone number so we can reach you.")}
            )
        if self.party_size < 1:
            raise ValidationError({"party_size": _("At least one person.")})
