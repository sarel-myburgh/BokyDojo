"""Events and the public RSVP page — plan §3.

⚠ The invitation is the only page in BokyDojo an anonymous stranger can open, so
most of what follows is about what that page will *not* do: reach a member,
reveal an unpublished event, be found without its token, or accept unlimited
posts.
"""

from __future__ import annotations

import datetime
import os

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


def test_the_pin_uses_a_plus_code_when_one_is_given(world):
    """⚠ Plus code wins over coordinates and address — most precise first, and
    it is the thing people in Cambodia actually exchange."""
    with allow_unscoped("test"):
        world["event"].plus_code = "HW4C+8Q Phnom Penh"
        world["event"].latitude = 11.556400
        world["event"].longitude = 104.928200
        world["event"].save()

    assert "HW4C%2B8Q" in world["event"].map_url


def test_the_pin_still_uses_coordinates_when_there_is_no_plus_code(world):
    """⚠ Rows created before Plus Codes existed keep working."""
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
    #
    # ⚠ Matched on word boundaries, not as substrings. The page carries a CSRF
    # token and the event's own 32-character secret, both random — "cvv" turns
    # up inside one often enough to fail a build, which is exactly what it did.
    # A test that fails on a coin toss teaches people to re-run it.
    import re

    for banned in ("card number", "cvv", "stripe", "payway"):
        assert not re.search(rf"\b{banned}\b", body, re.IGNORECASE), (
            f"the public page mentions {banned!r}"
        )


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
            "plus_code": "",
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


# -- custom questions ---------------------------------------------------------


@pytest.fixture
def question(world):
    from apps.events.models import EventFormField

    with allow_unscoped("test"):
        return EventFormField.objects.create(
            event=world["event"],
            label="Current grade",
            kind=EventFormField.Kind.TEXT,
            is_required=True,
            order=0,
        )


def test_a_custom_question_appears_on_the_public_form(client, world, question):
    body = client.get(public_url(world["event"])).content.decode()

    assert "Current grade" in body


def test_a_required_custom_question_is_enforced(client, world, question):
    client.post(
        public_url(world["event"]),
        {"name": "Dara", "email": "d@example.com", "party_size": 1, "phone": "", "note": ""},
    )

    with allow_unscoped("test"):
        assert not EventRsvp.objects.filter(event=world["event"]).exists()


def test_the_answer_is_stored_against_the_reply(client, world, question):
    client.post(
        public_url(world["event"]),
        {
            "name": "Dara",
            "email": "d@example.com",
            "party_size": 1,
            "phone": "",
            "note": "",
            question.field_name: "3rd Kyu",
        },
    )

    with allow_unscoped("test"):
        rsvp = EventRsvp.objects.get(event=world["event"])
    assert rsvp.answers[str(question.pk)]["value"] == "3rd Kyu"
    # ⚠ The label is copied in beside the id so a later rename does not turn
    # this answer into a value nobody can interpret.
    assert rsvp.answers[str(question.pk)]["label"] == "Current grade"


def test_one_events_questions_never_appear_on_another(client, world):
    """⚠ The form is built per request from this event's own questions. A
    class-level field would leak one event's form onto the other's page."""
    from apps.events.models import EventFormField

    with allow_unscoped("test"):
        other = Event.objects.create(
            organization=world["org"],
            name="Other Event",
            starts_at=timezone.now() + datetime.timedelta(days=5),
            is_published=True,
        )
        EventFormField.objects.create(event=other, label="Weight category", order=0)

    body = client.get(public_url(world["event"])).content.decode()

    assert "Weight category" not in body


def test_a_choice_question_offers_only_its_own_options(client, world):
    from apps.events.models import EventFormField

    with allow_unscoped("test"):
        EventFormField.objects.create(
            event=world["event"],
            label="T-shirt size",
            kind=EventFormField.Kind.CHOICE,
            options="Small\nMedium\nLarge",
            order=1,
        )

    body = client.get(public_url(world["event"])).content.decode()

    assert "T-shirt size" in body
    for size in ("Small", "Medium", "Large"):
        assert size in body


def test_a_choice_question_needs_at_least_two_options(world):
    from django.core.exceptions import ValidationError

    from apps.events.models import EventFormField

    field = EventFormField(
        event=world["event"], label="Pick", kind=EventFormField.Kind.CHOICE, options="Only one"
    )

    with pytest.raises(ValidationError):
        field.clean()


def test_an_instructor_cannot_change_the_form(client, world):
    client.force_login(world["teacher_user"])

    assert client.get(reverse("event-form-builder", args=[world["event"].pk])).status_code == 403


def test_an_admin_can_add_a_question(client, world):
    from apps.events.models import EventFormField

    client.force_login(world["boss_user"])

    client.post(
        reverse("event-form-builder", args=[world["event"].pk]),
        {"label": "Any allergies?", "kind": "paragraph", "help_text": "", "options": ""},
    )

    with allow_unscoped("test"):
        assert EventFormField.objects.filter(event=world["event"], label="Any allergies?").exists()


def test_removing_a_question_keeps_answers_already_given(client, world, question):
    """⚠ Deleting a question changes what is asked next. It is not permission to
    rewrite what people already told you."""
    client.post(
        public_url(world["event"]),
        {
            "name": "Dara",
            "email": "d@example.com",
            "party_size": 1,
            "phone": "",
            "note": "",
            question.field_name: "3rd Kyu",
        },
    )
    client.force_login(world["boss_user"])

    client.post(reverse("event-form-field-delete", args=[world["event"].pk, question.pk]))

    body = client.get(reverse("event-attendees", args=[world["event"].pk])).content.decode()
    assert "3rd Kyu" in body, "an answer disappeared when its question was removed"
    assert "no longer asked" in body


# -- who is coming ------------------------------------------------------------


def test_the_attendee_list_counts_replies_and_heads(client, world):
    """⚠ Two different numbers. One family replying for four is one line and
    four people, and the door needs the second."""
    with allow_unscoped("test"):
        EventRsvp.objects.create(event=world["event"], name="A", email="a@e.com", party_size=4)
        EventRsvp.objects.create(event=world["event"], name="B", email="b@e.com", party_size=1)
    client.force_login(world["boss_user"])

    response = client.get(reverse("event-attendees", args=[world["event"].pk]))

    assert response.context["reply_count"] == 2
    assert response.context["head_count"] == 5


def test_the_attendee_list_shows_custom_answers(client, world, question):
    with allow_unscoped("test"):
        EventRsvp.objects.create(
            event=world["event"],
            name="Dara",
            email="d@e.com",
            answers={str(question.pk): {"label": "Current grade", "value": "3rd Kyu"}},
        )
    client.force_login(world["boss_user"])

    body = client.get(reverse("event-attendees", args=[world["event"].pk])).content.decode()

    assert "Current grade" in body
    assert "3rd Kyu" in body


def test_the_attendee_list_is_scoped_to_the_organisation(client, world):
    client.force_login(world["boss_user"])

    response = client.get(reverse("event-attendees", args=[world["elsewhere_event"].pk]))

    assert response.status_code == 404


# -- the spreadsheet ----------------------------------------------------------


def test_the_export_downloads_as_a_spreadsheet(client, world, question):
    with allow_unscoped("test"):
        EventRsvp.objects.create(
            event=world["event"],
            name="Dara Sok",
            email="dara@example.com",
            party_size=2,
            answers={str(question.pk): {"label": "Current grade", "value": "3rd Kyu"}},
        )
    client.force_login(world["boss_user"])

    response = client.get(reverse("event-attendees-export", args=[world["event"].pk]))
    body = response.content.decode()

    assert response.status_code == 200
    assert "text/csv" in response["Content-Type"]
    assert "attachment" in response["Content-Disposition"]
    assert "Dara Sok" in body
    assert "Current grade" in body
    assert "3rd Kyu" in body


def test_the_export_neutralises_spreadsheet_formulas(client, world, question):
    """⚠ These values were typed by anonymous members of the public. A cell
    starting with "=" is a formula that runs when an administrator opens the
    file in Excel — this is the one export in the product fed by strangers."""
    with allow_unscoped("test"):
        EventRsvp.objects.create(
            event=world["event"],
            name="=cmd|'/c calc'!A1",
            email="x@example.com",
            answers={str(question.pk): {"label": "Current grade", "value": "+1+1"}},
        )
    client.force_login(world["boss_user"])

    body = client.get(reverse("event-attendees-export", args=[world["event"].pk])).content.decode()

    assert "\"'=cmd" in body or "'=cmd" in body
    assert "'+1+1" in body
    for line in body.splitlines()[1:]:
        for cell in line.split(","):
            stripped = cell.strip().strip('"')
            assert not stripped.startswith(("=", "+", "@")), f"unescaped formula cell: {cell!r}"


def test_the_export_is_audited(client, world):
    """⚠ csv_report_response writes the audit entry before releasing the file,
    and refuses the download if that write fails. A list of people is leaving
    the system; who took it and when is the record that matters afterwards."""
    from apps.core.models import AuditLog

    with allow_unscoped("test"):
        EventRsvp.objects.create(event=world["event"], name="A", email="a@e.com")
        before = AuditLog.objects.count()
    client.force_login(world["boss_user"])

    client.get(reverse("event-attendees-export", args=[world["event"].pk]))

    with allow_unscoped("test"):
        assert AuditLog.objects.count() > before


def test_the_export_cannot_reach_another_organisation(client, world):
    client.force_login(world["boss_user"])

    response = client.get(reverse("event-attendees-export", args=[world["elsewhere_event"].pk]))

    assert response.status_code == 404


# -- plus codes ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["HW4C+8Q Phnom Penh", "hw4c+8q phnom penh", "7P28HW4C+8Q", "6PH58QMF+FX"],
)
def test_a_good_plus_code_is_accepted(value):
    from apps.events.plus_codes import validate_plus_code

    validate_plus_code(value)


@pytest.mark.parametrize(
    "value",
    ["not a code", "HW4C8Q Phnom Penh", "ABCD+EF Somewhere", "HW4C+", "+8Q"],
)
def test_a_bad_plus_code_is_refused(value):
    """⚠ A, E, I, L, O, S, U and 0/1 are not in the alphabet — they are the
    characters people confuse when copying a code off a shopfront."""
    from django.core.exceptions import ValidationError

    from apps.events.plus_codes import validate_plus_code

    with pytest.raises(ValidationError):
        validate_plus_code(value)


def test_a_short_plus_code_needs_its_town(world):
    """⚠ "HW4C+8Q" alone is ambiguous worldwide — it would open a map somewhere
    plausible and wrong."""
    from django.core.exceptions import ValidationError

    from apps.events.plus_codes import validate_plus_code

    with pytest.raises(ValidationError):
        validate_plus_code("HW4C+8Q")


def test_the_plus_code_is_upper_cased_but_the_town_is_left_alone(world):
    from apps.events.plus_codes import normalise

    assert normalise("hw4c+8q phnom penh") == "HW4C+8Q phnom penh"


# -- payment link -------------------------------------------------------------


def test_a_payment_link_shows_on_the_invitation(client, world):
    with allow_unscoped("test"):
        world["event"].payment_url = "https://pay.ababank.com/abc123"
        world["event"].save()

    body = client.get(public_url(world["event"])).content.decode()

    assert "pay.ababank.com/abc123" in body
    assert "Pay with this link" in body


def test_a_payment_link_must_be_http_or_https(client, world):
    """⚠ This value goes straight into an href on a page anybody can open.

    ⚠ Tested with ftp://, not javascript:. Django's URLField already rejects
    javascript: on its own, so that version of this test passed without the
    check ever running — a mutation showed it. URLField *does* accept ftp, so
    ftp is what proves our own restriction is doing the work.
    """
    client.force_login(world["boss_user"])

    response = client.post(
        reverse("event-create"),
        {
            "name": "Hostile",
            "kind": "grading",
            "dojo": "",
            "summary": "",
            "details": "",
            "starts_at": "2027-01-01T09:00",
            "ends_at": "",
            "location_name": "",
            "address": "",
            "plus_code": "",
            "price": "0",
            "price_currency": "USD",
            "payment_note": "",
            "payment_url": "ftp://evil.example.com/pay",
            "capacity": 0,
            "rsvp_closes_at": "",
            "visibility": "private",
        },
    )

    assert response.status_code == 200, "the hostile link was accepted"
    with allow_unscoped("test"):
        assert not Event.objects.filter(name="Hostile").exists()


def test_nothing_here_confirms_a_payment(world):
    """⚠ Worth stating: this is a link, not an integration. Nothing in the model
    records that money arrived, and no screen should imply it did."""
    field_names = {f.name for f in Event._meta.get_fields()}

    assert not field_names & {"is_paid", "paid_at", "payment_status", "transaction_id"}


# -- public file attachments --------------------------------------------------


def an_image(name="proof.png"):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "navy").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@pytest.fixture
def file_question(world, settings, tmp_path):
    from apps.events.models import EventFormField

    settings.MEDIA_ROOT = tmp_path / "media"
    with allow_unscoped("test"):
        return EventFormField.objects.create(
            event=world["event"],
            label="Proof of payment",
            kind=EventFormField.Kind.FILE,
            is_required=False,
            order=0,
        )


def test_a_file_question_puts_an_upload_on_the_public_form(client, world, file_question):
    body = client.get(public_url(world["event"])).content.decode()

    assert "Proof of payment" in body
    assert 'type="file"' in body
    assert 'enctype="multipart/form-data"' in body


def test_there_is_no_upload_unless_an_admin_added_one(client, world):
    """⚠ Off by default. A public file upload is the highest-risk surface in the
    product and it only exists when somebody deliberately asks for one."""
    body = client.get(public_url(world["event"])).content.decode()

    assert 'type="file"' not in body


def test_somebody_can_attach_a_file_to_their_reply(client, world, file_question):
    from apps.events.models import RsvpAttachment

    client.post(
        public_url(world["event"]),
        {
            "name": "Dara",
            "email": "d@example.com",
            "party_size": 1,
            "phone": "",
            "note": "",
            file_question.field_name: an_image(),
        },
    )

    with allow_unscoped("test"):
        attachment = RsvpAttachment.objects.get()
    assert attachment.label == "Proof of payment"
    assert attachment.document.kind == "event_attachment"


def test_an_svg_attachment_is_refused(client, world, file_question):
    """⚠ SVG is a script container that renders as a picture. There is no safe
    way to serve an attacker-supplied one from our own origin."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.events.models import RsvpAttachment

    client.post(
        public_url(world["event"]),
        {
            "name": "Dara",
            "email": "d@example.com",
            "party_size": 1,
            "phone": "",
            "note": "",
            file_question.field_name: SimpleUploadedFile(
                "x.svg",
                b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>",
                content_type="image/svg+xml",
            ),
        },
    )

    with allow_unscoped("test"):
        assert not RsvpAttachment.objects.exists()


def test_a_file_pretending_to_be_an_image_is_refused(client, world, file_question):
    """⚠ The filename and the Content-Type are both attacker-controlled. The
    first bytes decide what the file actually is."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.events.models import RsvpAttachment

    client.post(
        public_url(world["event"]),
        {
            "name": "Dara",
            "email": "d@example.com",
            "party_size": 1,
            "phone": "",
            "note": "",
            file_question.field_name: SimpleUploadedFile(
                "payload.png", b"<?php system($_GET[0]); ?>", content_type="image/png"
            ),
        },
    )

    with allow_unscoped("test"):
        assert not RsvpAttachment.objects.exists()


def test_an_oversized_attachment_is_refused(client, world, file_question, monkeypatch):
    """⚠ Capped far below the authenticated limit: this is a box anybody on the
    internet can post to, and the cap plus the rate limit are together what stop
    it being a way to fill the disk.

    ⚠ The cap is lowered for the test rather than the file made enormous. Two
    earlier attempts tested something else entirely: PNG magic bytes followed by
    zeros were refused as a broken image, and a genuinely huge one was truncated
    in transit and refused as a truncated image. Both passed while the cap did
    nothing, and both were caught by mutating the cap away. Shrinking the limit
    exercises the limit.
    """
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.events.models import RsvpAttachment

    monkeypatch.setattr("apps.core.uploads.MAX_PUBLIC_UPLOAD_BYTES", 8 * 1024)

    buffer = io.BytesIO()
    Image.frombytes("RGB", (200, 200), os.urandom(200 * 200 * 3)).save(buffer, format="PNG")
    assert buffer.tell() > 8 * 1024, "the fixture is not over the lowered cap"
    oversized = SimpleUploadedFile("big.png", buffer.getvalue(), content_type="image/png")

    response = client.post(
        public_url(world["event"]),
        {
            "name": "Dara",
            "email": "d@example.com",
            "party_size": 1,
            "phone": "",
            "note": "",
            file_question.field_name: oversized,
        },
    )

    assert "too big" in response.content.decode(), "no size message was shown"
    with allow_unscoped("test"):
        assert not RsvpAttachment.objects.exists()


def test_an_attachment_within_the_cap_is_accepted(client, world, file_question):
    """⚠ The other half. A cap that refuses everything is not a cap."""
    from apps.events.models import RsvpAttachment

    client.post(
        public_url(world["event"]),
        {
            "name": "Dara",
            "email": "d@example.com",
            "party_size": 1,
            "phone": "",
            "note": "",
            file_question.field_name: an_image(),
        },
    )

    with allow_unscoped("test"):
        assert RsvpAttachment.objects.count() == 1


def test_an_attachment_is_never_served_to_the_public(client, world, file_question):
    """⚠ Not even to the person who uploaded it. A public upload that is
    publicly readable is a file-hosting service with our name on it."""
    from apps.events.models import RsvpAttachment

    client.post(
        public_url(world["event"]),
        {
            "name": "Dara",
            "email": "d@example.com",
            "party_size": 1,
            "phone": "",
            "note": "",
            file_question.field_name: an_image(),
        },
    )
    with allow_unscoped("test"):
        attachment = RsvpAttachment.objects.get()

    client.logout()
    response = client.get(reverse("rsvp-attachment", args=[world["event"].pk, attachment.pk]))

    assert response.status_code in (302, 403), "an anonymous visitor could read the upload"


def test_an_instructor_cannot_read_an_attachment(client, world, file_question):
    """⚠ EVENT_ATTACHMENT is released only to somebody who can administer the
    organisation."""
    from apps.core.documents import may_read
    from apps.core.models import Document
    from apps.identity.actors import actor_for_user

    with allow_unscoped("test"):
        document = Document.objects.create(
            organization=world["org"],
            kind=Document.Kind.EVENT_ATTACHMENT,
            original_filename="proof.png",
            content_type="image/png",
            byte_size=10,
            checksum="x" * 64,
            storage_key="k",
        )

    assert not may_read(actor_for_user(world["teacher_user"]), document, governance_model="central")
    assert may_read(actor_for_user(world["boss_user"]), document, governance_model="central")


def test_staff_can_open_an_attachment(client, world, file_question):
    from apps.events.models import RsvpAttachment

    client.post(
        public_url(world["event"]),
        {
            "name": "Dara",
            "email": "d@example.com",
            "party_size": 1,
            "phone": "",
            "note": "",
            file_question.field_name: an_image(),
        },
    )
    with allow_unscoped("test"):
        attachment = RsvpAttachment.objects.get()

    client.force_login(world["boss_user"])
    response = client.get(reverse("rsvp-attachment", args=[world["event"].pk, attachment.pk]))

    assert response.status_code == 200
    # ⚠ attachment, never inline: a stranger's PDF rendered in-page would run
    # its own JavaScript in our origin.
    assert "attachment" in response["Content-Disposition"]


# -- event poster and payment QR ----------------------------------------------


def test_a_poster_is_served_only_through_the_event_token(client, world, settings, tmp_path):
    from apps.core.documents import store
    from apps.core.models import Document
    from apps.identity.actors import actor_for_user

    settings.MEDIA_ROOT = tmp_path / "media"
    with allow_unscoped("test"):
        document = store(
            an_image("poster.png"),
            organization=world["org"],
            kind=Document.Kind.EVENT_IMAGE,
            actor=actor_for_user(world["boss_user"]),
        )
        world["event"].image = document
        world["event"].save()

    good = client.get(reverse("event-public-image", args=[world["event"].public_token, "image"]))
    wrong = client.get(reverse("event-public-image", args=[new_public_token(), "image"]))

    assert good.status_code == 200
    assert good["Content-Type"] == "image/png"
    assert wrong.status_code == 404


def test_the_image_route_answers_only_to_its_two_names(client, world, settings, tmp_path):
    """⚠ An allowlist, not an if/else — falling through to the QR for any
    unrecognised value would make the route answer to names it never had.

    ⚠ The QR has to actually exist for this to prove anything. Without it the
    fall-through returns 404 for its own reasons and the test passes whether or
    not the allowlist is there — which a mutation demonstrated.
    """
    from apps.core.documents import store
    from apps.core.models import Document
    from apps.identity.actors import actor_for_user

    settings.MEDIA_ROOT = tmp_path / "media"
    with allow_unscoped("test"):
        world["event"].payment_qr = store(
            an_image("qr.png"),
            organization=world["org"],
            kind=Document.Kind.EVENT_IMAGE,
            actor=actor_for_user(world["boss_user"]),
        )
        world["event"].save()

    real = client.get(
        reverse("event-public-image", args=[world["event"].public_token, "payment_qr"])
    )
    made_up = client.get(
        reverse("event-public-image", args=[world["event"].public_token, "anything"])
    )

    assert real.status_code == 200, "the QR should be served under its own name"
    assert made_up.status_code == 404


def test_the_public_image_route_serves_only_event_images(client, world, settings, tmp_path):
    """⚠ The kind check is what stops this route becoming a way to read any
    document at all. Attaching a student photograph to an event must not make it
    publicly fetchable.
    """
    from apps.core.documents import store
    from apps.core.models import Document
    from apps.identity.actors import actor_for_user

    settings.MEDIA_ROOT = tmp_path / "media"
    with allow_unscoped("test"):
        smuggled = store(
            an_image("child.png"),
            organization=world["org"],
            kind=Document.Kind.PHOTO,
            actor=actor_for_user(world["boss_user"]),
        )
        world["event"].image = smuggled
        world["event"].save()

    response = client.get(
        reverse("event-public-image", args=[world["event"].public_token, "image"])
    )

    assert response.status_code == 404, "a non-event document was served publicly"
