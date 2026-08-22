"""Events and the public RSVP page — plan §3.

⚠ The invitation is the only page in BokyDojo an anonymous stranger can open, so
most of what follows is about what that page will *not* do: reach a member,
reveal an unpublished event, be found without its token, or accept unlimited
posts.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.scoping import allow_unscoped
from apps.events.models import Event, EventRsvp, new_public_token
from apps.identity.models import (
    Dojo,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _clear_throttle():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def world():
    with allow_unscoped("event test setup"):
        org = Organization.objects.create(name="Shimbukai", slug="shimbukai")
        other = Organization.objects.create(name="Elsewhere", slug="elsewhere")
        dojo = Dojo.objects.create(
            organization=org, name="Sen Sok", slug="sen-sok", timezone="Asia/Phnom_Penh"
        )
        boss = Person.objects.create(organization=org, given_name="Ops", family_name="Admin")
        RoleAssignment.objects.create(
            organization=org, person=boss, role=Role.ORG_ADMIN, scope_type=ScopeType.ORG
        )
        boss_user = User.objects.create_user("ops@example.com", PASSWORD, person=boss)

        teacher = Person.objects.create(organization=org, given_name="Mei", family_name="Kato")
        RoleAssignment.objects.create(
            organization=org,
            person=teacher,
            role=Role.INSTRUCTOR,
            scope_type=ScopeType.DOJO,
            dojo=dojo,
        )
        teacher_user = User.objects.create_user("mei@example.com", PASSWORD, person=teacher)

        event = Event.objects.create(
            organization=org,
            dojo=dojo,
            name="Autumn Grading",
            kind=Event.Kind.GRADING,
            starts_at=timezone.now() + datetime.timedelta(days=14),
            location_name="Sen Sok Hall",
            address="12 Street 1986, Phnom Penh",
            price_minor_units=1500,
            price_currency="USD",
            is_published=True,
        )
        elsewhere_event = Event.objects.create(
            organization=other,
            name="Someone Else's Event",
            starts_at=timezone.now() + datetime.timedelta(days=3),
            is_published=True,
        )
    return {
        "org": org,
        "other": other,
        "dojo": dojo,
        "boss_user": boss_user,
        "teacher_user": teacher_user,
        "teacher": teacher,
        "event": event,
        "elsewhere_event": elsewhere_event,
    }


def public_url(event):
    return reverse("event-public", args=[event.public_token])


# -- the public page: what it will not do -------------------------------------


def test_an_unpublished_event_is_not_reachable_even_with_the_token(client, world):
    """⚠ Publishing is the deliberate act. Holding the link is not enough before
    somebody has decided the event is ready to be seen."""
    with allow_unscoped("test"):
        world["event"].is_published = False
        world["event"].save()

    assert client.get(public_url(world["event"])).status_code == 404


def test_a_wrong_token_is_a_404_and_not_a_hint(client, world):
    response = client.get(reverse("event-public", args=[new_public_token()]))

    assert response.status_code == 404


def test_the_event_cannot_be_reached_by_its_id(client, world):
    """⚠ The id is a UUID that appears in staff URLs and in audit logs. Only the
    token opens the public page."""
    response = client.get(reverse("event-public", args=[str(world["event"].pk)]))

    assert response.status_code == 404


def test_the_public_page_names_nobody_from_the_database(client, world):
    """⚠ The containment claim, checked rather than asserted in a comment.

    Everything rendered comes off the Event row. No member, instructor or
    student name may appear on a page a stranger can open.
    """
    body = client.get(public_url(world["event"])).content.decode()

    assert "Autumn Grading" in body
    for leaked in ("Mei", "Kato", "ops@example.com", "mei@example.com", "Ops", "Admin"):
        assert leaked not in body, f"public page leaked {leaked!r}"


def test_a_private_event_asks_search_engines_to_stay_away(client, world):
    body = client.get(public_url(world["event"])).content.decode()

    assert 'name="robots"' in body
    assert "noindex" in body


def test_a_public_event_is_allowed_to_be_indexed(client, world):
    with allow_unscoped("test"):
        world["event"].visibility = Event.Visibility.PUBLIC
        world["event"].save()

    body = client.get(public_url(world["event"])).content.decode()

    assert "noindex" not in body


def test_the_page_sends_no_referrer_so_the_token_does_not_leak_to_google(client, world):
    """⚠ The token is in the URL. Clicking through to the map would otherwise
    hand the whole secret address to Google in the Referer header."""
    body = client.get(public_url(world["event"])).content.decode()

    assert 'name="referrer"' in body
    assert "no-referrer" in body


def test_the_public_page_offers_no_way_into_the_application(client, world):
    """⚠ No navigation, no account menu, nowhere to go. It extends the bare
    base template rather than the signed-in shell."""
    body = client.get(public_url(world["event"])).content.decode()

    for route in ("today", "student-list", "org-settings", "login"):
        assert reverse(route) not in body, f"public page links to {route}"


# -- the map ------------------------------------------------------------------


def test_the_map_is_a_link_and_never_an_embed(client, world):
    """⚠ An embedded Google map would load their script into a page whose CSP is
    deliberately closed, and report every visitor to Google."""
    body = client.get(public_url(world["event"])).content.decode()

    assert "google.com/maps" in body
    assert "<iframe" not in body
    assert "maps.googleapis.com" not in body


def test_the_pin_uses_coordinates_when_they_are_given(world):
    with allow_unscoped("test"):
        world["event"].latitude = 11.556400
        world["event"].longitude = 104.928200
        world["event"].save()

    assert "11.5564,104.9282" in world["event"].map_url


def test_the_pin_falls_back_to_the_address(world):
    assert "Street" in world["event"].map_url.replace("%20", " ").replace("+", " ")


def test_one_coordinate_without_the_other_is_refused(world):
    """A lone latitude points at the prime meridian — confidently wrong."""
    from django.core.exceptions import ValidationError

    world["event"].latitude = 11.5564
    world["event"].longitude = None

    with pytest.raises(ValidationError):
        world["event"].clean()


# -- price --------------------------------------------------------------------


def test_the_price_is_displayed_and_no_money_is_taken(client, world):
    body = client.get(public_url(world["event"])).content.decode()

    assert "USD 15.00" in body
    # ⚠ Nothing on this page collects payment details.
    for banned in ("card number", "cvv", "stripe", "payway"):
        assert banned not in body.lower()


def test_a_free_event_says_free(world):
    with allow_unscoped("test"):
        world["event"].price_minor_units = 0

    assert world["event"].price_display == "Free"


# -- replying -----------------------------------------------------------------


def test_somebody_can_reply(client, world):
    response = client.post(
        public_url(world["event"]),
        {"name": "Dara Sok", "email": "dara@example.com", "party_size": 2, "phone": "", "note": ""},
    )

    assert response.status_code == 200
    assert "Thank you" in response.content.decode()
    with allow_unscoped("test"):
        rsvp = EventRsvp.objects.get(event=world["event"])
    assert rsvp.name == "Dara Sok"
    assert rsvp.party_size == 2


def test_a_reply_needs_a_way_to_reach_them(client, world):
    client.post(
        public_url(world["event"]),
        {"name": "No Contact", "email": "", "phone": "", "party_size": 1, "note": ""},
    )

    with allow_unscoped("test"):
        assert not EventRsvp.objects.filter(event=world["event"]).exists()


def test_replies_are_refused_once_they_close(client, world):
    with allow_unscoped("test"):
        world["event"].rsvp_closes_at = timezone.now() - datetime.timedelta(hours=1)
        world["event"].save()

    client.post(
        public_url(world["event"]),
        {"name": "Too Late", "email": "late@example.com", "party_size": 1, "phone": "", "note": ""},
    )

    with allow_unscoped("test"):
        assert not EventRsvp.objects.filter(event=world["event"]).exists()


def test_replying_is_rate_limited(client, world):
    """⚠ The only unauthenticated write in the product. Without a cap it is a
    spam relay and a way to fill the database."""
    url = public_url(world["event"])
    for i in range(12):
        client.post(
            url,
            {
                "name": f"Person {i}",
                "email": f"p{i}@example.com",
                "party_size": 1,
                "phone": "",
                "note": "",
            },
        )

    with allow_unscoped("test"):
        stored = EventRsvp.objects.filter(event=world["event"]).count()

    assert stored < 12, "the form accepted every post — it is not rate limited"


def test_no_ip_address_is_kept_against_a_reply(world):
    """⚠ The throttle needs one for an hour and the cache holds it there.
    Writing it to the database would mean keeping a record of where every parent
    was when they replied, permanently, to stop duplicate form posts."""
    field_names = {f.name for f in EventRsvp._meta.get_fields()}

    assert not field_names & {"ip_address", "ip", "remote_addr", "user_agent"}


# -- staff side ---------------------------------------------------------------


def test_events_from_another_organisation_are_invisible(client, world):
    client.force_login(world["boss_user"])

    body = client.get(reverse("event-list")).content.decode()

    assert "Autumn Grading" in body
    assert "Someone Else's Event" not in body


def test_an_instructor_cannot_create_an_event(client, world):
    client.force_login(world["teacher_user"])

    assert client.get(reverse("event-create")).status_code == 403


def test_an_admin_can_create_an_event(client, world):
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("event-create"),
        {
            "name": "Winter Competition",
            "kind": Event.Kind.COMPETITION,
            "dojo": "",
            "summary": "",
            "details": "",
            "starts_at": "2027-01-15T09:00",
            "ends_at": "",
            "location_name": "Olympic Stadium",
            "address": "",
            "latitude": "",
            "longitude": "",
            "price": "25",
            "price_currency": "USD",
            "payment_note": "Pay at the door",
            "capacity": 0,
            "rsvp_closes_at": "",
            "visibility": Event.Visibility.PUBLIC,
        },
    )

    assert response.status_code == 302
    with allow_unscoped("test"):
        created = Event.objects.get(name="Winter Competition")
    assert created.price_minor_units == 2500
    # ⚠ Published deliberately, never on creation.
    assert created.is_published is False


def test_a_new_event_is_not_published(world):
    with allow_unscoped("test"):
        event = Event.objects.create(
            organization=world["org"],
            name="Draft",
            starts_at=timezone.now() + datetime.timedelta(days=1),
        )

    assert event.is_published is False
    assert event.visibility == Event.Visibility.PRIVATE


def test_issuing_a_new_link_kills_the_old_one(client, world):
    """⚠ The only way to actually revoke a link that went to the wrong people."""
    old_url = public_url(world["event"])
    client.force_login(world["boss_user"])

    client.post(reverse("event-new-link", args=[world["event"].pk]))

    client.logout()
    assert client.get(old_url).status_code == 404
    with allow_unscoped("test"):
        world["event"].refresh_from_db()
    assert client.get(public_url(world["event"])).status_code == 200


def test_unpublishing_takes_the_page_down(client, world):
    client.force_login(world["boss_user"])
    client.post(reverse("event-publish", args=[world["event"].pk]))
    client.logout()

    assert client.get(public_url(world["event"])).status_code == 404


def test_an_instructor_cannot_publish(client, world):
    client.force_login(world["teacher_user"])

    response = client.post(reverse("event-publish", args=[world["event"].pk]))

    assert response.status_code == 403


def test_staff_can_see_and_delete_a_reply(client, world):
    with allow_unscoped("test"):
        rsvp = EventRsvp.objects.create(
            event=world["event"], name="Dara Sok", email="dara@example.com"
        )
    client.force_login(world["boss_user"])

    body = client.get(reverse("event-detail", args=[world["event"].pk])).content.decode()
    assert "Dara Sok" in body

    client.post(reverse("rsvp-delete", args=[world["event"].pk, rsvp.pk]))
    with allow_unscoped("test"):
        assert not EventRsvp.objects.filter(pk=rsvp.pk).exists()


def test_the_token_is_long_enough_to_be_a_secret():
    """⚠ It is the whole authorisation for a private event."""
    tokens = {new_public_token() for _ in range(200)}

    assert len(tokens) == 200
    assert all(len(t) >= 30 for t in tokens)
