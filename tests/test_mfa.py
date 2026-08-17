"""Mandatory and optional TOTP MFA — TODO 0.6.2/0.6.3, SEC §2.1."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core import audit
from apps.core.scoping import allow_unscoped
from apps.identity import mfa
from apps.identity.mfa import current_totp, ensure_credential, generate_recovery_codes
from apps.identity.models import (
    MfaCredential,
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _enforce_mfa(settings):
    """⚠ This module tests MFA, so it must run with MFA as it ships.

    ``config/settings/test.py`` turns enforcement off so that every *other*
    test's admin login is not routed through an enrolment screen. That is
    convenient and it is also why these tests passed for the wrong reason for a
    while: the login view checked ``user_requires_mfa`` directly and ignored the
    setting, so enforcement here was accidental rather than configured. Now the
    predicate honours the switch and this fixture states the assumption out loud.
    """
    settings.MFA_ENFORCEMENT_ENABLED = True


PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


def make_user(*, role=Role.ORG_ADMIN, financial=False, export=False, slug="mfa-org"):
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="MFA Org", slug=slug)
        person = Person.objects.create(organization=org, given_name="Mina", family_name="Lee")
        assignment = RoleAssignment.objects.create(
            organization=org,
            person=person,
            role=role,
            scope_type=ScopeType.ORG,
            can_view_financials=financial,
            can_export_pii=export,
        )
        user = User.objects.create_user(
            email=f"{slug}@example.com", password=PASSWORD, person=person
        )
    return user, assignment


def password_login(client, user):
    return client.post(reverse("login"), {"email": user.email, "password": PASSWORD})


def confirmed_credential(user):
    credential = ensure_credential(user)
    credential.confirmed_at = timezone.now()
    _, credential.recovery_code_hashes = generate_recovery_codes()
    credential.save(update_fields=["confirmed_at", "recovery_code_hashes", "updated_at"])
    return credential


def test_admin_password_starts_setup_without_authenticating():
    user, _ = make_user()
    client = Client()

    response = password_login(client, user)

    assert response.status_code == 302
    assert response.url == reverse("mfa-setup")
    assert "_auth_user_id" not in client.session
    assert get_unscoped_credential(user).confirmed_at is None


def get_unscoped_credential(user):
    with allow_unscoped("test assertion"):
        return MfaCredential.objects.get(user=user)


def test_totp_seed_is_encrypted_in_the_database():
    user, _ = make_user()
    credential = ensure_credential(user)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT totp_secret FROM identity_mfacredential WHERE id = %s",
            [str(credential.pk).replace("-", "")],
        )
        stored = cursor.fetchone()[0]

    assert stored != credential.totp_secret
    assert credential.totp_secret not in stored


def test_setup_confirms_totp_and_shows_recovery_codes_only_once(monkeypatch):
    monkeypatch.setattr(mfa.time, "time", lambda: 1_800_000_000)
    user, _ = make_user()
    client = Client()
    password_login(client, user)
    credential = get_unscoped_credential(user)

    response = client.post(reverse("mfa-setup"), {"code": current_totp(credential.totp_secret)})

    assert response.status_code == 302
    assert response.url == reverse("mfa-recovery-codes")
    assert client.session["_auth_user_id"] == str(user.pk)
    credential.refresh_from_db()
    shown_codes = list(client.session["_dojomaster_mfa_recovery_display"])
    assert len(shown_codes) == 10
    assert not any(code in credential.recovery_code_hashes for code in shown_codes)

    first = client.get(reverse("mfa-recovery-codes"))
    second = client.get(reverse("mfa-recovery-codes"))
    assert first.status_code == 200
    assert shown_codes[0].encode() in first.content
    assert second.status_code == 302
    assert shown_codes[0].encode() not in second.content


def test_confirmed_admin_must_complete_challenge(monkeypatch):
    monkeypatch.setattr(mfa.time, "time", lambda: 1_800_000_000)
    user, _ = make_user()
    credential = confirmed_credential(user)
    client = Client()

    first = password_login(client, user)
    assert first.url == reverse("mfa-challenge")
    assert "_auth_user_id" not in client.session

    response = client.post(reverse("mfa-challenge"), {"code": current_totp(credential.totp_secret)})
    assert response.status_code == 302
    assert response.url == reverse("today")
    assert client.session["_auth_user_id"] == str(user.pk)


def test_same_totp_time_step_cannot_be_replayed(monkeypatch):
    monkeypatch.setattr(mfa.time, "time", lambda: 1_800_000_000)
    user, _ = make_user()
    credential = confirmed_credential(user)
    code = current_totp(credential.totp_secret)
    client = Client()

    password_login(client, user)
    assert client.post(reverse("mfa-challenge"), {"code": code}).status_code == 302
    client.post(reverse("logout"))
    password_login(client, user)

    replay = client.post(reverse("mfa-challenge"), {"code": code})
    assert replay.status_code == 401
    assert "_auth_user_id" not in client.session


def test_recovery_code_is_stored_hashed_and_consumed_once():
    user, _ = make_user()
    credential = confirmed_credential(user)
    code, digests = generate_recovery_codes()
    credential.recovery_code_hashes = digests
    credential.save(update_fields=["recovery_code_hashes", "updated_at"])
    client = Client()

    password_login(client, user)
    assert client.post(reverse("mfa-challenge"), {"code": code[0]}).status_code == 302
    client.post(reverse("logout"))
    password_login(client, user)
    reused = client.post(reverse("mfa-challenge"), {"code": code[0]})

    assert reused.status_code == 401
    credential.refresh_from_db()
    assert len(credential.recovery_code_hashes) == 9


@pytest.mark.parametrize("flag", ["financial", "export"])
def test_sensitive_permission_flags_make_mfa_mandatory(flag):
    kwargs = {flag: True}
    user, _ = make_user(role=Role.INSTRUCTOR, slug=f"mfa-{flag}", **kwargs)

    response = password_login(Client(), user)

    assert response.url == reverse("mfa-setup")


def test_ordinary_instructor_can_opt_in_but_is_not_forced():
    user, _ = make_user(role=Role.INSTRUCTOR)

    response = password_login(Client(), user)

    assert response.status_code == 302
    assert response.url == reverse("today")


def test_mfa_failures_are_rate_limited():
    user, _ = make_user()
    confirmed_credential(user)
    client = Client()
    password_login(client, user)

    for _ in range(5):
        assert client.post(reverse("mfa-challenge"), {"code": "000000"}).status_code == 401
    assert client.post(reverse("mfa-challenge"), {"code": "000000"}).status_code == 429


def test_privilege_promotion_forces_step_up_on_an_existing_session():
    user, assignment = make_user(role=Role.INSTRUCTOR)
    client = Client()
    assert password_login(client, user).url == reverse("today")

    assignment.role = Role.ORG_ADMIN
    assignment.save(update_fields=["role", "updated_at"])
    response = client.get(reverse("today"))

    assert response.status_code == 302
    assert reverse("mfa-setup") in response.url
    assert client.get(response.url).status_code == 200


def test_optional_enrollment_requires_the_current_password():
    user, _ = make_user(role=Role.INSTRUCTOR)
    client = Client()
    password_login(client, user)

    refused = client.post(reverse("mfa-setup"), {"action": "start", "password": "wrong"})
    accepted = client.post(
        reverse("mfa-setup"),
        {"action": "start", "password": PASSWORD},
    )

    assert refused.status_code == 401
    assert accepted.status_code == 302
    assert get_unscoped_credential(user).confirmed_at is None


def test_pending_second_factor_expires():
    user, _ = make_user()
    client = Client()
    password_login(client, user)
    session = client.session
    session["_dojomaster_pending_mfa_started"] -= 3600
    session.save()

    response = client.get(reverse("mfa-challenge"))

    assert response.status_code == 302
    assert response.url == reverse("login")
    assert "_auth_user_id" not in client.session


def test_credential_rejects_a_user_from_another_organisation():
    user, _ = make_user()
    other = Organization.objects.create(name="Other", slug="other")
    credential = MfaCredential(
        organization=other,
        user=user,
        totp_secret=mfa.generate_totp_secret(),
    )

    with pytest.raises(ValidationError):
        credential.save()


def test_audit_snapshot_never_contains_mfa_material():
    user, _ = make_user()
    credential = ensure_credential(user)

    snapshot = audit.snapshot(credential)

    assert "totp_secret" not in snapshot
    assert "recovery_code_hashes" not in snapshot
