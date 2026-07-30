"""Django admin registration and tenant scoping — TASK_BRIEF admin surface."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.core.models import AuditLog
from apps.core.scoping import allow_unscoped
from apps.identity.models import (
    Organization,
    Person,
    Role,
    RoleAssignment,
    ScopeType,
    User,
)

pytestmark = pytest.mark.django_db


# Every model we register must load its changelist for a superuser.
REGISTERED_CHANGE_LISTS = [
    ("identity", "organization"),
    ("identity", "dojo"),
    ("identity", "person"),
    ("identity", "user"),
    ("identity", "roleassignment"),
    ("identity", "studentprofile"),
    ("identity", "guardianlink"),
    ("identity", "emergencycontact"),
    ("identity", "instructorassignment"),
    ("identity", "enrollment"),
    ("identity", "transferrecord"),
    ("scheduling", "classtemplate"),
    ("scheduling", "closureperiod"),
    ("scheduling", "classsession"),
    ("attendance", "attendancerecord"),
    ("ranks", "style"),
    ("ranks", "rankladder"),
    ("ranks", "rank"),
    ("ranks", "studentstyletrack"),
    ("ranks", "rankaward"),
    ("core", "document"),
    ("core", "setting"),
    ("core", "auditlog"),
]


@pytest.fixture
def superuser():
    return User.objects.create_superuser(
        email="root@example.com",
        password="test-password-long",
    )


@pytest.fixture
def two_org_staff():
    """Two organisations, one staff user scoped to org A only."""
    with allow_unscoped("test setup"):
        org_a = Organization.objects.create(name="Alpha Karate", slug="alpha-admin")
        org_b = Organization.objects.create(name="Beta Kai", slug="beta-admin")
        alice = Person.objects.create(
            organization=org_a, given_name="Alice", family_name="Admin"
        )
        bob = Person.objects.create(
            organization=org_b, given_name="Bob", family_name="Other"
        )
        Person.objects.create(
            organization=org_a, given_name="Sam", family_name="StudentA"
        )
        Person.objects.create(
            organization=org_b, given_name="Pat", family_name="StudentB"
        )
        RoleAssignment.objects.create(
            organization=org_a,
            person=alice,
            role=Role.ORG_ADMIN,
            scope_type=ScopeType.ORG,
        )
        staff = User.objects.create_user(
            email="alice@alpha.example",
            password="test-password-long",
            person=alice,
            is_staff=True,
        )
    return {
        "org_a": org_a,
        "org_b": org_b,
        "alice": alice,
        "bob": bob,
        "staff": staff,
    }


def test_every_registered_changelist_loads_for_superuser(superuser):
    client = Client()
    assert client.login(email="root@example.com", password="test-password-long")

    for app_label, model_name in REGISTERED_CHANGE_LISTS:
        url = reverse(f"admin:{app_label}_{model_name}_changelist")
        response = client.get(url)
        assert response.status_code == 200, f"{url} returned {response.status_code}"


def test_org_staff_sees_only_own_organisation_rows(two_org_staff):
    staff = two_org_staff["staff"]
    client = Client()
    assert client.login(email=staff.email, password="test-password-long")

    response = client.get(reverse("admin:identity_person_changelist"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "StudentA" in content
    assert "Alice" in content
    assert "StudentB" not in content
    assert "Bob" not in content
    assert "Beta" not in content


def test_auditlog_admin_refuses_add_change_delete(superuser):
    model_admin = admin.site._registry[AuditLog]
    factory = RequestFactory()
    request = factory.get("/admin/")
    request.user = superuser

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False

    client = Client()
    assert client.login(email="root@example.com", password="test-password-long")
    add_url = reverse("admin:core_auditlog_add")
    response = client.get(add_url)
    assert response.status_code == 403


def test_person_admin_delete_soft_deletes(superuser, two_org_staff):
    person = two_org_staff["bob"]
    client = Client()
    assert client.login(email="root@example.com", password="test-password-long")

    delete_url = reverse("admin:identity_person_delete", args=[person.pk])
    response = client.post(delete_url, {"post": "yes"})
    assert response.status_code in (200, 302)

    with allow_unscoped("assert soft delete survived"):
        person.refresh_from_db()
        assert person.deleted_at is not None
        assert Person.objects.filter(pk=person.pk).exists()


def test_all_task_models_are_registered():
    """Guard against forgetting to register a model listed in the brief."""
    expected = {
        ("identity", "organization"),
        ("identity", "dojo"),
        ("identity", "person"),
        ("identity", "user"),
        ("identity", "roleassignment"),
        ("identity", "studentprofile"),
        ("identity", "guardianlink"),
        ("identity", "emergencycontact"),
        ("identity", "instructorassignment"),
        ("ranks", "style"),
        ("ranks", "rankladder"),
        ("ranks", "rank"),
        ("ranks", "studentstyletrack"),
        ("ranks", "rankaward"),
        ("core", "document"),
        ("core", "setting"),
        ("core", "auditlog"),
    }
    registered = {
        (model._meta.app_label, model._meta.model_name) for model in admin.site._registry
    }
    missing = expected - registered
    assert not missing, f"Models not registered in admin: {missing}"
