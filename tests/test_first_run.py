"""One-time first-run installation wizard — TODO 0.7.4."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.identity.models import Dojo, GovernanceModel, Organization, Role, RoleAssignment, User
from apps.identity.setup import SETUP_LOCK_KEY

pytestmark = pytest.mark.django_db
PASSWORD = "Renshi-Demo-Password-42!"


def payload(**overrides):
    values = {
        "organization_name": "Renshi Karate Association",
        "organization_slug": "renshi-karate",
        "governance_model": GovernanceModel.CENTRAL,
        "country": "KH",
        "timezone": "Asia/Phnom_Penh",
        "currency": "USD",
        "dojo_name": "Main Dojo",
        "dojo_city": "Phnom Penh",
        "admin_given_name": "Sarel",
        "admin_family_name": "Owner",
        "admin_email": "owner@example.com",
        "admin_password": PASSWORD,
        "admin_password_confirm": PASSWORD,
        "setup_token": "",
    }
    values.update(overrides)
    return values


@pytest.fixture(autouse=True)
def clear_setup_lock():
    cache.delete(SETUP_LOCK_KEY)
    yield
    cache.delete(SETUP_LOCK_KEY)


def test_empty_installation_offers_setup(client):
    login = client.get(reverse("login"))
    setup = client.get(reverse("first-run"))

    assert login.status_code == 200
    assert reverse("first-run").encode() in login.content
    assert setup.status_code == 200
    assert b"Create installation" in setup.content


def test_setup_creates_org_dojo_owner_and_mandatory_admin_role(client, settings):
    # ⚠ Asserts that the very first owner account is forced to enrol MFA, so it
    # has to run with enforcement on. test.py turns it off for everybody else's
    # convenience; without this the assertion silently tests nothing.
    settings.MFA_ENFORCEMENT_ENABLED = True
    response = client.post(reverse("first-run"), payload())

    assert response.status_code == 302
    assert response.url == reverse("login")
    organization = Organization.objects.get(slug="renshi-karate")
    dojo = Dojo.objects.for_organization(organization.pk).get()
    user = User.objects.get(email="owner@example.com")
    with allow_unscoped("test assertion"):
        assignment = RoleAssignment.objects.get(person=user.person)
    assert dojo.organization == organization
    assert user.check_password(PASSWORD)
    assert assignment.role == Role.ORG_ADMIN
    assert assignment.can_view_financials is True
    assert assignment.can_export_pii is True
    assert AuditLog.objects.filter(action="first_run_setup").exists()

    login = client.post(
        reverse("login"),
        {"email": user.email, "password": PASSWORD},
    )
    assert login.url == reverse("mfa-setup")


def test_the_first_owner_signs_straight_in_by_default(client, settings):
    """⚠ The very first sign-in on a brand new installation, with the shipped
    settings rather than forced ones.

    The test above deliberately turns enforcement ON to check that path still
    works. Nothing checked the default, which is the one every real deployment
    uses — so "MFA is compulsory on first sign in" could be reported twice while
    the suite stayed green.
    """
    assert settings.MFA_ENFORCEMENT_ENABLED is False, "the shipped default is off"

    client.post(reverse("first-run"), payload())
    user = User.objects.get(email="owner@example.com")

    login = client.post(
        reverse("login"),
        {"email": user.email, "password": PASSWORD},
        follow=True,
    )

    assert login.request["PATH_INFO"] != reverse("mfa-setup")
    assert reverse("mfa-setup") not in [step[0] for step in login.redirect_chain]
    assert client.get(reverse("today")).status_code == 200


def test_setup_closes_permanently_after_installation(client):
    client.post(reverse("first-run"), payload())

    assert client.get(reverse("first-run")).status_code == 404
    assert client.post(reverse("first-run"), payload()).status_code == 404
    assert reverse("first-run").encode() not in client.get(reverse("login")).content


def test_configured_setup_token_is_required_and_constant_path(client, settings):
    settings.FIRST_RUN_SETUP_TOKEN = "deployment-secret"

    refused = client.post(reverse("first-run"), payload(setup_token="wrong"))
    assert refused.status_code == 403
    assert not Organization.objects.exists()

    accepted = client.post(
        reverse("first-run"),
        payload(setup_token="deployment-secret"),
    )
    assert accepted.status_code == 302
    assert Organization.objects.count() == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timezone", "Not/A_Timezone"),
        ("currency", "US"),
        ("country", "Cambodia"),
        ("admin_password", "short"),
    ],
)
def test_invalid_foundation_values_are_rejected_without_partial_data(client, field, value):
    submitted = payload(**{field: value})
    if field == "admin_password":
        submitted["admin_password_confirm"] = value

    response = client.post(reverse("first-run"), submitted)

    assert response.status_code == 400
    assert not Organization.objects.exists()
    assert not User.objects.exists()


def test_setup_lock_prevents_concurrent_creation(client):
    cache.set(SETUP_LOCK_KEY, "locked", 60)

    response = client.post(reverse("first-run"), payload())

    assert response.status_code == 409
    assert not Organization.objects.exists()


def test_transaction_rolls_back_when_a_late_write_fails(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("apps.identity.setup.audit.record", fail)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(reverse("first-run"), payload())

    assert not Organization.objects.exists()
    assert not User.objects.exists()
