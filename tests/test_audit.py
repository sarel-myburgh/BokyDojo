"""Audit trail — TODO 0.3.5, SEC 2.6."""

from __future__ import annotations

import pytest

from apps.core.audit import diff, record, record_change, snapshot
from apps.core.models import AuditLog
from apps.core.scoping import Actor, allow_unscoped
from apps.identity.models import Organization, Person

pytestmark = pytest.mark.django_db


@pytest.fixture
def org_and_person():
    with allow_unscoped("test setup"):
        org = Organization.objects.create(name="Test Org", slug="test-org")
        person = Person.objects.create(
            organization=org, given_name="Dara", family_name="Sok", city="Siem Reap"
        )
    return org, person


def _actor_for(org, person):
    return Actor(user_id=None, person_id=person.pk, organization_id=org.pk)


def test_record_writes_an_entry(org_and_person):
    org, person = org_and_person
    entry = record(
        AuditLog.Action.UPDATE,
        actor=_actor_for(org, person),
        subject=person,
        note="changed city",
    )
    assert entry is not None
    assert entry.subject_type == "identity.Person"
    assert entry.subject_id == str(person.pk)
    assert entry.organization_id == org.pk
    assert entry.actor_person_id == person.pk


def test_audit_entries_cannot_be_deleted(org_and_person):
    org, person = org_and_person
    entry = record(AuditLog.Action.CREATE, subject=person)
    with pytest.raises(NotImplementedError):
        entry.delete()


def test_snapshot_excludes_sensitive_fields(org_and_person):
    _org, person = org_and_person
    person.pin_hash = "should-never-appear"
    data = snapshot(person)
    assert "pin_hash" not in data
    assert data["given_name"] == "Dara"


def test_snapshot_truncates_long_values(org_and_person):
    _org, person = org_and_person
    person.given_name = "x" * 5000
    data = snapshot(person)
    assert len(data["given_name"]) <= 2001
    assert data["given_name"].endswith("…")


def test_diff_returns_only_changed_keys():
    before = {"a": 1, "b": 2, "c": 3}
    after = {"a": 1, "b": 99, "c": 3}
    changed_before, changed_after = diff(before, after)
    assert changed_before == {"b": 2}
    assert changed_after == {"b": 99}


def test_diff_returns_none_when_nothing_changed():
    assert diff({"a": 1}, {"a": 1}) == (None, None)


def test_record_change_captures_a_field_edit(org_and_person):
    _org, person = org_and_person
    before = snapshot(person)
    person.city = "Battambang"
    person.save(update_fields=["city"])

    entry = record_change(AuditLog.Action.UPDATE, person, before=before)
    assert entry.before == {"city": "Siem Reap"}
    assert entry.after == {"city": "Battambang"}


def test_record_survives_a_broken_audit_table(monkeypatch, org_and_person):
    """An audit failure is logged loudly but must not break the request."""
    _org, person = org_and_person

    def explode(*args, **kwargs):
        raise RuntimeError("table is on fire")

    monkeypatch.setattr(AuditLog.objects, "create", explode)
    assert record(AuditLog.Action.UPDATE, subject=person) is None


def test_strict_mode_propagates_failures(monkeypatch, org_and_person):
    """Permission changes and exports must not silently go unrecorded."""
    _org, person = org_and_person

    def explode(*args, **kwargs):
        raise RuntimeError("table is on fire")

    monkeypatch.setattr(AuditLog.objects, "create", explode)
    with pytest.raises(RuntimeError):
        record(AuditLog.Action.PERMISSION_CHANGE, subject=person, strict=True)


def test_system_actor_is_labelled(org_and_person):
    _org, person = org_and_person
    entry = record(AuditLog.Action.EXPORT, actor=Actor.system(), subject=person)
    assert entry.actor_label == "system"
