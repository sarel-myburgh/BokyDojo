"""Sign-in, sign-out and session lifetime — TODO 0.6.4/0.6.5, SEC §2.1."""

from __future__ import annotations

import re

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.core.sessions import LAST_SEEN_KEY, STARTED_AT_KEY
from apps.identity.models import Organization, Person, Role, RoleAssignment, ScopeType, User

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


def _without_csrf(response) -> str:
    """Response body with the per-render CSRF token removed."""
    return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", response.content.decode())


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def instructor_user():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Test Org", slug="test-org")
        person = Person.objects.create(
            organization=org, given_name="Takeshi", family_name="Yamada"
        )
        RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
        return User.objects.create_user(
            email="takeshi@example.com", password=PASSWORD, person=person
        )


# -- signing in ---------------------------------------------------------------


def test_login_page_renders(client):
    response = client.get(reverse("login"))
    assert response.status_code == 200
    assert b"password" in response.content.lower()


def test_correct_credentials_sign_in(client, instructor_user):
    response = client.post(
        reverse("login"), {"email": "takeshi@example.com", "password": PASSWORD}
    )

    assert response.status_code == 302
    assert response.url == reverse("today")
    assert client.session["_auth_user_id"] == str(instructor_user.pk)


def test_email_is_case_insensitive(client, instructor_user):
    response = client.post(
        reverse("login"), {"email": "Takeshi@Example.COM", "password": PASSWORD}
    )
    assert response.status_code == 302


def test_successful_login_stamps_the_session_clocks(client, instructor_user):
    client.post(reverse("login"), {"email": "takeshi@example.com", "password": PASSWORD})

    assert STARTED_AT_KEY in client.session
    assert LAST_SEEN_KEY in client.session


def test_successful_login_is_audited(client, instructor_user):
    client.post(reverse("login"), {"email": "takeshi@example.com", "password": PASSWORD})

    entry = AuditLog.objects.filter(action="login").first()
    assert entry is not None
    assert entry.actor_label == "takeshi@example.com"


# -- failure is uniform (no enumeration) --------------------------------------


def test_wrong_password_is_refused(client, instructor_user):
    response = client.post(
        reverse("login"), {"email": "takeshi@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert "_auth_user_id" not in client.session


def test_unknown_email_gives_the_same_answer_as_a_wrong_password(client, instructor_user):
    wrong_password = client.post(
        reverse("login"), {"email": "takeshi@example.com", "password": "wrong-password"}
    )
    unknown_user = client.post(
        reverse("login"), {"email": "nobody@example.com", "password": "wrong-password"}
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    # The rendered body must not distinguish the two cases. The CSRF token is
    # masked afresh on every render, so it is normalised out before comparing.
    assert _without_csrf(wrong_password) == _without_csrf(unknown_user)


def test_inactive_user_cannot_sign_in(client, instructor_user):
    instructor_user.is_active = False
    instructor_user.save(update_fields=["is_active"])

    response = client.post(
        reverse("login"), {"email": "takeshi@example.com", "password": PASSWORD}
    )

    assert response.status_code == 401
    assert "_auth_user_id" not in client.session


def test_failed_login_is_audited(client, instructor_user):
    client.post(reverse("login"), {"email": "takeshi@example.com", "password": "nope"})
    assert AuditLog.objects.filter(action="login_failed").exists()


# -- the throttle is actually wired (the 0.6.5 debt) --------------------------


def test_repeated_failures_lock_the_account_out(client, instructor_user):
    for _ in range(5):
        response = client.post(
            reverse("login"), {"email": "takeshi@example.com", "password": "nope"}
        )
        assert response.status_code == 401

    locked = client.post(
        reverse("login"), {"email": "takeshi@example.com", "password": "nope"}
    )
    assert locked.status_code == 429


def test_lockout_applies_even_to_the_correct_password(client, instructor_user):
    """Otherwise an attacker learns the password is right from the response."""
    for _ in range(5):
        client.post(reverse("login"), {"email": "takeshi@example.com", "password": "nope"})

    response = client.post(
        reverse("login"), {"email": "takeshi@example.com", "password": PASSWORD}
    )

    assert response.status_code == 429
    assert "_auth_user_id" not in client.session


def test_a_successful_login_clears_the_counter(client, instructor_user):
    for _ in range(3):
        client.post(reverse("login"), {"email": "takeshi@example.com", "password": "nope"})

    client.post(reverse("login"), {"email": "takeshi@example.com", "password": PASSWORD})
    client.post(reverse("logout"))

    for _ in range(3):
        response = client.post(
            reverse("login"), {"email": "takeshi@example.com", "password": "nope"}
        )
        assert response.status_code == 401, "the earlier failures should not still count"


def test_throttle_rejection_is_audited(client, instructor_user):
    for _ in range(6):
        client.post(reverse("login"), {"email": "takeshi@example.com", "password": "nope"})

    assert AuditLog.objects.filter(action="login_failed", note="rejected by throttle").exists()


# -- redirects ----------------------------------------------------------------


def test_next_parameter_is_honoured_when_local(client, instructor_user):
    response = client.post(
        reverse("login"),
        {"email": "takeshi@example.com", "password": PASSWORD, "next": "/reports/attendance/"},
    )
    assert response.url == "/reports/attendance/"


@pytest.mark.parametrize(
    "hostile",
    ["https://evil.example.com/", "//evil.example.com/", "http://evil.example.com"],
)
def test_offsite_next_is_ignored(client, instructor_user, hostile):
    """An open redirect on the login page is a phishing accelerator."""
    response = client.post(
        reverse("login"),
        {"email": "takeshi@example.com", "password": PASSWORD, "next": hostile},
    )
    assert response.url == reverse("today")


def test_signed_in_user_visiting_login_is_sent_onward(client, instructor_user):
    client.force_login(instructor_user)
    response = client.get(reverse("login"))
    assert response.status_code == 302


# -- signing out --------------------------------------------------------------


def test_logout_clears_the_session(client, instructor_user):
    client.force_login(instructor_user)

    response = client.post(reverse("logout"))

    assert response.status_code == 302
    assert "_auth_user_id" not in client.session


def test_logout_refuses_get(client, instructor_user):
    """A GET logout can be fired by any <img> tag on any page."""
    client.force_login(instructor_user)
    response = client.get(reverse("logout"))
    assert response.status_code == 405
    assert "_auth_user_id" in client.session


def test_logout_is_audited(client, instructor_user):
    client.force_login(instructor_user)
    client.post(reverse("logout"))
    assert AuditLog.objects.filter(action="logout").exists()


# -- session lifetime (TODO 0.6.4) -------------------------------------------


def test_idle_session_is_signed_out(client, instructor_user, settings):
    settings.SESSION_IDLE_TIMEOUT_SECONDS = 1
    client.post(reverse("login"), {"email": "takeshi@example.com", "password": PASSWORD})

    session = client.session
    session[LAST_SEEN_KEY] = session[LAST_SEEN_KEY] - 3600
    session.save()

    response = client.get(reverse("today"))

    assert response.status_code == 302
    assert reverse("login") in response.url
    assert "_auth_user_id" not in client.session


def test_absolute_cap_signs_out_even_an_active_session(client, instructor_user, settings):
    settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS = 60
    client.post(reverse("login"), {"email": "takeshi@example.com", "password": PASSWORD})

    session = client.session
    session[STARTED_AT_KEY] = session[STARTED_AT_KEY] - 7200
    session[LAST_SEEN_KEY] = session[LAST_SEEN_KEY]  # still active
    session.save()

    response = client.get(reverse("today"))

    assert response.status_code == 302
    assert "_auth_user_id" not in client.session


def test_active_session_survives(client, instructor_user):
    client.post(reverse("login"), {"email": "takeshi@example.com", "password": PASSWORD})
    response = client.get(reverse("today"))
    assert response.status_code == 200


def test_anonymous_user_is_sent_to_login(client):
    response = Client().get(reverse("today"))
    assert response.status_code == 302
    assert reverse("login") in response.url
