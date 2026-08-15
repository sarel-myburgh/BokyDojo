"""Password-reset security behavior — TODO 0.6.6, SEC §2.1."""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.identity.models import Organization, Person, User

pytestmark = pytest.mark.django_db
OLD_PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "new-correct-horse-battery"


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def reset_user():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Reset Org", slug="reset-org")
        person = Person.objects.create(organization=org, given_name="Aiko", family_name="Tan")
        return User.objects.create_user(
            email="aiko@example.com",
            password=OLD_PASSWORD,
            person=person,
        )


def without_csrf(response):
    return re.sub(
        rb'name="csrfmiddlewaretoken" value="[^"]+"',
        b"",
        response.content,
    )


def request_reset(client, email):
    return client.post(reverse("password-reset"), {"email": email})


def reset_path_from_email():
    match = re.search(r"https?://[^/]+(/password-reset/[^/]+/[^/]+/)", mail.outbox[-1].body)
    assert match is not None
    return match.group(1)


def test_known_and_unknown_addresses_get_identical_responses_and_one_email(reset_user):
    known_client = Client()
    unknown_client = Client()

    known = request_reset(known_client, reset_user.email)
    unknown = request_reset(unknown_client, "unknown@example.com")

    assert known.status_code == unknown.status_code == 200
    assert without_csrf(known) == without_csrf(unknown)
    assert len(mail.outbox) == 2
    assert mail.outbox[0].to == [reset_user.email]
    assert mail.outbox[1].to == ["unknown@example.com"]
    assert "/password-reset/" in mail.outbox[0].body
    assert "/password-reset/" not in mail.outbox[1].body


def test_reset_token_changes_password_and_is_single_use(reset_user):
    client = Client()
    request_reset(client, reset_user.email)
    path = reset_path_from_email()

    changed = client.post(
        path,
        {"password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD},
    )
    reused = client.post(
        path,
        {"password": "another-valid-password", "password_confirm": "another-valid-password"},
    )

    assert changed.status_code == 302
    assert changed.url == reverse("password-reset-complete")
    assert reused.status_code == 400
    reset_user.refresh_from_db()
    assert reset_user.check_password(NEW_PASSWORD)
    assert reset_user.last_password_change is not None


def test_expired_reset_token_is_rejected(reset_user, settings):
    settings.PASSWORD_RESET_TIMEOUT = 60
    uid = urlsafe_base64_encode(force_bytes(reset_user.pk))
    timestamp = default_token_generator._num_seconds(default_token_generator._now()) - 61
    token = default_token_generator._make_token_with_timestamp(
        reset_user,
        timestamp,
        default_token_generator.secret,
    )
    path = reverse(
        "password-reset-confirm",
        kwargs={"uidb64": uid, "token": token},
    )

    response = Client().post(
        path,
        {"password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD},
    )

    assert response.status_code == 400
    reset_user.refresh_from_db()
    assert reset_user.check_password(OLD_PASSWORD)


def test_password_reset_requests_are_throttled_without_changing_the_response(reset_user):
    client = Client()

    responses = [request_reset(client, reset_user.email) for _ in range(4)]

    assert all(response.status_code == 200 for response in responses)
    assert len(mail.outbox) == 3
    assert AuditLog.objects.filter(
        action="password_reset_requested",
        note="throttled",
    ).exists()


def test_invalid_uid_and_token_are_generic():
    response = Client().get(
        reverse(
            "password-reset-confirm",
            kwargs={"uidb64": "not-a-user", "token": "not-a-token"},
        )
    )

    assert response.status_code == 200
    assert b"invalid or has expired" in response.content


def test_mismatched_or_weak_password_is_refused(reset_user):
    client = Client()
    request_reset(client, reset_user.email)
    path = reset_path_from_email()

    mismatch = client.post(
        path,
        {"password": NEW_PASSWORD, "password_confirm": "different-password"},
    )
    weak = client.post(path, {"password": "short", "password_confirm": "short"})

    assert mismatch.status_code == 400
    assert weak.status_code == 400
    reset_user.refresh_from_db()
    assert reset_user.check_password(OLD_PASSWORD)
